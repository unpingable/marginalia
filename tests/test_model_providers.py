# SPDX-License-Identifier: Apache-2.0
"""Deterministic qualification cases for configured model providers."""

from __future__ import annotations

import asyncio
import inspect
import json
from pathlib import Path

import httpx
import pytest

from gov_webui import model_providers as model_providers_module
from gov_webui.model_providers import (
    AnthropicMessagesTransport,
    OpenAICompatibleTransport,
    ProviderConfigurationError,
    ProviderError,
    load_provider_catalog,
)


def write_config(path: Path, *, default_model: str = "local-model") -> Path:
    path.write_text(
        json.dumps(
            {
                "version": 1,
                "default_model": default_model,
                "providers": [
                    {
                        "id": "existing",
                        "protocol": "existing-command",
                        "models": [
                            {
                                "id": "existing-default",
                                "label": "Existing backend",
                            }
                        ],
                    },
                    {
                        "id": "local",
                        "protocol": "openai-compatible",
                        "base_url": "http://provider.test/v1",
                        "models": [
                            {
                                "id": "local-model",
                                "model": "upstream-local",
                                "label": "Local model",
                                "tokenizer_encoding": "cl100k_base",
                                "token_safety_multiplier": 1.2,
                            }
                        ],
                    },
                    {
                        "id": "remote",
                        "protocol": "openai-compatible",
                        "base_url": "https://provider.example/v1",
                        "api_key_env": "REMOTE_TEST_KEY",
                        "timeout_seconds": 4,
                        "models": [
                            {
                                "id": "remote-model",
                                "model": "upstream-remote",
                                "label": "Remote model",
                            }
                        ],
                    },
                    {
                        "id": "anthropic",
                        "protocol": "anthropic-messages",
                        "base_url": "https://api.anthropic.test/v1",
                        "api_key_env": "ANTHROPIC_TEST_KEY",
                        "timeout_seconds": 4,
                        "models": [
                            {
                                "id": "anthropic-model",
                                "model": "claude-test-model",
                                "label": "Anthropic model",
                            }
                        ],
                    },
                ],
            }
        ),
        encoding="utf-8",
    )
    return path


def test_configuration_is_explicit_and_preserves_provider_model_distinction(
    tmp_path: Path,
) -> None:
    catalog = load_provider_catalog(write_config(tmp_path / "providers.json"))
    selected = catalog.resolve("local-model")

    assert catalog.default_model == "local-model"
    assert [model.id for model in catalog.models] == [
        "existing-default",
        "local-model",
        "remote-model",
        "anthropic-model",
    ]
    assert selected.provider_id == "local"
    assert selected.model_id == "upstream-local"
    assert selected.label == "Local model"
    assert selected.tokenizer_encoding == "cl100k_base"
    assert selected.token_safety_multiplier == 1.2


@pytest.mark.parametrize(
    "mutation, expected",
    [
        (lambda config: config["providers"].append(config["providers"][0]), "duplicate provider"),
        (
            lambda config: config["providers"][1]["models"].append(
                {"id": "local-model", "model": "second", "label": "Duplicate"}
            ),
            "duplicate configured model",
        ),
        (
            lambda config: config["providers"][1].update({"base_url": "provider.test/v1"}),
            r"http.*URL",
        ),
        (
            lambda config: config["providers"][1].update({"protocol": "unknown"}),
            "unsupported",
        ),
        (
            lambda config: config.update({"unexpected": True}),
            "unsupported fields",
        ),
    ],
)
def test_invalid_configuration_fails_clearly(tmp_path: Path, mutation, expected: str) -> None:
    path = write_config(tmp_path / "providers.json")
    config = json.loads(path.read_text(encoding="utf-8"))
    mutation(config)
    path.write_text(json.dumps(config), encoding="utf-8")

    with pytest.raises(ProviderConfigurationError, match=expected):
        load_provider_catalog(path)


def test_unknown_model_refused_without_fallback(tmp_path: Path) -> None:
    catalog = load_provider_catalog(write_config(tmp_path / "providers.json"))

    with pytest.raises(ProviderConfigurationError, match="not explicitly configured"):
        catalog.resolve("not-enabled")

    assert catalog.resolve("local-model").provider_id == "local"


def test_compatible_aliases_require_exact_protocol_provider_and_upstream_model(
    tmp_path: Path,
) -> None:
    path = write_config(tmp_path / "providers.json")
    config = json.loads(path.read_text(encoding="utf-8"))
    config["providers"][1]["models"].extend(
        [
            {
                "id": "local-model-maintenance",
                "model": "upstream-local",
                "label": "Same upstream alias",
                "purpose": "context-maintenance",
            },
            {
                "id": "local-model-other",
                "model": "upstream-other",
                "label": "Different upstream",
            },
        ]
    )
    path.write_text(json.dumps(config), encoding="utf-8")
    catalog = load_provider_catalog(path)

    assert catalog.compatible_model_ids(catalog.resolve("local-model-maintenance")) == frozenset(
        {"local-model", "local-model-maintenance"}
    )


def test_missing_credential_refused_without_substitution(tmp_path: Path) -> None:
    catalog = load_provider_catalog(write_config(tmp_path / "providers.json"))

    with pytest.raises(ProviderError) as caught:
        catalog.require_available("remote-model", environ={})

    assert caught.value.code == "missing_credential"
    assert caught.value.provider_id == "remote"
    assert caught.value.model_id == "upstream-remote"
    assert catalog.resolve("local-model").provider_id == "local"


def test_anthropic_configuration_requires_credential_variable(tmp_path: Path) -> None:
    path = write_config(tmp_path / "providers.json")
    config = json.loads(path.read_text(encoding="utf-8"))
    config["providers"][3].pop("api_key_env")
    path.write_text(json.dumps(config), encoding="utf-8")

    with pytest.raises(ProviderConfigurationError, match="api_key_env is required"):
        load_provider_catalog(path)


def test_unavailable_configured_default_uses_first_available_model(tmp_path: Path) -> None:
    catalog = load_provider_catalog(
        write_config(tmp_path / "providers.json", default_model="remote-model")
    )

    assert catalog.available_default(environ={}).id == "existing-default"
    assert catalog.require_available("", environ={}).id == "existing-default"

    with pytest.raises(ProviderError) as explicit:
        catalog.require_available("remote-model", environ={})
    assert explicit.value.code == "missing_credential"


@pytest.mark.asyncio
async def test_openai_compatible_request_construction(tmp_path: Path) -> None:
    model = load_provider_catalog(write_config(tmp_path / "providers.json")).resolve("remote-model")
    captured: dict[str, object] = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["authorization"] = request.headers.get("Authorization")
        captured["payload"] = json.loads(request.content)
        return httpx.Response(
            200,
            json={
                "model": "upstream-remote",
                "choices": [
                    {
                        "message": {"role": "assistant", "content": "bounded reply"},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {
                    "prompt_tokens": 4,
                    "completion_tokens": 2,
                    "total_tokens": 6,
                },
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        response = await OpenAICompatibleTransport(
            model,
            environ={"REMOTE_TEST_KEY": "fixture-secret"},
            client=client,
        ).complete(
            [
                {"role": "system", "content": "Keep the voice spare."},
                {"role": "user", "content": "Continue."},
                {"role": "assistant", "content": "Previously."},
            ],
            temperature=0.2,
            max_tokens=64,
        )

    assert captured["url"] == "https://provider.example/v1/chat/completions"
    assert captured["authorization"] == "Bearer fixture-secret"
    assert captured["payload"] == {
        "model": "upstream-remote",
        "messages": [
            {"role": "system", "content": "Keep the voice spare."},
            {"role": "user", "content": "Continue."},
            {"role": "assistant", "content": "Previously."},
        ],
        "stream": False,
        "temperature": 0.2,
        "max_tokens": 64,
    }
    assert response.content == "bounded reply"
    assert response.model_id == "upstream-remote"
    assert response.usage["total_tokens"] == 6


@pytest.mark.asyncio
async def test_http_failure_is_normalized_without_response_body(tmp_path: Path) -> None:
    model = load_provider_catalog(write_config(tmp_path / "providers.json")).resolve("local-model")

    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, text="private provider response body")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(ProviderError) as caught:
            await OpenAICompatibleTransport(model, client=client).complete(
                [{"role": "user", "content": "Hello"}]
            )

    assert caught.value.code == "http_error"
    assert caught.value.status_code == 429
    assert "private provider response body" not in str(caught.value)


@pytest.mark.asyncio
async def test_malformed_response_is_normalized(tmp_path: Path) -> None:
    model = load_provider_catalog(write_config(tmp_path / "providers.json")).resolve("local-model")

    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"choices": []})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(ProviderError) as caught:
            await OpenAICompatibleTransport(model, client=client).complete(
                [{"role": "user", "content": "Hello"}]
            )

    assert caught.value.code == "malformed_response"


@pytest.mark.asyncio
async def test_timeout_is_normalized(tmp_path: Path) -> None:
    model = load_provider_catalog(write_config(tmp_path / "providers.json")).resolve("local-model")

    async def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("late", request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(ProviderError) as caught:
            await OpenAICompatibleTransport(model, client=client).complete(
                [{"role": "user", "content": "Hello"}]
            )

    assert caught.value.code == "read_timeout"


@pytest.mark.asyncio
async def test_cancellation_propagates(tmp_path: Path) -> None:
    model = load_provider_catalog(write_config(tmp_path / "providers.json")).resolve("local-model")
    started = asyncio.Event()

    async def handler(request: httpx.Request) -> httpx.Response:
        started.set()
        await asyncio.Event().wait()
        raise AssertionError("unreachable")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        task = asyncio.create_task(
            OpenAICompatibleTransport(model, client=client).complete(
                [{"role": "user", "content": "Hello"}]
            )
        )
        await started.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task


@pytest.mark.asyncio
async def test_streaming_deltas_and_usage(tmp_path: Path) -> None:
    model = load_provider_catalog(write_config(tmp_path / "providers.json")).resolve("local-model")
    events = (
        'data: {"choices":[{"delta":{"content":"one "},"finish_reason":null}]}\n\n'
        'data: {"choices":[{"delta":{"content":"two"},"finish_reason":"stop"}]}\n\n'
        'data: {"choices":[],"usage":{"prompt_tokens":3,"completion_tokens":2,'
        '"total_tokens":5}}\n\n'
        "data: [DONE]\n\n"
    )

    async def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        assert payload["stream"] is True
        return httpx.Response(200, text=events)

    chunks = []
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        async for chunk in OpenAICompatibleTransport(model, client=client).stream(
            [{"role": "user", "content": "Hello"}]
        ):
            chunks.append(chunk)

    assert "".join(chunk.content for chunk in chunks) == "one two"
    assert chunks[-1].finish_reason == "stop"
    assert chunks[-1].usage == {
        "prompt_tokens": 3,
        "completion_tokens": 2,
        "total_tokens": 5,
    }


@pytest.mark.asyncio
async def test_anthropic_messages_request_construction(tmp_path: Path) -> None:
    model = load_provider_catalog(write_config(tmp_path / "providers.json")).resolve(
        "anthropic-model"
    )
    captured: dict[str, object] = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["api_key"] = request.headers.get("x-api-key")
        captured["version"] = request.headers.get("anthropic-version")
        captured["payload"] = json.loads(request.content)
        return httpx.Response(
            200,
            json={
                "id": "msg_fixture",
                "type": "message",
                "role": "assistant",
                "model": "claude-test-model",
                "content": [
                    {"type": "text", "text": "bounded "},
                    {"type": "text", "text": "reply"},
                ],
                "stop_reason": "end_turn",
                "usage": {"input_tokens": 5, "output_tokens": 2},
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        response = await AnthropicMessagesTransport(
            model,
            environ={"ANTHROPIC_TEST_KEY": "fixture-secret"},
            client=client,
        ).complete(
            [
                {"role": "system", "content": "Keep the voice spare."},
                {"role": "user", "content": "Continue."},
                {"role": "assistant", "content": "Previously."},
            ],
            temperature=0.2,
            max_tokens=64,
        )

    assert captured == {
        "url": "https://api.anthropic.test/v1/messages",
        "api_key": "fixture-secret",
        "version": "2023-06-01",
        "payload": {
            "model": "claude-test-model",
            "messages": [
                {"role": "user", "content": "Continue."},
                {"role": "assistant", "content": "Previously."},
            ],
            "max_tokens": 64,
            "stream": False,
            "system": "Keep the voice spare.",
            "temperature": 0.2,
        },
    }
    assert response.content == "bounded reply"
    assert response.model_id == "claude-test-model"
    assert response.usage == {
        "prompt_tokens": 5,
        "completion_tokens": 2,
        "total_tokens": 7,
    }
    assert response.finish_reason == "end_turn"


@pytest.mark.asyncio
async def test_anthropic_messages_stream_is_normalized(tmp_path: Path) -> None:
    model = load_provider_catalog(write_config(tmp_path / "providers.json")).resolve(
        "anthropic-model"
    )
    body = "\n".join(
        [
            "event: message_start",
            'data: {"type":"message_start","message":{"model":"claude-test-model","usage":{"input_tokens":8}}}',
            "",
            "event: content_block_delta",
            'data: {"type":"content_block_delta","delta":{"type":"text_delta","text":"hello"}}',
            "",
            "event: message_delta",
            'data: {"type":"message_delta","delta":{"stop_reason":"end_turn"},"usage":{"output_tokens":3}}',
            "",
            "event: message_stop",
            'data: {"type":"message_stop"}',
            "",
        ]
    )

    async def handler(request: httpx.Request) -> httpx.Response:
        assert json.loads(request.content)["stream"] is True
        return httpx.Response(200, text=body, headers={"content-type": "text/event-stream"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        chunks = [
            chunk
            async for chunk in AnthropicMessagesTransport(
                model,
                environ={"ANTHROPIC_TEST_KEY": "fixture-secret"},
                client=client,
            ).stream([{"role": "user", "content": "Hello"}])
        ]

    assert [chunk.content for chunk in chunks] == ["hello", ""]
    assert chunks[-1].usage == {
        "prompt_tokens": 8,
        "completion_tokens": 3,
        "total_tokens": 11,
    }
    assert chunks[-1].finish_reason == "end_turn"


def test_context_window_is_optional_and_bounded(tmp_path: Path) -> None:
    """A declared window is parsed; absence stays unconstrained."""

    def config(window_value) -> Path:
        payload = {
            "version": 1,
            "default_model": "windowed",
            "providers": [
                {
                    "id": "ollama",
                    "protocol": "openai-compatible",
                    "base_url": "http://localhost:11434/v1",
                    "models": [
                        {"id": "windowed", "model": "m", "label": "Windowed"},
                        {"id": "plain", "model": "m2", "label": "Plain"},
                    ],
                }
            ],
        }
        if window_value is not _ABSENT:
            payload["providers"][0]["models"][0]["context_window_tokens"] = window_value
        path = tmp_path / "providers.json"
        path.write_text(json.dumps(payload), encoding="utf-8")
        return path

    catalog = load_provider_catalog(config(32_000))
    assert catalog.resolve("windowed").context_window_tokens == 32_000
    # A model that declares nothing is unconstrained, not zero.
    assert catalog.resolve("plain").context_window_tokens is None

    catalog = load_provider_catalog(config(_ABSENT))
    assert catalog.resolve("windowed").context_window_tokens is None

    for bad in (0, -1, 999, 10_000_001, "32000", 32_000.5, True):
        with pytest.raises(ProviderConfigurationError, match="context_window_tokens"):
            load_provider_catalog(config(bad))


_ABSENT = object()


# ---------------------------------------------------------------------------
# OpenRouter-backed models
#
# OpenRouter is an OpenAI-compatible gateway, so it is configuration over the
# existing transport rather than a protocol of its own. These cases pin the
# parts that are specific to it: namespaced upstream slugs, the gateway base
# URL, credentials by environment name, and the error envelope it can return
# with a success status.
# ---------------------------------------------------------------------------

OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"
OPENROUTER_DEFAULT_MODEL = "z-ai/glm-5.3-flash"


def write_openrouter_config(
    path: Path,
    *,
    upstream_model: str = OPENROUTER_DEFAULT_MODEL,
    base_url: str = OPENROUTER_BASE_URL,
    api_key_env: str | None = "OPENROUTER_API_KEY",
) -> Path:
    provider: dict[str, object] = {
        "id": "openrouter",
        "protocol": "openai-compatible",
        "base_url": base_url,
        "timeout_seconds": 180,
        "models": [
            {
                "id": "openrouter-glm-5.3-flash",
                "model": upstream_model,
                "label": "GLM 5.3 Flash (OpenRouter)",
            }
        ],
    }
    if api_key_env is not None:
        provider["api_key_env"] = api_key_env
    path.write_text(
        json.dumps(
            {
                "version": 1,
                "default_model": "openrouter-glm-5.3-flash",
                "providers": [provider],
            }
        ),
        encoding="utf-8",
    )
    return path


def test_openrouter_model_is_configured_without_a_new_protocol(tmp_path: Path) -> None:
    """A namespaced gateway slug is an upstream model name, not a configured id."""
    catalog = load_provider_catalog(write_openrouter_config(tmp_path / "providers.json"))
    model = catalog.resolve("openrouter-glm-5.3-flash")

    assert model.protocol == "openai-compatible"
    assert model.provider_id == "openrouter"
    assert model.model_id == "z-ai/glm-5.3-flash"
    assert model.base_url == "https://openrouter.ai/api/v1"
    assert model.api_key_env == "OPENROUTER_API_KEY"
    assert model.purpose == "writing"
    # Selectable by writers and usable as the configured default.
    assert catalog.default_model == "openrouter-glm-5.3-flash"


def test_openrouter_model_id_is_configuration_not_transport_logic(tmp_path: Path) -> None:
    """Switching to another OpenRouter model is a configuration edit."""
    catalog = load_provider_catalog(
        write_openrouter_config(tmp_path / "providers.json", upstream_model="z-ai/glm-5.3")
    )
    assert catalog.resolve("openrouter-glm-5.3-flash").model_id == "z-ai/glm-5.3"
    assert "glm-5.3-flash" not in inspect.getsource(model_providers_module)


def test_openrouter_base_url_is_overridable(tmp_path: Path) -> None:
    catalog = load_provider_catalog(
        write_openrouter_config(
            tmp_path / "providers.json", base_url="https://gateway.internal/api/v1"
        )
    )
    assert catalog.resolve("openrouter-glm-5.3-flash").base_url == "https://gateway.internal/api/v1"


def test_openrouter_missing_credential_disables_without_substitution(tmp_path: Path) -> None:
    """An unset key fails clearly, and never silently falls back to another model."""
    catalog = load_provider_catalog(write_openrouter_config(tmp_path / "providers.json"))
    model = catalog.resolve("openrouter-glm-5.3-flash")

    assert model.availability_error({}) == (
        "required credential environment variable OPENROUTER_API_KEY is not set"
    )
    assert model.availability_error({"OPENROUTER_API_KEY": "placeholder"}) is None
    with pytest.raises(ProviderError) as caught:
        catalog.require_available("openrouter-glm-5.3-flash", {})
    assert caught.value.code == "missing_credential"
    assert "OPENROUTER_API_KEY" in str(caught.value)


def test_unrelated_backends_do_not_require_the_openrouter_credential(tmp_path: Path) -> None:
    """Adding the gateway must not make other providers depend on its key."""
    catalog = load_provider_catalog(write_config(tmp_path / "providers.json"))
    environ = {"REMOTE_TEST_KEY": "placeholder", "ANTHROPIC_TEST_KEY": "placeholder"}

    for model_id in ("existing-default", "local-model", "remote-model", "anthropic-model"):
        assert catalog.resolve(model_id).availability_error(environ) is None


@pytest.mark.asyncio
async def test_openrouter_request_and_response_translation(tmp_path: Path) -> None:
    """Marginalia's message shape in, Marginalia's response shape out."""
    model = load_provider_catalog(write_openrouter_config(tmp_path / "providers.json")).resolve(
        "openrouter-glm-5.3-flash"
    )
    captured: dict[str, object] = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["authorization"] = request.headers.get("Authorization")
        captured["payload"] = json.loads(request.content)
        return httpx.Response(
            200,
            json={
                "id": "gen-fixture",
                "model": "z-ai/glm-5.3-flash",
                "choices": [
                    {
                        "message": {"role": "assistant", "content": "The station clock stops."},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {"prompt_tokens": 11, "completion_tokens": 5, "total_tokens": 16},
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        response = await OpenAICompatibleTransport(
            model,
            environ={"OPENROUTER_API_KEY": "fixture-secret"},
            client=client,
        ).complete(
            [
                {"role": "system", "content": "Keep the voice spare."},
                {"role": "user", "content": "Continue."},
            ],
            max_tokens=64,
        )

    assert captured["url"] == "https://openrouter.ai/api/v1/chat/completions"
    assert captured["authorization"] == "Bearer fixture-secret"
    assert captured["payload"] == {
        "model": "z-ai/glm-5.3-flash",
        "messages": [
            {"role": "system", "content": "Keep the voice spare."},
            {"role": "user", "content": "Continue."},
        ],
        "stream": False,
        "max_tokens": 64,
    }
    assert response.content == "The station clock stops."
    assert response.model_id == "z-ai/glm-5.3-flash"
    assert response.usage == {"prompt_tokens": 11, "completion_tokens": 5, "total_tokens": 16}
    assert response.finish_reason == "stop"


@pytest.mark.asyncio
async def test_openrouter_credential_is_not_repeated_in_errors(tmp_path: Path) -> None:
    """A failure must not carry the bearer token into the typed error."""
    model = load_provider_catalog(write_openrouter_config(tmp_path / "providers.json")).resolve(
        "openrouter-glm-5.3-flash"
    )

    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, text="invalid api key sk-or-v1-fixture-secret")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(ProviderError) as caught:
            await OpenAICompatibleTransport(
                model,
                environ={"OPENROUTER_API_KEY": "sk-or-v1-fixture-secret"},
                client=client,
            ).complete([{"role": "user", "content": "Hello"}])

    rendered = f"{caught.value!r} {caught.value}"
    assert caught.value.code == "http_error"
    assert caught.value.status_code == 401
    assert "sk-or-v1-fixture-secret" not in rendered
    assert "invalid api key" not in rendered


@pytest.mark.asyncio
async def test_gateway_error_with_success_status_is_typed_not_malformed(tmp_path: Path) -> None:
    """A refusal returned as HTTP 200 is a provider error, not a corrupt body.

    Aggregating gateways report rate limits and routing failures this way. It
    must never read as an empty successful generation.
    """
    model = load_provider_catalog(write_openrouter_config(tmp_path / "providers.json")).resolve(
        "openrouter-glm-5.3-flash"
    )

    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"error": {"code": 429, "message": "rate limited upstream"}},
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(ProviderError) as caught:
            await OpenAICompatibleTransport(
                model,
                environ={"OPENROUTER_API_KEY": "fixture-secret"},
                client=client,
            ).complete([{"role": "user", "content": "Hello"}])

    assert caught.value.code == "provider_error"
    assert caught.value.status_code == 429
    assert "rate limited upstream" not in str(caught.value)


@pytest.mark.asyncio
async def test_openrouter_model_substitution_is_refused(tmp_path: Path) -> None:
    """A gateway that answers with a different model must not be accepted."""
    model = load_provider_catalog(write_openrouter_config(tmp_path / "providers.json")).resolve(
        "openrouter-glm-5.3-flash"
    )

    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "model": "z-ai/glm-5.3",
                "choices": [
                    {"message": {"role": "assistant", "content": "text"}, "finish_reason": "stop"}
                ],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(ProviderError) as caught:
            await OpenAICompatibleTransport(
                model,
                environ={"OPENROUTER_API_KEY": "fixture-secret"},
                client=client,
            ).complete([{"role": "user", "content": "Hello"}])

    assert caught.value.code == "model_mismatch"


@pytest.mark.asyncio
async def test_openrouter_streaming_tolerates_gateway_keepalive_comments(
    tmp_path: Path,
) -> None:
    """Gateways emit SSE comment lines while routing; they are not data."""
    model = load_provider_catalog(write_openrouter_config(tmp_path / "providers.json")).resolve(
        "openrouter-glm-5.3-flash"
    )

    body = (
        ": OPENROUTER PROCESSING\n\n"
        'data: {"model":"z-ai/glm-5.3-flash","choices":[{"delta":{"content":"The "}}]}\n\n'
        ": OPENROUTER PROCESSING\n\n"
        'data: {"model":"z-ai/glm-5.3-flash","choices":[{"delta":{"content":"clock"},'
        '"finish_reason":"stop"}],"usage":{"prompt_tokens":3,"completion_tokens":2,'
        '"total_tokens":5}}\n\n'
        "data: [DONE]\n\n"
    )

    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=body)

    chunks = []
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        async for chunk in OpenAICompatibleTransport(
            model,
            environ={"OPENROUTER_API_KEY": "fixture-secret"},
            client=client,
        ).stream([{"role": "user", "content": "Continue."}]):
            chunks.append(chunk)

    assert "".join(chunk.content for chunk in chunks if chunk.content) == "The clock"
    assert chunks[-1].usage == {"prompt_tokens": 3, "completion_tokens": 2, "total_tokens": 5}
    assert chunks[-1].finish_reason == "stop"


@pytest.mark.asyncio
async def test_reasoning_truncation_is_reported_as_truncation_not_corruption(
    tmp_path: Path,
) -> None:
    """Null content with a length stop is an exhausted output budget.

    Reasoning models spend the completion budget before answering. Calling that a
    malformed body sends an operator after the wrong problem, and it must not
    become an empty successful generation either.
    """
    model = load_provider_catalog(write_openrouter_config(tmp_path / "providers.json")).resolve(
        "openrouter-glm-5.3-flash"
    )

    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "model": "z-ai/glm-5.3-flash",
                "choices": [
                    {
                        "message": {"role": "assistant", "content": None, "reasoning": "..."},
                        "finish_reason": "length",
                    }
                ],
                "usage": {"prompt_tokens": 19, "completion_tokens": 16, "total_tokens": 35},
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(ProviderError) as caught:
            await OpenAICompatibleTransport(
                model,
                environ={"OPENROUTER_API_KEY": "fixture-secret"},
                client=client,
            ).complete([{"role": "user", "content": "Hello"}])

    assert caught.value.code == "truncated_response"


@pytest.mark.asyncio
async def test_null_content_without_a_length_stop_remains_malformed(tmp_path: Path) -> None:
    """The narrower code must not swallow genuinely unusable bodies."""
    model = load_provider_catalog(write_openrouter_config(tmp_path / "providers.json")).resolve(
        "openrouter-glm-5.3-flash"
    )

    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "model": "z-ai/glm-5.3-flash",
                "choices": [{"message": {"role": "assistant", "content": None}}],
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(ProviderError) as caught:
            await OpenAICompatibleTransport(
                model,
                environ={"OPENROUTER_API_KEY": "fixture-secret"},
                client=client,
            ).complete([{"role": "user", "content": "Hello"}])

    assert caught.value.code == "malformed_response"


def test_locality_is_declared_and_defaults_to_hosted(tmp_path: Path) -> None:
    """An undeclared provider is never presented to the writer as local.

    "Local" is a privacy and cost claim. Marginalia cannot observe where weights
    run — a base URL's hostname is a guess — so the deployment declares it and
    the absent value fails toward the weaker claim.
    """
    catalog = load_provider_catalog(write_config(tmp_path / "providers.json"))

    # Named "local-model", served over http, no credential — every heuristic a
    # picker might apply says local. None of them is evidence.
    undeclared = catalog.resolve("local-model")
    assert undeclared.inference == "hosted"
    assert undeclared.category == "hosted-model"
    assert undeclared.public_dict()["category_label"] == "Hosted models"


def test_declared_local_inference_reaches_the_writer_facing_category(tmp_path: Path) -> None:
    path = tmp_path / "providers.json"
    payload = json.loads(write_config(path).read_text(encoding="utf-8"))
    for provider in payload["providers"]:
        if provider["id"] == "local":
            provider["inference"] = "local"
    path.write_text(json.dumps(payload), encoding="utf-8")

    model = load_provider_catalog(path).resolve("local-model")
    assert model.kind == "model"
    assert model.inference == "local"
    assert model.access == "open"
    assert model.category == "local-model"
    assert model.category_label == "Local models"


def test_agent_protocols_are_subscription_agents_not_local_models(tmp_path: Path) -> None:
    """An agent process running here does not make its inference local."""
    catalog = load_provider_catalog(write_config(tmp_path / "providers.json"))
    agent = catalog.resolve("existing-default")
    assert agent.kind == "agent"
    # The parser forbids api_key_env on every agent protocol, so the credential
    # is always the tool's own login. That makes "subscription" a derivation.
    assert agent.api_key_env is None
    assert agent.access == "subscription"
    assert agent.category == "subscription-agent"
    assert agent.category_label == "Subscription agents"


def test_metered_api_model_is_a_hosted_model(tmp_path: Path) -> None:
    catalog = load_provider_catalog(write_config(tmp_path / "providers.json"))
    remote = catalog.resolve("remote-model")
    assert (remote.kind, remote.access, remote.category) == ("model", "api", "hosted-model")


def test_unsupported_inference_value_is_refused(tmp_path: Path) -> None:
    path = tmp_path / "providers.json"
    payload = json.loads(write_config(path).read_text(encoding="utf-8"))
    payload["providers"][1]["inference"] = "on-prem"
    path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ProviderConfigurationError, match="inference must be 'local' or 'hosted'"):
        load_provider_catalog(path)


def test_read_bound_defaults_to_the_total_budget_not_a_short_slice(tmp_path: Path) -> None:
    """A first token tens of seconds away must not read as an idle provider.

    Cold local weights take ~30s to load before emitting anything, and a
    reasoning model spends its opening minute producing tokens a non-streaming
    caller cannot see. A read bound shorter than the total budget cuts those
    requests off at the moment they were about to succeed.
    """
    path = tmp_path / "providers.json"
    payload = json.loads(write_config(path).read_text(encoding="utf-8"))
    for provider in payload["providers"]:
        if provider["id"] == "local":
            provider["timeout_seconds"] = 600
    path.write_text(json.dumps(payload), encoding="utf-8")

    model = load_provider_catalog(path).resolve("local-model")
    assert model.timeout_seconds == 600
    assert model.read_timeout_seconds == 600
    # The connect bound stays short: a refused or blackholed endpoint is a
    # different failure, and waiting the full budget on it helps nobody.
    assert model.connect_timeout_seconds == 10


def test_deployment_may_still_declare_a_tighter_read_bound(tmp_path: Path) -> None:
    path = tmp_path / "providers.json"
    payload = json.loads(write_config(path).read_text(encoding="utf-8"))
    for provider in payload["providers"]:
        if provider["id"] == "local":
            provider["timeout_seconds"] = 600
            provider["read_timeout_seconds"] = 120
    path.write_text(json.dumps(payload), encoding="utf-8")

    assert load_provider_catalog(path).resolve("local-model").read_timeout_seconds == 120

# SPDX-License-Identifier: Apache-2.0
"""Deterministic qualification cases for configured model providers."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import httpx
import pytest

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

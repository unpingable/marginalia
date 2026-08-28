# SPDX-License-Identifier: Apache-2.0
"""Typed model-provider configuration and OpenAI-compatible transport."""

from __future__ import annotations

import asyncio
import json
import os
import re
from collections.abc import AsyncIterator, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import httpx


_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]*$")
_ENV_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_PROTOCOLS = {"existing-command", "openai-compatible"}


class ProviderConfigurationError(ValueError):
    """Provider configuration is invalid and cannot be used safely."""


class ProviderError(RuntimeError):
    """Normalized provider failure that is safe to show without response bodies."""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        provider_id: str,
        model_id: str,
        status_code: int | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.provider_id = provider_id
        self.model_id = model_id
        self.status_code = status_code

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "code": self.code,
            "message": str(self),
            "provider_id": self.provider_id,
            "model_id": self.model_id,
        }
        if self.status_code is not None:
            result["status_code"] = self.status_code
        return result


@dataclass(frozen=True)
class ConfiguredModel:
    """One explicitly enabled human-selectable model."""

    id: str
    label: str
    provider_id: str
    model_id: str
    protocol: str
    base_url: str | None
    api_key_env: str | None
    timeout_seconds: float
    command_model: str | None = None

    def availability_error(
        self, environ: Mapping[str, str] | None = None
    ) -> str | None:
        env = os.environ if environ is None else environ
        if self.api_key_env and not env.get(self.api_key_env):
            return f"required credential environment variable {self.api_key_env} is not set"
        return None

    def public_dict(
        self, environ: Mapping[str, str] | None = None
    ) -> dict[str, Any]:
        unavailable = self.availability_error(environ)
        result: dict[str, Any] = {
            "id": self.id,
            "label": self.label,
            "provider_id": self.provider_id,
            "model_id": self.model_id,
            "protocol": self.protocol,
            "available": unavailable is None,
        }
        if unavailable:
            result["unavailable_reason"] = unavailable
        return result


@dataclass(frozen=True)
class ProviderCatalog:
    """Validated provider configuration with a globally unique model namespace."""

    default_model: str
    models: tuple[ConfiguredModel, ...]

    def __post_init__(self) -> None:
        lookup = {model.id: model for model in self.models}
        if len(lookup) != len(self.models):
            raise ProviderConfigurationError("duplicate configured model id")
        if self.default_model not in lookup:
            raise ProviderConfigurationError(
                f"default_model {self.default_model!r} is not an enabled model"
            )

    def resolve(self, model_id: str | None) -> ConfiguredModel:
        selected = model_id or self.default_model
        for model in self.models:
            if model.id == selected:
                return model
        raise ProviderConfigurationError(
            f"model {selected!r} is not explicitly configured"
        )

    def require_available(
        self,
        model_id: str | None,
        environ: Mapping[str, str] | None = None,
    ) -> ConfiguredModel:
        model = self.resolve(model_id)
        unavailable = model.availability_error(environ)
        if unavailable:
            raise ProviderError(
                "missing_credential",
                unavailable,
                provider_id=model.provider_id,
                model_id=model.model_id,
            )
        return model


def _require_object(value: Any, location: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ProviderConfigurationError(f"{location} must be an object")
    return value


def _require_list(value: Any, location: str) -> list[Any]:
    if not isinstance(value, list):
        raise ProviderConfigurationError(f"{location} must be a list")
    return value


def _required_string(value: Any, location: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ProviderConfigurationError(f"{location} must be a non-empty string")
    return value.strip()


def _identifier(value: Any, location: str) -> str:
    result = _required_string(value, location)
    if not _ID_RE.fullmatch(result):
        raise ProviderConfigurationError(
            f"{location} must use letters, digits, '.', '_', ':', or '-'"
        )
    return result


def _base_url(value: Any, location: str) -> str:
    result = _required_string(value, location).rstrip("/")
    parsed = urlsplit(result)
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.netloc
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
    ):
        raise ProviderConfigurationError(
            f"{location} must be an http(s) URL without credentials, query, or fragment"
        )
    return result


def _reject_unknown(data: dict[str, Any], allowed: set[str], location: str) -> None:
    unknown = sorted(set(data) - allowed)
    if unknown:
        raise ProviderConfigurationError(
            f"{location} contains unsupported fields: {', '.join(unknown)}"
        )


def load_provider_catalog(path: str | Path) -> ProviderCatalog:
    """Load and strictly validate one versioned JSON provider configuration."""

    config_path = Path(path)
    try:
        raw = json.loads(config_path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise ProviderConfigurationError(
            f"cannot read provider configuration: {exc.strerror or type(exc).__name__}"
        ) from exc
    except json.JSONDecodeError as exc:
        raise ProviderConfigurationError(
            f"provider configuration is not valid JSON: line {exc.lineno}"
        ) from exc

    root = _require_object(raw, "configuration")
    _reject_unknown(root, {"version", "default_model", "providers"}, "configuration")
    if root.get("version") != 1:
        raise ProviderConfigurationError("configuration.version must be 1")

    default_model = _identifier(root.get("default_model"), "configuration.default_model")
    providers = _require_list(root.get("providers"), "configuration.providers")
    if not providers:
        raise ProviderConfigurationError("configuration.providers must not be empty")

    provider_ids: set[str] = set()
    configured_ids: set[str] = set()
    models: list[ConfiguredModel] = []

    for provider_index, provider_value in enumerate(providers):
        location = f"configuration.providers[{provider_index}]"
        provider = _require_object(provider_value, location)
        _reject_unknown(
            provider,
            {
                "id",
                "protocol",
                "base_url",
                "api_key_env",
                "timeout_seconds",
                "models",
            },
            location,
        )
        provider_id = _identifier(provider.get("id"), f"{location}.id")
        if provider_id in provider_ids:
            raise ProviderConfigurationError(f"duplicate provider id {provider_id!r}")
        provider_ids.add(provider_id)

        protocol = _required_string(provider.get("protocol"), f"{location}.protocol")
        if protocol not in _PROTOCOLS:
            raise ProviderConfigurationError(
                f"{location}.protocol {protocol!r} is unsupported"
            )

        raw_timeout = provider.get("timeout_seconds", 120)
        if (
            isinstance(raw_timeout, bool)
            or not isinstance(raw_timeout, (int, float))
            or not 0.1 <= float(raw_timeout) <= 1800
        ):
            raise ProviderConfigurationError(
                f"{location}.timeout_seconds must be between 0.1 and 1800"
            )
        timeout_seconds = float(raw_timeout)

        base_url: str | None = None
        api_key_env: str | None = None
        if protocol == "openai-compatible":
            base_url = _base_url(provider.get("base_url"), f"{location}.base_url")
            raw_env = provider.get("api_key_env")
            if raw_env is not None:
                api_key_env = _required_string(raw_env, f"{location}.api_key_env")
                if not _ENV_RE.fullmatch(api_key_env):
                    raise ProviderConfigurationError(
                        f"{location}.api_key_env is not an environment variable name"
                    )
        elif provider.get("base_url") is not None or provider.get("api_key_env") is not None:
            raise ProviderConfigurationError(
                f"{location} command provider cannot define base_url or api_key_env"
            )

        provider_models = _require_list(provider.get("models"), f"{location}.models")
        if not provider_models:
            raise ProviderConfigurationError(f"{location}.models must not be empty")

        for model_index, model_value in enumerate(provider_models):
            model_location = f"{location}.models[{model_index}]"
            model = _require_object(model_value, model_location)
            _reject_unknown(model, {"id", "model", "label"}, model_location)
            configured_id = _identifier(model.get("id"), f"{model_location}.id")
            if configured_id in configured_ids:
                raise ProviderConfigurationError(
                    f"duplicate configured model id {configured_id!r}"
                )
            configured_ids.add(configured_id)
            label = _required_string(model.get("label"), f"{model_location}.label")

            raw_model_id = model.get("model")
            command_model: str | None = None
            if protocol == "openai-compatible":
                upstream_model = _identifier(raw_model_id, f"{model_location}.model")
            else:
                if raw_model_id is None:
                    upstream_model = configured_id
                else:
                    command_model = _identifier(raw_model_id, f"{model_location}.model")
                    upstream_model = command_model

            models.append(
                ConfiguredModel(
                    id=configured_id,
                    label=label,
                    provider_id=provider_id,
                    model_id=upstream_model,
                    protocol=protocol,
                    base_url=base_url,
                    api_key_env=api_key_env,
                    timeout_seconds=timeout_seconds,
                    command_model=command_model,
                )
            )

    return ProviderCatalog(default_model=default_model, models=tuple(models))


@dataclass(frozen=True)
class ProviderResponse:
    content: str
    model_id: str
    usage: dict[str, int]
    finish_reason: str | None


@dataclass(frozen=True)
class ProviderChunk:
    content: str = ""
    usage: dict[str, int] | None = None
    finish_reason: str | None = None


class OpenAICompatibleTransport:
    """One bounded transport for explicitly configured chat-completions APIs."""

    def __init__(
        self,
        model: ConfiguredModel,
        *,
        environ: Mapping[str, str] | None = None,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        if model.protocol != "openai-compatible" or not model.base_url:
            raise ValueError("model is not configured for openai-compatible transport")
        self.model = model
        self._environ = os.environ if environ is None else environ
        self._client = client

    @property
    def endpoint(self) -> str:
        assert self.model.base_url is not None
        return f"{self.model.base_url}/chat/completions"

    def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self.model.api_key_env:
            credential = self._environ.get(self.model.api_key_env)
            if not credential:
                raise ProviderError(
                    "missing_credential",
                    f"required credential environment variable "
                    f"{self.model.api_key_env} is not set",
                    provider_id=self.model.provider_id,
                    model_id=self.model.model_id,
                )
            headers["Authorization"] = f"Bearer {credential}"
        return headers

    def _payload(
        self,
        messages: Sequence[Mapping[str, str]],
        *,
        stream: bool,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> dict[str, Any]:
        normalized: list[dict[str, str]] = []
        for index, message in enumerate(messages):
            role = message.get("role")
            content = message.get("content")
            if role not in {"system", "user", "assistant"} or not isinstance(content, str):
                raise ProviderError(
                    "invalid_request",
                    f"message {index} must have a supported role and string content",
                    provider_id=self.model.provider_id,
                    model_id=self.model.model_id,
                )
            normalized.append({"role": role, "content": content})
        if not normalized:
            raise ProviderError(
                "invalid_request",
                "at least one message is required",
                provider_id=self.model.provider_id,
                model_id=self.model.model_id,
            )
        payload: dict[str, Any] = {
            "model": self.model.model_id,
            "messages": normalized,
            "stream": stream,
        }
        if temperature is not None:
            payload["temperature"] = temperature
        if max_tokens is not None:
            payload["max_tokens"] = max_tokens
        return payload

    def _error(
        self, code: str, message: str, *, status_code: int | None = None
    ) -> ProviderError:
        return ProviderError(
            code,
            message,
            provider_id=self.model.provider_id,
            model_id=self.model.model_id,
            status_code=status_code,
        )

    async def complete(
        self,
        messages: Sequence[Mapping[str, str]],
        *,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> ProviderResponse:
        payload = self._payload(
            messages, stream=False, temperature=temperature, max_tokens=max_tokens
        )
        owns_client = self._client is None
        client = self._client or httpx.AsyncClient(timeout=self.model.timeout_seconds)
        try:
            response = await client.post(
                self.endpoint, headers=self._headers(), json=payload
            )
            if response.status_code < 200 or response.status_code >= 300:
                raise self._error(
                    "http_error",
                    f"provider returned HTTP {response.status_code}",
                    status_code=response.status_code,
                )
            try:
                data = response.json()
                choice = data["choices"][0]
                content = choice["message"]["content"]
                if not isinstance(content, str):
                    raise TypeError
                response_model = data.get("model", self.model.model_id)
                if not isinstance(response_model, str):
                    raise TypeError
                if response_model != self.model.model_id:
                    raise self._error(
                        "model_mismatch",
                        "provider returned a different model than requested",
                    )
                usage_raw = data.get("usage") or {}
                usage = {
                    "prompt_tokens": int(usage_raw.get("prompt_tokens", 0)),
                    "completion_tokens": int(usage_raw.get("completion_tokens", 0)),
                    "total_tokens": int(usage_raw.get("total_tokens", 0)),
                }
                finish_reason = choice.get("finish_reason")
            except (KeyError, IndexError, TypeError, ValueError, json.JSONDecodeError) as exc:
                raise self._error(
                    "malformed_response",
                    "provider returned a malformed chat-completion response",
                ) from exc
            return ProviderResponse(content, response_model, usage, finish_reason)
        except ProviderError:
            raise
        except httpx.TimeoutException as exc:
            raise self._error("timeout", "provider request timed out") from exc
        except httpx.HTTPError as exc:
            raise self._error("transport_error", "provider request failed") from exc
        except asyncio.CancelledError:
            raise
        finally:
            if owns_client:
                await client.aclose()

    async def stream(
        self,
        messages: Sequence[Mapping[str, str]],
        *,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> AsyncIterator[ProviderChunk]:
        payload = self._payload(
            messages, stream=True, temperature=temperature, max_tokens=max_tokens
        )
        owns_client = self._client is None
        client = self._client or httpx.AsyncClient(timeout=self.model.timeout_seconds)
        saw_event = False
        final_usage: dict[str, int] | None = None
        finish_reason: str | None = None
        try:
            async with client.stream(
                "POST", self.endpoint, headers=self._headers(), json=payload
            ) as response:
                if response.status_code < 200 or response.status_code >= 300:
                    raise self._error(
                        "http_error",
                        f"provider returned HTTP {response.status_code}",
                        status_code=response.status_code,
                    )
                async for line in response.aiter_lines():
                    if not line or line.startswith(":"):
                        continue
                    if not line.startswith("data:"):
                        continue
                    raw_event = line[5:].strip()
                    if raw_event == "[DONE]":
                        break
                    try:
                        data = json.loads(raw_event)
                        response_model = data.get("model")
                        if response_model is not None and response_model != self.model.model_id:
                            raise self._error(
                                "model_mismatch",
                                "provider returned a different model than requested",
                            )
                        choice = (data.get("choices") or [None])[0]
                        if choice is not None:
                            delta = choice.get("delta") or {}
                            content = delta.get("content", "")
                            if content is None:
                                content = ""
                            if not isinstance(content, str):
                                raise TypeError
                            if choice.get("finish_reason") is not None:
                                finish_reason = str(choice["finish_reason"])
                            if content:
                                saw_event = True
                                yield ProviderChunk(content=content)
                        usage_raw = data.get("usage")
                        if usage_raw:
                            final_usage = {
                                "prompt_tokens": int(usage_raw.get("prompt_tokens", 0)),
                                "completion_tokens": int(
                                    usage_raw.get("completion_tokens", 0)
                                ),
                                "total_tokens": int(usage_raw.get("total_tokens", 0)),
                            }
                    except (TypeError, ValueError, json.JSONDecodeError) as exc:
                        raise self._error(
                            "malformed_response",
                            "provider returned malformed streaming data",
                        ) from exc
            if not saw_event and finish_reason is None and final_usage is None:
                raise self._error(
                    "malformed_response",
                    "provider stream ended without completion data",
                )
            yield ProviderChunk(
                usage=final_usage or {
                    "prompt_tokens": 0,
                    "completion_tokens": 0,
                    "total_tokens": 0,
                },
                finish_reason=finish_reason or "stop",
            )
        except ProviderError:
            raise
        except httpx.TimeoutException as exc:
            raise self._error("timeout", "provider request timed out") from exc
        except httpx.HTTPError as exc:
            raise self._error("transport_error", "provider request failed") from exc
        except asyncio.CancelledError:
            raise
        finally:
            if owns_client:
                await client.aclose()

# SPDX-License-Identifier: Apache-2.0
"""Typed model-provider configuration and bounded HTTP/process transports."""

from __future__ import annotations

import asyncio
import json
import os
import re
import signal
from collections.abc import AsyncIterator, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import httpx


_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]*$")
_ENV_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_MODEL_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]*$")
_PROTOCOLS = {
    "anthropic-messages",
    "existing-command",
    "local-command",
    "openai-compatible",
}
_COMMAND_ADAPTERS = {"claude-code", "kimi-code"}


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
class CommandConfiguration:
    """Typed process configuration for one supported local command adapter."""

    adapter: str
    executable_env: str
    working_directory_env: str


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
    connect_timeout_seconds: float
    read_timeout_seconds: float
    timeout_seconds: float
    command_model: str | None = None
    command: CommandConfiguration | None = None

    def availability_error(self, environ: Mapping[str, str] | None = None) -> str | None:
        env = os.environ if environ is None else environ
        if self.api_key_env and not env.get(self.api_key_env):
            return f"required credential environment variable {self.api_key_env} is not set"
        if self.command:
            executable = env.get(self.command.executable_env, "").strip()
            if not executable:
                return (
                    "required command environment variable "
                    f"{self.command.executable_env} is not set"
                )
            if not os.path.isfile(executable) or not os.access(executable, os.X_OK):
                return "configured command executable is unavailable"
            working_directory = env.get(self.command.working_directory_env, "").strip()
            if not working_directory:
                return (
                    "required command environment variable "
                    f"{self.command.working_directory_env} is not set"
                )
            if not os.path.isdir(working_directory):
                return "configured command working directory is unavailable"
        return None

    def public_dict(self, environ: Mapping[str, str] | None = None) -> dict[str, Any]:
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
        raise ProviderConfigurationError(f"model {selected!r} is not explicitly configured")

    def available_default(
        self,
        environ: Mapping[str, str] | None = None,
    ) -> ConfiguredModel | None:
        """Return the configured default when usable, otherwise the first usable model."""
        configured_default = self.resolve(self.default_model)
        if configured_default.availability_error(environ) is None:
            return configured_default
        return next(
            (model for model in self.models if model.availability_error(environ) is None),
            None,
        )

    def require_available(
        self,
        model_id: str | None,
        environ: Mapping[str, str] | None = None,
    ) -> ConfiguredModel:
        model = self.resolve(model_id)
        if not model_id:
            model = self.available_default(environ) or model
        unavailable = model.availability_error(environ)
        if unavailable:
            raise ProviderError(
                "missing_credential" if model.api_key_env else "unavailable_command",
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


def _model_name(value: Any, location: str) -> str:
    result = _required_string(value, location)
    if not _MODEL_RE.fullmatch(result):
        raise ProviderConfigurationError(f"{location} must use a bounded provider model name")
    return result


def _environment_name(value: Any, location: str) -> str:
    result = _required_string(value, location)
    if not _ENV_RE.fullmatch(result):
        raise ProviderConfigurationError(f"{location} is not an environment variable name")
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
                "command",
                "connect_timeout_seconds",
                "read_timeout_seconds",
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
            raise ProviderConfigurationError(f"{location}.protocol {protocol!r} is unsupported")

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

        def timeout_field(name: str, default: float) -> float:
            raw_value = provider.get(name, default)
            if (
                isinstance(raw_value, bool)
                or not isinstance(raw_value, (int, float))
                or not 0.1 <= float(raw_value) <= timeout_seconds
            ):
                raise ProviderConfigurationError(
                    f"{location}.{name} must be between 0.1 and timeout_seconds"
                )
            return float(raw_value)

        connect_timeout_seconds = timeout_field(
            "connect_timeout_seconds", min(10.0, timeout_seconds)
        )
        read_timeout_seconds = timeout_field("read_timeout_seconds", min(30.0, timeout_seconds))

        base_url: str | None = None
        api_key_env: str | None = None
        command: CommandConfiguration | None = None
        if protocol in {"anthropic-messages", "openai-compatible"}:
            base_url = _base_url(provider.get("base_url"), f"{location}.base_url")
            raw_env = provider.get("api_key_env")
            if raw_env is not None:
                api_key_env = _environment_name(raw_env, f"{location}.api_key_env")
            if protocol == "anthropic-messages" and api_key_env is None:
                raise ProviderConfigurationError(
                    f"{location}.api_key_env is required for anthropic-messages"
                )
        elif protocol == "local-command":
            if provider.get("base_url") is not None or provider.get("api_key_env") is not None:
                raise ProviderConfigurationError(
                    f"{location} local command provider cannot define base_url or api_key_env"
                )
            command_data = _require_object(provider.get("command"), f"{location}.command")
            _reject_unknown(
                command_data,
                {"adapter", "executable_env", "working_directory_env"},
                f"{location}.command",
            )
            adapter = _required_string(command_data.get("adapter"), f"{location}.command.adapter")
            if adapter not in _COMMAND_ADAPTERS:
                raise ProviderConfigurationError(
                    f"{location}.command.adapter {adapter!r} is unsupported"
                )
            command = CommandConfiguration(
                adapter=adapter,
                executable_env=_environment_name(
                    command_data.get("executable_env"),
                    f"{location}.command.executable_env",
                ),
                working_directory_env=_environment_name(
                    command_data.get("working_directory_env"),
                    f"{location}.command.working_directory_env",
                ),
            )
        elif (
            provider.get("base_url") is not None
            or provider.get("api_key_env") is not None
            or provider.get("command") is not None
        ):
            raise ProviderConfigurationError(
                f"{location} existing command provider cannot define transport configuration"
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
                raise ProviderConfigurationError(f"duplicate configured model id {configured_id!r}")
            configured_ids.add(configured_id)
            label = _required_string(model.get("label"), f"{model_location}.label")

            raw_model_id = model.get("model")
            command_model: str | None = None
            if protocol in {
                "anthropic-messages",
                "openai-compatible",
                "local-command",
            }:
                upstream_model = _model_name(raw_model_id, f"{model_location}.model")
                if protocol == "local-command":
                    command_model = upstream_model
            else:
                if raw_model_id is None:
                    upstream_model = configured_id
                else:
                    command_model = _model_name(raw_model_id, f"{model_location}.model")
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
                    connect_timeout_seconds=connect_timeout_seconds,
                    read_timeout_seconds=read_timeout_seconds,
                    timeout_seconds=timeout_seconds,
                    command_model=command_model,
                    command=command,
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


def _http_timeout(model: ConfiguredModel) -> httpx.Timeout:
    """Use distinct connection and read-idle bounds inside the total deadline."""
    return httpx.Timeout(
        model.read_timeout_seconds,
        connect=model.connect_timeout_seconds,
    )


def _deadline_error(model: ConfiguredModel) -> ProviderError:
    return ProviderError(
        "deadline_exceeded",
        f"provider execution exceeded its {model.timeout_seconds:g}-second deadline",
        provider_id=model.provider_id,
        model_id=model.model_id,
    )


async def _bounded_http_call(awaitable: Any, model: ConfiguredModel) -> Any:
    try:
        async with asyncio.timeout(model.timeout_seconds):
            return await awaitable
    except TimeoutError as exc:
        raise _deadline_error(model) from exc


async def _bounded_http_stream(
    stream: AsyncIterator[ProviderChunk], model: ConfiguredModel
) -> AsyncIterator[ProviderChunk]:
    try:
        async with asyncio.timeout(model.timeout_seconds):
            async for chunk in stream:
                yield chunk
    except TimeoutError as exc:
        raise _deadline_error(model) from exc
    finally:
        close = getattr(stream, "aclose", None)
        if close is not None:
            await close()


async def _stop_process(process: asyncio.subprocess.Process) -> None:
    """Stop one campaign-owned command process and its direct process group."""

    if process.returncode is not None:
        return
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    try:
        await asyncio.wait_for(process.wait(), timeout=5)
    except TimeoutError:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            return
        await process.wait()


class LocalCommandTransport:
    """Bounded process transport with typed adapters for supported local CLIs."""

    def __init__(
        self,
        model: ConfiguredModel,
        *,
        environ: Mapping[str, str] | None = None,
    ) -> None:
        if model.protocol != "local-command" or model.command is None:
            raise ValueError("model is not configured for local-command transport")
        self.model = model
        self._environ = os.environ if environ is None else environ

    def _resolved_command(self) -> tuple[str, str]:
        unavailable = self.model.availability_error(self._environ)
        if unavailable:
            raise ProviderError(
                "unavailable_command",
                unavailable,
                provider_id=self.model.provider_id,
                model_id=self.model.model_id,
            )
        assert self.model.command is not None
        return (
            self._environ[self.model.command.executable_env].strip(),
            self._environ[self.model.command.working_directory_env].strip(),
        )

    def _invocation(self, executable: str, prompt: str) -> tuple[list[str], bytes | None]:
        assert self.model.command is not None
        if self.model.command.adapter == "kimi-code":
            return (
                [
                    executable,
                    "--model",
                    self.model.model_id,
                    "--prompt",
                    prompt,
                    "--output-format",
                    "stream-json",
                ],
                None,
            )
        if self.model.command.adapter == "claude-code":
            return (
                [
                    executable,
                    "--print",
                    "--output-format",
                    "json",
                    "--verbose",
                    "--model",
                    self.model.model_id,
                ],
                prompt.encode("utf-8"),
            )
        raise ProviderError(
            "unsupported_command",
            "configured command adapter is unsupported",
            provider_id=self.model.provider_id,
            model_id=self.model.model_id,
        )

    def _command_label(self) -> str:
        assert self.model.command is not None
        return "Claude Code" if self.model.command.adapter == "claude-code" else "Kimi Code"

    def _error_from_exit(self, returncode: int, stderr: str) -> ProviderError:
        command_label = self._command_label()
        normalized = stderr.casefold()
        if "usage limit" in normalized or "quota" in normalized:
            code = "usage_limit"
            message = f"{command_label} usage window is exhausted"
            status_code = 403
        elif (
            "auth" in normalized
            or "403" in normalized
            or "login" in normalized
            or "not logged in" in normalized
        ):
            code = "command_refused"
            message = f"{command_label} authentication or usage authorization was refused"
            status_code = 403
        elif returncode == 75:
            code = "command_retryable"
            message = f"{command_label} reported a temporary provider failure"
            status_code = None
        else:
            code = "command_error"
            message = f"{command_label} command failed with exit code {returncode}"
            status_code = None
        return ProviderError(
            code,
            message,
            provider_id=self.model.provider_id,
            model_id=self.model.model_id,
            status_code=status_code,
        )

    def _parse_kimi_response(self, stdout: str) -> ProviderResponse:
        assistant_messages: list[str] = []
        for line in stdout.splitlines():
            if not line.strip():
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ProviderError(
                    "malformed_response",
                    "Kimi Code returned malformed stream-json output",
                    provider_id=self.model.provider_id,
                    model_id=self.model.model_id,
                ) from exc
            if not isinstance(event, dict) or event.get("role") != "assistant":
                continue
            content = event.get("content")
            if isinstance(content, str):
                text = content
            elif isinstance(content, list):
                text = "".join(
                    part.get("text", "")
                    for part in content
                    if isinstance(part, dict)
                    and part.get("type") == "text"
                    and isinstance(part.get("text"), str)
                )
            else:
                text = ""
            if text:
                assistant_messages.append(text)
        if not assistant_messages:
            raise ProviderError(
                "malformed_response",
                "Kimi Code returned no assistant response",
                provider_id=self.model.provider_id,
                model_id=self.model.model_id,
            )
        return ProviderResponse(
            content=assistant_messages[-1],
            model_id=self.model.model_id,
            usage={"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
            finish_reason="stop",
        )

    def _parse_claude_response(self, stdout: str) -> ProviderResponse:
        try:
            data = json.loads(stdout)
        except json.JSONDecodeError as exc:
            raise ProviderError(
                "malformed_response",
                "Claude Code returned malformed JSON output",
                provider_id=self.model.provider_id,
                model_id=self.model.model_id,
            ) from exc
        if isinstance(data, list):
            data = next(
                (item for item in data if isinstance(item, dict) and item.get("type") == "result"),
                None,
            )
        if isinstance(data, dict) and data.get("is_error") is True:
            detail = data.get("result", "")
            raise self._error_from_exit(1, detail if isinstance(detail, str) else "")
        if not isinstance(data, dict) or not isinstance(data.get("result"), str):
            raise ProviderError(
                "malformed_response",
                "Claude Code returned no assistant response",
                provider_id=self.model.provider_id,
                model_id=self.model.model_id,
            )
        content = data["result"]
        if not content:
            raise ProviderError(
                "malformed_response",
                "Claude Code returned no assistant response",
                provider_id=self.model.provider_id,
                model_id=self.model.model_id,
            )
        raw_usage = data.get("usage", {})
        if not isinstance(raw_usage, dict):
            raw_usage = {}
        prompt_tokens = raw_usage.get("input_tokens", 0)
        completion_tokens = raw_usage.get("output_tokens", 0)
        if not isinstance(prompt_tokens, int):
            prompt_tokens = 0
        if not isinstance(completion_tokens, int):
            completion_tokens = 0
        return ProviderResponse(
            content=content,
            model_id=self.model.model_id,
            usage={
                "prompt_tokens": prompt_tokens,
                "completion_tokens": completion_tokens,
                "total_tokens": prompt_tokens + completion_tokens,
            },
            finish_reason="stop",
        )

    def _parse_response(self, stdout: str) -> ProviderResponse:
        assert self.model.command is not None
        if self.model.command.adapter == "claude-code":
            return self._parse_claude_response(stdout)
        return self._parse_kimi_response(stdout)

    async def complete(self, prompt: str) -> ProviderResponse:
        executable, working_directory = self._resolved_command()
        arguments, stdin = self._invocation(executable, prompt)
        try:
            process = await asyncio.create_subprocess_exec(
                *arguments,
                cwd=working_directory,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                start_new_session=True,
            )
        except OSError as exc:
            raise ProviderError(
                "unavailable_command",
                "configured command could not be started",
                provider_id=self.model.provider_id,
                model_id=self.model.model_id,
            ) from exc
        try:
            stdout_bytes, stderr_bytes = await asyncio.wait_for(
                process.communicate(input=stdin), timeout=self.model.timeout_seconds
            )
        except TimeoutError as exc:
            await _stop_process(process)
            raise ProviderError(
                "timeout",
                f"{self._command_label()} command timed out",
                provider_id=self.model.provider_id,
                model_id=self.model.model_id,
            ) from exc
        except asyncio.CancelledError:
            await _stop_process(process)
            raise
        stdout = stdout_bytes.decode("utf-8", errors="replace")
        stderr = stderr_bytes.decode("utf-8", errors="replace")[-8192:]
        if process.returncode != 0:
            raise self._error_from_exit(process.returncode or 1, stderr)
        return self._parse_response(stdout)


class _OpenAICompatibleTransportCore:
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
                    f"required credential environment variable {self.model.api_key_env} is not set",
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

    def _error(self, code: str, message: str, *, status_code: int | None = None) -> ProviderError:
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
        return await _bounded_http_call(
            self._complete(messages, temperature=temperature, max_tokens=max_tokens),
            self.model,
        )

    async def _complete(
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
        client = self._client or httpx.AsyncClient(timeout=_http_timeout(self.model))
        try:
            response = await client.post(self.endpoint, headers=self._headers(), json=payload)
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
        except httpx.ConnectTimeout as exc:
            raise self._error("connect_timeout", "provider connection timed out") from exc
        except httpx.ReadTimeout as exc:
            raise self._error("read_timeout", "provider response became idle") from exc
        except httpx.TimeoutException as exc:
            raise self._error("transport_timeout", "provider transport timed out") from exc
        except httpx.HTTPError as exc:
            raise self._error("transport_error", "provider request failed") from exc
        except asyncio.CancelledError:
            raise
        finally:
            if owns_client:
                await client.aclose()


class _AnthropicMessagesTransportCore:
    """Bounded transport for Anthropic's native Messages API."""

    def __init__(
        self,
        model: ConfiguredModel,
        *,
        environ: Mapping[str, str] | None = None,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        if model.protocol != "anthropic-messages" or not model.base_url:
            raise ValueError("model is not configured for anthropic-messages transport")
        self.model = model
        self._environ = os.environ if environ is None else environ
        self._client = client

    @property
    def endpoint(self) -> str:
        assert self.model.base_url is not None
        return f"{self.model.base_url}/messages"

    def _headers(self) -> dict[str, str]:
        assert self.model.api_key_env is not None
        credential = self._environ.get(self.model.api_key_env)
        if not credential:
            raise self._error(
                "missing_credential",
                f"required credential environment variable {self.model.api_key_env} is not set",
            )
        return {
            "Content-Type": "application/json",
            "anthropic-version": "2023-06-01",
            "x-api-key": credential,
        }

    def _payload(
        self,
        messages: Sequence[Mapping[str, str]],
        *,
        stream: bool,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> dict[str, Any]:
        system: list[str] = []
        normalized: list[dict[str, str]] = []
        for index, message in enumerate(messages):
            role = message.get("role")
            content = message.get("content")
            if role not in {"system", "user", "assistant"} or not isinstance(content, str):
                raise self._error(
                    "invalid_request",
                    f"message {index} must have a supported role and string content",
                )
            if role == "system":
                system.append(content)
            else:
                normalized.append({"role": role, "content": content})
        if not normalized:
            raise self._error(
                "invalid_request", "at least one user or assistant message is required"
            )
        payload: dict[str, Any] = {
            "model": self.model.model_id,
            "messages": normalized,
            "max_tokens": 4096 if max_tokens is None else max_tokens,
            "stream": stream,
        }
        if system:
            payload["system"] = "\n\n".join(system)
        if temperature is not None:
            payload["temperature"] = temperature
        return payload

    def _error(self, code: str, message: str, *, status_code: int | None = None) -> ProviderError:
        return ProviderError(
            code,
            message,
            provider_id=self.model.provider_id,
            model_id=self.model.model_id,
            status_code=status_code,
        )

    @staticmethod
    def _usage(input_tokens: object, output_tokens: object) -> dict[str, int]:
        prompt_tokens = int(input_tokens or 0)
        completion_tokens = int(output_tokens or 0)
        return {
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": prompt_tokens + completion_tokens,
        }

    async def complete(
        self,
        messages: Sequence[Mapping[str, str]],
        *,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> ProviderResponse:
        return await _bounded_http_call(
            self._complete(messages, temperature=temperature, max_tokens=max_tokens),
            self.model,
        )

    async def _complete(
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
        client = self._client or httpx.AsyncClient(timeout=_http_timeout(self.model))
        try:
            response = await client.post(self.endpoint, headers=self._headers(), json=payload)
            if response.status_code < 200 or response.status_code >= 300:
                raise self._error(
                    "http_error",
                    f"provider returned HTTP {response.status_code}",
                    status_code=response.status_code,
                )
            try:
                data = response.json()
                response_model = data["model"]
                if response_model != self.model.model_id:
                    raise self._error(
                        "model_mismatch",
                        "provider returned a different model than requested",
                    )
                blocks = data["content"]
                if not isinstance(blocks, list):
                    raise TypeError
                text_parts = [
                    block["text"]
                    for block in blocks
                    if isinstance(block, dict) and block.get("type") == "text"
                ]
                if not text_parts or not all(isinstance(part, str) for part in text_parts):
                    raise TypeError
                usage_raw = data.get("usage") or {}
                usage = self._usage(usage_raw.get("input_tokens"), usage_raw.get("output_tokens"))
                finish_reason = data.get("stop_reason")
                if finish_reason is not None and not isinstance(finish_reason, str):
                    raise TypeError
            except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
                raise self._error(
                    "malformed_response",
                    "provider returned a malformed Anthropic Messages response",
                ) from exc
            return ProviderResponse("".join(text_parts), response_model, usage, finish_reason)
        except ProviderError:
            raise
        except httpx.ConnectTimeout as exc:
            raise self._error("connect_timeout", "provider connection timed out") from exc
        except httpx.ReadTimeout as exc:
            raise self._error("read_timeout", "provider response became idle") from exc
        except httpx.TimeoutException as exc:
            raise self._error("transport_timeout", "provider transport timed out") from exc
        except httpx.HTTPError as exc:
            raise self._error("transport_error", "provider request failed") from exc
        except asyncio.CancelledError:
            raise
        finally:
            if owns_client:
                await client.aclose()


class AnthropicMessagesTransport(_AnthropicMessagesTransportCore):
    """Anthropic Messages transport with native SSE streaming support."""

    async def stream(
        self,
        messages: Sequence[Mapping[str, str]],
        *,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> AsyncIterator[ProviderChunk]:
        async for chunk in _bounded_http_stream(
            self._stream(messages, temperature=temperature, max_tokens=max_tokens),
            self.model,
        ):
            yield chunk

    async def _stream(
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
        client = self._client or httpx.AsyncClient(timeout=_http_timeout(self.model))
        input_tokens = 0
        output_tokens = 0
        finish_reason: str | None = None
        saw_message_start = False
        saw_message_stop = False
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
                    if not line or line.startswith(":") or not line.startswith("data:"):
                        continue
                    try:
                        data = json.loads(line[5:].strip())
                        event_type = data.get("type")
                        if event_type == "message_start":
                            message = data["message"]
                            if message["model"] != self.model.model_id:
                                raise self._error(
                                    "model_mismatch",
                                    "provider returned a different model than requested",
                                )
                            input_tokens = int((message.get("usage") or {}).get("input_tokens", 0))
                            saw_message_start = True
                        elif event_type == "content_block_delta":
                            delta = data["delta"]
                            if delta.get("type") == "text_delta":
                                content = delta["text"]
                                if not isinstance(content, str):
                                    raise TypeError
                                if content:
                                    yield ProviderChunk(content=content)
                        elif event_type == "message_delta":
                            delta = data.get("delta") or {}
                            if delta.get("stop_reason") is not None:
                                finish_reason = str(delta["stop_reason"])
                            output_tokens = int(
                                (data.get("usage") or {}).get("output_tokens", output_tokens)
                            )
                        elif event_type == "message_stop":
                            saw_message_stop = True
                        elif event_type == "error":
                            raise self._error(
                                "provider_error", "provider reported a streaming error"
                            )
                    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
                        raise self._error(
                            "malformed_response",
                            "provider returned malformed Anthropic streaming data",
                        ) from exc
            if not saw_message_start or not saw_message_stop:
                raise self._error(
                    "malformed_response",
                    "provider stream ended without a complete Anthropic message",
                )
            yield ProviderChunk(
                usage=self._usage(input_tokens, output_tokens),
                finish_reason=finish_reason or "end_turn",
            )
        except ProviderError:
            raise
        except httpx.ConnectTimeout as exc:
            raise self._error("connect_timeout", "provider connection timed out") from exc
        except httpx.ReadTimeout as exc:
            raise self._error("read_timeout", "provider response became idle") from exc
        except httpx.TimeoutException as exc:
            raise self._error("transport_timeout", "provider transport timed out") from exc
        except httpx.HTTPError as exc:
            raise self._error("transport_error", "provider request failed") from exc
        except asyncio.CancelledError:
            raise
        finally:
            if owns_client:
                await client.aclose()


class OpenAICompatibleTransport(_OpenAICompatibleTransportCore):
    """OpenAI-compatible transport with streaming Chat Completions support."""

    async def stream(
        self,
        messages: Sequence[Mapping[str, str]],
        *,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> AsyncIterator[ProviderChunk]:
        async for chunk in _bounded_http_stream(
            self._stream(messages, temperature=temperature, max_tokens=max_tokens),
            self.model,
        ):
            yield chunk

    async def _stream(
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
        client = self._client or httpx.AsyncClient(timeout=_http_timeout(self.model))
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
                                "completion_tokens": int(usage_raw.get("completion_tokens", 0)),
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
                usage=final_usage
                or {
                    "prompt_tokens": 0,
                    "completion_tokens": 0,
                    "total_tokens": 0,
                },
                finish_reason=finish_reason or "stop",
            )
        except ProviderError:
            raise
        except httpx.ConnectTimeout as exc:
            raise self._error("connect_timeout", "provider connection timed out") from exc
        except httpx.ReadTimeout as exc:
            raise self._error("read_timeout", "provider response became idle") from exc
        except httpx.TimeoutException as exc:
            raise self._error("transport_timeout", "provider transport timed out") from exc
        except httpx.HTTPError as exc:
            raise self._error("transport_error", "provider request failed") from exc
        except asyncio.CancelledError:
            raise
        finally:
            if owns_client:
                await client.aclose()

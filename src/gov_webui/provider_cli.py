# SPDX-License-Identifier: Apache-2.0
"""Codex-command compatible dispatcher for Marginalia's configured providers."""

from __future__ import annotations

import asyncio
import json
import os
import signal
import subprocess
import sys
from collections.abc import Sequence

from gov_webui.model_providers import (
    AnthropicMessagesTransport,
    OpenAICompatibleTransport,
    LocalCommandTransport,
    ConfiguredModel,
    ProviderConfigurationError,
    ProviderError,
    load_provider_catalog,
)


def _model_argument(argv: Sequence[str]) -> str:
    for index, value in enumerate(argv):
        if value in {"-m", "--model"}:
            if index + 1 >= len(argv):
                raise ProviderConfigurationError(f"{value} requires a model value")
            return argv[index + 1]
    return ""


def _native_arguments(argv: Sequence[str], command_model: str | None) -> list[str]:
    result: list[str] = []
    index = 0
    while index < len(argv):
        value = argv[index]
        if value in {"-m", "--model"}:
            if index + 1 >= len(argv):
                raise ProviderConfigurationError(f"{value} requires a model value")
            if command_model:
                result.extend((value, command_model))
            index += 2
            continue
        result.append(value)
        index += 1
    return result


def _emit(event: dict[str, object]) -> None:
    print(json.dumps(event, separators=(",", ":")), flush=True)


def _native_timeout_seconds() -> float:
    raw = os.environ.get("MARGINALIA_CODEX_TIMEOUT_SECONDS", "240").strip()
    try:
        timeout = float(raw)
    except ValueError as exc:
        raise RuntimeError(
            "MARGINALIA_CODEX_TIMEOUT_SECONDS must be a number between 0.1 and 1800"
        ) from exc
    if not 0.1 <= timeout <= 1800:
        raise RuntimeError("MARGINALIA_CODEX_TIMEOUT_SECONDS must be between 0.1 and 1800")
    return timeout


def _signal_process_group(process: subprocess.Popen[bytes], signum: int) -> None:
    try:
        os.killpg(process.pid, signum)
    except ProcessLookupError:
        pass


def _stop_native_process(process: subprocess.Popen[bytes]) -> None:
    _signal_process_group(process, signal.SIGTERM)
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        _signal_process_group(process, signal.SIGKILL)
        process.wait()


def _delegate_native(argv: Sequence[str], command_model: str | None) -> int:
    native_path = os.environ.get("CODEX_NATIVE_PATH", "/opt/codex/codex")
    if not os.path.isfile(native_path) or not os.access(native_path, os.X_OK):
        raise RuntimeError(f"Codex executable is unavailable at {native_path}")
    timeout = _native_timeout_seconds()
    try:
        process = subprocess.Popen(
            [native_path, *_native_arguments(argv, command_model)],
            start_new_session=True,
        )
    except OSError as exc:
        raise RuntimeError("Codex executable could not be started") from exc

    def forward_signal(signum: int, _frame: object) -> None:
        _signal_process_group(process, signum)

    previous_handlers = {
        signum: signal.signal(signum, forward_signal) for signum in (signal.SIGINT, signal.SIGTERM)
    }
    try:
        try:
            returncode = process.wait(timeout=timeout)
        except subprocess.TimeoutExpired as exc:
            _stop_native_process(process)
            raise RuntimeError(f"Codex response timed out after {timeout:g} seconds") from exc
    finally:
        for signum, handler in previous_handlers.items():
            signal.signal(signum, handler)

    return returncode if returncode >= 0 else 128 - returncode


async def _run_http_provider(model: ConfiguredModel, prompt: str) -> None:
    if model.protocol == "anthropic-messages":
        transport = AnthropicMessagesTransport(model)
    elif model.protocol == "openai-compatible":
        transport = OpenAICompatibleTransport(model)
    else:
        raise ProviderConfigurationError(
            f"model {model.id!r} has unsupported HTTP protocol {model.protocol!r}"
        )
    usage = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
    async for chunk in transport.stream([{"role": "user", "content": prompt}]):
        if chunk.content:
            _emit(
                {
                    "type": "item.completed",
                    "item": {"type": "agent_message", "text": chunk.content},
                }
            )
        if chunk.usage:
            usage = chunk.usage
    _emit(
        {
            "type": "turn.completed",
            "usage": {
                "input_tokens": usage.get("prompt_tokens", 0),
                "output_tokens": usage.get("completion_tokens", 0),
            },
        }
    )


async def _run_local_command(model: ConfiguredModel, prompt: str) -> None:
    response = await LocalCommandTransport(model).complete(prompt)
    _emit(
        {
            "type": "item.completed",
            "item": {"type": "agent_message", "text": response.content},
        }
    )
    _emit(
        {
            "type": "turn.completed",
            "usage": {"input_tokens": 0, "output_tokens": 0},
        }
    )


def _emit_failure(message: str) -> None:
    print(message, file=sys.stderr, flush=True)


def main(argv: Sequence[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    try:
        selected_id = _model_argument(args)
        config_path = os.environ.get("MARGINALIA_MODEL_CONFIG", "").strip()
        if not config_path:
            if selected_id not in {"", "codex-default"}:
                return _delegate_native(args, selected_id)
            return _delegate_native(args, None)

        catalog = load_provider_catalog(config_path)
        model = catalog.require_available(selected_id)
        if model.protocol == "existing-command":
            return _delegate_native(args, model.command_model)

        prompt = sys.stdin.read()
        if model.protocol == "local-command":
            asyncio.run(_run_local_command(model, prompt))
        else:
            asyncio.run(_run_http_provider(model, prompt))
        return 0
    except ProviderError as exc:
        _emit_failure(str(exc))
        return 1
    except (ProviderConfigurationError, RuntimeError) as exc:
        _emit_failure(str(exc))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

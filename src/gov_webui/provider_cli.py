# SPDX-License-Identifier: Apache-2.0
"""Codex-command compatible dispatcher for Marginalia's configured providers."""

from __future__ import annotations

import asyncio
import json
import os
import sys
from collections.abc import Sequence

from gov_webui.model_providers import (
    OpenAICompatibleTransport,
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


def _delegate_native(argv: Sequence[str], command_model: str | None) -> None:
    native_path = os.environ.get("CODEX_NATIVE_PATH", "/opt/codex/codex")
    if not os.path.isfile(native_path) or not os.access(native_path, os.X_OK):
        raise RuntimeError(f"Codex executable is unavailable at {native_path}")
    os.execv(native_path, [native_path, *_native_arguments(argv, command_model)])


async def _run_openai_compatible(model: ConfiguredModel, prompt: str) -> None:
    transport = OpenAICompatibleTransport(model)
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


def main(argv: Sequence[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    try:
        selected_id = _model_argument(args)
        config_path = os.environ.get("MARGINALIA_MODEL_CONFIG", "").strip()
        if not config_path:
            if selected_id not in {"", "codex-default"}:
                _delegate_native(args, selected_id)
            _delegate_native(args, None)
            return 0

        catalog = load_provider_catalog(config_path)
        model = catalog.require_available(selected_id)
        if model.protocol == "existing-command":
            _delegate_native(args, model.command_model)
            return 0

        prompt = sys.stdin.read()
        asyncio.run(_run_openai_compatible(model, prompt))
        return 0
    except ProviderError as exc:
        _emit({"type": "error", "message": str(exc), "error": exc.to_dict()})
        return 1
    except (ProviderConfigurationError, RuntimeError) as exc:
        _emit({"type": "error", "message": str(exc)})
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

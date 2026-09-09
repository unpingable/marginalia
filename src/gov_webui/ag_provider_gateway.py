# SPDX-License-Identifier: Apache-2.0
"""Strict subprocess boundary to ag-ng's credential-isolated provider daemon."""

from __future__ import annotations

import base64
import hashlib
import json
import subprocess
import time
from pathlib import Path
from typing import Any

from gov_webui.generation_executor import (
    ExecutorError,
    ProviderDefinitiveFailure,
    ProviderOutcomeUnknown,
)
from gov_webui.model_providers import ConfiguredModel, ProviderCatalog, load_provider_catalog


class AgProviderGateway:
    """Prepare, execute, and reconcile exact ag-providerd transactions."""

    def __init__(
        self,
        providerctl: Path,
        providerctl_config: Path,
        model_config: Path,
        *,
        timeout_seconds: float = 1830,
    ) -> None:
        for path, label in (
            (providerctl, "ag-providerctl"),
            (providerctl_config, "providerctl config"),
            (model_config, "model config"),
        ):
            if not path.is_absolute():
                raise ExecutorError(f"{label} path must be absolute")
        if not 30 <= timeout_seconds <= 1860:
            raise ExecutorError("providerctl timeout must be between 30 and 1860 seconds")
        self.providerctl = providerctl
        self.providerctl_config = providerctl_config
        self.catalog: ProviderCatalog = load_provider_catalog(model_config)
        self.timeout_seconds = timeout_seconds

    def prepare(
        self,
        payload: dict[str, Any],
        *,
        project_id: str,
        session_id: str,
        docket_attempt: str,
        docket_marker: str,
        actual_route: str,
    ) -> dict[str, Any]:
        model, body, method = self._request(payload)
        if model.provider_id != actual_route:
            raise ExecutorError("frozen provider route does not match the selected model")
        now_ms = int(time.time() * 1000)
        request = {
            "schema": "ag.providerctl.prepare-inference/v1",
            "project": project_id,
            "session": session_id,
            "session_nonce": _nonce("marginalia.provider-session/v1", session_id),
            "capability_nonce": _nonce(
                "marginalia.provider-capability/v1", f"{docket_attempt}\0{docket_marker}"
            ),
            "not_before_unix_ms": max(1, now_ms - 60_000),
            "expires_at_unix_ms": now_ms + 3_600_000,
            "endpoint": model.provider_id,
            "model": model.model_id,
            "method": method,
            "sanitized_headers": {"content-type": "application/json"},
            "request_bytes": base64.b64encode(_canonical(body)).decode("ascii"),
        }
        value = self._invoke("prepare", request, timeout=30)
        dispatch = value.get("dispatch") if isinstance(value, dict) else None
        if not _is_digest(dispatch):
            raise ExecutorError("ag-providerctl prepare omitted its deterministic dispatch")
        return value

    def execute(self, transaction: dict[str, Any], *, selected_model: str) -> dict[str, Any]:
        dispatch = transaction.get("dispatch")
        if not _is_digest(dispatch):
            raise ExecutorError("prepared provider transaction has no dispatch")
        response = self._invoke("execute", transaction, timeout=self.timeout_seconds)
        self._require_available(response, dispatch)
        return self.fetch(dispatch, selected_model=selected_model)

    def fetch(self, dispatch: str, *, selected_model: str) -> dict[str, Any]:
        result = self._invoke(
            "call", {"method": "fetch_inference", "dispatch": dispatch}, timeout=30
        )
        response = self._ok_response(result)
        if response.get("kind") != "inference" or response.get("dispatch") != dispatch:
            raise ProviderOutcomeUnknown("provider response is not yet durably fetchable")
        encoded = response.get("event_stream")
        exact = response.get("exact_event_stream")
        if not isinstance(encoded, str) or not _is_digest(exact):
            raise ProviderOutcomeUnknown("provider custody response is malformed")
        try:
            event_bytes = base64.b64decode(encoded, validate=True)
        except (ValueError, TypeError) as exc:
            raise ProviderOutcomeUnknown("provider custody response has invalid base64") from exc
        if "sha256:" + hashlib.sha256(event_bytes).hexdigest() != exact:
            raise ProviderOutcomeUnknown("provider custody response digest mismatch")
        try:
            event = json.loads(event_bytes)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ProviderOutcomeUnknown("provider custody event is malformed") from exc
        normalized = self._normalize(event, dispatch, exact, selected_model)
        normalized["provider_evidence"] = {
            "dispatch": dispatch,
            "exact_event_stream": exact,
            "event_stream": encoded,
        }
        return normalized

    def acknowledge(self, dispatch: str, exact_event_stream: str, custody: str) -> None:
        result = self._invoke(
            "call",
            {
                "method": "acknowledge_inference_custody",
                "dispatch": dispatch,
                "exact_event_stream": exact_event_stream,
                "governor_custody_record": custody,
            },
            timeout=30,
        )
        response = self._ok_response(result)
        if response.get("kind") != "inference_custody_acknowledged":
            raise ProviderOutcomeUnknown("provider custody acknowledgment was not committed")

    def _request(self, payload: dict[str, Any]) -> tuple[ConfiguredModel, dict[str, Any], str]:
        if not isinstance(payload, dict) or set(payload) != {"context_id", "messages", "model"}:
            raise ExecutorError("frozen provider request does not have the exact v1 shape")
        model = self.catalog.resolve(payload["model"])
        configured_unavailable = model.availability_error(include_runtime=False)
        if configured_unavailable:
            raise ExecutorError(
                f"configured provider model is unavailable: {configured_unavailable}"
            )
        messages = payload["messages"]
        if not isinstance(messages, list) or not messages:
            raise ExecutorError("frozen provider request has no messages")
        normalized: list[dict[str, str]] = []
        for index, message in enumerate(messages):
            if not isinstance(message, dict) or set(message) != {"role", "content"}:
                raise ExecutorError(f"message {index} does not have the exact shape")
            if message["role"] not in {"system", "user", "assistant"} or not isinstance(
                message["content"], str
            ):
                raise ExecutorError(f"message {index} is invalid")
            normalized.append({"role": message["role"], "content": message["content"]})
        if model.protocol == "openai-compatible":
            return (
                model,
                {
                    "model": model.model_id,
                    "messages": normalized,
                    "stream": False,
                    "max_tokens": model.max_output_tokens,
                },
                "chat.completions.create",
            )
        if model.protocol == "anthropic-messages":
            system = [item["content"] for item in normalized if item["role"] == "system"]
            body: dict[str, Any] = {
                "model": model.model_id,
                "messages": [item for item in normalized if item["role"] != "system"],
                "max_tokens": model.max_output_tokens,
                "stream": False,
            }
            if not body["messages"]:
                raise ExecutorError("Anthropic request has no user or assistant message")
            if system:
                body["system"] = "\n\n".join(system)
            return model, body, "messages.create"
        if model.protocol in {"local-command", "existing-command"}:
            return model, {"model": model.model_id, "messages": normalized}, "command.complete"
        raise ExecutorError(f"unsupported provider protocol: {model.protocol}")

    def _normalize(
        self, event: Any, dispatch: str, exact: str, selected_model: str
    ) -> dict[str, Any]:
        if not isinstance(event, dict):
            raise ProviderOutcomeUnknown("provider custody event is not an object")
        kind = event.get("event")
        if kind == "transport_failure":
            raise ProviderOutcomeUnknown(
                f"provider transport ended without complete response: {event.get('class', 'unknown')}"
            )
        if kind == "response_limit_exceeded":
            raise ProviderOutcomeUnknown("provider response exceeded durable custody bound")
        if kind != "http_response":
            raise ProviderOutcomeUnknown("provider custody event has an unknown type")
        status = event.get("status")
        if not isinstance(status, int):
            raise ProviderOutcomeUnknown("provider response status is malformed")
        if not 200 <= status < 300:
            raise ProviderDefinitiveFailure(f"provider returned terminal status {status}")
        if event.get("protocol_terminal") is not True:
            raise ProviderOutcomeUnknown("provider response lacks a terminal protocol event")
        encoded_body = event.get("body")
        if not isinstance(encoded_body, str):
            raise ProviderOutcomeUnknown("provider response body is missing")
        try:
            body = base64.b64decode(encoded_body, validate=True).decode("utf-8")
        except (ValueError, UnicodeDecodeError) as exc:
            raise ProviderOutcomeUnknown("provider response body is not canonical UTF-8") from exc

        transaction_model = self.catalog.resolve(selected_model)
        content, usage, observed = self._parse_body(transaction_model, body)
        return {
            "outcome": "authored",
            "content": content,
            "model": transaction_model.id,
            "usage": usage,
            # Selection and observation are deliberately different facts.  In
            # particular, a command adapter's configured model argument is not
            # evidence of which physical model the external tool actually ran.
            "execution_identity": {
                "configured_provider_id": transaction_model.provider_id,
                "configured_model_id": transaction_model.model_id,
                "observed_provider_id": observed.get("provider_id"),
                "observed_model_id": observed.get("model_id"),
                "observed_status": observed["status"],
            },
            "receipt": {
                "receipt_id": exact,
                "authority": "ag-ng",
                "provider_dispatch": dispatch,
            },
        }

    @staticmethod
    def _parse_body(
        model: ConfiguredModel, body: str
    ) -> tuple[str, dict[str, int], dict[str, str | None]]:
        if model.protocol == "openai-compatible":
            try:
                data = json.loads(body)
                choice = data["choices"][0]
                content = choice["message"]["content"]
                usage_raw = data.get("usage") or {}
                observed_model = data.get("model")
                observed_provider = data.get("provider") or data.get("provider_id")
                if observed_model is not None and observed_model != model.model_id:
                    raise TypeError
                if not isinstance(content, str):
                    raise TypeError
                usage = _usage(
                    usage_raw.get("prompt_tokens", 0), usage_raw.get("completion_tokens", 0)
                )
            except (KeyError, IndexError, TypeError, ValueError, json.JSONDecodeError) as exc:
                raise ProviderOutcomeUnknown("provider returned malformed chat completion") from exc
            return (
                content,
                usage,
                {
                    "provider_id": (
                        observed_provider if isinstance(observed_provider, str) else None
                    ),
                    "model_id": observed_model if isinstance(observed_model, str) else None,
                    "status": "attested" if isinstance(observed_model, str) else "unavailable",
                },
            )
        if model.protocol == "anthropic-messages":
            try:
                data = json.loads(body)
                if data["model"] != model.model_id:
                    raise TypeError
                content = "".join(
                    block["text"]
                    for block in data["content"]
                    if isinstance(block, dict) and block.get("type") == "text"
                )
                raw = data.get("usage") or {}
                usage = _usage(raw.get("input_tokens", 0), raw.get("output_tokens", 0))
            except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
                raise ProviderOutcomeUnknown(
                    "provider returned malformed Anthropic response"
                ) from exc
            if not content:
                raise ProviderOutcomeUnknown("provider returned no authored text")
            return (
                content,
                usage,
                {
                    # Anthropic's Messages response attests its model but does
                    # not carry a physical-provider identity field.
                    "provider_id": None,
                    "model_id": data["model"],
                    "status": "attested",
                },
            )
        if model.command and model.command.adapter == "claude-code":
            try:
                data = json.loads(body)
                if isinstance(data, list):
                    data = next(
                        item
                        for item in data
                        if isinstance(item, dict) and item.get("type") == "result"
                    )
                content = data["result"]
                raw = data.get("usage") or {}
                usage = _usage(raw.get("input_tokens", 0), raw.get("output_tokens", 0))
                observed_model = data.get("model")
            except (KeyError, StopIteration, TypeError, ValueError, json.JSONDecodeError) as exc:
                raise ProviderOutcomeUnknown("Claude Code returned malformed output") from exc
            if not isinstance(content, str) or not content:
                raise ProviderOutcomeUnknown("Claude Code returned no authored text")
            return (
                content,
                usage,
                {
                    "provider_id": None,
                    "model_id": observed_model if isinstance(observed_model, str) else None,
                    "status": "attested" if isinstance(observed_model, str) else "unavailable",
                },
            )
        messages: list[str] = []
        prompt_tokens = completion_tokens = 0
        observed_model: str | None = None
        for line in body.splitlines():
            try:
                event = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ProviderOutcomeUnknown("command provider returned malformed JSONL") from exc
            if model.command and model.command.adapter == "kimi-code":
                if isinstance(event, dict) and event.get("role") == "assistant":
                    value = event.get("content")
                    if isinstance(value, str):
                        messages.append(value)
                if isinstance(event, dict) and isinstance(event.get("model"), str):
                    observed_model = event["model"]
            elif isinstance(event, dict):
                item = event.get("item")
                if event.get("type") == "item.completed" and isinstance(item, dict):
                    if item.get("type") == "agent_message" and isinstance(item.get("text"), str):
                        messages.append(item["text"])
                raw = event.get("usage")
                if event.get("type") == "turn.completed" and isinstance(raw, dict):
                    prompt_tokens = _nonnegative_int(raw.get("input_tokens", 0))
                    completion_tokens = _nonnegative_int(raw.get("output_tokens", 0))
        if not messages:
            raise ProviderOutcomeUnknown("command provider returned no authored text")
        return (
            messages[-1],
            _usage(prompt_tokens, completion_tokens),
            {
                "provider_id": None,
                "model_id": observed_model,
                "status": "attested" if observed_model is not None else "unavailable",
            },
        )

    def _invoke(
        self, operation: str, document: dict[str, Any], *, timeout: float
    ) -> dict[str, Any]:
        try:
            completed = subprocess.run(
                [str(self.providerctl), "--config", str(self.providerctl_config), operation],
                input=_canonical(document),
                capture_output=True,
                check=False,
                timeout=timeout,
            )
        except subprocess.TimeoutExpired as exc:
            raise ProviderOutcomeUnknown(
                "providerctl wait ended before provider custody resolved"
            ) from exc
        except OSError as exc:
            raise ExecutorError("ag-providerctl could not be started") from exc
        if completed.returncode != 0:
            raise ExecutorError("ag-providerctl refused the request")
        try:
            value = json.loads(completed.stdout)
        except json.JSONDecodeError as exc:
            raise ExecutorError("ag-providerctl returned malformed JSON") from exc
        if not isinstance(value, dict):
            raise ExecutorError("ag-providerctl returned a non-object")
        return value

    @staticmethod
    def _ok_response(result: dict[str, Any]) -> dict[str, Any]:
        if result.get("status") != "ok" or not isinstance(result.get("response"), dict):
            code = result.get("code", "unknown")
            if code in {"indeterminate", "not_found"}:
                raise ProviderOutcomeUnknown(f"provider custody is unresolved: {code}")
            raise ExecutorError(f"ag-providerd refused the request: {code}")
        return result["response"]

    def _require_available(self, result: dict[str, Any], dispatch: str) -> None:
        response = self._ok_response(result)
        kind = response.get("kind")
        if kind == "inference_available" and response.get("dispatch") == dispatch:
            return
        if kind == "inference_custody_acknowledged" and response.get("dispatch") == dispatch:
            return
        raise ProviderOutcomeUnknown("provider dispatch did not become durably available")


def _canonical(value: Any) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
    ).encode("utf-8")


def _nonce(domain: str, value: str) -> str:
    return hashlib.sha256(domain.encode("ascii") + b"\0" + value.encode("utf-8")).hexdigest()[:32]


def _is_digest(value: object) -> bool:
    if not isinstance(value, str) or len(value) != 71 or not value.startswith("sha256:"):
        return False
    return all(character in "0123456789abcdef" for character in value[7:])


def _nonnegative_int(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ProviderOutcomeUnknown("provider usage is malformed")
    return value


def _usage(prompt: object, completion: object) -> dict[str, int]:
    prompt_tokens = _nonnegative_int(prompt)
    completion_tokens = _nonnegative_int(completion)
    return {
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "total_tokens": prompt_tokens + completion_tokens,
    }

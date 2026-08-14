"""Shared governed-chat fakes for application-boundary tests."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock


def authority_receipt(
    *,
    receipt_id: str = "r_test_authority",
    verdict: str = "pass",
    gate: str = "chat_bridge",
) -> dict[str, object]:
    return {
        "receipt_id": receipt_id,
        "schema_version": 4,
        "timestamp": "2026-08-13T00:00:00+00:00",
        "gate": gate,
        "verdict": verdict,
        "subject_hash": "subject-test",
        "evidence_hash": "evidence-test",
        "policy_hash": "policy-test",
        "principal_id": "local",
        "tenant_id": "default",
        "auth_method": "none",
        "receipt_role": "authority",
    }


def fake_governed_chat(
    *,
    content: str = "Hello from test",
    model: str = "test-model",
    usage: dict[str, int] | None = None,
    violations: list[dict] | None = None,
    footer: str | None = None,
    pending: dict | None = None,
) -> AsyncMock:
    receipt = authority_receipt(verdict="block" if pending else "pass")
    mock = AsyncMock()
    mock.chat_send = AsyncMock(return_value={
        "content": content,
        "model": model,
        "usage": usage
        or {"prompt_tokens": 5, "completion_tokens": 3, "total_tokens": 8},
        "violations": violations or [],
        "footer": footer,
        "pending": pending,
        "receipt": receipt,
    })
    mock.provider = AsyncMock(return_value={
        "type": "mock",
        "connected": True,
        "model": model,
    })
    mock.models = AsyncMock(return_value=[{"id": model, "owned_by": "mock"}])
    mock.contract_info = AsyncMock(return_value={
        "capabilities": {
            "governed_chat": {
                "contract_version": "1",
                "context_scoped_pending": True,
                "authoritative_receipts": True,
            }
        }
    })
    mock.format_pending_message = MagicMock(
        side_effect=lambda value: (
            value.get("prompt") or "This response needs your decision."
        )
    )
    mock.close = AsyncMock()
    return mock

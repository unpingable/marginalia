"""Focused tests for Marginalia's AG anti-corruption boundary."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from gov_webui.governed_chat_adapter import (
    GovernedChatAdapter,
    GovernedChatContractError,
)
from support import authority_receipt


def _client(tmp_path: Path) -> AsyncMock:
    governor_dir = (tmp_path / ".governor").resolve()
    client = AsyncMock()
    client.hello.return_value = {
        "capabilities": {
            "governed_chat": {
                "contract_version": "1",
                "context_scoped_pending": True,
                "authoritative_receipts": True,
            }
        },
        "governor": {"governor_dir": str(governor_dir)},
    }
    client.commit_pending.return_value = None
    return client


@pytest.mark.asyncio
async def test_rejects_missing_authoritative_receipt(tmp_path: Path):
    client = _client(tmp_path)
    client.chat_send.return_value = {
        "content": "unproven",
        "model": "mock",
        "pending": None,
        "receipt": None,
    }
    adapter = GovernedChatAdapter(
        client, context_id="erin", expected_governor_dir=tmp_path / ".governor"
    )

    with pytest.raises(GovernedChatContractError, match="without an authoritative"):
        await adapter.chat_send([{"role": "user", "content": "continue"}])


@pytest.mark.asyncio
async def test_rejects_receipt_not_confirmed_by_ag_store(tmp_path: Path):
    client = _client(tmp_path)
    receipt = authority_receipt()
    client.chat_send.return_value = {
        "content": "unconfirmed",
        "model": "mock",
        "pending": None,
        "receipt": receipt,
    }
    client.receipt_detail.return_value = {
        "receipt": {**receipt, "receipt_id": "different"},
        "evidence": {"context_id": "erin"},
    }
    adapter = GovernedChatAdapter(
        client, context_id="erin", expected_governor_dir=tmp_path / ".governor"
    )

    with pytest.raises(GovernedChatContractError, match="did not confirm"):
        await adapter.chat_send([{"role": "user", "content": "continue"}])


@pytest.mark.asyncio
async def test_provider_is_discovered_from_daemon(tmp_path: Path):
    client = _client(tmp_path)
    client.chat_backend.return_value = {
        "type": "anthropic",
        "connected": True,
        "model": "daemon-model",
    }
    adapter = GovernedChatAdapter(
        client, context_id="erin", expected_governor_dir=tmp_path / ".governor"
    )

    assert await adapter.provider() == client.chat_backend.return_value
    client.chat_backend.assert_awaited_once_with()


@pytest.mark.asyncio
async def test_codex_models_delegate_to_authenticated_cli_default(tmp_path: Path):
    client = _client(tmp_path)
    client.chat_backend.return_value = {"type": "codex", "connected": True}
    client.chat_models.return_value = [
        {"id": "o3", "owned_by": "codex"},
        {"id": "o4-mini", "owned_by": "codex"},
    ]
    adapter = GovernedChatAdapter(
        client, context_id="erin", expected_governor_dir=tmp_path / ".governor"
    )

    assert await adapter.models() == [
        {"id": GovernedChatAdapter.CODEX_DEFAULT_MODEL, "owned_by": "codex"}
    ]
    client.chat_models.assert_not_awaited()


@pytest.mark.asyncio
async def test_every_pending_call_supplies_context(tmp_path: Path):
    client = _client(tmp_path)
    adapter = GovernedChatAdapter(
        client,
        context_id="erin-novel",
        expected_governor_dir=tmp_path / ".governor",
    )

    assert await adapter.pending() is None
    client.commit_pending.assert_awaited_once_with("erin-novel")

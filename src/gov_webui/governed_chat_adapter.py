# SPDX-License-Identifier: Apache-2.0
"""Marginalia's application-facing boundary to Agent Governor.

This is deliberately not a general governance SDK.  It owns only the live
contract needed by one governed conversational application and keeps daemon
protocol details out of the FastAPI application.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, AsyncIterator, Literal

from gov_webui.daemon_client import DaemonChatClient


class GovernedChatContractError(RuntimeError):
    """AG is reachable but does not satisfy Marginalia's required contract."""


class GovernedChatAdapter:
    """Narrow, context-bound interface from Marginalia to AG."""

    CONTRACT_VERSION = "1"

    def __init__(
        self,
        client: DaemonChatClient,
        *,
        context_id: str,
        expected_governor_dir: str | Path,
    ) -> None:
        if not context_id:
            raise ValueError("context_id must not be empty")
        self._client = client
        self.context_id = context_id
        self.expected_governor_dir = Path(expected_governor_dir).resolve()
        self._contract: dict[str, Any] | None = None

    async def close(self) -> None:
        await self._client.close()

    async def contract_info(self) -> dict[str, Any]:
        """Validate and return the AG governed-chat handshake."""
        hello = await self._client.hello()
        capability = hello.get("capabilities", {}).get("governed_chat", {})
        if capability.get("contract_version") != self.CONTRACT_VERSION:
            raise GovernedChatContractError(
                "AG does not expose governed_chat contract version "
                f"{self.CONTRACT_VERSION}"
            )
        if capability.get("context_scoped_pending") is not True:
            raise GovernedChatContractError(
                "AG does not guarantee context-scoped pending state"
            )
        if capability.get("authoritative_receipts") is not True:
            raise GovernedChatContractError(
                "AG does not return authoritative governed-chat receipts"
            )

        raw_dir = hello.get("governor", {}).get("governor_dir")
        if not raw_dir:
            raise GovernedChatContractError(
                "AG handshake omitted its authoritative governor_dir"
            )
        actual_dir = Path(raw_dir).resolve()
        if actual_dir != self.expected_governor_dir:
            raise GovernedChatContractError(
                "Marginalia/AG state-root mismatch: "
                f"expected {self.expected_governor_dir}, daemon uses {actual_dir}"
            )

        self._contract = hello
        return hello

    async def _ensure_contract(self) -> None:
        if self._contract is None:
            await self.contract_info()

    async def provider(self) -> dict[str, Any]:
        """Return the provider/backend that actually owns governed execution."""
        await self._ensure_contract()
        return await self._client.chat_backend()

    async def models(self) -> list[dict[str, str]]:
        """Return models advertised by AG's configured provider."""
        await self._ensure_contract()
        return await self._client.chat_models()

    async def receipt_detail(self, receipt_id: str) -> dict[str, Any]:
        await self._ensure_contract()
        return await self._client.receipt_detail(receipt_id)

    async def pending(self) -> dict[str, Any] | None:
        """Return durable pending state for this adapter's context only."""
        await self._ensure_contract()
        pending = await self._client.commit_pending(self.context_id)
        if pending is None:
            return None
        self._validate_pending_context(pending)
        receipt_id = pending.get("receipt_id")
        if receipt_id:
            detail = await self._authoritative_receipt(receipt_id)
            if detail.get("evidence", {}).get("context_id") != self.context_id:
                raise GovernedChatContractError(
                    "pending receipt evidence belongs to another context"
                )
        return pending

    async def chat_send(
        self,
        messages: list[dict[str, str]],
        model: str = "",
    ) -> dict[str, Any]:
        """Generate one governed response and validate its final AG receipt."""
        await self._ensure_contract()
        before = await self.pending()
        result = await self._client.chat_send(
            messages=messages,
            model=model,
            context_id=self.context_id,
        )
        detail = await self._validate_chat_result(result)
        await self._validate_chat_resolution_transition(before, detail)
        return result

    async def chat_stream(
        self,
        messages: list[dict[str, str]],
        model: str = "",
    ) -> AsyncIterator[tuple[str | None, dict[str, Any] | None]]:
        """Stream deltas, validating the authoritative receipt before finality."""
        await self._ensure_contract()
        before = await self.pending()
        saw_final = False
        async for delta, final in self._client.chat_stream(
            messages=messages,
            model=model,
            context_id=self.context_id,
        ):
            if final is not None:
                detail = await self._validate_chat_result(final)
                await self._validate_chat_resolution_transition(before, detail)
                saw_final = True
            yield delta, final
        if not saw_final:
            raise GovernedChatContractError(
                "AG stream ended without a final governed outcome"
            )

    async def resolve_pending(
        self,
        action: Literal["fix", "revise", "proceed"],
        *,
        corrected_text: str | None = None,
        new_anchor_text: str | None = None,
        reason: str = "",
        scope: str | None = None,
        expiry: str | None = None,
    ) -> dict[str, Any]:
        """Resolve this context's pending state and verify receipt linkage."""
        before = await self.pending()
        if before is None:
            return {
                "action": action,
                "success": False,
                "message": "No pending violation in this context.",
            }

        if action == "fix":
            if corrected_text is None:
                raise ValueError("corrected_text is required for fix")
            result = await self._client.commit_fix(self.context_id, corrected_text)
        elif action == "revise":
            result = await self._client.commit_revise(
                self.context_id, new_anchor_text
            )
        elif action == "proceed":
            result = await self._client.commit_proceed(
                self.context_id,
                reason=reason,
                scope=scope,
                expiry=expiry,
            )
        else:
            raise ValueError(f"unsupported resolution action: {action}")

        if result.get("success"):
            detail = await self._validate_resolution_result(result)
            evidence = detail.get("evidence", {})
            if evidence.get("pending_id") != before.get("id"):
                raise GovernedChatContractError(
                    "resolution receipt does not identify the resolved pending state"
                )
            if evidence.get("original_receipt_id") != before.get("receipt_id"):
                raise GovernedChatContractError(
                    "resolution receipt is not linked to the blocking receipt"
                )
            if await self._client.commit_pending(self.context_id) is not None:
                raise GovernedChatContractError(
                    "AG reported a successful resolution but pending state remains"
                )
        return result

    @staticmethod
    def format_pending_message(pending: dict[str, Any]) -> str:
        """Render AG pending state without exposing receipt ceremony."""
        from governor.violation_resolver import format_violation_prompt

        return format_violation_prompt(
            pending.get("violations", []), pending.get("mode", "general")
        )

    def _validate_pending_context(self, pending: dict[str, Any]) -> None:
        if pending.get("context_id") != self.context_id:
            raise GovernedChatContractError(
                "AG returned pending state from another chat context"
            )

    async def _authoritative_receipt(self, receipt_id: str) -> dict[str, Any]:
        detail = await self._client.receipt_detail(receipt_id)
        receipt = detail.get("receipt") or {}
        if receipt.get("receipt_id") != receipt_id:
            raise GovernedChatContractError(
                "AG receipt store did not confirm the returned receipt reference"
            )
        if receipt.get("receipt_role") != "authority":
            raise GovernedChatContractError(
                "governed outcome is not backed by an AG authority receipt"
            )
        return detail

    async def _validate_chat_result(
        self, result: dict[str, Any]
    ) -> dict[str, Any]:
        receipt = result.get("receipt") or {}
        receipt_id = receipt.get("receipt_id")
        if not receipt_id:
            raise GovernedChatContractError(
                "AG returned a governed outcome without an authoritative receipt"
            )
        detail = await self._authoritative_receipt(receipt_id)
        stored = detail["receipt"]
        for field in ("gate", "verdict", "subject_hash", "evidence_hash"):
            if receipt.get(field) != stored.get(field):
                raise GovernedChatContractError(
                    f"returned receipt disagrees with AG authority store: {field}"
                )

        evidence = detail.get("evidence") or {}
        if evidence.get("context_id") != self.context_id:
            raise GovernedChatContractError(
                "governed outcome receipt belongs to another chat context"
            )

        pending = result.get("pending")
        if pending:
            self._validate_pending_context(pending)
            if stored.get("gate") != "chat_bridge" or stored.get("verdict") != "block":
                raise GovernedChatContractError(
                    "pending outcome is not linked to a blocking chat receipt"
                )
        elif stored.get("gate") not in {"chat_bridge", "violation_resolution"}:
            raise GovernedChatContractError(
                "unexpected receipt gate for governed chat outcome"
            )
        return detail

    async def _validate_chat_resolution_transition(
        self,
        before: dict[str, Any] | None,
        detail: dict[str, Any],
    ) -> None:
        """Validate a resolution returned through chat.send/chat.stream."""
        if detail["receipt"].get("gate") != "violation_resolution":
            return
        if before is None:
            raise GovernedChatContractError(
                "AG returned a resolution without prior pending state"
            )
        evidence = detail.get("evidence") or {}
        if evidence.get("pending_id") != before.get("id"):
            raise GovernedChatContractError(
                "chat resolution receipt identifies different pending state"
            )
        if evidence.get("original_receipt_id") != before.get("receipt_id"):
            raise GovernedChatContractError(
                "chat resolution receipt is not linked to the blocking receipt"
            )
        if await self._client.commit_pending(self.context_id) is not None:
            raise GovernedChatContractError(
                "AG chat resolution left pending state uncleared"
            )

    async def _validate_resolution_result(
        self, result: dict[str, Any]
    ) -> dict[str, Any]:
        receipt_id = (result.get("receipt") or {}).get("receipt_id")
        if not receipt_id:
            raise GovernedChatContractError(
                "AG resolved pending state without an authoritative receipt"
            )
        detail = await self._authoritative_receipt(receipt_id)
        receipt = detail["receipt"]
        evidence = detail.get("evidence") or {}
        if receipt.get("gate") != "violation_resolution":
            raise GovernedChatContractError(
                "resolution result is not backed by a resolution receipt"
            )
        if evidence.get("context_id") != self.context_id:
            raise GovernedChatContractError(
                "resolution receipt belongs to another chat context"
            )
        return detail

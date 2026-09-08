# SPDX-License-Identifier: Apache-2.0
"""Read-only compatibility tests for archived receipt_v1 evidence."""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def historical_receipt_client(monkeypatch: pytest.MonkeyPatch):
    import gov_webui.adapter as adapter

    monkeypatch.setattr(adapter, "MARGINALIA_ENABLE_DONOR_ROUTES", False)
    monkeypatch.setattr(adapter, "GOVERNOR_AUTH_TOKEN", "")
    return TestClient(adapter.app), adapter


def _receipt_dicts(count: int) -> list[dict]:
    from receipt_v1 import ReceiptBuilder, ReceiptChain
    from receipt_v1.types import Action, Actor, ExecutionStatus, Provenance

    chain = ReceiptChain()
    receipts = []
    for index in range(count):
        receipt = (
            ReceiptBuilder()
            .actor(Actor(agent_id="webui", session_id="historical-test"))
            .tool("gov.chat_completion", {"turn_seq": index + 1})
            .decision(Action.ALLOW, "gov.passthrough", reason_human="historical test")
            .execution(ExecutionStatus.SUCCESS)
            .provenance(
                Provenance(
                    deployment_id="test",
                    instance_id="test-instance",
                    governor_version="0.1.0",
                )
            )
            .build(chain.next())
        )
        chain.append(receipt)
        receipts.append(receipt.to_dict())
    return receipts


def test_historical_receipt_export_preserves_canonical_jsonl(
    historical_receipt_client, monkeypatch: pytest.MonkeyPatch
) -> None:
    client, adapter = historical_receipt_client
    receipts = _receipt_dicts(2)
    monkeypatch.setattr(adapter, "_load_receipt_v1_dicts", lambda: receipts)

    response = client.get("/v1/historical-receipts/export")

    assert response.status_code == 200
    assert "application/x-ndjson" in response.headers["content-type"]
    exported = [json.loads(line) for line in response.text.splitlines() if line.strip()]
    assert len(exported) == 2
    assert all("receipt_id" in receipt for receipt in exported)


def test_historical_receipt_archive_is_discoverable(historical_receipt_client) -> None:
    client, _ = historical_receipt_client

    endpoints = client.get("/api/info").json()["endpoints"]

    assert endpoints["historical_receipts_export"] == "/v1/historical-receipts/export"
    assert endpoints["historical_receipts_verify"] == "/v1/historical-receipts/verify"
    assert endpoints["historical_receipts_verify_upload"] == "/v1/historical-receipts/verify-upload"


def test_historical_receipt_verify_accepts_an_intact_chain(
    historical_receipt_client, monkeypatch: pytest.MonkeyPatch
) -> None:
    client, adapter = historical_receipt_client
    monkeypatch.setattr(adapter, "_load_receipt_v1_dicts", lambda: _receipt_dicts(3))

    response = client.post("/v1/historical-receipts/verify")

    assert response.status_code == 200
    report = response.json()["report"]
    assert report["scheme"] == "receipt_v1"
    assert report["receipt_version"] == "1.0"
    assert report["receipt_count"] == 3
    assert report["valid"] is True
    assert report["error_count"] == 0


def test_historical_receipt_verify_rejects_a_tampered_hash(
    historical_receipt_client,
) -> None:
    client, _ = historical_receipt_client
    receipts = _receipt_dicts(1)
    receipts[0]["receipt_hash"] = "a" * 64

    response = client.post(
        "/v1/historical-receipts/verify-upload",
        content=json.dumps(receipts[0]) + "\n",
    )

    assert response.status_code == 200
    report = response.json()["report"]
    assert report["valid"] is False
    assert any(finding["code"] == "hash_mismatch" for finding in report["findings"])


def test_historical_receipt_verify_rejects_a_chain_break(
    historical_receipt_client,
) -> None:
    client, _ = historical_receipt_client
    receipts = _receipt_dicts(2)
    receipts[1]["chain"]["parent_receipt_hash"] = "b" * 64

    response = client.post(
        "/v1/historical-receipts/verify-upload",
        content="\n".join(json.dumps(receipt) for receipt in receipts) + "\n",
    )

    assert response.status_code == 200
    report = response.json()["report"]
    assert report["valid"] is False
    assert any(finding["code"] == "chain_break" for finding in report["findings"])


def test_historical_receipt_export_reports_an_empty_archive(
    historical_receipt_client, monkeypatch: pytest.MonkeyPatch
) -> None:
    client, adapter = historical_receipt_client
    monkeypatch.setattr(adapter, "_load_receipt_v1_dicts", lambda: [])

    response = client.get("/v1/historical-receipts/export")

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "no_receipts"


def test_historical_receipt_verify_reports_the_invalid_jsonl_line(
    historical_receipt_client,
) -> None:
    client, _ = historical_receipt_client

    response = client.post(
        "/v1/historical-receipts/verify-upload",
        content='{"valid": true}\nnot valid json\n',
    )

    assert response.status_code == 400
    error = response.json()["error"]
    assert error["code"] == "invalid_jsonl"
    assert "line 2" in error["message"]

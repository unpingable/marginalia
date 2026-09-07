# SPDX-License-Identifier: Apache-2.0
"""Application-owned observation and standing processes for ag-ng/Docket."""

from __future__ import annotations

import hashlib
import json
import os
import sys
from pathlib import Path
from typing import Any

from gov_webui.generation_store import GenerationStore, LogicalRequest, LogicalStatus


OBSERVATION_RESOLVER_ID = "marginalia.generation-applicability-resolver/v1"
OBSERVATION_BASIS_TYPE = "marginalia.generation-applicability/v1"
STANDING_RESOLVER_ID = "marginalia.generation-standing/v1"


class BoundaryRefusal(RuntimeError):
    """The process cannot produce current evidence for the exact request."""


def ag_digest(domain: str, value: Any) -> str:
    payload = canonical(value)
    hasher = hashlib.sha256()
    hasher.update(b"ag-ng\0digest\0v1\0")
    hasher.update(len(domain).to_bytes(16, "big"))
    hasher.update(domain.encode("utf-8"))
    hasher.update(len(payload).to_bytes(16, "big"))
    hasher.update(payload)
    return "sha256:" + hasher.hexdigest()


def canonical(value: Any) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")


def generation_subject(request: LogicalRequest) -> str:
    return ag_digest(
        "marginalia.generation-subject/v1",
        {"project_id": request.project_id, "session_id": request.session_id},
    )


def generation_scope(request: LogicalRequest) -> str:
    return ag_digest(
        "marginalia.generation-scope/v1",
        {
            "expected_revision": request.expected_revision,
            "canon_fingerprint": request.canon_fingerprint,
            "guidance_fingerprint": request.guidance_fingerprint,
        },
    )


def _generation_databases() -> list[Path]:
    root = Path(os.environ.get("GOVERNOR_CONTEXTS_DIR", "/data/.governor"))
    if not root.is_absolute():
        raise BoundaryRefusal("GOVERNOR_CONTEXTS_DIR must be absolute")
    return sorted(root.glob("*/marginalia/generation.sqlite"))


def _request_for_observation(observation: str) -> LogicalRequest:
    matches = []
    for path in _generation_databases():
        request = GenerationStore(path).find_by_request_digest(observation)
        if request is not None:
            matches.append(request)
    if len(matches) != 1:
        raise BoundaryRefusal("observation does not identify exactly one generation request")
    request = matches[0]
    if request.status not in {LogicalStatus.DISPATCHING, LogicalStatus.UNKNOWN}:
        raise BoundaryRefusal(f"generation request is not dispatchable: {request.status}")
    return request


def observation_resolution(value: dict[str, Any]) -> dict[str, Any]:
    required = {"schema", "key", "observation", "subject", "now_unix_ms"}
    if not isinstance(value, dict) or set(value) != required:
        raise BoundaryRefusal("observation request does not have the exact v1 shape")
    if value["schema"] != "ag.governed-loop.observation-request/v1":
        raise BoundaryRefusal("unsupported observation request schema")
    request = _request_for_observation(value["observation"])
    if value["subject"] != generation_subject(request):
        raise BoundaryRefusal("observation subject differs from the frozen request")
    basis = {
        "schema": "ag.governed-loop.typed-observation-basis/v1",
        "basis_type": OBSERVATION_BASIS_TYPE,
        "basis_identity": request.request_digest,
    }
    now = value["now_unix_ms"]
    return {
        "schema": "ag.governed-loop.observation-resolution/v3",
        "key": value["key"],
        "observation": value["observation"],
        "currentness": ag_digest(
            "marginalia.generation-currentness/v1",
            {"request": request.request_digest, "at": now},
        ),
        "normalized_preconditions": ag_digest(
            "ag.governed-loop.typed-observation-basis/v1", basis
        ),
        "basis": basis,
        "resolver_id": OBSERVATION_RESOLVER_ID,
        "subject": value["subject"],
        "status": "current",
        "resolved_at_unix_ms": now,
        "fresh_until_unix_ms": now + 30_000,
    }


def standing_resolution(value: dict[str, Any]) -> dict[str, Any]:
    required = {
        "schema",
        "key",
        "observation",
        "proposal",
        "subject",
        "scope",
        "now_unix_ms",
    }
    if not isinstance(value, dict) or set(value) != required:
        raise BoundaryRefusal("standing request does not have the exact v1 shape")
    if value["schema"] != "ag.governed-loop.standing-request/v1":
        raise BoundaryRefusal("unsupported standing request schema")
    request = _request_for_observation(value["observation"])
    if value["subject"] != generation_subject(request) or value["scope"] != generation_scope(request):
        raise BoundaryRefusal("standing subject/scope differs from frozen request")
    now = value["now_unix_ms"]
    identity = {key: value[key] for key in sorted(required - {"schema", "now_unix_ms"})}
    return {
        "schema": "ag.governed-loop.standing-resolution/v2",
        "resolution": ag_digest("marginalia.generation-standing-resolution/v1", identity),
        "currentness": ag_digest(
            "marginalia.generation-standing-currentness/v1", {**identity, "at": now}
        ),
        "mandate": ag_digest(
            "marginalia.generation-service-mandate/v1", {"project_id": request.project_id}
        ),
        "key": value["key"],
        "observation": value["observation"],
        "proposal": value["proposal"],
        "subject": value["subject"],
        "scope": value["scope"],
        "resolver_id": STANDING_RESOLVER_ID,
        "status": "current",
        "resolved_at_unix_ms": now,
        "expires_at_unix_ms": now + 30_000,
    }


def docket_standing_resolution(value: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != {"schema", "issuance", "now_unix_ms"}:
        raise BoundaryRefusal("Docket standing request does not have the exact v1 shape")
    if value["schema"] != "docket.governed-loop.execution-standing-request/v1":
        raise BoundaryRefusal("unsupported Docket standing request schema")
    issuance = value["issuance"]
    if not isinstance(issuance, dict):
        raise BoundaryRefusal("Docket standing request has no issuance")
    now = value["now_unix_ms"]
    return {
        "schema": "docket.governed-loop.execution-standing-resolution/v1",
        "resolution": ag_digest("marginalia.docket-standing-resolution/v1", value),
        "currentness": ag_digest(
            "marginalia.docket-standing-currentness/v1", {"issuance": issuance["issuance"], "at": now}
        ),
        "execution_standing": ag_digest(
            "marginalia.docket-execution-standing/v1", {"issuance": issuance["issuance"]}
        ),
        "issuance": issuance["issuance"],
        "campaign": issuance["key"]["campaign"],
        "occurrence": issuance["key"]["occurrence"],
        "subject": issuance["subject"],
        "scope": issuance["scope"],
        "status": "current",
        "resolved_at_unix_ms": now,
        "expires_at_unix_ms": now + 30_000,
    }


def _main(resolver) -> int:
    try:
        content = sys.stdin.buffer.read(1_048_577)
        if len(content) > 1_048_576:
            raise BoundaryRefusal("resolver request exceeds 1 MiB")
        value = json.loads(content)
        result = resolver(value)
        sys.stdout.buffer.write(canonical(result) + b"\n")
        return 0
    except Exception as exc:
        sys.stderr.write(f"Marginalia resolver refused: {exc}\n")
        return 1


def observation_main() -> int:
    return _main(observation_resolution)


def standing_main() -> int:
    return _main(standing_resolution)


def docket_standing_main() -> int:
    return _main(docket_standing_resolution)

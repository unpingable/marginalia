# SPDX-License-Identifier: Apache-2.0
"""Durable identities and custody state for asynchronous generations.

This store deliberately separates one writer request from each provider
dispatch and from each returned candidate.  It records facts; AG authorizes
exact work, Docket owns attempt custody, and session/canon code decides whether
a candidate is still applicable.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import StrEnum
from pathlib import Path
from typing import Any, Iterable


class GenerationStoreError(RuntimeError):
    """Base generation-store error."""


class IdempotencyConflict(GenerationStoreError):
    """A client request ID was reused for different frozen work."""


class GenerationTransitionError(GenerationStoreError):
    """The requested transition is not legal from durable state."""


class GenerationDisabled(GenerationStoreError):
    """New dispatches are disabled for this project."""


class LogicalStatus(StrEnum):
    QUEUED = "queued"
    DISPATCHING = "dispatching"
    CANDIDATE = "candidate"
    ACCEPTED = "accepted"
    BLOCKED = "blocked"
    UNKNOWN = "unknown"
    FAILED = "failed"


class DispatchStatus(StrEnum):
    RESERVED = "reserved"
    EXECUTING = "executing"
    CANDIDATE = "candidate"
    BLOCKED = "blocked"
    UNKNOWN = "unknown"
    FAILED = "failed"


@dataclass(frozen=True)
class LogicalRequest:
    id: str
    client_request_id: str
    project_id: str
    session_id: str
    expected_revision: int
    canon_fingerprint: str
    guidance_fingerprint: str
    original_model: str
    original_route: str
    fallback_policy: tuple[tuple[str, str], ...]
    request_digest: str
    status: LogicalStatus
    candidate_id: str | None
    accepted_candidate_id: str | None
    last_error: str | None
    created_at: str
    updated_at: str


@dataclass(frozen=True)
class Dispatch:
    id: str
    logical_request_id: str
    ordinal: int
    actual_model: str
    actual_route: str
    request_digest: str
    status: DispatchStatus
    provider_execution_id: str | None
    candidate_id: str | None
    created_at: str
    updated_at: str


@dataclass(frozen=True)
class Candidate:
    id: str
    logical_request_id: str
    dispatch_id: str
    response_digest: str
    evidence_ref: str
    accepted_message_id: str | None
    created_at: str


@dataclass(frozen=True)
class CreateResult:
    request: LogicalRequest
    created: bool


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _canonical(value: Any) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")


def _digest(domain: str, value: Any) -> str:
    material = domain.encode("ascii") + b"\0" + _canonical(value)
    return "sha256:" + hashlib.sha256(material).hexdigest()


class GenerationStore:
    """SQLite custody index shared by the web and worker processes."""

    SCHEMA_VERSION = 1

    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=10, isolation_level=None)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 10000")
        return connection

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.execute("PRAGMA journal_mode = WAL")
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS generation_settings (
                    project_id TEXT PRIMARY KEY,
                    dispatch_enabled INTEGER NOT NULL CHECK(dispatch_enabled IN (0, 1)),
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS logical_request (
                    id TEXT PRIMARY KEY,
                    client_request_id TEXT NOT NULL,
                    project_id TEXT NOT NULL,
                    session_id TEXT NOT NULL,
                    expected_revision INTEGER NOT NULL CHECK(expected_revision >= 0),
                    canon_fingerprint TEXT NOT NULL,
                    guidance_fingerprint TEXT NOT NULL,
                    original_model TEXT NOT NULL,
                    original_route TEXT NOT NULL,
                    fallback_policy_json TEXT NOT NULL,
                    request_digest TEXT NOT NULL,
                    request_json TEXT NOT NULL,
                    status TEXT NOT NULL CHECK(status IN
                        ('queued','dispatching','candidate','accepted','blocked','unknown','failed')),
                    candidate_id TEXT,
                    accepted_candidate_id TEXT,
                    last_error TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE(project_id, client_request_id)
                );

                CREATE TABLE IF NOT EXISTS dispatch (
                    id TEXT PRIMARY KEY,
                    logical_request_id TEXT NOT NULL REFERENCES logical_request(id),
                    ordinal INTEGER NOT NULL CHECK(ordinal >= 0),
                    actual_model TEXT NOT NULL,
                    actual_route TEXT NOT NULL,
                    request_digest TEXT NOT NULL,
                    status TEXT NOT NULL CHECK(status IN
                        ('reserved','executing','candidate','blocked','unknown','failed')),
                    provider_execution_id TEXT,
                    candidate_id TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE(logical_request_id, ordinal),
                    UNIQUE(logical_request_id, request_digest)
                );

                CREATE TABLE IF NOT EXISTS generation_candidate (
                    id TEXT PRIMARY KEY,
                    logical_request_id TEXT NOT NULL REFERENCES logical_request(id),
                    dispatch_id TEXT NOT NULL REFERENCES dispatch(id),
                    response_digest TEXT NOT NULL,
                    evidence_ref TEXT NOT NULL,
                    accepted_message_id TEXT,
                    created_at TEXT NOT NULL,
                    UNIQUE(dispatch_id, response_digest)
                );

                CREATE TABLE IF NOT EXISTS generation_event (
                    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                    logical_request_id TEXT NOT NULL REFERENCES logical_request(id),
                    dispatch_id TEXT,
                    candidate_id TEXT,
                    event_type TEXT NOT NULL,
                    detail_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                """
            )
            connection.execute(f"PRAGMA user_version = {self.SCHEMA_VERSION}")

    @staticmethod
    def _fallbacks(value: Iterable[dict[str, str]]) -> tuple[tuple[str, str], ...]:
        result: list[tuple[str, str]] = []
        for item in value:
            model = str(item.get("model", "")).strip()
            route = str(item.get("route", "")).strip()
            if not model or not route:
                raise ValueError("each fallback requires non-empty model and route")
            pair = (model, route)
            if pair not in result:
                result.append(pair)
        return tuple(result)

    def set_dispatch_enabled(self, project_id: str, enabled: bool) -> None:
        now = _now()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                """INSERT INTO generation_settings(project_id,dispatch_enabled,updated_at)
                   VALUES(?,?,?) ON CONFLICT(project_id) DO UPDATE SET
                   dispatch_enabled=excluded.dispatch_enabled, updated_at=excluded.updated_at""",
                (project_id, int(enabled), now),
            )
            connection.commit()

    def dispatch_enabled(self, project_id: str) -> bool:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT dispatch_enabled FROM generation_settings WHERE project_id=?",
                (project_id,),
            ).fetchone()
        return bool(row[0]) if row is not None else False

    def create_request(
        self,
        *,
        client_request_id: str,
        project_id: str,
        session_id: str,
        expected_revision: int,
        canon_fingerprint: str,
        guidance_fingerprint: str,
        original_model: str,
        original_route: str,
        fallback_policy: Iterable[dict[str, str]] = (),
        request: dict[str, Any],
    ) -> CreateResult:
        client_request_id = client_request_id.strip()
        if not client_request_id:
            raise ValueError("client_request_id must not be empty")
        fallbacks = self._fallbacks(fallback_policy)
        frozen = {
            "schema": "marginalia.logical-generation/v1",
            "client_request_id": client_request_id,
            "project_id": project_id,
            "session_id": session_id,
            "expected_revision": expected_revision,
            "canon_fingerprint": canon_fingerprint,
            "guidance_fingerprint": guidance_fingerprint,
            "original_selection": {"model": original_model, "route": original_route},
            "authorized_fallback_policy": [
                {"model": model, "route": route} for model, route in fallbacks
            ],
            "request": request,
        }
        request_digest = _digest("marginalia.logical-generation/v1", frozen)
        logical_id = "gen_" + uuid.uuid4().hex
        now = _now()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            existing = connection.execute(
                "SELECT * FROM logical_request WHERE project_id=? AND client_request_id=?",
                (project_id, client_request_id),
            ).fetchone()
            if existing is not None:
                if existing["request_digest"] != request_digest:
                    connection.rollback()
                    raise IdempotencyConflict(
                        "client request ID is already bound to different frozen work"
                    )
                connection.commit()
                return CreateResult(self._logical(existing), False)
            connection.execute(
                """INSERT INTO logical_request(
                       id,client_request_id,project_id,session_id,expected_revision,
                       canon_fingerprint,guidance_fingerprint,original_model,original_route,
                       fallback_policy_json,request_digest,request_json,status,created_at,updated_at
                   ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    logical_id,
                    client_request_id,
                    project_id,
                    session_id,
                    expected_revision,
                    canon_fingerprint,
                    guidance_fingerprint,
                    original_model,
                    original_route,
                    _canonical([{"model": m, "route": r} for m, r in fallbacks]).decode(),
                    request_digest,
                    _canonical(request).decode(),
                    LogicalStatus.QUEUED,
                    now,
                    now,
                ),
            )
            self._event(connection, logical_id, "request_created", {"digest": request_digest})
            row = connection.execute(
                "SELECT * FROM logical_request WHERE id=?", (logical_id,)
            ).fetchone()
            connection.commit()
        assert row is not None
        return CreateResult(self._logical(row), True)

    def request_payload(self, logical_request_id: str) -> dict[str, Any]:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT request_json FROM logical_request WHERE id=?", (logical_request_id,)
            ).fetchone()
        if row is None:
            raise KeyError(logical_request_id)
        return json.loads(row[0])

    def reserve_dispatch(
        self,
        logical_request_id: str,
        *,
        model: str | None = None,
        route: str | None = None,
    ) -> Dispatch:
        """Reserve one authorized route. Unknown attempts can never enter here."""
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT * FROM logical_request WHERE id=?", (logical_request_id,)
            ).fetchone()
            if row is None:
                connection.rollback()
                raise KeyError(logical_request_id)
            request = self._logical(row)
            if not self._dispatch_enabled(connection, request.project_id):
                connection.rollback()
                raise GenerationDisabled("new dispatches are disabled for this project")
            if request.status is not LogicalStatus.QUEUED:
                connection.rollback()
                raise GenerationTransitionError(
                    f"cannot reserve a dispatch while request is {request.status}"
                )
            prior = connection.execute(
                "SELECT COUNT(*) FROM dispatch WHERE logical_request_id=?",
                (logical_request_id,),
            ).fetchone()[0]
            authorized = ((request.original_model, request.original_route),) + request.fallback_policy
            selected = (model, route) if model is not None and route is not None else authorized[prior]
            if selected not in authorized or (prior == 0 and selected != authorized[0]):
                connection.rollback()
                raise GenerationTransitionError("dispatch route is outside the frozen fallback policy")
            if prior >= len(authorized) or selected != authorized[prior]:
                connection.rollback()
                raise GenerationTransitionError("dispatch route is out of authorized order")
            dispatch_digest = _digest(
                "marginalia.provider-dispatch/v1",
                {
                    "logical_request": request.request_digest,
                    "ordinal": prior,
                    "actual_model": selected[0],
                    "actual_route": selected[1],
                    "request": json.loads(row["request_json"]),
                },
            )
            dispatch_id = "dsp_" + uuid.uuid4().hex
            now = _now()
            connection.execute(
                """INSERT INTO dispatch(
                       id,logical_request_id,ordinal,actual_model,actual_route,request_digest,
                       status,created_at,updated_at
                   ) VALUES(?,?,?,?,?,?,?,?,?)""",
                (
                    dispatch_id,
                    logical_request_id,
                    prior,
                    selected[0],
                    selected[1],
                    dispatch_digest,
                    DispatchStatus.RESERVED,
                    now,
                    now,
                ),
            )
            connection.execute(
                "UPDATE logical_request SET status=?,updated_at=? WHERE id=?",
                (LogicalStatus.DISPATCHING, now, logical_request_id),
            )
            self._event(
                connection,
                logical_request_id,
                "dispatch_reserved",
                {"dispatch": dispatch_id, "digest": dispatch_digest},
                dispatch_id=dispatch_id,
            )
            result = connection.execute("SELECT * FROM dispatch WHERE id=?", (dispatch_id,)).fetchone()
            connection.commit()
        assert result is not None
        return self._dispatch(result)

    def mark_executing(self, dispatch_id: str, provider_execution_id: str | None = None) -> None:
        self._dispatch_transition(
            dispatch_id,
            expected=DispatchStatus.RESERVED,
            dispatch_status=DispatchStatus.EXECUTING,
            logical_status=LogicalStatus.DISPATCHING,
            event="provider_dispatch_started",
            provider_execution_id=provider_execution_id,
        )

    def record_candidate(
        self,
        dispatch_id: str,
        *,
        response_digest: str,
        evidence_ref: str,
    ) -> Candidate:
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            dispatch_row = connection.execute(
                "SELECT * FROM dispatch WHERE id=?", (dispatch_id,)
            ).fetchone()
            if dispatch_row is None:
                connection.rollback()
                raise KeyError(dispatch_id)
            dispatch = self._dispatch(dispatch_row)
            existing = connection.execute(
                "SELECT * FROM generation_candidate WHERE dispatch_id=? AND response_digest=?",
                (dispatch_id, response_digest),
            ).fetchone()
            if existing is not None:
                connection.commit()
                return self._candidate(existing)
            if dispatch.status not in {DispatchStatus.EXECUTING, DispatchStatus.UNKNOWN}:
                connection.rollback()
                raise GenerationTransitionError(
                    f"cannot attach a candidate while dispatch is {dispatch.status}"
                )
            conflicting = connection.execute(
                "SELECT id FROM generation_candidate WHERE dispatch_id=?", (dispatch_id,)
            ).fetchone()
            if conflicting is not None:
                connection.rollback()
                raise GenerationTransitionError("dispatch already has a different candidate")
            candidate_id = _digest(
                "marginalia.generation-candidate/v1",
                {"dispatch": dispatch.request_digest, "response": response_digest},
            )
            now = _now()
            connection.execute(
                "INSERT INTO generation_candidate VALUES(?,?,?,?,?,?,?)",
                (
                    candidate_id,
                    dispatch.logical_request_id,
                    dispatch_id,
                    response_digest,
                    evidence_ref,
                    None,
                    now,
                ),
            )
            connection.execute(
                "UPDATE dispatch SET status=?,candidate_id=?,updated_at=? WHERE id=?",
                (DispatchStatus.CANDIDATE, candidate_id, now, dispatch_id),
            )
            connection.execute(
                "UPDATE logical_request SET status=?,candidate_id=?,updated_at=? WHERE id=?",
                (LogicalStatus.CANDIDATE, candidate_id, now, dispatch.logical_request_id),
            )
            self._event(
                connection,
                dispatch.logical_request_id,
                "candidate_recorded",
                {"response_digest": response_digest, "evidence_ref": evidence_ref},
                dispatch_id=dispatch_id,
                candidate_id=candidate_id,
            )
            row = connection.execute(
                "SELECT * FROM generation_candidate WHERE id=?", (candidate_id,)
            ).fetchone()
            connection.commit()
        assert row is not None
        return self._candidate(row)

    def mark_unknown(self, dispatch_id: str, reason: str) -> None:
        self._dispatch_transition(
            dispatch_id,
            expected=(DispatchStatus.RESERVED, DispatchStatus.EXECUTING),
            dispatch_status=DispatchStatus.UNKNOWN,
            logical_status=LogicalStatus.UNKNOWN,
            event="dispatch_outcome_unknown",
            error=reason,
        )

    def mark_failed(self, dispatch_id: str, reason: str) -> None:
        """Record only a qualified terminal failure, never mere timeout/absence."""
        self._dispatch_transition(
            dispatch_id,
            expected=(DispatchStatus.RESERVED, DispatchStatus.EXECUTING),
            dispatch_status=DispatchStatus.FAILED,
            logical_status=LogicalStatus.FAILED,
            event="dispatch_failed",
            error=reason,
        )

    def get_request(self, logical_request_id: str) -> LogicalRequest | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM logical_request WHERE id=?", (logical_request_id,)
            ).fetchone()
        return self._logical(row) if row is not None else None

    def get_by_client_id(self, project_id: str, client_request_id: str) -> LogicalRequest | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM logical_request WHERE project_id=? AND client_request_id=?",
                (project_id, client_request_id),
            ).fetchone()
        return self._logical(row) if row is not None else None

    def get_dispatch(self, dispatch_id: str) -> Dispatch | None:
        with self._connect() as connection:
            row = connection.execute("SELECT * FROM dispatch WHERE id=?", (dispatch_id,)).fetchone()
        return self._dispatch(row) if row is not None else None

    def get_candidate(self, candidate_id: str) -> Candidate | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM generation_candidate WHERE id=?", (candidate_id,)
            ).fetchone()
        return self._candidate(row) if row is not None else None

    def events(self, logical_request_id: str) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                """SELECT sequence,dispatch_id,candidate_id,event_type,detail_json,created_at
                   FROM generation_event WHERE logical_request_id=? ORDER BY sequence""",
                (logical_request_id,),
            ).fetchall()
        return [
            {
                "sequence": row["sequence"],
                "dispatch_id": row["dispatch_id"],
                "candidate_id": row["candidate_id"],
                "event_type": row["event_type"],
                "detail": json.loads(row["detail_json"]),
                "created_at": row["created_at"],
            }
            for row in rows
        ]

    def _dispatch_transition(
        self,
        dispatch_id: str,
        *,
        expected: DispatchStatus | tuple[DispatchStatus, ...],
        dispatch_status: DispatchStatus,
        logical_status: LogicalStatus,
        event: str,
        provider_execution_id: str | None = None,
        error: str | None = None,
    ) -> None:
        allowed = (expected,) if isinstance(expected, DispatchStatus) else expected
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute("SELECT * FROM dispatch WHERE id=?", (dispatch_id,)).fetchone()
            if row is None:
                connection.rollback()
                raise KeyError(dispatch_id)
            dispatch = self._dispatch(row)
            if dispatch.status not in allowed:
                connection.rollback()
                raise GenerationTransitionError(
                    f"cannot move dispatch from {dispatch.status} to {dispatch_status}"
                )
            now = _now()
            connection.execute(
                """UPDATE dispatch SET status=?,provider_execution_id=COALESCE(?,provider_execution_id),
                   updated_at=? WHERE id=?""",
                (dispatch_status, provider_execution_id, now, dispatch_id),
            )
            connection.execute(
                "UPDATE logical_request SET status=?,last_error=?,updated_at=? WHERE id=?",
                (logical_status, error, now, dispatch.logical_request_id),
            )
            self._event(
                connection,
                dispatch.logical_request_id,
                event,
                {"reason": error} if error else {},
                dispatch_id=dispatch_id,
            )
            connection.commit()

    @staticmethod
    def _dispatch_enabled(connection: sqlite3.Connection, project_id: str) -> bool:
        row = connection.execute(
            "SELECT dispatch_enabled FROM generation_settings WHERE project_id=?", (project_id,)
        ).fetchone()
        return bool(row[0]) if row is not None else False

    @staticmethod
    def _event(
        connection: sqlite3.Connection,
        logical_request_id: str,
        event_type: str,
        detail: dict[str, Any],
        *,
        dispatch_id: str | None = None,
        candidate_id: str | None = None,
    ) -> None:
        connection.execute(
            """INSERT INTO generation_event(
                   logical_request_id,dispatch_id,candidate_id,event_type,detail_json,created_at
               ) VALUES(?,?,?,?,?,?)""",
            (
                logical_request_id,
                dispatch_id,
                candidate_id,
                event_type,
                _canonical(detail).decode(),
                _now(),
            ),
        )

    @staticmethod
    def _logical(row: sqlite3.Row) -> LogicalRequest:
        return LogicalRequest(
            id=row["id"],
            client_request_id=row["client_request_id"],
            project_id=row["project_id"],
            session_id=row["session_id"],
            expected_revision=row["expected_revision"],
            canon_fingerprint=row["canon_fingerprint"],
            guidance_fingerprint=row["guidance_fingerprint"],
            original_model=row["original_model"],
            original_route=row["original_route"],
            fallback_policy=tuple(
                (item["model"], item["route"])
                for item in json.loads(row["fallback_policy_json"])
            ),
            request_digest=row["request_digest"],
            status=LogicalStatus(row["status"]),
            candidate_id=row["candidate_id"],
            accepted_candidate_id=row["accepted_candidate_id"],
            last_error=row["last_error"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    @staticmethod
    def _dispatch(row: sqlite3.Row) -> Dispatch:
        return Dispatch(
            id=row["id"],
            logical_request_id=row["logical_request_id"],
            ordinal=row["ordinal"],
            actual_model=row["actual_model"],
            actual_route=row["actual_route"],
            request_digest=row["request_digest"],
            status=DispatchStatus(row["status"]),
            provider_execution_id=row["provider_execution_id"],
            candidate_id=row["candidate_id"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    @staticmethod
    def _candidate(row: sqlite3.Row) -> Candidate:
        return Candidate(
            id=row["id"],
            logical_request_id=row["logical_request_id"],
            dispatch_id=row["dispatch_id"],
            response_digest=row["response_digest"],
            evidence_ref=row["evidence_ref"],
            accepted_message_id=row["accepted_message_id"],
            created_at=row["created_at"],
        )

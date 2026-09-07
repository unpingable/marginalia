# SPDX-License-Identifier: Apache-2.0
"""Docket-facing executor mechanics for one Marginalia generation.

The core is transport-independent and deliberately requires an evidence writer
before provider execution.  Gate 3D supplies the deployment evidence backend;
until then this module is not wired into the application or container.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import StrEnum
from pathlib import Path
from typing import Any, Callable, Protocol

from gov_webui.generation_store import DispatchStatus, GenerationStore


EXECUTOR_PLAN_SCHEMA = "marginalia.generation-executor-plan/v1"
EXECUTOR_WORK_SCHEMA = "marginalia.generation-dispatch/v1"
DOCKET_DISPATCH_KEYS = frozenset({"attempt", "marker", "work_schema", "work", "subject", "scope"})


class ExecutorError(RuntimeError):
    """The exact executor request cannot be satisfied."""


class ProviderOutcomeUnknown(ExecutorError):
    """Provider execution may have occurred but no qualified response is held."""


class ProviderDefinitiveFailure(ExecutorError):
    """Qualified provider evidence establishes a terminal failure."""


class AttemptState(StrEnum):
    RESERVED = "reserved"
    EXECUTING = "executing"
    SUCCESS = "success"
    FAILURE = "failure"
    INDETERMINATE = "indeterminate"


@dataclass(frozen=True)
class ExecutorPlan:
    attempt_store: Path
    generation_store: Path
    evidence_root: Path
    evidence_keyring: Path
    evidence_retention_days: int
    marginalia_dispatch_id: str
    request_digest: str
    subject: str
    scope: str

    @classmethod
    def from_file(cls, path: Path) -> ExecutorPlan:
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ExecutorError(f"cannot read executor plan: {exc}") from exc
        required = {
            "schema",
            "attempt_store",
            "generation_store",
            "evidence_root",
            "evidence_keyring",
            "evidence_retention_days",
            "marginalia_dispatch_id",
            "request_digest",
            "subject",
            "scope",
        }
        if not isinstance(value, dict) or set(value) != required:
            raise ExecutorError("executor plan does not have the exact v1 shape")
        if value["schema"] != EXECUTOR_PLAN_SCHEMA:
            raise ExecutorError("unsupported executor plan schema")
        for key in required - {"schema", "evidence_retention_days"}:
            if not isinstance(value[key], str) or not value[key]:
                raise ExecutorError(f"executor plan {key} must be a non-empty string")
        attempt_store = Path(value["attempt_store"])
        generation_store = Path(value["generation_store"])
        evidence_root = Path(value["evidence_root"])
        evidence_keyring = Path(value["evidence_keyring"])
        if not all(
            path.is_absolute()
            for path in (attempt_store, generation_store, evidence_root, evidence_keyring)
        ):
            raise ExecutorError("executor store paths must be absolute")
        retention = value["evidence_retention_days"]
        if not isinstance(retention, int) or isinstance(retention, bool) or not 1 <= retention <= 3650:
            raise ExecutorError("evidence_retention_days must be an integer between 1 and 3650")
        for key in ("request_digest", "subject", "scope"):
            _require_digest(value[key], f"executor plan {key}")
        return cls(
            attempt_store=attempt_store,
            generation_store=generation_store,
            evidence_root=evidence_root,
            evidence_keyring=evidence_keyring,
            evidence_retention_days=retention,
            marginalia_dispatch_id=value["marginalia_dispatch_id"],
            request_digest=value["request_digest"],
            subject=value["subject"],
            scope=value["scope"],
        )

    def canonical_value(self) -> dict[str, Any]:
        return {
            "attempt_store": str(self.attempt_store),
            "generation_store": str(self.generation_store),
            "evidence_root": str(self.evidence_root),
            "evidence_keyring": str(self.evidence_keyring),
            "evidence_retention_days": self.evidence_retention_days,
            "marginalia_dispatch_id": self.marginalia_dispatch_id,
            "request_digest": self.request_digest,
            "schema": EXECUTOR_PLAN_SCHEMA,
            "scope": self.scope,
            "subject": self.subject,
        }

    @property
    def identity(self) -> str:
        return _digest("marginalia.generation-executor-plan/v1", self.canonical_value())


@dataclass(frozen=True)
class DocketDispatch:
    attempt: str
    marker: str
    work_schema: str
    work: str
    subject: str
    scope: str

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> DocketDispatch:
        if not isinstance(value, dict) or set(value) != DOCKET_DISPATCH_KEYS:
            raise ExecutorError("Docket dispatch does not have the exact v1 shape")
        if value["work_schema"] != EXECUTOR_WORK_SCHEMA:
            raise ExecutorError("Docket dispatch work schema is not Marginalia v1")
        for key in DOCKET_DISPATCH_KEYS:
            if not isinstance(value[key], str) or not value[key]:
                raise ExecutorError(f"Docket dispatch {key} must be a non-empty string")
            if key != "work_schema":
                _require_digest(value[key], f"Docket dispatch {key}")
        return cls(**value)

    def canonical_value(self) -> dict[str, str]:
        return {
            "attempt": self.attempt,
            "marker": self.marker,
            "scope": self.scope,
            "subject": self.subject,
            "work": self.work,
            "work_schema": self.work_schema,
        }


@dataclass(frozen=True)
class ExecutorOutcome:
    attempt: str
    marker: str
    receipt: str
    outcome: str

    def to_dict(self) -> dict[str, str]:
        return {
            "attempt": self.attempt,
            "marker": self.marker,
            "outcome": self.outcome,
            "receipt": self.receipt,
        }


@dataclass(frozen=True)
class EvidenceReference:
    reference: str
    response_digest: str


class EvidenceWriter(Protocol):
    def __call__(
        self,
        response: dict[str, Any],
        *,
        logical_request_id: str,
        dispatch_id: str,
        docket_attempt: str,
    ) -> EvidenceReference: ...


Provider = Callable[[dict[str, Any]], dict[str, Any]]


class ExecutorAttemptStore:
    """Executor-local idempotency journal; it grants no dispatch authority."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.execute("PRAGMA journal_mode = WAL")
            connection.execute(
                """CREATE TABLE IF NOT EXISTS executor_attempt (
                       attempt TEXT PRIMARY KEY,
                       marker TEXT NOT NULL UNIQUE,
                       dispatch_json TEXT NOT NULL,
                       dispatch_digest TEXT NOT NULL,
                       state TEXT NOT NULL CHECK(state IN
                           ('reserved','executing','success','failure','indeterminate')),
                       outcome_json TEXT,
                       evidence_ref TEXT,
                       created_at TEXT NOT NULL,
                       updated_at TEXT NOT NULL
                   )"""
            )

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=10, isolation_level=None)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout = 10000")
        return connection

    def reserve(self, dispatch: DocketDispatch) -> ExecutorOutcome | None:
        payload = _canonical(dispatch.canonical_value()).decode()
        digest = _digest("marginalia.docket-dispatch/v1", dispatch.canonical_value())
        now = _now()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT * FROM executor_attempt WHERE attempt=?", (dispatch.attempt,)
            ).fetchone()
            if row is not None:
                if row["dispatch_digest"] != digest or row["marker"] != dispatch.marker:
                    connection.rollback()
                    raise ExecutorError("attempt replay substituted immutable dispatch facts")
                if row["outcome_json"]:
                    connection.commit()
                    return ExecutorOutcome(**json.loads(row["outcome_json"]))
                connection.commit()
                return None
            marker = connection.execute(
                "SELECT attempt FROM executor_attempt WHERE marker=?", (dispatch.marker,)
            ).fetchone()
            if marker is not None:
                connection.rollback()
                raise ExecutorError("Docket marker was reused across attempts")
            connection.execute(
                "INSERT INTO executor_attempt VALUES(?,?,?,?,?,?,?,?,?)",
                (
                    dispatch.attempt,
                    dispatch.marker,
                    payload,
                    digest,
                    AttemptState.RESERVED,
                    None,
                    None,
                    now,
                    now,
                ),
            )
            connection.commit()
        return None

    def state(self, attempt: str) -> AttemptState | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT state FROM executor_attempt WHERE attempt=?", (attempt,)
            ).fetchone()
        return AttemptState(row[0]) if row is not None else None

    def begin(self, dispatch: DocketDispatch) -> None:
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT state FROM executor_attempt WHERE attempt=?", (dispatch.attempt,)
            ).fetchone()
            if row is None or AttemptState(row[0]) is not AttemptState.RESERVED:
                connection.rollback()
                raise ExecutorError("attempt is not durably reserved")
            connection.execute(
                "UPDATE executor_attempt SET state=?,updated_at=? WHERE attempt=?",
                (AttemptState.EXECUTING, _now(), dispatch.attempt),
            )
            connection.commit()

    def finish(
        self,
        dispatch: DocketDispatch,
        outcome: ExecutorOutcome,
        *,
        evidence_ref: str,
    ) -> ExecutorOutcome:
        if outcome.attempt != dispatch.attempt or outcome.marker != dispatch.marker:
            raise ExecutorError("executor outcome is not bound to its dispatch")
        state = AttemptState(outcome.outcome)
        canonical = _canonical(outcome.to_dict()).decode()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT dispatch_digest,outcome_json FROM executor_attempt WHERE attempt=?",
                (dispatch.attempt,),
            ).fetchone()
            if row is None:
                connection.rollback()
                raise ExecutorError("cannot finish an unknown attempt")
            expected = _digest("marginalia.docket-dispatch/v1", dispatch.canonical_value())
            if row["dispatch_digest"] != expected:
                connection.rollback()
                raise ExecutorError("attempt finish substituted dispatch facts")
            if row["outcome_json"] is not None:
                prior = ExecutorOutcome(**json.loads(row["outcome_json"]))
                connection.commit()
                if prior != outcome:
                    raise ExecutorError("terminal attempt outcome cannot be replaced")
                return prior
            connection.execute(
                """UPDATE executor_attempt SET state=?,outcome_json=?,evidence_ref=?,updated_at=?
                   WHERE attempt=?""",
                (state, canonical, evidence_ref, _now(), dispatch.attempt),
            )
            connection.commit()
        return outcome

    def reconcile(self, dispatch: DocketDispatch) -> ExecutorOutcome:
        """Read durable evidence only; this method never invokes provider mechanics."""
        payload_digest = _digest("marginalia.docket-dispatch/v1", dispatch.canonical_value())
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM executor_attempt WHERE attempt=?", (dispatch.attempt,)
            ).fetchone()
        if row is None:
            raise ExecutorError("cannot reconcile an unknown attempt")
        if row["dispatch_digest"] != payload_digest or row["marker"] != dispatch.marker:
            raise ExecutorError("reconciliation substituted immutable dispatch facts")
        if row["outcome_json"]:
            return ExecutorOutcome(**json.loads(row["outcome_json"]))
        receipt = _digest(
            "marginalia.executor-indeterminate/v1",
            {
                "attempt": dispatch.attempt,
                "dispatch": payload_digest,
                "last_durable_state": row["state"],
            },
        )
        return ExecutorOutcome(
            attempt=dispatch.attempt,
            marker=dispatch.marker,
            receipt=receipt,
            outcome=AttemptState.INDETERMINATE,
        )


class GenerationExecutor:
    """Executes an exact Docket dispatch once and retains exact response custody."""

    def __init__(
        self,
        plan: ExecutorPlan,
        *,
        provider: Provider,
        evidence_writer: EvidenceWriter,
    ) -> None:
        self.plan = plan
        self.provider = provider
        self.evidence_writer = evidence_writer
        self.attempts = ExecutorAttemptStore(plan.attempt_store)
        self.generations = GenerationStore(plan.generation_store)

    def _validate(self, dispatch: DocketDispatch) -> None:
        if dispatch.work != self.plan.identity:
            raise ExecutorError("Docket work does not match the sealed executor plan")
        if dispatch.subject != self.plan.subject or dispatch.scope != self.plan.scope:
            raise ExecutorError("Docket subject or scope does not match the sealed executor plan")
        durable = self.generations.get_dispatch(self.plan.marginalia_dispatch_id)
        if durable is None:
            raise ExecutorError("Marginalia dispatch record is missing")
        if durable.request_digest != self.plan.request_digest:
            raise ExecutorError("Marginalia request digest differs from the executor plan")

    def execute(self, dispatch: DocketDispatch) -> ExecutorOutcome:
        self._validate(dispatch)
        replay = self.attempts.reserve(dispatch)
        if replay is not None:
            return replay
        current = self.attempts.state(dispatch.attempt)
        if current is not AttemptState.RESERVED:
            # A prior process crossed the mechanics boundary. Only reconciliation
            # is legal; execute must not turn restart into another provider call.
            raise ExecutorError("attempt already crossed the provider execution boundary")
        self.attempts.begin(dispatch)
        durable = self.generations.get_dispatch(self.plan.marginalia_dispatch_id)
        assert durable is not None
        if durable.status is DispatchStatus.RESERVED:
            self.generations.mark_executing(durable.id, dispatch.attempt)
        elif durable.status is not DispatchStatus.EXECUTING:
            raise ExecutorError(f"Marginalia dispatch is not executable: {durable.status}")
        request = self.generations.request_payload(durable.logical_request_id)
        try:
            response = self.provider(request)
            evidence = self.evidence_writer(
                response,
                logical_request_id=durable.logical_request_id,
                dispatch_id=durable.id,
                docket_attempt=dispatch.attempt,
            )
            candidate = self.generations.record_candidate(
                durable.id,
                response_digest=evidence.response_digest,
                evidence_ref=evidence.reference,
            )
            receipt = _digest(
                "marginalia.executor-success/v1",
                {
                    "attempt": dispatch.attempt,
                    "candidate": candidate.id,
                    "evidence": evidence.reference,
                    "response": evidence.response_digest,
                },
            )
            return self.attempts.finish(
                dispatch,
                ExecutorOutcome(dispatch.attempt, dispatch.marker, receipt, "success"),
                evidence_ref=evidence.reference,
            )
        except ProviderDefinitiveFailure as exc:
            receipt = _digest(
                "marginalia.executor-failure/v1",
                {"attempt": dispatch.attempt, "reason": str(exc)},
            )
            self.generations.mark_failed(durable.id, str(exc))
            return self.attempts.finish(
                dispatch,
                ExecutorOutcome(dispatch.attempt, dispatch.marker, receipt, "failure"),
                evidence_ref=receipt,
            )
        except Exception as exc:
            # Once provider invocation began, lack of a retained response is
            # indeterminate. Cancellation and a browser wait ending are not proof
            # that no execution or billing occurred.
            reason = str(exc) or type(exc).__name__
            receipt = _digest(
                "marginalia.executor-indeterminate/v1",
                {"attempt": dispatch.attempt, "reason": reason},
            )
            self.generations.mark_unknown(durable.id, reason)
            return self.attempts.finish(
                dispatch,
                ExecutorOutcome(dispatch.attempt, dispatch.marker, receipt, "indeterminate"),
                evidence_ref=receipt,
            )

    def reconcile(self, dispatch: DocketDispatch) -> ExecutorOutcome:
        self._validate(dispatch)
        return self.attempts.reconcile(dispatch)


def parse_docket_dispatch(content: bytes) -> DocketDispatch:
    if len(content) > 1_048_576:
        raise ExecutorError("Docket dispatch exceeds 1 MiB")
    try:
        value = json.loads(content)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ExecutorError(f"invalid Docket dispatch JSON: {exc}") from exc
    return DocketDispatch.from_dict(value)


def _require_digest(value: str, label: str) -> None:
    if len(value) != 71 or not value.startswith("sha256:"):
        raise ExecutorError(f"{label} is not a canonical SHA-256 digest")
    try:
        bytes.fromhex(value[7:])
    except ValueError as exc:
        raise ExecutorError(f"{label} is not a canonical SHA-256 digest") from exc
    if value[7:] != value[7:].lower():
        raise ExecutorError(f"{label} is not a canonical SHA-256 digest")


def _canonical(value: Any) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")


def _digest(domain: str, value: Any) -> str:
    return "sha256:" + hashlib.sha256(
        domain.encode("ascii") + b"\0" + _canonical(value)
    ).hexdigest()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()

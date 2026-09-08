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


EXECUTOR_PLAN_SCHEMA_V1 = "marginalia.generation-executor-plan/v1"
EXECUTOR_PLAN_SCHEMA = "marginalia.generation-executor-plan/v2"
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
    providerctl: Path | None = None
    providerctl_config: Path | None = None
    model_config: Path | None = None
    schema: str = EXECUTOR_PLAN_SCHEMA_V1

    @classmethod
    def from_file(cls, path: Path) -> ExecutorPlan:
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ExecutorError(f"cannot read executor plan: {exc}") from exc
        common = {
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
        v2 = {"providerctl", "providerctl_config", "model_config"}
        if not isinstance(value, dict) or value.get("schema") not in {
            EXECUTOR_PLAN_SCHEMA_V1,
            EXECUTOR_PLAN_SCHEMA,
        }:
            raise ExecutorError("unsupported executor plan schema")
        expected = common | (v2 if value["schema"] == EXECUTOR_PLAN_SCHEMA else set())
        if set(value) != expected:
            raise ExecutorError("executor plan does not have the exact versioned shape")
        for key in expected - {"schema", "evidence_retention_days"}:
            if not isinstance(value[key], str) or not value[key]:
                raise ExecutorError(f"executor plan {key} must be a non-empty string")
        attempt_store = Path(value["attempt_store"])
        generation_store = Path(value["generation_store"])
        evidence_root = Path(value["evidence_root"])
        evidence_keyring = Path(value["evidence_keyring"])
        provider_paths = tuple(Path(value[key]) for key in v2) if v2 <= set(value) else ()
        if not all(
            path.is_absolute()
            for path in (
                attempt_store,
                generation_store,
                evidence_root,
                evidence_keyring,
                *provider_paths,
            )
        ):
            raise ExecutorError("executor store paths must be absolute")
        retention = value["evidence_retention_days"]
        if (
            not isinstance(retention, int)
            or isinstance(retention, bool)
            or not 1 <= retention <= 3650
        ):
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
            providerctl=(Path(value["providerctl"]) if "providerctl" in value else None),
            providerctl_config=(
                Path(value["providerctl_config"]) if "providerctl_config" in value else None
            ),
            model_config=(Path(value["model_config"]) if "model_config" in value else None),
            schema=value["schema"],
        )

    def canonical_value(self) -> dict[str, Any]:
        value = {
            "attempt_store": str(self.attempt_store),
            "generation_store": str(self.generation_store),
            "evidence_root": str(self.evidence_root),
            "evidence_keyring": str(self.evidence_keyring),
            "evidence_retention_days": self.evidence_retention_days,
            "marginalia_dispatch_id": self.marginalia_dispatch_id,
            "request_digest": self.request_digest,
            "schema": self.schema,
            "scope": self.scope,
            "subject": self.subject,
        }
        if self.schema == EXECUTOR_PLAN_SCHEMA:
            if None in (self.providerctl, self.providerctl_config, self.model_config):
                raise ExecutorError("v2 executor plan requires ag-providerd paths")
            value.update(
                {
                    "providerctl": str(self.providerctl),
                    "providerctl_config": str(self.providerctl_config),
                    "model_config": str(self.model_config),
                }
            )
        return value

    @property
    def identity(self) -> str:
        return _digest(self.schema, self.canonical_value())


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


class DurableProvider(Protocol):
    def prepare(
        self,
        payload: dict[str, Any],
        *,
        project_id: str,
        session_id: str,
        docket_attempt: str,
        docket_marker: str,
        actual_route: str,
    ) -> dict[str, Any]: ...

    def execute(self, transaction: dict[str, Any], *, selected_model: str) -> dict[str, Any]: ...

    def fetch(self, dispatch: str, *, selected_model: str) -> dict[str, Any]: ...

    def acknowledge(self, dispatch: str, exact_event_stream: str, custody: str) -> None: ...


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
                       provider_transaction_json TEXT,
                       provider_dispatch TEXT,
                       exact_event_stream TEXT,
                       response_digest TEXT,
                       created_at TEXT NOT NULL,
                       updated_at TEXT NOT NULL
                   )"""
            )
            columns = {row[1] for row in connection.execute("PRAGMA table_info(executor_attempt)")}
            for name in (
                "provider_transaction_json",
                "provider_dispatch",
                "exact_event_stream",
                "response_digest",
            ):
                if name not in columns:
                    connection.execute(f"ALTER TABLE executor_attempt ADD COLUMN {name} TEXT")

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
                """INSERT INTO executor_attempt(
                       attempt,marker,dispatch_json,dispatch_digest,state,outcome_json,
                       evidence_ref,provider_transaction_json,provider_dispatch,
                       exact_event_stream,response_digest,created_at,updated_at
                   ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    dispatch.attempt,
                    dispatch.marker,
                    payload,
                    digest,
                    AttemptState.RESERVED,
                    None,
                    None,
                    None,
                    None,
                    None,
                    None,
                    now,
                    now,
                ),
            )
            connection.commit()
        return None

    def bind_provider_transaction(
        self, dispatch: DocketDispatch, transaction: dict[str, Any]
    ) -> dict[str, Any]:
        provider_dispatch = transaction.get("dispatch")
        _require_digest(provider_dispatch, "prepared provider dispatch")
        encoded = _canonical(transaction).decode()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT * FROM executor_attempt WHERE attempt=?", (dispatch.attempt,)
            ).fetchone()
            if row is None or row["marker"] != dispatch.marker:
                connection.rollback()
                raise ExecutorError("provider transaction has no matching reserved attempt")
            if row["provider_transaction_json"] is not None:
                if (
                    row["provider_transaction_json"] != encoded
                    or row["provider_dispatch"] != provider_dispatch
                ):
                    connection.rollback()
                    raise ExecutorError("prepared provider transaction was substituted")
                connection.commit()
                return json.loads(row["provider_transaction_json"])
            if AttemptState(row["state"]) is not AttemptState.RESERVED:
                connection.rollback()
                raise ExecutorError("provider transaction was not bound before execution")
            connection.execute(
                """UPDATE executor_attempt
                   SET provider_transaction_json=?,provider_dispatch=?,updated_at=?
                   WHERE attempt=?""",
                (encoded, provider_dispatch, _now(), dispatch.attempt),
            )
            connection.commit()
        return transaction

    def provider_custody(
        self, dispatch: DocketDispatch
    ) -> tuple[dict[str, Any] | None, str | None, str | None, str | None]:
        with self._connect() as connection:
            row = connection.execute(
                """SELECT marker,provider_transaction_json,provider_dispatch,
                          exact_event_stream,response_digest
                   FROM executor_attempt WHERE attempt=?""",
                (dispatch.attempt,),
            ).fetchone()
        if row is None or row["marker"] != dispatch.marker:
            raise ExecutorError("provider custody has no matching attempt")
        transaction = (
            json.loads(row["provider_transaction_json"])
            if row["provider_transaction_json"] is not None
            else None
        )
        return (
            transaction,
            row["provider_dispatch"],
            row["exact_event_stream"],
            row["response_digest"],
        )

    def stage_evidence(
        self,
        dispatch: DocketDispatch,
        *,
        evidence_ref: str,
        exact_event_stream: str,
        response_digest: str,
    ) -> None:
        for value, label in (
            (exact_event_stream, "exact provider event stream"),
            (response_digest, "response digest"),
        ):
            _require_digest(value, label)
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT * FROM executor_attempt WHERE attempt=?", (dispatch.attempt,)
            ).fetchone()
            if row is None or row["marker"] != dispatch.marker:
                connection.rollback()
                raise ExecutorError("staged evidence has no matching attempt")
            existing = (row["evidence_ref"], row["exact_event_stream"], row["response_digest"])
            proposed = (evidence_ref, exact_event_stream, response_digest)
            if any(existing):
                connection.commit()
                if existing != proposed:
                    raise ExecutorError("staged provider evidence was substituted")
                return
            connection.execute(
                """UPDATE executor_attempt SET evidence_ref=?,exact_event_stream=?,
                          response_digest=?,updated_at=? WHERE attempt=?""",
                (*proposed, _now(), dispatch.attempt),
            )
            connection.commit()

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
        provider: Provider | DurableProvider,
        evidence_writer: EvidenceWriter,
    ) -> None:
        self.plan = plan
        self.provider = provider
        self.evidence_writer = evidence_writer
        self.attempts = ExecutorAttemptStore(plan.attempt_store)
        self.generations = GenerationStore(plan.generation_store)

    def _durable_provider(self) -> DurableProvider | None:
        required = ("prepare", "execute", "fetch", "acknowledge")
        return (
            self.provider
            if all(callable(getattr(self.provider, name, None)) for name in required)
            else None
        )  # type: ignore[return-value]

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
        durable = self.generations.get_dispatch(self.plan.marginalia_dispatch_id)
        assert durable is not None
        request = self.generations.get_request(durable.logical_request_id)
        if request is None:
            raise ExecutorError("Marginalia logical request is missing")
        payload = self.generations.dispatch_payload(durable.id)
        provider = self._durable_provider()
        transaction: dict[str, Any] | None = None
        if provider is not None:
            try:
                transaction = provider.prepare(
                    payload,
                    project_id=request.project_id,
                    session_id=request.session_id,
                    docket_attempt=dispatch.attempt,
                    docket_marker=dispatch.marker,
                    actual_route=durable.actual_route,
                )
                transaction = self.attempts.bind_provider_transaction(dispatch, transaction)
            except Exception as exc:
                # Preparation is local and performs no provider dispatch. A
                # rejected or unavailable preparation is therefore a qualified
                # pre-dispatch failure, not an indeterminate execution.
                reason = str(exc) or type(exc).__name__
                receipt = _digest(
                    "marginalia.executor-predispatch-failure/v1",
                    {"attempt": dispatch.attempt, "reason": reason},
                )
                self.generations.mark_failed(durable.id, reason)
                return self.attempts.finish(
                    dispatch,
                    ExecutorOutcome(dispatch.attempt, dispatch.marker, receipt, "failure"),
                    evidence_ref=receipt,
                )
        self.attempts.begin(dispatch)
        if durable.status is DispatchStatus.RESERVED:
            provider_execution_id = transaction.get("dispatch") if transaction else dispatch.attempt
            self.generations.mark_executing(durable.id, provider_execution_id)
        elif durable.status is not DispatchStatus.EXECUTING:
            raise ExecutorError(f"Marginalia dispatch is not executable: {durable.status}")
        try:
            response = (
                provider.execute(transaction, selected_model=durable.actual_model)
                if provider is not None and transaction is not None
                else self.provider(payload)  # type: ignore[operator]
            )
            return self._retain_success(dispatch, durable, response, provider)
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
            current_dispatch = self.generations.get_dispatch(durable.id)
            if current_dispatch is not None and current_dispatch.status in {
                DispatchStatus.RESERVED,
                DispatchStatus.EXECUTING,
            }:
                self.generations.mark_unknown(durable.id, reason)
            outcome = ExecutorOutcome(dispatch.attempt, dispatch.marker, receipt, "indeterminate")
            if provider is None:
                return self.attempts.finish(dispatch, outcome, evidence_ref=receipt)
            return outcome

    def reconcile(self, dispatch: DocketDispatch) -> ExecutorOutcome:
        self._validate(dispatch)
        provider = self._durable_provider()
        if provider is None:
            return self.attempts.reconcile(dispatch)
        state = self.attempts.state(dispatch.attempt)
        if state in {AttemptState.SUCCESS, AttemptState.FAILURE, AttemptState.INDETERMINATE}:
            return self.attempts.reconcile(dispatch)
        transaction, provider_dispatch, exact_event_stream, response_digest = (
            self.attempts.provider_custody(dispatch)
        )
        if transaction is None or provider_dispatch is None:
            return self.attempts.reconcile(dispatch)
        durable = self.generations.get_dispatch(self.plan.marginalia_dispatch_id)
        assert durable is not None
        candidate = (
            self.generations.get_candidate(durable.candidate_id)
            if durable.candidate_id is not None
            else None
        )
        try:
            if candidate is not None:
                if exact_event_stream is None or response_digest is None:
                    raise ExecutorError("candidate exists without staged provider custody")
                provider.acknowledge(provider_dispatch, exact_event_stream, response_digest)
                return self._finish_success(
                    dispatch, candidate.id, candidate.evidence_ref, response_digest
                )
            response = provider.fetch(provider_dispatch, selected_model=durable.actual_model)
            return self._retain_success(dispatch, durable, response, provider)
        except ProviderDefinitiveFailure as exc:
            reason = str(exc)
            self.generations.mark_failed(durable.id, reason)
            receipt = _digest(
                "marginalia.executor-failure/v1",
                {"attempt": dispatch.attempt, "reason": reason},
            )
            return self.attempts.finish(
                dispatch,
                ExecutorOutcome(dispatch.attempt, dispatch.marker, receipt, "failure"),
                evidence_ref=receipt,
            )
        except Exception as exc:
            reason = str(exc) or type(exc).__name__
            if durable.status in {DispatchStatus.RESERVED, DispatchStatus.EXECUTING}:
                self.generations.mark_unknown(durable.id, reason)
            receipt = _digest(
                "marginalia.executor-indeterminate/v1",
                {"attempt": dispatch.attempt, "reason": reason},
            )
            return ExecutorOutcome(dispatch.attempt, dispatch.marker, receipt, "indeterminate")

    def _retain_success(
        self,
        dispatch: DocketDispatch,
        durable: Any,
        response: dict[str, Any],
        provider: DurableProvider | None,
    ) -> ExecutorOutcome:
        evidence = self.evidence_writer(
            response,
            logical_request_id=durable.logical_request_id,
            dispatch_id=durable.id,
            docket_attempt=dispatch.attempt,
        )
        provider_evidence = response.get("provider_evidence")
        exact_event_stream = (
            provider_evidence.get("exact_event_stream")
            if isinstance(provider_evidence, dict)
            else evidence.response_digest
        )
        self.attempts.stage_evidence(
            dispatch,
            evidence_ref=evidence.reference,
            exact_event_stream=exact_event_stream,
            response_digest=evidence.response_digest,
        )
        candidate = self.generations.record_candidate(
            durable.id,
            response_digest=evidence.response_digest,
            evidence_ref=evidence.reference,
        )
        if provider is not None:
            provider_dispatch = (
                provider_evidence.get("dispatch") if isinstance(provider_evidence, dict) else None
            )
            _require_digest(provider_dispatch, "retained provider dispatch")
            provider.acknowledge(provider_dispatch, exact_event_stream, evidence.response_digest)
        return self._finish_success(
            dispatch, candidate.id, evidence.reference, evidence.response_digest
        )

    def _finish_success(
        self, dispatch: DocketDispatch, candidate_id: str, evidence_ref: str, response_digest: str
    ) -> ExecutorOutcome:
        receipt = _digest(
            "marginalia.executor-success/v1",
            {
                "attempt": dispatch.attempt,
                "candidate": candidate_id,
                "evidence": evidence_ref,
                "response": response_digest,
            },
        )
        return self.attempts.finish(
            dispatch,
            ExecutorOutcome(dispatch.attempt, dispatch.marker, receipt, "success"),
            evidence_ref=evidence_ref,
        )


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
    return (
        "sha256:" + hashlib.sha256(domain.encode("ascii") + b"\0" + _canonical(value)).hexdigest()
    )


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()

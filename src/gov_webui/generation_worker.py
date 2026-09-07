# SPDX-License-Identifier: Apache-2.0
"""Durable worker that composes ag-ng, Docket, and Marginalia's executor."""

from __future__ import annotations

import base64
import json
import os
import secrets
import subprocess
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from gov_webui.generation_boundaries import (
    OBSERVATION_BASIS_TYPE,
    OBSERVATION_RESOLVER_ID,
    STANDING_RESOLVER_ID,
    ag_digest,
    canonical,
    generation_scope,
    generation_subject,
)
from gov_webui.generation_executor import EXECUTOR_PLAN_SCHEMA, EXECUTOR_WORK_SCHEMA, ExecutorPlan
from gov_webui.generation_store import (
    Dispatch,
    DispatchStatus,
    GenerationStore,
    LogicalRequest,
    LogicalStatus,
)


class GenerationWorkerError(RuntimeError):
    """A durable worker transition could not be completed or classified."""


_RING_ED25519_PKCS8_PREFIX = bytes.fromhex("3051020101300506032b657004220420")
_RING_ED25519_PUBLIC_PREFIX = bytes.fromhex("812100")


def ring_ed25519_public_key(document: bytes) -> bytes:
    """Validate ring's fixed Ed25519 PKCS#8 v2 envelope and return its public key."""
    private_start = len(_RING_ED25519_PKCS8_PREFIX)
    private_end = private_start + 32
    public_start = private_end + len(_RING_ED25519_PUBLIC_PREFIX)
    if (
        len(document) != public_start + 32
        or not document.startswith(_RING_ED25519_PKCS8_PREFIX)
        or document[private_end:public_start] != _RING_ED25519_PUBLIC_PREFIX
    ):
        raise GenerationWorkerError("AG issuer key is not ring Ed25519 PKCS#8 v2")
    derived = (
        Ed25519PrivateKey.from_private_bytes(document[private_start:private_end])
        .public_key()
        .public_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PublicFormat.Raw,
        )
    )
    encoded = document[public_start:]
    if not secrets.compare_digest(derived, encoded):
        raise GenerationWorkerError("AG issuer key public component does not match its seed")
    return encoded


@dataclass(frozen=True)
class WorkerConfig:
    contexts_root: Path
    ag_loopctl: Path
    docket: Path
    observation_resolver: Path
    standing_resolver: Path
    docket_standing_resolver: Path
    executor: Path
    issuer_key: Path
    evidence_keyring: Path
    retention_days: int = 30
    poll_seconds: float = 2.0

    @classmethod
    def from_environment(cls) -> WorkerConfig:
        def absolute(name: str, default: str) -> Path:
            result = Path(os.environ.get(name, default))
            if not result.is_absolute():
                raise GenerationWorkerError(f"{name} must be absolute")
            return result

        retention = int(os.environ.get("MARGINALIA_EVIDENCE_RETENTION_DAYS", "30"))
        poll = float(os.environ.get("MARGINALIA_GENERATION_POLL_SECONDS", "2"))
        if not 1 <= retention <= 3650 or not 0.1 <= poll <= 300:
            raise GenerationWorkerError("invalid retention or worker poll interval")
        return cls(
            contexts_root=absolute("GOVERNOR_CONTEXTS_DIR", "/data/.governor"),
            ag_loopctl=absolute("MARGINALIA_AG_LOOPCTL", "/usr/local/bin/ag-loopctl"),
            docket=absolute("MARGINALIA_DOCKET", "/usr/local/bin/docket"),
            observation_resolver=absolute(
                "MARGINALIA_OBSERVATION_RESOLVER", "/usr/local/bin/marginalia-observation-resolver"
            ),
            standing_resolver=absolute(
                "MARGINALIA_STANDING_RESOLVER", "/usr/local/bin/marginalia-standing-resolver"
            ),
            docket_standing_resolver=absolute(
                "MARGINALIA_DOCKET_STANDING_RESOLVER",
                "/usr/local/bin/marginalia-docket-standing-resolver",
            ),
            executor=absolute(
                "MARGINALIA_GENERATION_EXECUTOR",
                "/usr/local/bin/marginalia-generation-executor",
            ),
            issuer_key=absolute(
                "MARGINALIA_AG_ISSUER_KEY_FILE", "/run/secrets/marginalia-ag-issuer.pk8"
            ),
            evidence_keyring=absolute(
                "MARGINALIA_EVIDENCE_KEY_FILE", "/run/secrets/marginalia-evidence-keys.json"
            ),
            retention_days=retention,
            poll_seconds=poll,
        )


class GovernedGeneration:
    """One logical request's persisted ag-ng/Docket transition driver."""

    def __init__(
        self,
        config: WorkerConfig,
        store: GenerationStore,
        request: LogicalRequest,
        dispatch: Dispatch,
    ) -> None:
        self.config = config
        self.store = store
        self.request = request
        self.context_root = store.path.parent.parent
        self.dispatch = dispatch
        self.root = (
            store.path.parent / "governed-generations" / request.id / "dispatches" / dispatch.id
        )
        self.config_dir = self.root / "config"
        self.state_dir = self.root / "state"
        self.logs_dir = self.root / "commands"
        self.database = self.state_dir / "campaign.sqlite"
        self.runtime_profile = self.config_dir / "runtime-profile.json"
        self.executor_plan_path = self.config_dir / "executor-plan.json"
        self.dispatch_started = self.state_dir / "dispatch-command-started"
        self._sequence = self._last_command_sequence()

    def prepare(self) -> None:
        for directory in (self.root, self.config_dir, self.state_dir, self.logs_dir):
            directory.mkdir(parents=True, exist_ok=True, mode=0o700)
            os.chmod(directory, 0o700)
        docket_state = self.context_root / "marginalia" / "docket-state"
        docket_state.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(docket_state, 0o700)
        self._require_deployment_files()
        subject = generation_subject(self.request)
        scope = generation_scope(self.request)
        plan = ExecutorPlan(
            attempt_store=self.context_root / "marginalia" / "executor-attempts.sqlite",
            generation_store=self.store.path,
            evidence_root=self.context_root / "marginalia" / "generation-evidence",
            evidence_keyring=self.config.evidence_keyring,
            evidence_retention_days=self.config.retention_days,
            marginalia_dispatch_id=self.dispatch.id,
            request_digest=self.dispatch.request_digest,
            subject=subject,
            scope=scope,
        )
        self._write_exact(self.executor_plan_path, plan.canonical_value())
        basis = {
            "schema": "ag.governed-loop.typed-observation-basis/v1",
            "basis_type": OBSERVATION_BASIS_TYPE,
            "basis_identity": self.request.request_digest,
        }
        catalog_path = self.config_dir / "catalog.json"
        self._write_exact(
            catalog_path,
            {
                "schema": "ag.governed-loop.exact-work-catalog/v2",
                "entries": {
                    EXECUTOR_WORK_SCHEMA: {
                        "work_schema": EXECUTOR_WORK_SCHEMA,
                        "subject": subject,
                        "scope": scope,
                        "observation_basis": {"kind": "typed_basis", "requirement": basis},
                    }
                },
            },
        )
        trust_path = self.config_dir / "docket-trust.json"
        self._write_exact(trust_path, self._trust_config())
        enrollment_path = self.config_dir / "runtime-profile-enrollment.json"
        self._write_exact(
            enrollment_path,
            {
                "schema": "ag.governed-loop.runtime-profile-enrollment/v1",
                "profile_label": f"marginalia-generation-{self.request.id}",
                "observation_resolver": str(self.config.observation_resolver),
                "observation_resolver_id": OBSERVATION_RESOLVER_ID,
                "standing_resolver": str(self.config.standing_resolver),
                "standing_resolver_id": STANDING_RESOLVER_ID,
                "max_standing_ttl_ms": 30_000,
                "exact_work_catalog": str(catalog_path),
                "controlling_review": None,
                "docket": {
                    "schema": "ag.governed-loop.docket-root-enrollment/v1",
                    "docket_program": str(self.config.docket),
                    "state_directory": str(docket_state),
                    "trust_config": str(trust_path),
                    "standing_resolver": str(self.config.docket_standing_resolver),
                    "executor_adapter": str(self.config.executor),
                    "issuer_principal": "marginalia-generation-worker",
                    "issuer_key_id": "marginalia-ag-issuer-v1",
                    "issuer_key": str(self.config.issuer_key),
                },
                "human_verifier": None,
            },
        )
        campaign = ag_digest("marginalia.generation-campaign/v1", self.request.id)
        genesis_path = self.config_dir / "genesis.json"
        self._write_exact(
            genesis_path,
            {
                "campaign": campaign,
                "occurrence": str(uuid.uuid5(uuid.NAMESPACE_URL, self.request.request_digest)),
                "program": ag_digest("marginalia.generation-program/v1", "one-provider-response"),
                "expected_ag_work": plan.identity,
                "residuals": [],
                "budget": {
                    "retry_limit": len(self.request.fallback_policy),
                    "retries_used": 0,
                    "probe_limit": 3,
                    "probes_used": 0,
                    "escalation_limit": 1,
                    "escalations_used": 0,
                },
            },
        )
        proposal_path = self.config_dir / "proposal.json"
        self._write_exact(
            proposal_path,
            {
                "observation": self.request.request_digest,
                "proposal": {
                    "schema": "ag.governed-loop.exact-work-proposal/v1",
                    "campaign": campaign,
                    "subject": subject,
                    "scope": scope,
                    "work_schema": EXECUTOR_WORK_SCHEMA,
                    "work": plan.identity,
                    "repair": None,
                },
                "class": "initial",
            },
        )
        if not self.runtime_profile.exists():
            self._run(
                "seal-runtime-profile",
                "--enrollment",
                str(enrollment_path),
                "--output",
                str(self.runtime_profile),
            )
        else:
            self._run("verify-runtime-profile", "--runtime-profile", str(self.runtime_profile))
        if not self.database.exists():
            self._run(
                "init",
                "--database",
                str(self.database),
                "--genesis",
                str(genesis_path),
                "--runtime-profile",
                str(self.runtime_profile),
            )

    def drive(self) -> str:
        """Advance existing durable state; never infer repeat safety from restart."""
        while True:
            inspection = self._run("inspect", "--database", str(self.database))
            counter = self._program_counter(inspection)
            if counter == "observation_required":
                self._run(
                    "record-proposal",
                    "--database",
                    str(self.database),
                    "--input",
                    str(self.config_dir / "proposal.json"),
                    "--observation-resolver",
                    str(self.config.observation_resolver),
                    "--expected-observation-resolver-id",
                    OBSERVATION_RESOLVER_ID,
                )
            elif counter == "proposal_recorded":
                self._run("require-standing", "--database", str(self.database))
            elif counter in {"standing_required", "admissible_pending_authorization"}:
                operation = "decide" if counter == "standing_required" else "authorize"
                self._run(operation, "--database", str(self.database), *self._gate_arguments())
            elif counter == "authorization_consumed":
                operation = "recover" if self.dispatch_started.exists() else "dispatch"
                if operation == "dispatch":
                    self._mark_dispatch_started()
                self._run(operation, "--database", str(self.database), *self._docket_arguments())
            elif counter == "dispatched":
                self._run("poll", "--database", str(self.database), *self._docket_arguments())
            elif counter == "reconciliation_required":
                self._run("recover", "--database", str(self.database), *self._docket_arguments())
                return counter
            elif counter in {"settled_observation_required", "halted", "completed"}:
                return counter
            else:
                raise GenerationWorkerError(f"unsupported ag-ng program counter: {counter}")

    def _gate_arguments(self) -> tuple[str, ...]:
        return (
            "--catalog",
            str(self.config_dir / "catalog.json"),
            "--observation-resolver",
            str(self.config.observation_resolver),
            "--expected-observation-resolver-id",
            OBSERVATION_RESOLVER_ID,
            "--standing-resolver",
            str(self.config.standing_resolver),
            "--expected-standing-resolver-id",
            STANDING_RESOLVER_ID,
            "--max-standing-ttl-ms",
            "30000",
        )

    def _docket_arguments(self) -> tuple[str, ...]:
        return (
            "--docket",
            str(self.config.docket),
            "--docket-state",
            str(self.context_root / "marginalia" / "docket-state"),
            "--docket-trust",
            str(self.config_dir / "docket-trust.json"),
            "--docket-standing-resolver",
            str(self.config.docket_standing_resolver),
            "--executor",
            str(self.config.executor),
            "--executor-config",
            str(self.executor_plan_path),
            "--issuer-principal",
            "marginalia-generation-worker",
            "--issuer-key-id",
            "marginalia-ag-issuer-v1",
            "--issuer-key",
            str(self.config.issuer_key),
        )

    def _run(self, *arguments: str) -> dict[str, Any]:
        self._sequence += 1
        command = [str(self.config.ag_loopctl), *arguments]
        completed = subprocess.run(command, capture_output=True, check=False)
        prefix = self.logs_dir / f"{self._sequence:04d}-{arguments[0]}"
        self._write_bytes(prefix.with_suffix(".stdout"), completed.stdout)
        self._write_bytes(prefix.with_suffix(".stderr"), completed.stderr)
        self._write_exact(
            prefix.with_suffix(".command.json"),
            {"argv": command, "returncode": completed.returncode},
        )
        if completed.returncode != 0:
            raise GenerationWorkerError(
                f"ag-loopctl {arguments[0]} refused; see {prefix.with_suffix('.stderr')}"
            )
        try:
            return json.loads(completed.stdout)
        except json.JSONDecodeError as exc:
            raise GenerationWorkerError(f"ag-loopctl {arguments[0]} returned invalid JSON") from exc

    def _last_command_sequence(self) -> int:
        sequences = []
        for path in self.logs_dir.glob("*-*.command.json"):
            prefix, separator, _ = path.name.partition("-")
            if separator and prefix.isdecimal():
                sequences.append(int(prefix))
        return max(sequences, default=0)

    @staticmethod
    def _program_counter(inspection: dict[str, Any]) -> str:
        state = inspection.get("current", {}).get("state")
        if not isinstance(state, dict) or len(state) != 1:
            raise GenerationWorkerError("ag-loopctl inspection has no exact program counter")
        return next(iter(state))

    def _trust_config(self) -> dict[str, Any]:
        try:
            public = ring_ed25519_public_key(self.config.issuer_key.read_bytes())
        except Exception as exc:
            raise GenerationWorkerError(f"cannot load AG issuer key: {exc}") from exc
        return {
            "issuers": [
                {
                    "issuer_principal": "marginalia-generation-worker",
                    "key_id": "marginalia-ag-issuer-v1",
                    "public_key": base64.urlsafe_b64encode(public).rstrip(b"=").decode("ascii"),
                }
            ]
        }

    def _require_deployment_files(self) -> None:
        for path in (
            self.config.ag_loopctl,
            self.config.docket,
            self.config.observation_resolver,
            self.config.standing_resolver,
            self.config.docket_standing_resolver,
            self.config.executor,
            self.config.issuer_key,
            self.config.evidence_keyring,
        ):
            if not path.is_file():
                raise GenerationWorkerError(f"required deployment file is missing: {path}")

    def _mark_dispatch_started(self) -> None:
        if self.dispatch_started.exists():
            return
        self._write_bytes(self.dispatch_started, b"dispatch command entered\n")

    @staticmethod
    def _write_exact(path: Path, value: Any) -> None:
        GovernedGeneration._write_bytes(path, canonical(value) + b"\n")

    @staticmethod
    def _write_bytes(path: Path, content: bytes) -> None:
        if path.exists():
            if path.read_bytes() != content:
                raise GenerationWorkerError(f"immutable checkpoint differs: {path}")
            return
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC, 0o600)
        try:
            os.write(descriptor, content)
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)


def process_one(config: WorkerConfig, store: GenerationStore, request: LogicalRequest) -> str:
    dispatches = store.list_dispatches(request.id)
    if not dispatches:
        dispatch = store.reserve_dispatch(request.id)
    else:
        dispatch = dispatches[-1]
        if any(item.status is not DispatchStatus.FAILED for item in dispatches[:-1]):
            raise GenerationWorkerError("fallback history contains a non-failed prior dispatch")
    governed = GovernedGeneration(config, store, request, dispatch)
    governed.prepare()
    try:
        return governed.drive()
    except Exception as exc:
        durable = store.get_dispatch(dispatch.id)
        if durable is not None and durable.status in {
            DispatchStatus.RESERVED,
            DispatchStatus.EXECUTING,
        }:
            store.mark_unknown(dispatch.id, str(exc))
        raise


def run_once(config: WorkerConfig) -> list[dict[str, str]]:
    results = []
    for path in sorted(config.contexts_root.glob("*/marginalia/generation.sqlite")):
        store = GenerationStore(path)
        for request in store.list_requests(
            (
                LogicalStatus.QUEUED,
                LogicalStatus.DISPATCHING,
                LogicalStatus.UNKNOWN,
                LogicalStatus.FAILED,
            )
        ):
            if request.status is LogicalStatus.FAILED:
                used = len(store.list_dispatches(request.id))
                available = 1 + len(request.fallback_policy)
                if used >= available or not store.dispatch_enabled(request.project_id):
                    continue
                try:
                    store.reserve_fallback(request.id)
                except Exception as exc:
                    results.append(
                        {"request_id": request.id, "state": "fallback_error", "error": str(exc)}
                    )
                    continue
                refreshed = store.get_request(request.id)
                assert refreshed is not None
                request = refreshed
            try:
                state = process_one(config, store, request)
                results.append({"request_id": request.id, "state": state})
            except Exception as exc:
                results.append({"request_id": request.id, "state": "error", "error": str(exc)})
    return results


def main() -> int:
    config = WorkerConfig.from_environment()
    print(json.dumps({"event": "generation_worker_started"}), flush=True)
    while True:
        for result in run_once(config):
            print(json.dumps({"event": "generation_worker_result", **result}), flush=True)
        time.sleep(config.poll_seconds)

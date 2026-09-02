# SPDX-License-Identifier: Apache-2.0
"""Cheap governor progress accounting for readiness and operations."""

from __future__ import annotations

import os
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _wedge_seconds() -> float:
    raw = os.environ.get("MARGINALIA_GOVERNOR_WEDGE_SECONDS", "255").strip()
    try:
        value = float(raw)
    except ValueError as exc:
        raise RuntimeError(
            "MARGINALIA_GOVERNOR_WEDGE_SECONDS must be a number between 1 and 3600"
        ) from exc
    if not 1 <= value <= 3600:
        raise RuntimeError("MARGINALIA_GOVERNOR_WEDGE_SECONDS must be between 1 and 3600")
    return value


@dataclass(frozen=True)
class InvocationToken:
    id: str


class GovernorProgress:
    """Tracks capacity ownership; it never performs provider work itself."""

    def __init__(self, *, wedge_seconds: float | None = None) -> None:
        self.wedge_seconds = wedge_seconds or _wedge_seconds()
        self._active: dict[str, tuple[float, str]] = {}
        self._last_success_at: str | None = None
        self._last_failure_at: str | None = None
        self._last_failure_class: str | None = None
        self._capacity_degraded = False

    def begin(self, backend: str) -> InvocationToken:
        token = InvocationToken(uuid.uuid4().hex)
        self._active[token.id] = (time.monotonic(), backend)
        return token

    def succeeded(self, token: InvocationToken) -> None:
        self._active.pop(token.id, None)
        self._last_success_at = _utc_now()
        self._capacity_degraded = False

    def failed(
        self,
        token: InvocationToken,
        failure_class: str,
        *,
        capacity_uncertain: bool = False,
    ) -> None:
        self._active.pop(token.id, None)
        self._last_failure_at = _utc_now()
        self._last_failure_class = failure_class
        if capacity_uncertain:
            self._capacity_degraded = True

    def snapshot(self) -> dict[str, object]:
        now = time.monotonic()
        active = [
            {"backend": backend, "age_seconds": round(now - started, 3)}
            for started, backend in self._active.values()
        ]
        oldest = max((item["age_seconds"] for item in active), default=0.0)
        wedged = bool(active) and oldest >= self.wedge_seconds
        ready = not wedged and not self._capacity_degraded
        return {
            "ready": ready,
            "in_flight": len(active),
            "oldest_in_flight_seconds": oldest,
            "wedge_threshold_seconds": self.wedge_seconds,
            "wedged": wedged,
            "capacity_degraded": self._capacity_degraded,
            "last_success_at": self._last_success_at,
            "last_failure_at": self._last_failure_at,
            "last_failure_class": self._last_failure_class,
            "active_backends": sorted({str(item["backend"]) for item in active}),
        }


governor_progress = GovernorProgress()

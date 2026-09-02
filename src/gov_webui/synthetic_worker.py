# SPDX-License-Identifier: Apache-2.0
"""Recurring, isolated production probe for governed reply liveness."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx


@dataclass(frozen=True)
class ProbeSpec:
    model: str
    interval_seconds: int


def _specs(raw: str) -> list[ProbeSpec]:
    result: list[ProbeSpec] = []
    for item in raw.split(","):
        item = item.strip()
        if not item:
            continue
        try:
            model, interval_raw = item.rsplit("@", 1)
            interval = int(interval_raw)
        except ValueError as exc:
            raise RuntimeError(
                "MARGINALIA_SYNTHETIC_MODELS must use model@interval_seconds entries"
            ) from exc
        if not model or not 300 <= interval <= 604800:
            raise RuntimeError("synthetic intervals must be between 300 and 604800 seconds")
        result.append(ProbeSpec(model=model, interval_seconds=interval))
    if not result:
        raise RuntimeError("at least one synthetic model must be configured")
    return result


def _timestamp() -> str:
    return datetime.now(timezone.utc).isoformat()


def _failure_class(exc: BaseException) -> str:
    if isinstance(exc, httpx.ConnectTimeout):
        return "connect_timeout"
    if isinstance(exc, httpx.ReadTimeout):
        return "read_timeout"
    if isinstance(exc, TimeoutError):
        return "deadline_exceeded"
    if isinstance(exc, httpx.HTTPStatusError):
        return f"http_{exc.response.status_code}"
    if isinstance(exc, httpx.HTTPError):
        return "transport_error"
    return type(exc).__name__


async def probe_once(
    *,
    base_url: str,
    model: str,
    timeout_seconds: float,
    auth_token: str = "",
    marker: str = "scheduled",
) -> dict[str, Any]:
    started = time.monotonic()
    headers = {"Authorization": f"Bearer {auth_token}"} if auth_token else {}
    record: dict[str, Any] = {
        "event": "governor_synthetic",
        "timestamp": _timestamp(),
        "model": model,
        "backend": model,
    }
    try:
        transport_timeout = httpx.Timeout(
            min(30.0, timeout_seconds), connect=min(10.0, timeout_seconds)
        )
        async with asyncio.timeout(timeout_seconds):
            async with httpx.AsyncClient(timeout=transport_timeout) as client:
                response = await client.post(
                    f"{base_url.rstrip('/')}/v1/internal/synthetic-governor",
                    headers=headers,
                    json={"model": "" if model == "default" else model, "marker": marker},
                )
                response.raise_for_status()
                body = response.json()
        if body.get("status") != "PASS" or not body.get("receipt_id"):
            raise RuntimeError("synthetic endpoint returned an incomplete result")
        record.update(
            {
                "result": "PASS",
                "backend": body.get("backend", model),
                "receipt_id": body["receipt_id"],
                "context_id": body.get("context_id"),
            }
        )
    except Exception as exc:
        record.update(
            {
                "result": "FAIL",
                "failure_class": _failure_class(exc),
                "error": str(exc)[:500],
            }
        )
    record["latency_ms"] = round((time.monotonic() - started) * 1000, 1)
    return record


def _append_record(path: Path, record: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(record, sort_keys=True, separators=(",", ":"))
    with path.open("a", encoding="utf-8") as handle:
        handle.write(line + "\n")
        handle.flush()
        os.fsync(handle.fileno())
    print(line, flush=True)


def _last_attempts(path: Path) -> dict[str, float]:
    result: dict[str, float] = {}
    if not path.exists():
        return result
    for line in path.read_text(encoding="utf-8").splitlines()[-1000:]:
        try:
            record = json.loads(line)
            when = datetime.fromisoformat(record["timestamp"]).timestamp()
            result[str(record["model"])] = max(result.get(str(record["model"]), 0.0), when)
        except (ValueError, KeyError, TypeError, json.JSONDecodeError):
            continue
    return result


async def _run_models(models: list[str], *, marker: str, record_path: Path) -> list[dict[str, Any]]:
    base_url = os.environ.get("MARGINALIA_SYNTHETIC_BASE_URL", "http://marginalia:8000")
    auth_token = os.environ.get("GOVERNOR_AUTH_TOKEN", "")
    timeout = float(os.environ.get("MARGINALIA_SYNTHETIC_TIMEOUT_SECONDS", "280"))
    if not 1 <= timeout <= 3600:
        raise RuntimeError("MARGINALIA_SYNTHETIC_TIMEOUT_SECONDS must be 1..3600")
    results = []
    for model in models:
        record = await probe_once(
            base_url=base_url,
            model=model,
            timeout_seconds=timeout,
            auth_token=auth_token,
            marker=marker,
        )
        _append_record(record_path, record)
        results.append(record)
    return results


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--model", action="append", default=[])
    parser.add_argument("--marker", default="scheduled")
    args = parser.parse_args()
    record_path = Path(
        os.environ.get(
            "MARGINALIA_SYNTHETIC_RECORD_PATH",
            "/backups/marginalia-synthetics.jsonl",
        )
    )
    specs = _specs(os.environ.get("MARGINALIA_SYNTHETIC_MODELS", "default@3600"))
    if args.once:
        models = args.model or [spec.model for spec in specs]
        results = asyncio.run(_run_models(models, marker=args.marker, record_path=record_path))
        return 0 if all(result["result"] == "PASS" for result in results) else 1

    poll_seconds = max(30, int(os.environ.get("MARGINALIA_SYNTHETIC_POLL_SECONDS", "60")))
    print(
        json.dumps(
            {
                "event": "governor_synthetic_worker_started",
                "models": [spec.model for spec in specs],
                "poll_seconds": poll_seconds,
                "record_path": str(record_path),
            },
            sort_keys=True,
        ),
        flush=True,
    )
    while True:
        now = time.time()
        last = _last_attempts(record_path)
        due = [
            spec.model for spec in specs if now - last.get(spec.model, 0.0) >= spec.interval_seconds
        ]
        if due:
            asyncio.run(_run_models(due, marker="scheduled", record_path=record_path))
        time.sleep(poll_seconds)


if __name__ == "__main__":
    raise SystemExit(main())

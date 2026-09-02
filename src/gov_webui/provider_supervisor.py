# SPDX-License-Identifier: Apache-2.0
"""Hard execution envelope for every governor-owned provider invocation."""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import time
import ctypes
from collections.abc import Sequence
from pathlib import Path


def _enable_child_subreaper() -> None:
    """Adopt provider descendants so daemonized children remain cleanable."""
    libc = ctypes.CDLL(None, use_errno=True)
    if libc.prctl(36, 1, 0, 0, 0) != 0:  # PR_SET_CHILD_SUBREAPER
        error = ctypes.get_errno()
        raise OSError(error, os.strerror(error))


def _deadline_seconds() -> float:
    raw = os.environ.get("MARGINALIA_GOVERNOR_INVOCATION_TIMEOUT_SECONDS", "1810").strip()
    try:
        value = float(raw)
    except ValueError as exc:
        raise RuntimeError(
            "MARGINALIA_GOVERNOR_INVOCATION_TIMEOUT_SECONDS must be a number between 0.1 and 3600"
        ) from exc
    if not 0.1 <= value <= 3600:
        raise RuntimeError(
            "MARGINALIA_GOVERNOR_INVOCATION_TIMEOUT_SECONDS must be between 0.1 and 3600"
        )
    return value


def _process_table() -> dict[int, int]:
    result: dict[int, int] = {}
    for entry in Path("/proc").iterdir():
        if not entry.name.isdigit():
            continue
        try:
            # The comm field may contain spaces and parentheses; fields after
            # the final ')' begin with state and parent PID.
            remainder = (entry / "stat").read_text(encoding="utf-8").rsplit(")", 1)[1]
            result[int(entry.name)] = int(remainder.split()[1])
        except (FileNotFoundError, PermissionError, ValueError, IndexError):
            continue
    return result


def _descendants(root_pid: int) -> list[int]:
    parents = _process_table()
    found: list[int] = []
    frontier = [root_pid]
    while frontier:
        parent = frontier.pop()
        children = [pid for pid, ppid in parents.items() if ppid == parent]
        found.extend(children)
        frontier.extend(children)
    return found


def _signal_tree(process: subprocess.Popen[bytes], signum: int) -> None:
    descendants = _descendants(process.pid)
    for pid in reversed(descendants):
        try:
            os.kill(pid, signum)
        except ProcessLookupError:
            pass
    try:
        os.killpg(process.pid, signum)
    except ProcessLookupError:
        pass


def _stop_tree(process: subprocess.Popen[bytes]) -> None:
    _signal_tree(process, signal.SIGTERM)
    try:
        process.wait(
            timeout=float(os.environ.get("MARGINALIA_PROVIDER_CLEANUP_GRACE_SECONDS", "5"))
        )
    except subprocess.TimeoutExpired:
        _signal_tree(process, signal.SIGKILL)
        process.wait()


def _reap_available_children() -> None:
    while True:
        try:
            pid, _status = os.waitpid(-1, os.WNOHANG)
        except ChildProcessError:
            return
        if pid == 0:
            return


def _cleanup_adopted_descendants() -> None:
    """Terminate and reap any invocation-owned descendants left after exit."""
    descendants = _descendants(os.getpid())
    for pid in reversed(descendants):
        try:
            os.kill(pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
    deadline = time.monotonic() + float(
        os.environ.get("MARGINALIA_PROVIDER_CLEANUP_GRACE_SECONDS", "5")
    )
    while descendants and time.monotonic() < deadline:
        _reap_available_children()
        descendants = [pid for pid in descendants if Path(f"/proc/{pid}").exists()]
        if descendants:
            time.sleep(0.01)
    for pid in descendants:
        try:
            os.kill(pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    while descendants:
        _reap_available_children()
        descendants = [pid for pid in descendants if Path(f"/proc/{pid}").exists()]
        if descendants:
            time.sleep(0.01)


def run(argv: Sequence[str], prompt: bytes) -> int:
    deadline = _deadline_seconds()
    _enable_child_subreaper()
    child_env = dict(os.environ)
    child_env["MARGINALIA_PROVIDER_SUPERVISED"] = "1"
    process = subprocess.Popen(
        [sys.executable, "-m", "gov_webui.provider_cli", *argv],
        env=child_env,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        start_new_session=True,
    )

    def forward_signal(signum: int, _frame: object) -> None:
        _signal_tree(process, signum)

    previous_handlers = {
        signum: signal.signal(signum, forward_signal) for signum in (signal.SIGINT, signal.SIGTERM)
    }
    try:
        try:
            stdout, stderr = process.communicate(input=prompt, timeout=deadline)
        except subprocess.TimeoutExpired as exc:
            _stop_tree(process)
            _cleanup_adopted_descendants()
            raise RuntimeError(
                f"Governor provider invocation timed out after {deadline:g} seconds"
            ) from exc
    finally:
        for signum, handler in previous_handlers.items():
            signal.signal(signum, handler)

    _cleanup_adopted_descendants()

    sys.stdout.buffer.write(stdout)
    sys.stdout.buffer.flush()
    sys.stderr.buffer.write(stderr)
    sys.stderr.buffer.flush()
    return process.returncode if process.returncode >= 0 else 128 - process.returncode


def main(argv: Sequence[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    try:
        return run(args, sys.stdin.buffer.read())
    except (OSError, RuntimeError) as exc:
        print(str(exc), file=sys.stderr, flush=True)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

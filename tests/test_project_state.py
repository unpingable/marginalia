# SPDX-License-Identifier: Apache-2.0
"""Cross-process project mutation lock tests."""

from __future__ import annotations

import multiprocessing
import queue
from pathlib import Path

from gov_webui.project_state import project_state_lock


def _acquire(context_root: str, events) -> None:
    with project_state_lock(Path(context_root)):
        events.put("acquired")


def test_project_lock_excludes_another_process(tmp_path: Path) -> None:
    context = multiprocessing.get_context("spawn")
    events = context.Queue()
    with project_state_lock(tmp_path):
        child = context.Process(target=_acquire, args=(str(tmp_path), events))
        child.start()
        try:
            events.get(timeout=0.25)
        except queue.Empty:
            pass
        else:
            raise AssertionError("second process entered the project mutation lock")
    assert events.get(timeout=5) == "acquired"
    child.join(timeout=5)
    assert child.exitcode == 0

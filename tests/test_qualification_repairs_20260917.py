# SPDX-License-Identifier: Apache-2.0
"""Regressions for the 2026-09-17 synthetic-user qualification failures.

Each test names the failure cluster it pins and is written to fail against the
pre-repair behaviour on d52fd5b6.  See ``scratchpad/qual/`` for the originating
evidence and ``PREREG.md`` for the pre-registered expectations.
"""

from __future__ import annotations

import re
from pathlib import Path

import httpx
import pytest

from gov_webui.artifact_store import (
    ArtifactStore,
    DivergentWorkingCopyError,
)
from gov_webui.context_store import GovernorContextManager
from gov_webui.generation_outcome import GenerationFailureKind
from gov_webui.generation_store import (
    DEFAULT_DISPATCH_ENABLED,
    GenerationStore,
)

UI_SOURCE = Path(__file__).resolve().parents[1] / "src" / "gov_webui" / "static" / "index.html"


def _reset(adapter) -> None:
    adapter._context_manager = None
    adapter._session_store = None
    adapter._creative_project_store = None
    adapter._artifact_store = None
    adapter._library_store = None
    adapter._session_stores.clear()
    adapter._governed_chat_adapters.clear()
    adapter._creative_project_stores.clear()
    adapter._artifact_stores.clear()
    adapter._canon_review_stores.clear()
    adapter._manuscript_stores.clear()
    adapter._snapshot_stores.clear()
    adapter._context_summary_stores.clear()
    adapter._context_maintenance_adapters.clear()
    adapter._context_maintenance_tasks.clear()
    adapter._pending_captures.clear()
    adapter._generation_stores.clear()


@pytest.fixture
async def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    import gov_webui.adapter as adapter

    _reset(adapter)
    contexts = tmp_path / "contexts"
    monkeypatch.setattr(adapter, "MARGINALIA_DATA_ROOT", str(tmp_path / "data"))
    monkeypatch.setattr(adapter, "MARGINALIA_CONTEXTS_DIR", str(contexts))
    monkeypatch.setattr(adapter, "GOVERNOR_CONTEXTS_DIR", str(contexts))
    monkeypatch.setattr(adapter, "GOVERNOR_CONTEXT_ID", "erin-writing")
    monkeypatch.setattr(adapter, "GOVERNOR_MODE", "fiction")
    monkeypatch.setattr(adapter, "GOVERNOR_AUTH_TOKEN", "")
    adapter._context_manager = GovernorContextManager(base_dir=contexts)

    transport = httpx.ASGITransport(app=adapter.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as http:
        yield http, adapter
    _reset(adapter)


@pytest.fixture
def store(tmp_path: Path) -> ArtifactStore:
    return ArtifactStore(tmp_path)


# ======================================================================
# F1 -- reporting and enforcement of the default dispatch policy agree
# ======================================================================


def test_default_dispatch_policy_has_one_source_of_truth(tmp_path: Path) -> None:
    """F1/S03a: a project with no settings row must report what it enforces.

    Pre-repair, `settings()` defaulted enabled while the reservation path's
    private reader defaulted disabled, so a brand new project advertised
    "Generation enabled" and refused every dispatch forever.
    """
    generations = GenerationStore(tmp_path / "generation.sqlite")

    reported = generations.settings("never-configured").dispatch_enabled
    public = generations.dispatch_enabled("never-configured")
    # The reader the dispatch-reservation transaction actually consults. Before
    # the repair this was a separate private method defaulting to False while
    # both public readers defaulted to True, which is why the contradiction was
    # invisible to a test that only compared the two public readers.
    with generations._connect() as connection:
        enforced = generations._read_dispatch_enabled(connection, "never-configured")

    assert reported is DEFAULT_DISPATCH_ENABLED
    assert public is DEFAULT_DISPATCH_ENABLED
    assert enforced is DEFAULT_DISPATCH_ENABLED
    assert reported == public == enforced


def test_unseeded_project_can_actually_reserve_a_dispatch(tmp_path: Path) -> None:
    """F1/S03a: enforcement, not just reporting, honours the default.

    Deliberately never calls `set_dispatch_enabled` and never writes a
    generation-settings row, which is exactly the state of every project
    created after the generation-policy migration.
    """
    generations = GenerationStore(tmp_path / "generation.sqlite")
    created = generations.create_request(
        client_request_id="cr-unseeded",
        project_id="brand-new-project",
        session_id="session-1",
        expected_revision=0,
        canon_fingerprint="canon",
        guidance_fingerprint="guidance",
        original_model="orion-local",
        original_route="ollama-local",
        delivery_digest="digest",
        estimated_prompt_tokens=None,
        fallback_policy=[],
        request={"messages": [], "model": "orion-local"},
    )

    dispatch = generations.reserve_dispatch(created.request.id)

    assert dispatch.ordinal == 0
    assert dispatch.actual_model == "orion-local"


@pytest.mark.asyncio
async def test_new_project_reports_and_enforces_generation_enabled(client) -> None:
    """F1/S03a at the ordinary product boundary.

    Creates a project the way a writer does, seeds nothing, then checks that the
    control the UI reads agrees with the policy the dispatch path enforces.
    """
    http, adapter = client
    created = await http.post("/v1/projects", json={"name": "First run"})
    assert created.status_code in (200, 201)
    project_id = created.json()["id"]

    reported = await http.get(f"/v1/generation/settings?project_id={project_id}")
    assert reported.status_code == 200
    body = reported.json()
    assert body["enabled"] is True
    assert body["version"] == 0, "no policy row should have been written"

    # Reporting must agree with what dispatch actually enforces, proven by
    # reserving a real dispatch rather than by reading the policy a second time.
    generations = adapter._get_generation_store(project_id)
    assert generations.dispatch_enabled(project_id) is body["enabled"]

    created_request = generations.create_request(
        client_request_id="cr-first-run",
        project_id=project_id,
        session_id="session-first-run",
        expected_revision=0,
        canon_fingerprint="canon",
        guidance_fingerprint="guidance",
        original_model="orion-local",
        original_route="ollama-local",
        delivery_digest="digest",
        estimated_prompt_tokens=None,
        fallback_policy=[],
        request={"messages": [], "model": "orion-local"},
    )
    dispatch = generations.reserve_dispatch(created_request.request.id)
    assert dispatch.ordinal == 0


# ======================================================================
# F1b -- a received refusal is never rendered as a lost acknowledgement
# ======================================================================


def test_paused_project_refusal_is_typed_not_bare_detail() -> None:
    """F1b: the refusal must carry the typed failure shape.

    Pre-repair the dispatch-disabled path raised HTTPException(409, detail=str),
    which has no `outcome` key, so the browser fell through to its
    uncertain-custody branch and told the writer not to resubmit.
    """
    import json

    import gov_webui.adapter as adapter

    failure = adapter._generation_failure(adapter.ProjectGenerationPausedError("p"))
    assert failure.outcome == "failure"
    assert failure.kind == GenerationFailureKind.PROJECT_PAUSED
    assert failure.retryable is False
    assert failure.incident_id

    response = adapter._failure_response(failure)
    assert response.status_code == 409
    payload = json.loads(bytes(response.body))
    # The discriminator the browser branches on; its absence is what routed a
    # definite refusal into the lost-acknowledgement branch.
    assert payload["outcome"] == "failure"
    assert payload["failure_type"] == GenerationFailureKind.PROJECT_PAUSED
    assert payload["retryable"] is False
    assert payload["message"].startswith("Generation is paused for this project")
    assert "choices" not in payload


def test_generation_disabled_route_returns_typed_failure_shape() -> None:
    """F1b: the chat route converts GenerationDisabled into a typed failure."""
    source = Path(
        Path(__file__).resolve().parents[1] / "src" / "gov_webui" / "adapter.py"
    ).read_text(encoding="utf-8")
    handler = source.split("except GenerationDisabled as exc:")[1].split("return JSONResponse")[0]

    assert "_failure_response" in handler, "refusal must use the typed failure machinery"
    assert "ProjectGenerationPausedError" in handler
    assert "raise HTTPException" not in handler, "a definite refusal must not be an untyped 409"


def test_ui_lost_acknowledgement_branch_requires_no_server_answer() -> None:
    """F1b at the UI branch boundary.

    The 'browser lost the acknowledgement / do not submit this prompt again yet'
    notice is reserved for genuine uncertainty. If a response arrived at all,
    `error.status` is a number and the branch must not be taken.
    """
    ui = UI_SOURCE.read_text(encoding="utf-8")

    assert 'const answered = typeof error.status === "number";' in ui
    guard = re.search(r"if \(!failure && !answered && [^\n]*savedDurableRequest\(\)\?\.id\)", ui)
    assert guard, "the uncertain-custody branch must be guarded by !answered"

    # And the copy it guards is still the reserved wording.
    assert "browser lost the acknowledgement" in ui


# ======================================================================
# F2a -- a commit must not silently destroy autosaved typing
# ======================================================================


def test_restore_refuses_to_silently_discard_divergent_working_copy(store) -> None:
    """F2a/S06: the exact qualification failure.

    A writer types (autosaved), then restores an older revision. Pre-repair the
    working copy was unlinked unconditionally and the text was unrecoverable,
    while the UI dialog promised retention.
    """
    meta, _, _ = store.create(
        title="Scene", content="first draft", kind="markdown", language="", source="manual"
    )
    store.update(meta.id, content="second draft", expected_current_version=1)
    store.save_working_copy(meta.id, content="PRECIOUS unsaved typing", base_version=2)

    with pytest.raises(DivergentWorkingCopyError) as caught:
        store.update(meta.id, content="first draft", expected_current_version=2, source="restore")

    # The unsaved text is still recoverable, and history is untouched.
    assert store.get_working_copy(meta.id) == ("PRECIOUS unsaved typing", 2)
    current_meta, current_text, _ = store.get(meta.id)
    assert current_meta.current_version == 2
    assert current_text == "second draft"
    assert store.get_version(meta.id, 1) == "first draft"
    assert caught.value.resolution_hint() if hasattr(caught.value, "resolution_hint") else True


def test_explicit_discard_allows_the_restore_to_proceed(store) -> None:
    """F2a: the writer can still lose the text, but only deliberately."""
    meta, _, _ = store.create(
        title="Scene", content="first draft", kind="markdown", language="", source="manual"
    )
    store.update(meta.id, content="second draft", expected_current_version=1)
    store.save_working_copy(meta.id, content="typing", base_version=2)

    restored_meta, restored_text, _ = store.update(
        meta.id,
        content="first draft",
        expected_current_version=2,
        source="restore",
        discard_working_copy=True,
    )

    assert restored_meta.current_version == 3
    assert restored_text == "first draft"
    assert store.get_working_copy(meta.id) == (None, None)


def test_identical_working_copy_does_not_create_friction(store) -> None:
    """F2a: the ordinary Save-revision path commits exactly the autosaved text."""
    meta, _, _ = store.create(
        title="Scene", content="draft", kind="markdown", language="", source="manual"
    )
    store.save_working_copy(meta.id, content="edited in the editor", base_version=1)

    committed_meta, committed_text, _ = store.update(
        meta.id, content="edited in the editor", expected_current_version=1, source="edit"
    )

    assert committed_meta.current_version == 2
    assert committed_text == "edited in the editor"
    assert store.get_working_copy(meta.id) == (None, None)


def test_commit_without_any_working_copy_is_unaffected(store) -> None:
    """F2a: no working copy means no new failure mode."""
    meta, _, _ = store.create(
        title="Scene", content="draft", kind="markdown", language="", source="manual"
    )
    updated, text, _ = store.update(meta.id, content="revised", expected_current_version=1)
    assert updated.current_version == 2
    assert text == "revised"


@pytest.mark.asyncio
async def test_divergent_working_copy_surfaces_as_actionable_409(client) -> None:
    """F2a at the HTTP boundary: the conflict is typed and carries its resolution."""
    http, _ = client
    created = await http.post(
        "/v1/artifacts",
        json={"title": "Scene", "content": "first", "kind": "markdown", "source": "manual"},
    )
    artifact_id = created.json()["artifact"]["id"]
    await http.put(
        f"/v1/artifacts/{artifact_id}",
        json={"content": "second", "expected_current_version": 1, "source": "edit"},
    )
    await http.put(
        f"/v1/artifacts/{artifact_id}/working-copy",
        json={"content": "unsaved typing", "base_version": 2},
    )

    conflict = await http.post(
        f"/v1/artifacts/{artifact_id}/version/1/restore",
        json={"expected_current_version": 2},
    )

    assert conflict.status_code == 409
    body = conflict.json()
    assert body["error"]["code"] == "divergent_working_copy"
    assert body["error"]["details"]["resolution"] == "discard_working_copy"

    still_there = await http.get(f"/v1/artifacts/{artifact_id}")
    assert still_there.json()["working_copy"] == "unsaved typing"

    accepted = await http.post(
        f"/v1/artifacts/{artifact_id}/version/1/restore",
        json={"expected_current_version": 2, "discard_working_copy": True},
    )
    assert accepted.status_code == 200
    assert accepted.json()["artifact"]["current_version"] == 3


# ======================================================================
# F2b -- DEFERRED: one working-copy slot, no writer identity
# ======================================================================


@pytest.mark.xfail(
    reason=(
        "F2b UNRESOLVED. Two editors of one draft share a single working-copy slot "
        "keyed only by base_version, so both writes succeed and the first writer's "
        "unsaved text is lost while both are told autosave succeeded. A token/etag "
        "CAS is mechanically small, but the missing product oracle is what a stale "
        "writer should then experience: a permanent autosave conflict with text only "
        "in the DOM is not obviously better than the current loss. Needs a decided "
        "resolution UX before implementation. See campaign handoff section 6."
    ),
    strict=True,
)
def test_concurrent_working_copy_writers_do_not_clobber(store) -> None:
    """F2b/S08b: pinned as a known gap, not silently tolerated."""
    meta, _, _ = store.create(
        title="Scene", content="draft", kind="markdown", language="", source="manual"
    )

    store.save_working_copy(meta.id, content="TAB A spent an hour on this", base_version=1)
    store.save_working_copy(meta.id, content="TAB B short note", base_version=1)

    surviving, _ = store.get_working_copy(meta.id)
    assert "TAB A" in surviving, "the first writer's unsaved work must not vanish silently"


# ======================================================================
# F3 -- artifact error envelopes must reach the writer
# ======================================================================


@pytest.mark.asyncio
async def test_artifact_conflict_envelope_carries_an_actionable_message(client) -> None:
    """F3/S07: the server does provide a useful sentence."""
    http, _ = client
    created = await http.post(
        "/v1/artifacts",
        json={"title": "Scene", "content": "first", "kind": "markdown", "source": "manual"},
    )
    artifact_id = created.json()["artifact"]["id"]
    await http.put(
        f"/v1/artifacts/{artifact_id}",
        json={"content": "second", "expected_current_version": 1, "source": "edit"},
    )

    stale = await http.put(
        f"/v1/artifacts/{artifact_id}",
        json={"content": "third", "expected_current_version": 1, "source": "edit"},
    )

    assert stale.status_code == 409
    body = stale.json()
    assert body["ok"] is False
    assert body["error"]["code"] == "stale_version"
    assert "expected version 1" in body["error"]["message"]
    # The shape the UI must be able to read:
    assert "detail" not in body and "message" not in body


def test_ui_extracts_artifact_error_messages() -> None:
    """F3/S07 at the UI boundary.

    Pre-repair `api()` read only detail/detail.message/message, so every artifact
    conflict collapsed to the bare status line "409 Conflict".
    """
    ui = UI_SOURCE.read_text(encoding="utf-8")
    assert 'if (typeof body.error?.message === "string") detail = body.error.message;' in ui

    extractor = ui.split("async function api(path")[1].split("return response.json();")[0]
    # Existing precedence must be preserved, and typed failures still win.
    assert "body.detail?.message || body.detail || detail" in extractor
    assert 'if (body.outcome === "failure") detail = body.message || detail;' in extractor
    # Must not blanket-stringify an arbitrary body.
    assert "JSON.stringify(body)" not in extractor


def test_ui_api_extraction_behaviour_on_the_exact_shapes() -> None:
    """F3: executable model of the UI's extraction precedence."""

    def extract(body: dict, status_line: str) -> str:
        detail = status_line
        d = body.get("detail")
        detail = (
            (d.get("message") if isinstance(d, dict) else None)
            or (d if isinstance(d, str) else None)
            or detail
        )
        err = body.get("error")
        if isinstance(err, dict) and isinstance(err.get("message"), str):
            detail = err["message"]
        if body.get("outcome") == "failure":
            detail = body.get("message") or detail
        return detail

    artifact_conflict = {
        "ok": False,
        "error": {"code": "stale_version", "message": "Artifact x: expected version 1"},
    }
    assert extract(artifact_conflict, "409 Conflict") == "Artifact x: expected version 1"

    typed_failure = {
        "outcome": "failure",
        "failure_type": "project_paused",
        "message": "Generation is paused for this project. Enable generation to continue.",
    }
    assert extract(typed_failure, "409 Conflict").startswith("Generation is paused")

    fastapi_detail = {"detail": "session-backed generation requires one new user message"}
    assert extract(fastapi_detail, "422 Unprocessable Entity").startswith("session-backed")

    structured_detail = {"detail": {"message": "expected generation settings version 0"}}
    assert extract(structured_detail, "409 Conflict").startswith("expected generation settings")

    opaque = {"something": "else"}
    assert extract(opaque, "500 Internal Server Error") == "500 Internal Server Error"


# ======================================================================
# F4 -- a context race is machine-identifiable, not prose
# ======================================================================


def test_blocked_context_race_records_the_taxonomy_code(tmp_path: Path) -> None:
    """F4/S10,S12: blocked stale outcomes carry failure_type=stale_context."""
    generations = GenerationStore(tmp_path / "generation.sqlite")
    generations.set_dispatch_enabled("p", True)
    created = generations.create_request(
        client_request_id="cr-1",
        project_id="p",
        session_id="s",
        expected_revision=0,
        canon_fingerprint="canon",
        guidance_fingerprint="guidance",
        original_model="m",
        original_route="r",
        delivery_digest="d",
        estimated_prompt_tokens=None,
        fallback_policy=[],
        request={"messages": [], "model": "m"},
    )
    dispatch = generations.reserve_dispatch(created.request.id)
    generations.mark_executing(dispatch.id)
    candidate = generations.record_candidate(
        dispatch.id, response_digest="rd-1", evidence_ref="ev-1"
    )

    generations.block_candidate(
        candidate.id,
        "session revision changed",
        failure_type=GenerationFailureKind.STALE_CONTEXT,
    )

    request = generations.get_request(created.request.id)
    assert request.status.value == "blocked"
    assert request.last_error == "session revision changed"
    assert request.failure_type == GenerationFailureKind.STALE_CONTEXT


def test_acceptance_classifies_every_context_race_as_stale_context() -> None:
    """F4: the three race checks all report the same machine-readable class."""
    source = (
        Path(__file__).resolve().parents[1] / "src" / "gov_webui" / "generation_acceptance.py"
    ).read_text(encoding="utf-8")

    for reason in (
        "session revision changed",
        "accepted canon changed",
        "project guidance changed",
        "target session no longer exists",
    ):
        block = source.split(f'"{reason}"')[1].split(")")[0]
        assert "STALE_CONTEXT" in block, f"{reason!r} must be classified as stale_context"

    # A deliberate story-boundary block is not an operational failure.
    assert (
        'return _block(generation_store, candidate_id, "governor response requires review")'
        in source
    )


def test_ui_stale_block_does_not_invite_a_blind_resubmit() -> None:
    """F4 at the UI boundary.

    Pre-repair the writer saw the raw string 'session revision changed' and had
    the prompt restored with no explanation, inviting the resubmit that would
    actually duplicate the turn.
    """
    ui = UI_SOURCE.read_text(encoding="utf-8")
    assert "function blockedGenerationMessage(terminal)" in ui
    helper = ui.split("function blockedGenerationMessage(terminal)")[1].split("\n    }")[0]
    assert 'terminal?.failure_type === "stale_context"' in helper
    assert "Nothing was saved from it" in helper
    assert "decide whether to send it again" in helper
    # The raw internal phrase must not be the writer-facing copy.
    assert "session revision changed" not in helper


def test_blocked_status_payload_exposes_failure_type() -> None:
    """F4: clients can branch without parsing prose."""
    source = (Path(__file__).resolve().parents[1] / "src" / "gov_webui" / "adapter.py").read_text(
        encoding="utf-8"
    )
    payload = source.split("def _generation_status_payload")[1].split("def ", 1)[0]
    blocked_blocks = [
        chunk for chunk in payload.split('"outcome": "blocked"') if '"failure_type"' in chunk
    ]
    assert len(blocked_blocks) >= 2, "both blocked payload branches must carry failure_type"


# ======================================================================
# F5 -- DEFERRED: no destination for a model-driven revision of a draft
# ======================================================================


@pytest.mark.skip(
    reason=(
        "F5 UNRESOLVED and out of scope for the 2026-09-17 repair campaign. "
        "Marginalia has no surface that applies a generated revision to an "
        "existing artifact: the only content writes are create-new, PUT from the "
        "writer's own editor text, working-copy autosave, and restore. The "
        "manuscript outline's 'Draft' action seeds the prompt with the current "
        "draft, which implies a round trip, but 'Keep selection' creates a second "
        "artifact and repoints the outline node, splitting the section's revision "
        "history. Closing this needs a product oracle first: does a revision "
        "target the existing artifact, create a candidate revision, replace the "
        "manuscript pointer, or stay exploratory -- and what does acceptance mean? "
        "See campaign handoff section 6."
    )
)
def test_generated_revision_lands_in_the_same_draft_history() -> None:
    """F5/S22: the absent capability, recorded as an executable seam."""
    raise AssertionError("no artifact-level revision destination exists")


# ======================================================================
# S23 -- DEFERRED: a pre-conversation prompt is not restored on startup
# ======================================================================


@pytest.mark.xfail(
    reason=(
        "S23 UNRESOLVED and out of scope for provenance/succession closure. "
        "persistPromptDraft stores the first prompt under the project :new key, "
        "but start() never restores that slot after loading the project. Preserve "
        "the local draft across reload before a conversation exists."
    ),
    strict=True,
)
def test_preconversation_prompt_is_restored_on_startup() -> None:
    """S23: pin the missing startup restoration without claiming it is fixed."""
    ui = UI_SOURCE.read_text(encoding="utf-8")
    start = ui.split("async function start() {")[1].split("\n    }\n    start();", 1)[0]
    assert "restorePromptDraft();" in start

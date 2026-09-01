# SPDX-License-Identifier: Apache-2.0
"""
Marginalia's OpenAI-compatible API and governed creative-writing donor UI.

Serves a self-contained fiction-writing UI at the root URL (/) and exposes an
OpenAI-compatible governed-chat API.  Agent Governor owns provider selection
and execution; Marginalia owns conversations and creative-project state.

Run with: uvicorn gov_webui.adapter:app --host 0.0.0.0 --port 8000

Primary UI:  http://localhost:8000
API info:    http://localhost:8000/api/info

Configuration via environment variables:
    MARGINALIA_DATA_ROOT - Persistent application root (default: ~/.marginalia)
    GOVERNOR_DAEMON_DIR  - AG daemon state directory (default: DATA_ROOT/.governor)
    GOVERNOR_CONTEXT_ID - Active context ID (default: "default")
    GOVERNOR_MODE       - Must be "fiction" for the Marginalia product
    GOVERNOR_CONTEXTS_DIR - AG context base (default: GOVERNOR_DAEMON_DIR)
    GOVERNOR_AUTH_TOKEN - Bearer token for mutating endpoints (default: "" = no auth)
    GOVERNOR_BIND_HOST  - Host to bind to (default: "127.0.0.1")

Historical code/research/operator routes remain temporarily available only
when MARGINALIA_ENABLE_DONOR_ROUTES=1.  They are disabled by default and are
not part of the served Marginalia product.
"""

from __future__ import annotations

import hashlib
import difflib
import io
import importlib.metadata
import json
import logging
import os
import re
import shutil
import time
import uuid
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, AsyncGenerator
from xml.sax.saxutils import escape as xml_escape

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse, Response, StreamingResponse
from pydantic import BaseModel, Field

from gov_webui.backup_store import BackupError, WorkspaceBackupManager
from gov_webui.daemon_client import DaemonAuthError, DaemonChatClient, default_socket_path
from gov_webui.creative_project import (
    CreativeProjectConfig,
    CreativeProjectContextMismatch,
    CreativeProjectError,
    CreativeProjectStore,
    CreativeProjectVersionConflict,
    render_project_context,
)
from gov_webui.canon_review_store import (
    CanonReviewNotFoundError,
    CanonReviewStore,
    CanonReviewStoreError,
)
from gov_webui.governed_chat_adapter import GovernedChatAdapter
from gov_webui.library_store import (
    ConversationLifecycle,
    ConversationLifecycleNotFoundError,
    LibraryStore,
    LibraryStoreError,
    ProjectNotFoundError,
    ProjectRecord,
    WorkspaceNotFoundError,
    WorkspaceRecord,
)
from gov_webui.model_providers import (
    ConfiguredModel,
    ProviderCatalog,
    ProviderConfigurationError,
    ProviderError,
    load_provider_catalog,
)
from gov_webui.markdown import render_writer_markdown
from gov_webui.manuscript_store import (
    ManuscriptNodeNotFoundError,
    ManuscriptStore,
    ManuscriptStoreError,
)
from gov_webui.ops import deployment_metadata, migration_preflight, schema_versions
from gov_webui.snapshot_store import (
    ProjectSnapshotStore,
    SnapshotNotFoundError,
    SnapshotStoreError,
)
from gov_webui.session_store import ChatSession, SessionMessage, SessionStore
from governor.context_manager import GovernorContextManager

logger = logging.getLogger(__name__)

# ============================================================================
# Configuration from environment
# ============================================================================

MARGINALIA_DATA_ROOT = os.environ.get("MARGINALIA_DATA_ROOT", str(Path.home() / ".marginalia"))
GOVERNOR_DAEMON_DIR = os.environ.get(
    "GOVERNOR_DAEMON_DIR", str(Path(MARGINALIA_DATA_ROOT) / ".governor")
)
BACKEND_TYPE = os.environ.get("BACKEND_TYPE", "daemon")
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
OLLAMA_HOST = os.environ.get("OLLAMA_HOST", "http://localhost:11434")
CLAUDE_PATH = os.environ.get("CLAUDE_PATH", "claude")  # Path to claude CLI for claude-code backend
CODEX_PATH = os.environ.get("CODEX_PATH", "codex")  # Path to codex CLI for codex backend
GOVERNOR_CONTEXT_ID = os.environ.get("GOVERNOR_CONTEXT_ID", "default")
GOVERNOR_MODE = os.environ.get("GOVERNOR_MODE", "fiction")
GOVERNOR_CONTEXTS_DIR = os.environ.get("GOVERNOR_CONTEXTS_DIR", GOVERNOR_DAEMON_DIR)
GOVERNOR_SHOW_OK_FOOTER = os.environ.get("GOVERNOR_SHOW_OK_FOOTER", "true").lower() in (
    "true",
    "1",
    "yes",
)
MARGINALIA_ENABLE_DONOR_ROUTES = os.environ.get("MARGINALIA_ENABLE_DONOR_ROUTES", "").lower() in (
    "true",
    "1",
    "yes",
)
MARGINALIA_BACKUP_ROOT = os.environ.get("MARGINALIA_BACKUP_ROOT", "/backups")
MARGINALIA_BACKUP_REQUIRE_REMOTE = os.environ.get(
    "MARGINALIA_BACKUP_REQUIRE_REMOTE", "false"
).lower() in {"true", "1", "yes"}
MARGINALIA_MODEL_CONFIG = os.environ.get(
    "MARGINALIA_MODEL_CONFIG", ""
).strip()

if GOVERNOR_MODE != "fiction" and not MARGINALIA_ENABLE_DONOR_ROUTES:
    raise RuntimeError(
        "Marginalia is fiction-only; GOVERNOR_MODE must be 'fiction'. "
        "Historical donor routes may be exercised only with "
        "MARGINALIA_ENABLE_DONOR_ROUTES=1."
    )

# Heavy AG research/operator modules are not imported by the product path.
# They remain behind the temporary donor-test switch until their historical
# tests and source can be removed in a later cleanup slice.
if MARGINALIA_ENABLE_DONOR_ROUTES:
    from governor.viewmodel import GovernorViewModel, build_viewmodel
    from gov_webui.summaries import (
        derive_history_days,
        derive_last_event,
        derive_one_sentence,
        derive_status_pill,
        derive_suggested_action,
        derive_why_feed,
    )

# Auth token — when set, mutating endpoints require Authorization: Bearer <token>
# When unset, all endpoints are open (dev mode).
GOVERNOR_AUTH_TOKEN = os.environ.get("GOVERNOR_AUTH_TOKEN", "")

# Host binding — default to loopback for safety; set to 0.0.0.0 explicitly if needed
GOVERNOR_BIND_HOST = os.environ.get("GOVERNOR_BIND_HOST", "127.0.0.1")

# Legacy run-builder default. Governed chat provider ownership belongs to AG.
_current_backend_type: str = BACKEND_TYPE

# ============================================================================
# Application setup
# ============================================================================


def _webui_version() -> str:
    """Single source of truth for the API version — derived from the package."""
    try:
        return importlib.metadata.version("marginalia")
    except importlib.metadata.PackageNotFoundError:
        return "0.1.0"


app = FastAPI(
    title="Marginalia",
    description="Standalone governed creative-writing application",
    version=_webui_version(),
    docs_url="/docs" if MARGINALIA_ENABLE_DONOR_ROUTES else None,
    redoc_url=None,
    openapi_url="/openapi.json" if MARGINALIA_ENABLE_DONOR_ROUTES else None,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ============================================================================
# Auth middleware — opt-in via GOVERNOR_AUTH_TOKEN
# ============================================================================

# Methods that require auth when token is configured
_AUTH_METHODS = {"POST", "PUT", "DELETE", "PATCH"}

# Paths exempt from auth even for mutating methods (health probes, etc.)
_AUTH_EXEMPT_PATHS = {"/health", "/api/info", "/docs", "/openapi.json"}

_PRODUCT_EXACT_PATHS = {
    "/",
    "/health",
    "/health/live",
    "/health/ready",
    "/api/info",
    "/v1/system",
    "/v1/models",
    "/v1/markdown",
    "/v1/backends",
    "/v1/backends/switch",
    "/v1/chat/completions",
    "/v1/governed-chat/pending",
    "/v1/governed-chat/resolve",
    "/v1/project",
    "/v1/project/export",
    "/v1/project/export.zip",
    "/v1/project/snapshots",
    "/v1/projects",
    "/v1/workspaces",
    "/v1/manuscript",
    "/v1/search",
    "/v1/entities",
}
_PRODUCT_PATH_PREFIXES = (
    "/v1/models/",
    "/v1/projects/",
    "/v1/workspaces/",
    "/v1/project/",
    "/v1/manuscript/",
    "/v1/conversations/",
    "/sessions/",
    "/governor/fiction/",
    "/governor/artifacts",
)


def _is_product_path(path: str) -> bool:
    return path in _PRODUCT_EXACT_PATHS or path.startswith(_PRODUCT_PATH_PREFIXES)


@app.middleware("http")
async def auth_middleware(request: Request, call_next):
    """Enforce the fiction-product route and optional bearer-auth boundaries."""
    if not MARGINALIA_ENABLE_DONOR_ROUTES and not _is_product_path(request.url.path):
        return JSONResponse(status_code=404, content={"detail": "Not found"})
    if GOVERNOR_AUTH_TOKEN and request.method in _AUTH_METHODS:
        if request.url.path not in _AUTH_EXEMPT_PATHS:
            auth_header = request.headers.get("authorization", "")
            if not auth_header.startswith("Bearer "):
                return JSONResponse(
                    status_code=401,
                    content={"detail": "Authorization header required: Bearer <token>"},
                )
            provided = auth_header[7:]  # strip "Bearer "
            if provided != GOVERNOR_AUTH_TOKEN:
                return JSONResponse(
                    status_code=403,
                    content={"detail": "Invalid auth token"},
                )
    return await call_next(request)


# ============================================================================
# Pydantic Models (OpenAI API format)
# ============================================================================


class ChatMessage(BaseModel):
    role: str
    content: str
    name: str | None = None


class ChatCompletionRequest(BaseModel):
    model: str = ""  # optional — default "" matches daemon behaviour
    messages: list[ChatMessage]
    temperature: float = 0.7
    top_p: float = 1.0
    n: int = 1
    stream: bool = False
    stop: list[str] | str | None = None
    max_tokens: int | None = None
    presence_penalty: float = 0.0
    frequency_penalty: float = 0.0
    user: str | None = None
    project_id: str | None = None


class ChatCompletionChoice(BaseModel):
    index: int
    message: ChatMessage
    finish_reason: str | None = None


class Usage(BaseModel):
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int


class ChatCompletionResponse(BaseModel):
    id: str
    object: str = "chat.completion"
    created: int
    model: str
    provider_id: str | None = None
    model_id: str | None = None
    choices: list[ChatCompletionChoice]
    usage: Usage | None = None
    receipt: dict


class ModelInfo(BaseModel):
    id: str
    object: str = "model"
    label: str | None = None
    provider_id: str | None = None
    model_id: str | None = None
    available: bool = True
    unavailable_reason: str | None = None
    created: int = 0
    owned_by: str = "system"


class ModelList(BaseModel):
    object: str = "list"
    data: list[ModelInfo]
    default_model: str | None = None


class BackendSwitchRequest(BaseModel):
    backend_type: str


class PendingResolutionRequest(BaseModel):
    action: str
    corrected_text: str | None = None
    new_anchor_text: str | None = None
    reason: str = ""
    scope: str | None = None
    expiry: str | None = None
    project_id: str | None = None


class CreativeProjectUpdateRequest(BaseModel):
    """The complete, deliberately small M1 project-settings form."""

    project_brief: str = Field(default="", max_length=20_000)
    collaborator_stance: str = Field(default="", max_length=20_000)
    voice_style_guidance: str = Field(default="", max_length=20_000)
    expected_version: int | None = Field(default=None, ge=1)
    project_id: str | None = None


class ProjectCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=160)
    workspace_id: str | None = None


class ProjectUpdateRequest(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=160)
    archived: bool | None = None


class WorkspaceCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=160)


class WorkspaceUpdateRequest(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=160)
    backup_enabled: bool | None = None
    backup_subdirectory: str | None = Field(default=None, min_length=1, max_length=120)
    backup_schedule: str | None = None
    backup_hour_utc: int | None = Field(default=None, ge=0, le=23)
    backup_retention_count: int | None = Field(default=None, ge=1, le=365)


class ManuscriptCreateRequest(BaseModel):
    project_id: str | None = None
    kind: str
    title: str = Field(min_length=1, max_length=240)
    parent_id: str | None = None
    artifact_id: str | None = None
    status: str = "idea"


class ManuscriptUpdateRequest(BaseModel):
    project_id: str | None = None
    title: str | None = Field(default=None, min_length=1, max_length=240)
    artifact_id: str | None = None
    set_artifact: bool = False
    status: str | None = None


class ManuscriptMoveRequest(BaseModel):
    project_id: str | None = None
    parent_id: str | None = None
    position: int = Field(default=0, ge=0)


class MarkdownRenderRequest(BaseModel):
    """Writer-visible Markdown, bounded like other project text fields."""

    content: str = Field(max_length=200_000)


class SnapshotCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=160)
    project_id: str | None = None


# ============================================================================
# Bridge setup (lazy init on first request)
# ============================================================================

# Compatibility sentinel for untouched donor tests/routes. Governed chat never
# constructs or calls a local ChatBridge.
_bridge: Any = None
_context_manager: GovernorContextManager | None = None
_session_store: SessionStore | None = None
_governed_chat_adapter: GovernedChatAdapter | None = None
_creative_project_store: CreativeProjectStore | None = None
_library_store: LibraryStore | None = None
_session_stores: dict[str, SessionStore] = {}
_governed_chat_adapters: dict[str, GovernedChatAdapter] = {}
_creative_project_stores: dict[str, CreativeProjectStore] = {}
_artifact_stores: dict[str, Any] = {}
_canon_review_stores: dict[str, CanonReviewStore] = {}
_manuscript_stores: dict[str, ManuscriptStore] = {}
_snapshot_stores: dict[str, ProjectSnapshotStore] = {}


def _configured_provider_catalog() -> ProviderCatalog | None:
    """Load host-selected providers without caching credentials or private values."""
    if not MARGINALIA_MODEL_CONFIG:
        return None
    return load_provider_catalog(MARGINALIA_MODEL_CONFIG)


def _resolve_configured_model(
    requested_model: str,
    *,
    require_available: bool = True,
) -> tuple[str, ConfiguredModel | None]:
    """Resolve one exact selection; absence of configuration preserves legacy mode."""
    catalog = _configured_provider_catalog()
    if catalog is None:
        return requested_model, None
    try:
        model = (
            catalog.require_available(requested_model)
            if require_available
            else catalog.resolve(requested_model)
        )
    except ProviderConfigurationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except ProviderError as exc:
        raise HTTPException(status_code=503, detail=exc.to_dict()) from exc
    return model.id, model


def _get_library_store() -> LibraryStore:
    """Return the additive organization sidecar and enroll legacy sessions."""
    global _library_store
    if _library_store is None:
        cm = _get_context_manager()
        # In production the context base is DATA_ROOT/.governor, so the
        # sidecar lives at DATA_ROOT/marginalia/library.json. Deriving it from
        # the context manager also keeps isolated tests isolated.
        path = cm.base_dir.parent / "marginalia" / "library.json"
        _library_store = LibraryStore(path, default_context_id=GOVERNOR_CONTEXT_ID)
        legacy_ids = [item["id"] for item in _get_default_session_store().list_summaries()]
        _library_store.sync_legacy_sessions(legacy_ids)
    return _library_store


def _get_backup_manager() -> WorkspaceBackupManager:
    """Bind backup operations to the same durable root as the live library."""
    return WorkspaceBackupManager(
        data_root=Path(MARGINALIA_DATA_ROOT),
        backup_root=Path(MARGINALIA_BACKUP_ROOT),
        default_context_id=GOVERNOR_CONTEXT_ID,
        deployment=deployment_metadata(),
        require_remote=MARGINALIA_BACKUP_REQUIRE_REMOTE,
    )


def _project_record(project_id: str | None = None) -> ProjectRecord:
    try:
        return _get_library_store().get_project(project_id)
    except ProjectNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


def _get_governed_chat_adapter(
    project_id: str | None = None,
) -> GovernedChatAdapter:
    """Get an AG boundary bound to the selected project's context."""
    global _governed_chat_adapter
    project = _project_record(project_id)
    if project.id == _get_library_store().snapshot().default_project_id:
        if _governed_chat_adapter is not None:
            return _governed_chat_adapter
    elif project.context_id in _governed_chat_adapters:
        return _governed_chat_adapters[project.context_id]

    if (
        _governed_chat_adapter is None
        or project.id != _get_library_store().snapshot().default_project_id
    ):
        socket_path = os.environ.get("GOVERNOR_SOCKET", "")
        if not socket_path:
            socket_path = str(default_socket_path(Path(GOVERNOR_DAEMON_DIR)))
        adapter = GovernedChatAdapter(
            DaemonChatClient(socket_path),
            context_id=project.context_id,
            expected_governor_dir=GOVERNOR_DAEMON_DIR,
        )
        if project.id == _get_library_store().snapshot().default_project_id:
            _governed_chat_adapter = adapter
        else:
            _governed_chat_adapters[project.context_id] = adapter
    return (
        _governed_chat_adapter
        if project.id == _get_library_store().snapshot().default_project_id
        else _governed_chat_adapters[project.context_id]
    )


def _get_context_manager() -> GovernorContextManager:
    global _context_manager
    if _context_manager is None:
        base_dir = Path(GOVERNOR_CONTEXTS_DIR) if GOVERNOR_CONTEXTS_DIR else None
        _context_manager = GovernorContextManager(base_dir=base_dir)
    return _context_manager


def _get_default_session_store() -> SessionStore:
    global _session_store
    if _session_store is None:
        cm = _get_context_manager()
        ctx = cm.get(GOVERNOR_CONTEXT_ID)
        if ctx is not None:
            sessions_dir = ctx.root / "sessions"
        else:
            # Context not created yet — compute path without writing to disk.
            # SessionStore.list_summaries() handles non-existent dir gracefully;
            # create() calls _ensure_dir() only when actually needed.
            sessions_dir = cm.base_dir / GOVERNOR_CONTEXT_ID / "sessions"
        _session_store = SessionStore(sessions_dir)
    return _session_store


def _get_session_store(project_id: str | None = None) -> SessionStore:
    project = _project_record(project_id)
    if project.context_id == GOVERNOR_CONTEXT_ID:
        return _get_default_session_store()
    if project.context_id not in _session_stores:
        cm = _get_context_manager()
        ctx = cm.get(project.context_id)
        sessions_dir = (
            ctx.root / "sessions"
            if ctx is not None
            else cm.base_dir / project.context_id / "sessions"
        )
        _session_stores[project.context_id] = SessionStore(sessions_dir)
    return _session_stores[project.context_id]


def _get_creative_project_store(
    project_id: str | None = None,
) -> CreativeProjectStore:
    """Return project state bound to the same context used for governed chat."""
    global _creative_project_store
    project = _project_record(project_id)
    if project.context_id == GOVERNOR_CONTEXT_ID and _creative_project_store is not None:
        return _creative_project_store
    if project.context_id in _creative_project_stores:
        return _creative_project_stores[project.context_id]
    if _creative_project_store is None or project.context_id != GOVERNOR_CONTEXT_ID:
        cm = _get_context_manager()
        context = cm.get_or_create(project.context_id, mode="fiction")
        if context.mode != "fiction":
            raise CreativeProjectError(
                f"context {project.context_id!r} is {context.mode!r}, not fiction"
            )
        store = CreativeProjectStore(
            context.root,
            project.context_id,
        )
        if project.context_id == GOVERNOR_CONTEXT_ID:
            _creative_project_store = store
        else:
            _creative_project_stores[project.context_id] = store
    return (
        _creative_project_store
        if project.context_id == GOVERNOR_CONTEXT_ID
        else _creative_project_stores[project.context_id]
    )


def _get_canon_review_store(project_id: str | None = None) -> CanonReviewStore:
    project = _project_record(project_id)
    if project.context_id not in _canon_review_stores:
        context = _get_context_manager().get_or_create(project.context_id, mode="fiction")
        _canon_review_stores[project.context_id] = CanonReviewStore(
            context.root / "marginalia" / "canon-review.json",
            project_id=project.id,
        )
    return _canon_review_stores[project.context_id]


def _get_manuscript_store(project_id: str | None = None) -> ManuscriptStore:
    project = _project_record(project_id)
    if project.context_id not in _manuscript_stores:
        context = _get_context_manager().get_or_create(project.context_id, mode="fiction")
        _manuscript_stores[project.context_id] = ManuscriptStore(
            context.root / "marginalia" / "manuscript.json"
        )
    return _manuscript_stores[project.context_id]


def _get_snapshot_store(project_id: str | None = None) -> ProjectSnapshotStore:
    project = _project_record(project_id)
    if project.id not in _snapshot_stores:
        root = _get_context_manager().base_dir.parent / "marginalia" / "snapshots"
        _snapshot_stores[project.id] = ProjectSnapshotStore(root, project_id=project.id)
    return _snapshot_stores[project.id]


def _project_payload(config: CreativeProjectConfig) -> dict[str, Any]:
    payload = config.model_dump(mode="json")
    payload["has_guidance"] = config.has_guidance
    return payload


def _build_project_context_message(project_id: str | None = None) -> dict[str, str] | None:
    """Build the persistent writing guidance sent through governed execution."""
    if GOVERNOR_MODE != "fiction":
        return None
    return render_project_context(_get_creative_project_store(project_id).get())


# ============================================================================
# API Endpoints
# ============================================================================


@app.get("/v1/models")
async def list_models() -> ModelList:
    """List only explicitly configured models when a catalog is present."""
    try:
        catalog = _configured_provider_catalog()
        if catalog is not None:
            available_default = catalog.available_default()
            return ModelList(
                default_model=available_default.id if available_default else None,
                data=[
                    ModelInfo(
                        id=model.id,
                        label=model.label,
                        owned_by=model.provider_id,
                        provider_id=model.provider_id,
                        model_id=model.model_id,
                        available=model.availability_error() is None,
                        unavailable_reason=model.availability_error(),
                    )
                    for model in catalog.models
                ],
            )
        adapter = _get_governed_chat_adapter()
        models = await adapter.models()
        return ModelList(
            data=[ModelInfo(id=m["id"], owned_by=m.get("owned_by", "system")) for m in models]
        )
    except ProviderConfigurationError as exc:
        raise HTTPException(status_code=500, detail=f"Provider configuration error: {exc}")
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Backend error: {e}")


@app.get("/v1/models/{model_id}")
async def get_model(model_id: str) -> ModelInfo:
    """Get info about a specific model."""
    catalog = _configured_provider_catalog()
    if catalog is not None:
        try:
            model = catalog.resolve(model_id)
        except ProviderConfigurationError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return ModelInfo(
            id=model.id,
            label=model.label,
            owned_by=model.provider_id,
            provider_id=model.provider_id,
            model_id=model.model_id,
            available=model.availability_error() is None,
            unavailable_reason=model.availability_error(),
        )
    provider = await _get_governed_chat_adapter().provider()
    return ModelInfo(id=model_id, owned_by=provider.get("type", "daemon"))


@app.post("/v1/markdown")
async def render_markdown(request: MarkdownRenderRequest) -> dict[str, str]:
    """Render inert writer Markdown for the conversation surface."""
    return {"html": render_writer_markdown(request.content)}


# ============================================================================
# Backend Switching Endpoints
# ============================================================================


@app.get("/v1/backends")
async def list_backends() -> dict[str, Any]:
    """Report only AG's real governed-execution provider."""
    try:
        governed_chat = _get_governed_chat_adapter()
        provider = await governed_chat.provider()
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Daemon error: {exc}")
    try:
        reachable = bool(await governed_chat.models())
    except Exception:
        reachable = False
    backend_type = provider.get("type", "unknown")
    connected = bool(provider.get("connected")) and reachable
    return {
        "backends": [
            {
                "type": backend_type,
                "available": connected,
                "active": True,
                "configured_by": "agent-governor-daemon",
            }
        ],
        "active": backend_type,
        "connected": connected,
        "authoritative": "agent-governor-daemon",
    }


@app.post("/v1/backends/switch")
async def switch_backend(request: BackendSwitchRequest) -> dict[str, Any]:
    """Reject local switches: the AG daemon owns the actual provider."""
    raise HTTPException(
        status_code=409,
        detail=(
            "Provider configuration is owned by the Agent Governor daemon; "
            f"Marginalia cannot switch it to {request.backend_type!r}."
        ),
    )


@app.get("/v1/governed-chat/pending")
async def governed_chat_pending(project_id: str | None = None) -> dict[str, Any]:
    """Observe durable pending state in Marginalia's active context."""
    try:
        return {"pending": await _get_governed_chat_adapter(project_id).pending()}
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Daemon error: {exc}")


@app.post("/v1/governed-chat/resolve")
async def governed_chat_resolve(
    request: PendingResolutionRequest,
) -> dict[str, Any]:
    """Resolve pending state through the same context-bound AG adapter."""
    if request.action not in {"fix", "revise", "proceed"}:
        raise HTTPException(
            status_code=400,
            detail="action must be one of: fix, revise, proceed",
        )
    try:
        return await _get_governed_chat_adapter(request.project_id).resolve_pending(
            request.action,
            corrected_text=request.corrected_text,
            new_anchor_text=request.new_anchor_text,
            reason=request.reason,
            scope=request.scope,
            expiry=request.expiry,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Daemon error: {exc}")


def _library_project_payload(
    project: ProjectRecord,
    *,
    conversation_count: int,
) -> dict[str, Any]:
    payload = project.model_dump(mode="json")
    payload["conversation_count"] = conversation_count
    payload["is_default"] = (
        project.id
        == _get_library_store().get_workspace(project.workspace_id).default_project_id
    )
    return payload


def _library_workspace_payload(
    workspace: WorkspaceRecord,
    *,
    project_count: int,
    conversation_count: int,
) -> dict[str, Any]:
    payload = workspace.model_dump(mode="json")
    payload.update(
        {
            "project_count": project_count,
            "conversation_count": conversation_count,
            "is_default": workspace.id
            == _get_library_store().snapshot().default_workspace_id,
        }
    )
    return payload


@app.get("/v1/workspaces")
async def list_library_workspaces() -> dict[str, Any]:
    """List lightweight household contexts; this is not an authentication boundary."""
    try:
        library = _get_library_store()
        conversation_counts = library.conversation_counts()
        workspaces = library.list_workspaces()
        return {
            "default_workspace_id": library.snapshot().default_workspace_id,
            "workspaces": [
                _library_workspace_payload(
                    workspace,
                    project_count=len(
                        library.list_projects(workspace_id=workspace.id)
                    ),
                    conversation_count=sum(
                        conversation_counts.get(project.id, 0)
                        for project in library.list_projects(workspace_id=workspace.id)
                    ),
                )
                for workspace in workspaces
            ],
        }
    except LibraryStoreError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.post("/v1/workspaces", status_code=201)
async def create_library_workspace(request: WorkspaceCreateRequest) -> dict[str, Any]:
    """Create one contextual partition and its first project in one atomic update."""
    try:
        workspace, project = _get_library_store().create_workspace(request.name)
        _get_context_manager().get_or_create(project.context_id, mode="fiction")
        return {
            **_library_workspace_payload(
                workspace,
                project_count=1,
                conversation_count=0,
            ),
            "default_project": _library_project_payload(
                project,
                conversation_count=0,
            ),
        }
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except LibraryStoreError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.patch("/v1/workspaces/{workspace_id}")
async def update_library_workspace(
    workspace_id: str,
    request: WorkspaceUpdateRequest,
) -> dict[str, Any]:
    """Rename a workspace or update its operational backup policy."""
    try:
        library = _get_library_store()
        workspace = library.update_workspace(
            workspace_id,
            name=request.name,
            backup_enabled=request.backup_enabled,
            backup_subdirectory=request.backup_subdirectory,
            backup_schedule=request.backup_schedule,
            backup_hour_utc=request.backup_hour_utc,
            backup_retention_count=request.backup_retention_count,
        )
        projects = library.list_projects(workspace_id=workspace.id)
        counts = library.conversation_counts()
        return _library_workspace_payload(
            workspace,
            project_count=len(projects),
            conversation_count=sum(counts.get(project.id, 0) for project in projects),
        )
    except WorkspaceNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except LibraryStoreError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.get("/v1/workspaces/{workspace_id}/backups")
async def list_workspace_backups(workspace_id: str) -> dict[str, Any]:
    """List backup archives and destination state for one workspace."""
    try:
        _get_library_store().get_workspace(workspace_id)
        manager = _get_backup_manager()
        return {
            "workspace_id": workspace_id,
            "destination": manager.backup_root_status(),
            "backups": manager.list(workspace_id),
        }
    except WorkspaceNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except BackupError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@app.post("/v1/workspaces/{workspace_id}/backups", status_code=201)
async def create_workspace_backup(workspace_id: str) -> dict[str, Any]:
    """Create and immediately verify an exact workspace archive."""
    try:
        return _get_backup_manager().create(workspace_id)
    except WorkspaceNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except BackupError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@app.post("/v1/workspaces/{workspace_id}/backups/{filename}/verify")
async def verify_workspace_backup(workspace_id: str, filename: str) -> dict[str, Any]:
    """Verify the archive manifest, every payload digest, and the outer checksum."""
    try:
        manager = _get_backup_manager()
        result = manager.verify(manager.resolve_archive(workspace_id, filename))
        if result["workspace_id"] != workspace_id:
            raise BackupError("backup manifest belongs to another workspace")
        return result
    except WorkspaceNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except BackupError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.post("/v1/workspaces/{workspace_id}/backups/{filename}/restore-test")
async def restore_test_workspace_backup(workspace_id: str, filename: str) -> dict[str, Any]:
    """Restore into an isolated temporary root and load real durable records."""
    try:
        manager = _get_backup_manager()
        result = manager.restore_test(manager.resolve_archive(workspace_id, filename))
        if result["workspace_id"] != workspace_id:
            raise BackupError("backup manifest belongs to another workspace")
        return result
    except WorkspaceNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except BackupError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.get("/v1/projects")
async def list_library_projects(
    include_archived: bool = True,
    workspace_id: str | None = None,
) -> dict[str, Any]:
    """List writing projects and their conversation counts."""
    try:
        library = _get_library_store()
        # A legacy session may have appeared since process initialization.
        legacy_ids = [item["id"] for item in _get_default_session_store().list_summaries()]
        library.sync_legacy_sessions(legacy_ids)
        counts = library.conversation_counts()
        workspace = library.get_workspace(workspace_id)
        projects = library.list_projects(
            include_archived=include_archived,
            workspace_id=workspace.id,
        )
        return {
            "workspace_id": workspace.id,
            "default_project_id": workspace.default_project_id,
            "projects": [
                _library_project_payload(
                    project,
                    conversation_count=counts.get(project.id, 0),
                )
                for project in projects
            ],
        }
    except WorkspaceNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except LibraryStoreError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.post("/v1/projects", status_code=201)
async def create_library_project(request: ProjectCreateRequest) -> dict[str, Any]:
    """Create an isolated fiction project without changing the current one."""
    try:
        project = _get_library_store().create_project(
            request.name,
            workspace_id=request.workspace_id,
        )
        _get_context_manager().get_or_create(project.context_id, mode="fiction")
        return _library_project_payload(project, conversation_count=0)
    except WorkspaceNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except LibraryStoreError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.patch("/v1/projects/{project_id}")
async def update_library_project(
    project_id: str,
    request: ProjectUpdateRequest,
) -> dict[str, Any]:
    """Rename or archive a writing project; project content is retained."""
    try:
        project = _get_library_store().update_project(
            project_id,
            name=request.name,
            archived=request.archived,
        )
        count = _get_library_store().conversation_counts().get(project.id, 0)
        return _library_project_payload(project, conversation_count=count)
    except ProjectNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except LibraryStoreError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


def _validate_manuscript_artifact(
    project_id: str,
    artifact_id: str | None,
) -> None:
    if artifact_id and not _get_artifact_store(project_id).exists(artifact_id):
        raise HTTPException(status_code=422, detail="manuscript artifact not found")


def _word_count(content: str) -> int:
    return len(re.findall(r"\b[\w’'-]+\b", content, flags=re.UNICODE))


def _safe_export_name(name: str, *, fallback: str = "marginalia-manuscript") -> str:
    safe = re.sub(r"[^a-zA-Z0-9._-]+", "-", name.strip()).strip("-.")
    return safe or fallback


def _compile_manuscript_payload(project_id: str) -> dict[str, Any]:
    project = _project_record(project_id)
    nodes, version = _get_manuscript_store(project.id).ordered_depth_first()
    artifact_store = _get_artifact_store(project.id)
    heading_levels = {"part": 1, "chapter": 2, "scene": 3}
    lines: list[str] = []
    sections: list[dict[str, Any]] = []
    missing_artifact_ids: list[str] = []
    total_words = 0
    for node in nodes:
        level = heading_levels[node.kind]
        lines.extend([f"{'#' * level} {node.title}", ""])
        content = ""
        if node.artifact_id:
            try:
                _, content, _ = artifact_store.get(node.artifact_id)
            except Exception:
                missing_artifact_ids.append(node.artifact_id)
            if content:
                lines.extend([content.rstrip(), ""])
        words = _word_count(content)
        total_words += words
        sections.append(
            {
                "node": node.model_dump(mode="json"),
                "content": content,
                "word_count": words,
            }
        )
    markdown = "\n".join(lines).rstrip() + ("\n" if lines else "")
    return {
        "project_id": project.id,
        "project_name": project.name,
        "manuscript_version": version,
        "node_count": len(nodes),
        "word_count": total_words,
        "missing_artifact_ids": missing_artifact_ids,
        "markdown": markdown,
        "sections": sections,
    }


def _docx_from_manuscript(payload: dict[str, Any]) -> bytes:
    """Build a minimal standards-compliant DOCX using only the standard library."""
    paragraphs: list[str] = []
    heading_styles = {"part": "Title", "chapter": "Heading1", "scene": "Heading2"}

    def paragraph(text: str, style: str | None = None) -> str:
        style_xml = f'<w:pPr><w:pStyle w:val="{style}"/></w:pPr>' if style else ""
        if not text:
            return f"<w:p>{style_xml}</w:p>"
        return (
            f"<w:p>{style_xml}<w:r><w:t xml:space=\"preserve\">"
            f"{xml_escape(text)}</w:t></w:r></w:p>"
        )

    for section in payload["sections"]:
        node = section["node"]
        paragraphs.append(paragraph(node["title"], heading_styles[node["kind"]]))
        for line in section["content"].splitlines():
            paragraphs.append(paragraph(line))

    document_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        f"<w:body>{''.join(paragraphs)}<w:sectPr/></w:body></w:document>"
    )
    content_types = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        '<Default Extension="xml" ContentType="application/xml"/>'
        '<Override PartName="/word/document.xml" '
        'ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
        "</Types>"
    )
    relationships = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" '
        'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" '
        'Target="word/document.xml"/>'
        "</Relationships>"
    )
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", content_types)
        archive.writestr("_rels/.rels", relationships)
        archive.writestr("word/document.xml", document_xml)
    return output.getvalue()


@app.get("/v1/manuscript")
async def get_manuscript(project_id: str | None = None) -> dict[str, Any]:
    """Return the selected project's ordered manuscript nodes."""
    project = _project_record(project_id)
    try:
        nodes, version = _get_manuscript_store(project.id).list_nodes()
        return {
            "project_id": project.id,
            "version": version,
            "nodes": [node.model_dump(mode="json") for node in nodes],
        }
    except ManuscriptStoreError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.get("/v1/manuscript/compile", response_model=None)
async def compile_manuscript(
    project_id: str | None = None,
    format: str = "markdown",
) -> dict[str, Any] | Response:
    """Compile the ordered manuscript to Markdown data or a DOCX download."""
    project = _project_record(project_id)
    if format not in {"markdown", "docx"}:
        raise HTTPException(status_code=422, detail="format must be markdown or docx")
    payload = _compile_manuscript_payload(project.id)
    filename = _safe_export_name(project.name)
    if format == "docx":
        return Response(
            content=_docx_from_manuscript(payload),
            media_type=(
                "application/vnd.openxmlformats-officedocument."
                "wordprocessingml.document"
            ),
            headers={
                "Content-Disposition": f'attachment; filename="{filename}.docx"'
            },
        )
    payload["filename"] = f"{filename}.md"
    return payload


@app.post("/v1/manuscript", status_code=201)
async def create_manuscript_node(
    request: ManuscriptCreateRequest,
) -> dict[str, Any]:
    """Add an ordered part, chapter, or scene reference."""
    project = _project_record(request.project_id)
    _validate_manuscript_artifact(project.id, request.artifact_id)
    try:
        node = _get_manuscript_store(project.id).create(
            kind=request.kind,
            title=request.title,
            parent_id=request.parent_id,
            artifact_id=request.artifact_id,
            status=request.status,
        )
        return node.model_dump(mode="json")
    except ManuscriptNodeNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except ManuscriptStoreError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.patch("/v1/manuscript/{node_id}")
async def update_manuscript_node(
    node_id: str,
    request: ManuscriptUpdateRequest,
) -> dict[str, Any]:
    """Rename, relink, or update the drafting status of a manuscript node."""
    project = _project_record(request.project_id)
    if request.set_artifact:
        _validate_manuscript_artifact(project.id, request.artifact_id)
    try:
        node = _get_manuscript_store(project.id).update(
            node_id,
            title=request.title,
            artifact_id=request.artifact_id,
            set_artifact=request.set_artifact,
            status=request.status,
        )
        return node.model_dump(mode="json")
    except ManuscriptNodeNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except ManuscriptStoreError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.post("/v1/manuscript/{node_id}/move")
async def move_manuscript_node(
    node_id: str,
    request: ManuscriptMoveRequest,
) -> dict[str, Any]:
    """Move a manuscript node to an explicit parent and sibling position."""
    project = _project_record(request.project_id)
    try:
        node = _get_manuscript_store(project.id).move(
            node_id,
            parent_id=request.parent_id,
            position=request.position,
        )
        return node.model_dump(mode="json")
    except ManuscriptNodeNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except ManuscriptStoreError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.delete("/v1/manuscript/{node_id}")
async def delete_manuscript_node(
    node_id: str,
    project_id: str | None = None,
) -> dict[str, Any]:
    """Delete an empty manuscript node; artifacts and child nodes are retained."""
    project = _project_record(project_id)
    try:
        if not _get_manuscript_store(project.id).delete(node_id):
            raise HTTPException(status_code=404, detail="manuscript node not found")
        return {"success": True}
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ManuscriptStoreError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.get("/v1/project")
async def get_creative_project(project_id: str | None = None) -> dict[str, Any]:
    """Return creative direction for the active fiction context."""
    try:
        return _project_payload(_get_creative_project_store(project_id).get())
    except CreativeProjectContextMismatch as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    except CreativeProjectError as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@app.put("/v1/project")
async def update_creative_project(
    request: CreativeProjectUpdateRequest,
) -> dict[str, Any]:
    """Atomically replace creative direction for the active fiction context."""
    try:
        config = _get_creative_project_store(request.project_id).update(
            project_brief=request.project_brief,
            collaborator_stance=request.collaborator_stance,
            voice_style_guidance=request.voice_style_guidance,
            expected_version=request.expected_version,
        )
        return _project_payload(config)
    except CreativeProjectVersionConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    except CreativeProjectContextMismatch as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    except CreativeProjectError as exc:
        raise HTTPException(status_code=500, detail=str(exc))


async def _build_project_export(project_id: str | None = None) -> dict[str, Any]:
    """Build one complete, portable project payload."""
    library_project = _project_record(project_id)
    project = _get_creative_project_store(project_id).get()
    story_bible = {
        "characters": (await list_characters(project_id)).get("characters", []),
        "world_rules": (await list_world_rules(project_id)).get("rules", []),
        "negative_constraints": (await list_forbidden(project_id)).get("forbidden", []),
    }

    sessions: list[dict[str, Any]] = []
    session_store = _get_session_store(project_id)
    for summary in session_store.list_summaries():
        session = session_store.get(summary["id"])
        if session is not None:
            payload = session.to_dict()
            try:
                lifecycle = _get_library_store().get_conversation(session.id)
                payload["lifecycle"] = lifecycle.model_dump(mode="json")
            except ConversationLifecycleNotFoundError:
                payload["lifecycle"] = None
            sessions.append(payload)

    artifacts = []
    artifact_store = _get_artifact_store(project_id)
    summaries, _ = artifact_store.list_all()
    for summary in summaries:
        meta, _, _ = artifact_store.get(summary.id)
        revisions = []
        for version in meta.versions:
            revisions.append(
                {
                    "version": version.version,
                    "created_at": version.created_at,
                    "source": version.source,
                    "content": artifact_store.get_version(meta.id, version.version),
                }
            )
        artifacts.append(
            {
                "id": meta.id,
                "title": meta.title,
                "kind": meta.kind,
                "artifact_type": meta.artifact_type,
                "project_id": meta.project_id or library_project.id,
                "provenance": meta.provenance.model_dump(mode="json"),
                "status": meta.status,
                "tags": list(meta.tags),
                "trashed_at": meta.trashed_at,
                "language": meta.language,
                "current_version": meta.current_version,
                "created_at": meta.created_at,
                "updated_at": meta.updated_at,
                "revisions": revisions,
            }
        )

    manuscript_nodes, manuscript_version = _get_manuscript_store(project_id).list_nodes()
    reviews = _get_canon_review_store(project_id).list(status="all")

    return {
        "schema": "marginalia.creative-project-export/v1",
        "exported_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "project": {
            "id": library_project.id,
            "name": library_project.name,
            "project_brief": project.project_brief,
            "collaborator_stance": project.collaborator_stance,
            "voice_style_guidance": project.voice_style_guidance,
        },
        "story_bible": story_bible,
        "conversations": sessions,
        "artifacts": artifacts,
        "manuscript": {
            "version": manuscript_version,
            "nodes": [node.model_dump(mode="json") for node in manuscript_nodes],
        },
        "canon_review": [item.model_dump(mode="json") for item in reviews],
    }


@app.get("/v1/project/export")
async def export_creative_project(project_id: str | None = None) -> dict[str, Any]:
    """Export every writer-owned project record as portable JSON."""
    return await _build_project_export(project_id)


def _project_export_zip(payload: dict[str, Any]) -> bytes:
    """Create a readable project bundle without third-party document tooling."""
    project = payload["project"]
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(
            "README.txt",
            "Marginalia project export\n\n"
            "Open manuscript.md for the compiled draft. JSON contains the complete "
            "portable record, including provenance and revisions.\n",
        )
        archive.writestr(
            "project-direction.md",
            f"# {project['name']}\n\n"
            f"## Project brief\n\n{project['project_brief'] or '_Not set._'}\n\n"
            f"## Collaborator stance\n\n{project['collaborator_stance'] or '_Not set._'}\n\n"
            f"## Voice and style\n\n{project['voice_style_guidance'] or '_Not set._'}\n",
        )
        bible = payload["story_bible"]
        canon_lines = ["# Canon", "", "## Characters", ""]
        for character in bible["characters"]:
            canon_lines.extend(
                [
                    f"### {character.get('name', 'Character')}",
                    "",
                    character.get("description", ""),
                    "",
                ]
            )
        canon_lines.extend(["## World rules", ""])
        canon_lines.extend(f"- {item.get('rule', '')}" for item in bible["world_rules"])
        canon_lines.extend(["", "## Negative constraints", ""])
        canon_lines.extend(
            f"- {item.get('description', '')}" for item in bible["negative_constraints"]
        )
        archive.writestr("canon.md", "\n".join(canon_lines).rstrip() + "\n")

        artifact_lookup: dict[str, dict[str, Any]] = {}
        for artifact in payload["artifacts"]:
            artifact_lookup[artifact["id"]] = artifact
            latest = artifact["revisions"][-1]["content"] if artifact["revisions"] else ""
            folder = artifact["artifact_type"].replace("_", "-")
            filename = _safe_export_name(artifact["title"], fallback=artifact["id"])
            archive.writestr(f"artifacts/{folder}/{filename}.md", latest)

        manuscript_lines: list[str] = []
        nodes = {node["id"]: node for node in payload["manuscript"]["nodes"]}
        children: dict[str | None, list[dict[str, Any]]] = {}
        for node in nodes.values():
            children.setdefault(node.get("parent_id"), []).append(node)
        for siblings in children.values():
            siblings.sort(key=lambda item: (item.get("position", 0), item["id"]))

        def render_manuscript(parent_id: str | None, depth: int) -> None:
            for node in children.get(parent_id, []):
                manuscript_lines.extend(
                    [f"{'#' * min(depth + 1, 6)} {node['title']}", ""]
                )
                artifact = artifact_lookup.get(node.get("artifact_id"))
                if artifact and artifact["revisions"]:
                    manuscript_lines.extend([artifact["revisions"][-1]["content"], ""])
                render_manuscript(node["id"], depth + 1)

        render_manuscript(None, 0)
        archive.writestr("manuscript.md", "\n".join(manuscript_lines).rstrip() + "\n")

        for conversation in payload["conversations"]:
            lines = [f"# {conversation['title']}", ""]
            for message in conversation.get("messages", []):
                lines.extend(
                    [f"## {message['role'].title()} · {message['timestamp']}", "", message["content"], ""]
                )
            filename = _safe_export_name(conversation["title"], fallback=conversation["id"])
            archive.writestr(f"conversations/{filename}.md", "\n".join(lines).rstrip() + "\n")

        archive.writestr(
            "marginalia-project.json",
            json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
        )
    return stream.getvalue()


@app.get("/v1/project/export.zip", response_model=None)
async def export_creative_project_zip(project_id: str | None = None) -> Response:
    """Download a writer-readable ZIP plus the complete portable JSON record."""
    payload = await _build_project_export(project_id)
    name = _safe_export_name(payload["project"]["name"], fallback="marginalia-project")
    return Response(
        content=_project_export_zip(payload),
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{name}.zip"'},
    )


@app.get("/v1/project/snapshots")
async def list_project_snapshots(project_id: str | None = None) -> dict[str, Any]:
    """List immutable named project checkpoints."""
    project = _project_record(project_id)
    try:
        snapshots = _get_snapshot_store(project.id).list()
        return {
            "project_id": project.id,
            "snapshots": [item.model_dump(mode="json") for item in snapshots],
        }
    except SnapshotStoreError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.post("/v1/project/snapshots", status_code=201)
async def create_project_snapshot(request: SnapshotCreateRequest) -> dict[str, Any]:
    """Capture a complete immutable project checkpoint."""
    project = _project_record(request.project_id)
    try:
        snapshot = _get_snapshot_store(project.id).create(
            name=request.name,
            payload=await _build_project_export(project.id),
        )
        return snapshot.model_dump(mode="json")
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except SnapshotStoreError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.get("/v1/project/snapshots/{snapshot_id}")
async def get_project_snapshot(
    snapshot_id: str,
    project_id: str | None = None,
) -> dict[str, Any]:
    """Return one checkpoint after verifying its content hash."""
    project = _project_record(project_id)
    try:
        snapshot, payload = _get_snapshot_store(project.id).get(snapshot_id)
        return {"snapshot": snapshot.model_dump(mode="json"), "payload": payload}
    except SnapshotNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except SnapshotStoreError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


def _search_snippet(text: str, query: str, *, radius: int = 90) -> str:
    """Return a compact plain-text excerpt around the first match."""
    flattened = " ".join(text.split())
    position = flattened.casefold().find(query.casefold())
    if position < 0:
        return flattened[: radius * 2] + ("…" if len(flattened) > radius * 2 else "")
    start = max(0, position - radius)
    end = min(len(flattened), position + len(query) + radius)
    return ("…" if start else "") + flattened[start:end] + ("…" if end < len(flattened) else "")


async def _search_project_records(project_id: str, query: str) -> list[dict[str, Any]]:
    normalized = query.strip()
    folded = normalized.casefold()
    results: list[dict[str, Any]] = []
    library = _get_library_store()
    session_store = _get_session_store(project_id)
    for summary in session_store.list_summaries():
        session = session_store.get(summary["id"])
        if session is None:
            continue
        try:
            lifecycle = library.get_conversation(session.id)
        except ConversationLifecycleNotFoundError:
            continue
        if folded in session.title.casefold():
            results.append(
                {
                    "kind": "conversation",
                    "id": session.id,
                    "title": session.title,
                    "snippet": f"{session.message_count} messages",
                    "conversation_id": session.id,
                    "message_id": None,
                    "artifact_id": None,
                    "archived": lifecycle.archived,
                    "updated_at": session.updated_at,
                }
            )
        for message in session.messages:
            if folded in message.content.casefold():
                results.append(
                    {
                        "kind": "message",
                        "id": message.id,
                        "title": session.title,
                        "snippet": _search_snippet(message.content, normalized),
                        "conversation_id": session.id,
                        "message_id": message.id,
                        "artifact_id": None,
                        "archived": lifecycle.archived,
                        "updated_at": message.timestamp,
                    }
                )

    artifact_store = _get_artifact_store(project_id)
    artifact_summaries, _ = artifact_store.list_all()
    for summary in artifact_summaries:
        _, content, _ = artifact_store.get(summary.id)
        searchable = "\n".join([summary.title, content, " ".join(summary.tags)])
        if folded in searchable.casefold():
            results.append(
                {
                    "kind": "artifact",
                    "id": summary.id,
                    "title": summary.title,
                    "snippet": _search_snippet(content or " ".join(summary.tags), normalized),
                    "conversation_id": summary.provenance.conversation_id,
                    "message_id": None,
                    "artifact_id": summary.id,
                    "trashed": summary.trashed_at is not None,
                    "updated_at": summary.updated_at,
                }
            )

    bible = {
        "canon_character": (await list_characters(project_id)).get("characters", []),
        "canon_rule": (await list_world_rules(project_id)).get("rules", []),
        "canon_constraint": (await list_forbidden(project_id)).get("forbidden", []),
    }
    for kind, records in bible.items():
        for record in records:
            title = record.get("name") or ("World rule" if kind == "canon_rule" else "Boundary")
            text = " ".join(str(value) for value in record.values() if value)
            if folded in text.casefold():
                results.append(
                    {
                        "kind": kind,
                        "id": record.get("id", title),
                        "title": title,
                        "snippet": _search_snippet(text, normalized),
                        "conversation_id": None,
                        "message_id": None,
                        "artifact_id": None,
                        "updated_at": "",
                    }
                )

    manuscript_nodes, _ = _get_manuscript_store(project_id).list_nodes()
    for node in manuscript_nodes:
        if folded in node.title.casefold():
            results.append(
                {
                    "kind": "manuscript",
                    "id": node.id,
                    "title": node.title,
                    "snippet": f"{node.kind.title()} · {node.status}",
                    "conversation_id": None,
                    "message_id": None,
                    "artifact_id": node.artifact_id,
                    "updated_at": node.updated_at,
                }
            )
    results.sort(key=lambda item: item.get("updated_at", ""), reverse=True)
    return results


@app.get("/v1/search")
async def search_project(
    q: str,
    project_id: str | None = None,
    limit: int = 100,
) -> dict[str, Any]:
    """Search conversations, artifacts, manuscript structure, and accepted canon."""
    project = _project_record(project_id)
    query = q.strip()
    if not query:
        return {"project_id": project.id, "query": "", "results": [], "total": 0}
    bounded_limit = max(1, min(limit, 250))
    results = await _search_project_records(project.id, query)
    return {
        "project_id": project.id,
        "query": query,
        "results": results[:bounded_limit],
        "total": len(results),
    }


@app.get("/v1/entities")
async def list_project_entities(project_id: str | None = None) -> dict[str, Any]:
    """Return accepted characters with explicit references back into exploration."""
    project = _project_record(project_id)
    characters = (await list_characters(project.id)).get("characters", [])
    entities = []
    for character in characters:
        pattern = re.compile(rf"(?<!\w){re.escape(character['name'])}(?!\w)", re.IGNORECASE)
        references = [
            item
            for item in await _search_project_records(project.id, character["name"])
            if item["kind"] in {"conversation", "message", "artifact", "manuscript"}
            and pattern.search(f"{item['title']} {item['snippet']}")
        ]
        entities.append(
            {
                **character,
                "reference_count": len(references),
                "references": references,
            }
        )
    return {"project_id": project.id, "entities": entities}


_LENGTH_BANDS = {
    "short": (0, 800),
    "medium": (800, 2000),
    "long": (2000, 10000),
}

CONSTRAINTS_FMT_VER = 1
_turn_seq = 0

# ── Config resolution tables ───────────────────────────────────────────────

_CONFIG_DEFAULTS: dict[str, dict[str, Any]] = {
    "code": {
        "artifact_type": None,
        "length": "medium",
        "voice": None,
        "citations": None,
        "personal_material": None,
        "format": {},
        "audience": None,
        "bans": None,
        "structure": None,
        "strict": False,
    },
    "research": {
        "artifact_type": None,
        "length": "medium",
        "voice": None,
        "citations": None,
        "personal_material": None,
        "format": {},
        "audience": None,
        "bans": None,
        "structure": None,
        "strict": False,
    },
}

# System hard constraints — override session/default unconditionally.
# MVP: empty.  Ready for C3 / admin policy.
_SYSTEM_CONSTRAINTS: dict[str, dict[str, Any]] = {
    "code": {},
    "research": {},
}


def _resolve_config_fields(mode: str) -> tuple[list[dict], dict, dict]:
    """Core config resolution: defaults → session → system clamps.

    Returns ``(fields, contract_dict, diagnostics)``.
    *fields* is an ordered list of ``{key, value, source, default_value, clamped}``.
    *contract_dict* is the raw ``state["contract"]`` for hash access.
    *diagnostics* contains ``clamped_fields``, ``unknown_keys``, ``warnings``.
    Raises on store read failure (caller handles).
    """
    defaults = _CONFIG_DEFAULTS.get(mode, {})
    system = _SYSTEM_CONSTRAINTS.get(mode, {})

    # ── Read session config from project store ──────────────────────────
    store = _get_project_store() if mode == "code" else _get_research_project_store()
    state = store.get_state()
    contract = state.get("contract", {})
    session_config = contract.get("config") or {}

    # ── Detect unknown keys ─────────────────────────────────────────────
    known_keys = set(defaults.keys())
    unknown_keys = sorted(k for k in session_config if k not in known_keys)

    # ── Merge: defaults → session → system ──────────────────────────────
    fields: list[dict] = []
    clamped_fields: list[dict] = []

    for key, default_value in defaults.items():
        value = default_value
        source = "default"

        if key in session_config:
            value = session_config[key]
            source = "session"

        clamped = False
        if key in system:
            requested = value
            value = system[key]
            source = "system"
            if requested != value:
                clamped = True
                clamped_fields.append(
                    {
                        "key": key,
                        "requested": requested,
                        "effective": value,
                        "source": "system",
                    }
                )

        fields.append(
            {
                "key": key,
                "value": value,
                "source": source,
                "default_value": default_value,
                "clamped": clamped,
            }
        )

    diagnostics = {
        "clamped_fields": clamped_fields,
        "unknown_keys": unknown_keys,
        "warnings": [],
    }
    return fields, contract, diagnostics


def _build_constraints_message() -> tuple[dict[str, str] | None, dict]:
    """Build a system message from the active project's config constraints.

    Returns (constraints_msg, contract_meta) where constraints_msg is
    {"role": "system", "content": ...} or None, and contract_meta has
    config_hash, config_hash_full, strict, mode for receipt building.
    Only applies in code or research modes.
    """
    mode = GOVERNOR_MODE
    _meta_base = {"mode": mode, "strict": False}
    if mode not in ("code", "research"):
        return None, _meta_base

    try:
        fields, contract, _diag = _resolve_config_fields(mode)
    except Exception as e:
        return None, {**_meta_base, "receipt_error": f"store_read_failed: {e}"}

    config = contract.get("config")
    if config:
        # Build typed constraints block from RESOLVED values
        resolved = {f["key"]: f["value"] for f in fields}
        lines = []
        hash_tag = contract.get("config_hash", "")
        hash_full = contract.get("config_hash_full", "")
        lines.append(f"[CONSTRAINTS config_hash={hash_tag}]")

        if resolved.get("artifact_type"):
            lines.append(f"artifact_type: {resolved['artifact_type']}")

        length = resolved.get("length", "medium")
        lo, hi = _LENGTH_BANDS.get(length, (800, 2000))
        lines.append(f"length_band: {length}")
        lines.append(f"length_min_words: {lo}")
        lines.append(f"length_max_words: {hi}")

        if resolved.get("voice"):
            v = resolved["voice"]
            lines.append(f"voice: {', '.join(v) if isinstance(v, list) else v}")

        if resolved.get("citations"):
            lines.append(f"citations: {resolved['citations']}")

        if resolved.get("personal_material"):
            lines.append(f"personal_material: {resolved['personal_material']}")

        fmt = resolved.get("format", {})
        if isinstance(fmt, dict):
            for key in ("tables", "bullets", "headings"):
                if key in fmt:
                    lines.append(f"format_{key}: {str(fmt[key]).lower()}")

        if resolved.get("audience"):
            lines.append(f"audience: {resolved['audience']}")

        if resolved.get("bans"):
            bans = resolved["bans"]
            lines.append(f"bans: {'; '.join(bans) if isinstance(bans, list) else bans}")

        if resolved.get("structure"):
            s = resolved["structure"]
            lines.append(f"structure: {', '.join(s) if isinstance(s, list) else s}")

        strict = resolved.get("strict", False)
        lines.append(f"strict: {str(strict).lower()}")

        lines.append("[/CONSTRAINTS]")
        meta = {
            "config_hash": hash_tag or None,
            "config_hash_full": hash_full or None,
            "strict": strict,
            "mode": mode,
        }
        return {"role": "system", "content": "\n".join(lines)}, meta

    # Fallback: raw constraints list (legacy, no resolver)
    constraints = contract.get("constraints", [])
    if constraints:
        block = "[CONSTRAINTS]\n" + "\n".join(constraints) + "\n[/CONSTRAINTS]"
        meta = {
            "config_hash": contract.get("config_hash"),
            "config_hash_full": contract.get("config_hash_full"),
            "strict": False,
            "mode": mode,
        }
        return {"role": "system", "content": block}, meta

    return None, _meta_base


@app.post("/v1/chat/completions", response_model=None)
async def chat_completions(
    request: ChatCompletionRequest,
) -> ChatCompletionResponse | StreamingResponse:
    """OpenAI-compatible chat completions endpoint.

    Delegates to the governor daemon for the full governed pipeline:
    pending check → augment → generate → check → receipt.
    """
    selected_model, model_identity = _resolve_configured_model(request.model)
    governed_chat = _get_governed_chat_adapter(request.project_id)
    messages = [{"role": m.role, "content": m.content} for m in request.messages]

    # Persistent creative direction is part of the same message list AG governs;
    # it is not a parallel prompt or an ungoverned provider call.
    try:
        project_context = _build_project_context_message(request.project_id)
    except CreativeProjectError as exc:
        raise HTTPException(status_code=500, detail=f"Project state error: {exc}")
    if project_context:
        insert_idx = 0
        for i, message in enumerate(messages):
            if message["role"] == "system":
                insert_idx = i + 1
        messages.insert(insert_idx, project_context)

    # Retained donor constraint blocks are relevant only when explicitly
    # running old code/research tests; normal Marginalia is fiction-only.
    constraints_msg, contract_meta = _build_constraints_message()
    if constraints_msg:
        insert_idx = 0
        for i, m in enumerate(messages):
            if m["role"] == "system":
                insert_idx = i + 1
        messages.insert(insert_idx, constraints_msg)

    if request.stream:
        return StreamingResponse(
            _stream_via_daemon(governed_chat, messages, selected_model, model_identity),
            media_type="text/event-stream",
        )

    try:
        result = await governed_chat.chat_send(
            messages=messages,
            model=selected_model,
        )
    except DaemonAuthError as e:
        raise HTTPException(
            status_code=401,
            detail=(
                "Claude Code is not logged in. "
                "Run `claude /login` in a terminal to re-authenticate."
            ),
        )
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Daemon error: {e}")

    # If daemon returned a pending violation, format as violation prompt
    if result.get("pending"):
        return _format_violation_pending_response(
            result,
            result.get("model") or selected_model,
            model_identity,
        )

    # Build content with optional governor footer
    content = result.get("content", "")
    footer = result.get("footer")
    if footer:
        content = f"{content}\n\n{footer}"

    # AG's final authority receipt is the only governed-execution receipt.
    resolved_model = result.get("model", selected_model)
    if model_identity is not None and resolved_model != selected_model:
        raise HTTPException(
            status_code=502,
            detail="daemon returned a different model than the explicit selection",
        )
    request_id = f"chatcmpl-{uuid.uuid4().hex[:8]}"
    receipt = result["receipt"]

    usage_data = result.get("usage") or {}
    return ChatCompletionResponse(
        id=request_id,
        created=int(time.time()),
        model=resolved_model,
        provider_id=model_identity.provider_id if model_identity else None,
        model_id=model_identity.model_id if model_identity else None,
        choices=[
            ChatCompletionChoice(
                index=0,
                message=ChatMessage(role="assistant", content=content),
                finish_reason="stop",
            )
        ],
        usage=Usage(
            prompt_tokens=usage_data.get("prompt_tokens", 0),
            completion_tokens=usage_data.get("completion_tokens", 0),
            total_tokens=usage_data.get("total_tokens", 0),
        ),
        receipt=receipt,
    )


async def _stream_via_daemon(
    governed_chat: GovernedChatAdapter,
    messages: list[dict[str, str]],
    model: str,
    model_identity: ConfiguredModel | None = None,
) -> AsyncGenerator[str, None]:
    """Stream response via daemon in OpenAI SSE format."""
    request_id = f"chatcmpl-{uuid.uuid4().hex[:8]}"
    final_result: dict | None = None
    turn_id = f"turn-{uuid.uuid4().hex[:12]}"

    try:
        async for delta, final in governed_chat.chat_stream(messages=messages, model=model):
            if delta:
                sse_chunk = {
                    "id": request_id,
                    "object": "chat.completion.chunk",
                    "created": int(time.time()),
                    "model": model,
                    "provider_id": model_identity.provider_id if model_identity else None,
                    "model_id": model_identity.model_id if model_identity else None,
                    "choices": [
                        {
                            "index": 0,
                            "delta": {"content": delta},
                            "finish_reason": None,
                        }
                    ],
                }
                yield f"data: {json.dumps(sse_chunk)}\n\n"
            if final is not None:
                final_result = final

        if final_result is None:
            raise RuntimeError("AG stream ended without a final governed outcome")

        resolved_model = final_result.get("model") or model
        if model_identity is not None and resolved_model != model:
            raise RuntimeError(
                "daemon returned a different model than the explicit selection"
            )
        receipt = final_result["receipt"]

        # If daemon returned a pending violation, emit it as a final chunk
        if final_result and final_result.get("pending"):
            pending = final_result["pending"]
            prompt = governed_chat.format_pending_message(pending)
            sse_chunk = {
                "id": request_id,
                "object": "chat.completion.chunk",
                "created": int(time.time()),
                "model": resolved_model,
                "provider_id": model_identity.provider_id if model_identity else None,
                "model_id": model_identity.model_id if model_identity else None,
                "choices": [
                    {
                        "index": 0,
                        "delta": {"content": f"\n\n{prompt}"},
                        "finish_reason": None,
                    }
                ],
            }
            yield f"data: {json.dumps(sse_chunk)}\n\n"

        # If daemon returned a footer, emit it
        if final_result and final_result.get("footer"):
            sse_chunk = {
                "id": request_id,
                "object": "chat.completion.chunk",
                "created": int(time.time()),
                "model": resolved_model,
                "provider_id": model_identity.provider_id if model_identity else None,
                "model_id": model_identity.model_id if model_identity else None,
                "choices": [
                    {
                        "index": 0,
                        "delta": {"content": f"\n\n{final_result['footer']}"},
                        "finish_reason": None,
                    }
                ],
            }
            yield f"data: {json.dumps(sse_chunk)}\n\n"

        # Final done chunk carries AG's authoritative linkage for API clients.
        done_chunk = {
            "id": request_id,
            "object": "chat.completion.chunk",
            "created": int(time.time()),
            "model": resolved_model,
            "provider_id": model_identity.provider_id if model_identity else None,
            "model_id": model_identity.model_id if model_identity else None,
            "choices": [
                {
                    "index": 0,
                    "delta": {},
                    "finish_reason": "stop",
                }
            ],
            "turn_id": turn_id,
            "receipt": receipt,
        }
        yield f"data: {json.dumps(done_chunk)}\n\n"
        yield "data: [DONE]\n\n"

    except DaemonAuthError:
        error_chunk = {
            "error": {
                "message": (
                    "Claude Code is not logged in. "
                    "Run `claude /login` in a terminal to re-authenticate."
                ),
                "type": "auth_error",
            }
        }
        yield f"data: {json.dumps(error_chunk)}\n\n"
    except Exception as e:
        error_chunk = {"error": {"message": str(e), "type": "server_error"}}
        yield f"data: {json.dumps(error_chunk)}\n\n"


def _format_violation_pending_response(
    pending: dict[str, Any],
    model: str,
    model_identity: ConfiguredModel | None = None,
) -> ChatCompletionResponse:
    """Format a pending violation as a ChatCompletionResponse.

    The response carries AG's blocking authority receipt but renders only the
    ordinary-user resolution prompt.
    """
    p = pending.get("pending", pending)
    prompt_text = GovernedChatAdapter.format_pending_message(p)

    return ChatCompletionResponse(
        id=f"chatcmpl-{uuid.uuid4().hex[:8]}",
        created=int(time.time()),
        model=model,
        provider_id=model_identity.provider_id if model_identity else None,
        model_id=model_identity.model_id if model_identity else None,
        choices=[
            ChatCompletionChoice(
                index=0,
                message=ChatMessage(role="assistant", content=prompt_text),
                finish_reason="stop",
            )
        ],
        usage=Usage(prompt_tokens=0, completion_tokens=0, total_tokens=0),
        receipt=pending["receipt"],
    )


# ============================================================================
# Session Endpoints
# ============================================================================


class CreateSessionRequest(BaseModel):
    model: str = ""
    title: str = Field(default="New conversation", min_length=1, max_length=200)
    project_id: str | None = None


class UpdateSessionRequest(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=200)
    archived: bool | None = None
    pinned: bool | None = None
    project_id: str | None = None
    model: str | None = None


class ForkSessionRequest(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    message_id: str | None = None
    project_id: str | None = None


class AppendMessageRequest(BaseModel):
    role: str
    content: str
    model: str | None = None
    provider_id: str | None = None
    model_id: str | None = None
    usage: dict[str, int] | None = None


def _conversation_location(
    session_id: str,
) -> tuple[ConversationLifecycle, SessionStore, ChatSession]:
    """Resolve lifecycle and content, lazily enrolling a legacy session."""
    library = _get_library_store()
    try:
        lifecycle = library.get_conversation(session_id)
    except ConversationLifecycleNotFoundError:
        legacy = _get_default_session_store().get(session_id)
        if legacy is None:
            raise HTTPException(status_code=404, detail="Session not found")
        library.sync_legacy_sessions([session_id])
        lifecycle = library.get_conversation(session_id)
    store = _get_session_store(lifecycle.project_id)
    session = store.get(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Session not found")
    return lifecycle, store, session


def _conversation_payload(
    session: ChatSession,
    lifecycle: ConversationLifecycle,
    *,
    summary: bool = False,
) -> dict[str, Any]:
    payload = session.to_summary() if summary else session.to_dict()
    payload.update(
        {
            "project_id": lifecycle.project_id,
            "archived": lifecycle.archived,
            "pinned": lifecycle.pinned,
            "parent_session_id": lifecycle.parent_session_id,
            "forked_at_message_id": lifecycle.forked_at_message_id,
            "word_count": sum(
                _word_count(message.content) for message in session.messages
            ),
        }
    )
    return payload


@app.get("/sessions/")
async def list_sessions(
    project_id: str | None = None,
    view: str = "active",
    q: str = "",
    sort: str = "updated_desc",
) -> dict[str, Any]:
    """List/search conversations in one project with lifecycle filtering."""
    if view not in {"active", "archived", "pinned", "all"}:
        raise HTTPException(status_code=422, detail="invalid conversation view")
    if sort not in {"updated_desc", "updated_asc", "title"}:
        raise HTTPException(status_code=422, detail="invalid conversation sort")

    project = _project_record(project_id)
    library = _get_library_store()
    store = _get_session_store(project.id)
    if project.id == library.snapshot().default_project_id:
        library.sync_legacy_sessions([item["id"] for item in store.list_summaries()])

    query = q.strip().casefold()
    items: list[dict[str, Any]] = []
    for summary in store.list_summaries():
        try:
            lifecycle = library.get_conversation(summary["id"])
        except ConversationLifecycleNotFoundError:
            continue
        if lifecycle.project_id != project.id:
            continue
        if view == "active" and lifecycle.archived:
            continue
        if view == "archived" and not lifecycle.archived:
            continue
        if view == "pinned" and (not lifecycle.pinned or lifecycle.archived):
            continue
        session = store.get(summary["id"])
        if session is None:
            continue
        if (
            query
            and query not in session.title.casefold()
            and not any(query in message.content.casefold() for message in session.messages)
        ):
            continue
        items.append(_conversation_payload(session, lifecycle, summary=True))

    if sort == "title":
        items.sort(key=lambda item: (item["title"].casefold(), item["id"]))
    else:
        items.sort(
            key=lambda item: item["updated_at"],
            reverse=sort == "updated_desc",
        )
    if view in {"active", "all"} and sort == "updated_desc":
        items.sort(key=lambda item: not item["pinned"])
    return {"project_id": project.id, "view": view, "sessions": items}


@app.get("/v1/conversations/tree")
async def conversation_branch_tree(project_id: str | None = None) -> dict[str, Any]:
    """Return explicit parent/child metadata for a project's explorations."""
    project = _project_record(project_id)
    store = _get_session_store(project.id)
    library = _get_library_store()
    summaries = {item["id"]: item for item in store.list_summaries()}
    if project.id == library.snapshot().default_project_id:
        library.sync_legacy_sessions(list(summaries))
    records = [
        record
        for record in library.snapshot().conversations.values()
        if record.project_id == project.id and record.session_id in summaries
    ]
    known_ids = {record.session_id for record in records}
    child_ids: dict[str, list[str]] = {session_id: [] for session_id in known_ids}
    nodes = []
    for record in records:
        summary = summaries[record.session_id]
        session = store.get(record.session_id)
        if record.parent_session_id in child_ids:
            child_ids[record.parent_session_id].append(record.session_id)
        nodes.append(
            {
                "id": record.session_id,
                "title": summary["title"],
                "parent_session_id": record.parent_session_id,
                "parent_in_project": record.parent_session_id in known_ids,
                "forked_at_message_id": record.forked_at_message_id,
                "archived": record.archived,
                "pinned": record.pinned,
                "message_count": summary["message_count"],
                "word_count": sum(
                    _word_count(message.content) for message in (session.messages if session else [])
                ),
                "updated_at": summary["updated_at"],
            }
        )
    for children in child_ids.values():
        children.sort(key=lambda item: summaries[item]["created_at"])
    for node in nodes:
        node["child_session_ids"] = child_ids[node["id"]]
    nodes.sort(key=lambda item: summaries[item["id"]]["created_at"])
    roots = [
        node["id"]
        for node in nodes
        if node["parent_session_id"] is None or not node["parent_in_project"]
    ]
    return {"project_id": project.id, "roots": roots, "nodes": nodes}


@app.post("/sessions/")
async def create_session(request: CreateSessionRequest) -> dict[str, Any]:
    """Create a new chat session."""
    project = _project_record(request.project_id)
    store = _get_session_store(project.id)
    title = request.title.strip()
    if not title:
        raise HTTPException(status_code=422, detail="conversation title must not be empty")
    selected_model, _ = _resolve_configured_model(request.model)
    session = store.create(
        context_id=project.context_id,
        model=selected_model,
        title=title,
    )
    try:
        lifecycle = _get_library_store().add_conversation(session.id, project.id)
    except Exception:
        store.delete(session.id)
        raise
    return _conversation_payload(session, lifecycle)


@app.get("/sessions/{session_id}")
async def get_session(session_id: str) -> dict[str, Any]:
    """Get a session with full message history."""
    lifecycle, _, session = _conversation_location(session_id)
    return _conversation_payload(session, lifecycle)


@app.delete("/sessions/{session_id}")
async def delete_session(session_id: str) -> dict[str, Any]:
    """Delete a session."""
    _, store, _ = _conversation_location(session_id)
    if not store.delete(session_id):
        raise HTTPException(status_code=404, detail="Session not found")
    _get_library_store().remove_conversation(session_id)
    return {"success": True}


@app.patch("/sessions/{session_id}")
async def update_session(session_id: str, request: UpdateSessionRequest) -> dict[str, Any]:
    """Rename, archive, pin, or move a conversation."""
    lifecycle, store, session = _conversation_location(session_id)
    if request.title is not None:
        title = request.title.strip()
        if not title:
            raise HTTPException(status_code=422, detail="conversation title must not be empty")
        if not store.update_title(session_id, title):
            raise HTTPException(status_code=404, detail="Session not found")
        session = store.get(session_id) or session

    if request.model is not None:
        selected_model, _ = _resolve_configured_model(request.model)
        if not store.update_model(session_id, selected_model):
            raise HTTPException(status_code=404, detail="Session not found")
        session = store.get(session_id) or session

    if request.project_id is not None and request.project_id != lifecycle.project_id:
        target_project = _project_record(request.project_id)
        target_store = _get_session_store(target_project.id)
        session.context_id = target_project.context_id
        target_store._write_session(session)
        lifecycle = _get_library_store().update_conversation(
            session_id,
            project_id=target_project.id,
            archived=request.archived,
            pinned=request.pinned,
        )
        store.delete(session_id)
    else:
        lifecycle = _get_library_store().update_conversation(
            session_id,
            archived=request.archived,
            pinned=request.pinned,
        )
    return _conversation_payload(session, lifecycle)


@app.post("/sessions/{session_id}/fork", status_code=201)
async def fork_session(
    session_id: str,
    request: ForkSessionRequest,
) -> dict[str, Any]:
    """Fork a conversation through an explicit point in its history."""
    source_lifecycle, _, source = _conversation_location(session_id)
    target_project = _project_record(request.project_id or source_lifecycle.project_id)
    target_store = _get_session_store(target_project.id)
    title = request.title.strip()
    if not title:
        raise HTTPException(status_code=422, detail="conversation title must not be empty")

    fork_messages = source.messages
    fork_point = request.message_id
    if fork_point is not None:
        matches = [i for i, message in enumerate(source.messages) if message.id == fork_point]
        if not matches:
            raise HTTPException(status_code=422, detail="fork message not found")
        fork_messages = source.messages[: matches[0] + 1]
    elif fork_messages:
        fork_point = fork_messages[-1].id

    fork = target_store.create(
        context_id=target_project.context_id,
        model=source.model,
        title=title,
    )
    fork.messages = [SessionMessage.from_dict(item.to_dict()) for item in fork_messages]
    fork.message_count = len(fork.messages)
    fork.updated_at = datetime.now(timezone.utc).isoformat()
    target_store._write_session(fork)
    try:
        lifecycle = _get_library_store().add_conversation(
            fork.id,
            target_project.id,
            parent_session_id=source.id,
            forked_at_message_id=fork_point,
        )
    except Exception:
        target_store.delete(fork.id)
        raise
    return _conversation_payload(fork, lifecycle)


@app.post("/sessions/{session_id}/messages")
async def append_message(session_id: str, request: AppendMessageRequest) -> dict[str, Any]:
    """Append a message to a session (write-through target)."""
    _, store, _ = _conversation_location(session_id)
    if (request.provider_id is None) != (request.model_id is None):
        raise HTTPException(
            status_code=422,
            detail="provider_id and model_id must be recorded together",
        )
    if request.provider_id is not None:
        _, configured = _resolve_configured_model(
            request.model or "", require_available=False
        )
        if configured is None or (
            configured.provider_id != request.provider_id
            or configured.model_id != request.model_id
        ):
            raise HTTPException(
                status_code=422,
                detail="message provenance does not match the configured model",
            )
    msg = SessionMessage.create(
        role=request.role,
        content=request.content,
        model=request.model,
        usage=request.usage,
        provider_id=request.provider_id,
        model_id=request.model_id,
    )
    if not store.append_message(session_id, msg):
        raise HTTPException(status_code=404, detail="Session not found")
    return msg.to_dict()


# ============================================================================
# Governor Endpoints
# ============================================================================


def _resolve_context(project_id: str | None = None) -> tuple[Any | None, str]:
    """Resolve the active governor context.

    Returns (context_or_None, context_id).
    """
    cm = _get_context_manager()
    project = _project_record(project_id)
    ctx = cm.get(project.context_id)
    return ctx, project.context_id


def _build_vm_for_context(ctx: Any) -> GovernorViewModel:
    """Build a GovernorViewModel from a resolved context."""
    return build_viewmodel(ctx.governor_dir, ctx.root)


@app.get("/governor/contexts")
async def list_contexts() -> dict[str, Any]:
    """List all governor contexts."""
    cm = _get_context_manager()
    contexts = cm.list_contexts()
    return {
        "active_context_id": GOVERNOR_CONTEXT_ID,
        "contexts": [ctx.to_dict() for ctx in contexts],
    }


@app.get("/governor/status")
async def governor_status() -> dict[str, Any]:
    """Show governor state for the active context.

    Backward-compat fields preserved; adds 'viewmodel' key with v2 schema.
    """
    ctx, context_id = _resolve_context()

    if ctx is None:
        return {
            "context_id": context_id,
            "initialized": False,
            "mode": GOVERNOR_MODE,
        }

    gov_dir = ctx.governor_dir
    has_governor = gov_dir.exists()
    has_fiction = (ctx.root / ".fiction-gov").exists()

    # Count facts and decisions
    facts_count = 0
    decisions_count = 0
    if has_governor:
        facts_index = gov_dir / "facts" / "index.json"
        if facts_index.exists():
            try:
                facts_data = json.loads(facts_index.read_text())
                facts_count = len(facts_data) if isinstance(facts_data, list) else 0
            except (json.JSONDecodeError, OSError):
                pass
        decisions_index = gov_dir / "decisions" / "index.json"
        if decisions_index.exists():
            try:
                dec_data = json.loads(decisions_index.read_text())
                decisions_count = len(dec_data) if isinstance(dec_data, list) else 0
            except (json.JSONDecodeError, OSError):
                pass

    # Build ViewModel v2
    vm = _build_vm_for_context(ctx)

    result: dict[str, Any] = {
        "context_id": ctx.context_id,
        "initialized": True,
        "mode": ctx.mode,
        "created_at": ctx.created_at,
        "has_governor": has_governor,
        "has_fiction_governor": has_fiction,
        "facts_count": facts_count,
        "decisions_count": decisions_count,
        "metadata": ctx.metadata,
        "viewmodel": vm.to_dict(),
    }

    # Research mode: include ED summary
    if ctx.mode == "research":
        try:
            from governor.research_store import ResearchStore

            store = ResearchStore(ctx.governor_dir)
            ed = store.compute_ed()
            result["research_ed"] = ed
        except Exception:
            pass

    return result


@app.get("/governor/now")
async def governor_now() -> dict[str, Any]:
    """Now screen: glanceable status for the active context."""
    ctx, context_id = _resolve_context()

    if ctx is None:
        return {
            "context_id": context_id,
            "status": "ok",
            "sentence": "OK: no governor context initialized.",
            "last_event": None,
            "suggested_action": None,
            "regime": None,
            "mode": GOVERNOR_MODE,
        }

    vm = _build_vm_for_context(ctx)

    now_result: dict[str, Any] = {
        "context_id": context_id,
        "status": derive_status_pill(vm),
        "sentence": derive_one_sentence(vm),
        "last_event": derive_last_event(vm),
        "suggested_action": derive_suggested_action(vm),
        "regime": vm.regime.name if vm.regime else None,
        "mode": ctx.mode,
    }

    # Research mode: override sentence with ED summary
    if ctx.mode == "research":
        try:
            from governor.research_store import ResearchStore

            store = ResearchStore(ctx.governor_dir)
            ed = store.compute_ed()
            now_result["sentence"] = (
                f"Discipline: {ed['total']} | {ed['floating']} floating | "
                f"{ed['open_uncertain']} uncertain"
            )
            now_result["research_ed"] = ed
        except Exception:
            pass

    return now_result


@app.get("/api/state")
async def api_state() -> dict[str, Any]:
    """Return the full GovernorViewModel (schema v2) for the active context.

    Alias documented in COMPAT.md §Contract versions. Returns the same
    ViewModel data that /governor/status embeds under the 'viewmodel' key, as
    a top-level JSON object. Non-chat path — works without a running daemon.
    Returns an empty ViewModel when no context has been initialised yet.
    """
    ctx, context_id = _resolve_context()
    if ctx is None:
        # Return an empty shell so callers can distinguish "not initialised"
        # from a transport error without a 404/503.
        return {
            "context_id": context_id,
            "initialized": False,
            "viewmodel": None,
        }
    vm = _build_vm_for_context(ctx)
    return {
        "context_id": ctx.context_id,
        "initialized": True,
        "viewmodel": vm.to_dict(),
    }


@app.get("/governor/why")
async def governor_why(limit: int = 20, severity: str | None = None) -> dict[str, Any]:
    """Why screen: decision/violation/claim feed."""
    ctx, context_id = _resolve_context()

    if ctx is None:
        return {"context_id": context_id, "feed": [], "total": 0}

    vm = _build_vm_for_context(ctx)
    feed = derive_why_feed(vm, limit=limit, severity_filter=severity)

    return {
        "context_id": context_id,
        "feed": feed,
        "total": len(feed),
    }


@app.get("/governor/history")
async def governor_history(days: int = 7) -> dict[str, Any]:
    """History screen: events grouped by calendar day."""
    ctx, context_id = _resolve_context()

    if ctx is None:
        return {"context_id": context_id, "days": []}

    vm = _build_vm_for_context(ctx)
    grouped = derive_history_days(vm, days=days)

    return {
        "context_id": context_id,
        "days": grouped,
    }


@app.get("/governor/detail/{item_id}")
async def governor_detail(item_id: str) -> dict[str, Any]:
    """Drill-down by ID prefix (dec_, clm_, ev_, vio_)."""
    ctx, context_id = _resolve_context()

    if ctx is None:
        raise HTTPException(status_code=404, detail="No governor context initialized.")

    vm = _build_vm_for_context(ctx)

    # Search by prefix
    if item_id.startswith("dec_"):
        for d in vm.decisions:
            if d.id == item_id:
                return {"id": item_id, "type": "decision", "data": d.to_dict()}
    elif item_id.startswith("clm_"):
        for c in vm.claims:
            if c.id == item_id:
                return {"id": item_id, "type": "claim", "data": c.to_dict()}
    elif item_id.startswith("ev_"):
        for e in vm.evidence:
            if e.id == item_id:
                return {"id": item_id, "type": "evidence", "data": e.to_dict()}
    elif item_id.startswith("vio_"):
        for v in vm.violations:
            if v.id == item_id:
                return {"id": item_id, "type": "violation", "data": v.to_dict()}

    raise HTTPException(status_code=404, detail=f"Item not found: {item_id}")


# ============================================================================
# Mode-Specific Endpoints (Fiction / Code)
# ============================================================================


# ============================================================================
# Research Mode Pydantic Models
# ============================================================================


class ClaimRequest(BaseModel):
    content: str
    scope: str = ""


class AssumptionRequest(BaseModel):
    content: str


class UncertaintyRequest(BaseModel):
    content: str
    attached_to: str = ""


class LinkRequest(BaseModel):
    link_type: str
    from_id: str
    to_id: str
    subtype: str = ""


class StatusChangeRequest(BaseModel):
    status: str


# ============================================================================
# Research Store (lazy init)
# ============================================================================

_research_store: Any = None


def _get_research_store() -> Any:
    """Lazy-init research store for the active context."""
    global _research_store
    if _research_store is None:
        from governor.research_store import ResearchStore

        cm = _get_context_manager()
        ctx = cm.get_or_create(GOVERNOR_CONTEXT_ID, mode=GOVERNOR_MODE)
        _research_store = ResearchStore(ctx.governor_dir)
    return _research_store


# ============================================================================
# Code Project Store (lazy init)
# ============================================================================

_project_store: Any = None


def _get_project_store() -> Any:
    """Lazy-init project store for code builder."""
    global _project_store
    if _project_store is None:
        from gov_webui.project_store import CodeProjectStore

        cm = _get_context_manager()
        ctx = cm.get_or_create(GOVERNOR_CONTEXT_ID, mode=GOVERNOR_MODE)
        _project_store = CodeProjectStore(ctx.governor_dir)
    return _project_store


_research_project_store: Any = None


def _get_research_project_store() -> Any:
    """Lazy-init project store for research builder."""
    global _research_project_store
    if _research_project_store is None:
        from gov_webui.project_store import CodeProjectStore, RESEARCH_EXTENSIONS

        cm = _get_context_manager()
        ctx = cm.get_or_create(GOVERNOR_CONTEXT_ID, mode=GOVERNOR_MODE)
        _research_project_store = CodeProjectStore(
            ctx.governor_dir,
            subdir="research_project",
            allowed_extensions=RESEARCH_EXTENSIONS,
        )
    return _research_project_store


_artifact_store: Any = None


def _get_artifact_store(project_id: str | None = None) -> Any:
    """Lazy-init the artifact store for one writing project."""
    global _artifact_store
    project = _project_record(project_id)
    if project.context_id == GOVERNOR_CONTEXT_ID and _artifact_store is not None:
        return _artifact_store
    if project.context_id in _artifact_stores:
        return _artifact_stores[project.context_id]
    if _artifact_store is None or project.context_id != GOVERNOR_CONTEXT_ID:
        from gov_webui.artifact_store import ArtifactStore

        cm = _get_context_manager()
        ctx = cm.get_or_create(project.context_id, mode=GOVERNOR_MODE)
        store = ArtifactStore(ctx.governor_dir)
        if project.context_id == GOVERNOR_CONTEXT_ID:
            _artifact_store = store
        else:
            _artifact_stores[project.context_id] = store
    return (
        _artifact_store
        if project.context_id == GOVERNOR_CONTEXT_ID
        else _artifact_stores[project.context_id]
    )


# ---------- Historical receipt V1 export (read-only) ----------


def _get_receipt_v1_jsonl_path() -> Path:
    """Canonical path for receipt_v1 JSONL file. Single source of truth."""
    cm = _get_context_manager()
    ctx = cm.get_or_create(GOVERNOR_CONTEXT_ID, mode=GOVERNOR_MODE)
    return Path(ctx.governor_dir) / "receipts" / "receipt_v1.jsonl"


def _load_receipt_v1_dicts() -> list[dict]:
    """Load all receipt_v1 dicts from JSONL in chronological order."""
    from receipt_v1.store import JsonlStore

    path = _get_receipt_v1_jsonl_path()
    store = JsonlStore(path)
    return store._all_dicts_chronological()


class CaptureRequest(BaseModel):
    """Request to scan text for canon-worthy statements."""

    text: str
    conversation_id: str | None = None
    message_id: str = ""
    project_id: str | None = None


class CaptureAcceptRequest(BaseModel):
    """Request to promote a pending capture to canon."""

    name: str = ""  # Character/entity name (may override detected)
    description: str = ""  # Description text
    capture_type: str = ""  # character, world_rule, relationship, constraint
    project_id: str | None = None


class CaptureUpdateRequest(BaseModel):
    subject: str | None = None
    statement: str | None = None
    kind: str | None = None
    project_id: str | None = None


class CharacterRequest(BaseModel):
    """Request to add a character."""

    name: str
    description: str | None = None
    voice: str | None = None
    wont: str | None = None  # Things they wouldn't do
    project_id: str | None = None


class WorldRuleRequest(BaseModel):
    """Request to add a world rule."""

    rule: str
    project_id: str | None = None


class ForbiddenRequest(BaseModel):
    """Request to add a forbidden thing."""

    description: str
    patterns: list[str] = Field(default_factory=list)
    project_id: str | None = None


class DecisionRequest(BaseModel):
    """Request to add a decision."""

    decision: str
    rationale: str | None = None


class ConstraintRequest(BaseModel):
    """Request to add a constraint."""

    constraint: str
    patterns: list[str] = Field(default_factory=list)


# -- Code Builder request models -------------------------------------------


class IntentUpdateRequest(BaseModel):
    text: str
    locked: bool = False
    expected_version: int | None = None


class ContractUpdateRequest(BaseModel):
    description: str = ""
    inputs: list[dict] = Field(default_factory=list)
    outputs: list[dict] = Field(default_factory=list)
    constraints: list[str] = Field(default_factory=list)
    transport: str = "stdio"
    expected_version: int | None = None
    config: dict | None = None
    config_hash: str | None = None
    config_hash_full: str | None = None


class PlanItemRequest(BaseModel):
    phase_idx: int
    text: str


class PlanItemStatusRequest(BaseModel):
    status: str
    expected_version: int | None = None


class PhaseRequest(BaseModel):
    name: str


class PhaseUpdateRequest(BaseModel):
    name: str | None = None
    locked: bool | None = None


class FileUpdateRequest(BaseModel):
    content: str
    turn_id: str | None = None


class RunRequest(BaseModel):
    filepath: str = "tool.py"
    stdin: str = ""
    timeout: int = 30
    force: bool = False


# -- Artifact Engine request models ----------------------------------------


class ArtifactCreateRequest(BaseModel):
    title: str = Field(..., min_length=1, max_length=200)
    content: str
    kind: str = "text"
    language: str = ""
    message_id: str | None = None
    source: str = "promote"
    source_turn_seq: int | None = None
    project_id: str | None = None
    artifact_type: str = "draft"
    conversation_id: str | None = None
    source_message_ids: list[str] = Field(default_factory=list)
    status: str = "idea"
    tags: list[str] = Field(default_factory=list)


class ArtifactUpdateRequest(BaseModel):
    content: str
    title: str | None = None
    expected_current_version: int | None = Field(default=None, ge=1)
    source: str = "manual"
    message_id: str | None = None
    source_turn_seq: int | None = None


class ArtifactLifecycleRequest(BaseModel):
    status: str | None = None
    tags: list[str] | None = None
    trashed: bool | None = None


class ArtifactWorkingCopyRequest(BaseModel):
    content: str
    base_version: int = Field(ge=1)


class ArtifactRestoreRequest(BaseModel):
    expected_current_version: int = Field(ge=1)


class ArtifactCanonProposalRequest(BaseModel):
    project_id: str | None = None
    kind: str
    subject: str = Field(default="", max_length=240)
    statement: str | None = Field(default=None, max_length=20_000)


@app.get("/governor/fiction/characters")
async def list_characters(project_id: str | None = None) -> dict[str, Any]:
    """List all characters for fiction mode."""
    ctx, _ = _resolve_context(project_id)
    if ctx is None:
        return {"characters": [], "message": "No governor context initialized."}

    from governor.continuity import AnchorType, create_registry

    registry = create_registry(ctx.governor_dir)
    anchors = registry.all()

    characters = []
    for a in anchors:
        if a.anchor_type == AnchorType.CANON and "char-" in a.id.lower():
            name = a.id.replace("char-", "").replace("-", " ").title()
            # Check for associated "wont" anchor
            wont_anchor = registry.get(f"{a.id}-wont")
            characters.append(
                {
                    "id": a.id,
                    "name": name,
                    "description": a.description,
                    "wont": wont_anchor.description if wont_anchor else None,
                }
            )

    return {"characters": characters}


@app.post("/governor/fiction/characters")
async def add_character(request: CharacterRequest) -> dict[str, Any]:
    """Add a character for fiction mode."""
    ctx, _ = _resolve_context(request.project_id)
    if ctx is None:
        raise HTTPException(status_code=400, detail="No governor context initialized.")

    from governor.continuity import Anchor, AnchorType, Severity, create_registry

    registry = create_registry(ctx.governor_dir)

    char_id = f"char-{request.name.lower().replace(' ', '-')}"

    # Build description
    desc_parts = []
    if request.description:
        desc_parts.append(f"Appearance: {request.description}")
    if request.voice:
        desc_parts.append(f"Voice: {request.voice}")
    description = "; ".join(desc_parts) if desc_parts else f"Character: {request.name}"

    # Create character anchor
    anchor = Anchor(
        id=char_id,
        anchor_type=AnchorType.CANON,
        description=description,
        severity=Severity.REJECT,
    )
    registry.register(anchor)

    # Create prohibition anchor if wont provided
    if request.wont:
        patterns = [p.strip() for p in request.wont.split(",")]
        wont_anchor = Anchor(
            id=f"{char_id}-wont",
            anchor_type=AnchorType.PROHIBITION,
            description=f"{request.name} wouldn't: {request.wont}",
            forbidden_patterns=patterns,
            severity=Severity.REJECT,
        )
        registry.register(wont_anchor)

    # Save
    registry.save(ctx.governor_dir / "continuity" / "anchors.json")

    return {
        "success": True,
        "message": f"{request.name} added. I'll remember.",
        "id": char_id,
    }


@app.delete("/governor/fiction/characters/{char_id}")
async def remove_character(
    char_id: str,
    project_id: str | None = None,
) -> dict[str, Any]:
    """Remove a character."""
    ctx, _ = _resolve_context(project_id)
    if ctx is None:
        raise HTTPException(status_code=400, detail="No governor context initialized.")

    from governor.continuity import create_registry

    registry = create_registry(ctx.governor_dir)
    anchor = registry.unregister(char_id)
    registry.unregister(f"{char_id}-wont")

    if anchor:
        registry.save(ctx.governor_dir / "continuity" / "anchors.json")
        return {"success": True, "message": "Character removed."}

    raise HTTPException(status_code=404, detail="Character not found.")


@app.get("/governor/fiction/world-rules")
async def list_world_rules(project_id: str | None = None) -> dict[str, Any]:
    """List all world rules for fiction mode."""
    ctx, _ = _resolve_context(project_id)
    if ctx is None:
        return {"rules": [], "message": "No governor context initialized."}

    from governor.continuity import AnchorType, create_registry

    registry = create_registry(ctx.governor_dir)
    anchors = registry.all()

    rules = []
    for a in anchors:
        if a.anchor_type == AnchorType.DEFINITION:
            rules.append(
                {
                    "id": a.id,
                    "rule": a.description,
                }
            )

    return {"rules": rules}


@app.post("/governor/fiction/world-rules")
async def add_world_rule(request: WorldRuleRequest) -> dict[str, Any]:
    """Add a world rule for fiction mode."""
    ctx, _ = _resolve_context(request.project_id)
    if ctx is None:
        raise HTTPException(status_code=400, detail="No governor context initialized.")

    from governor.continuity import Anchor, AnchorType, Severity, create_registry

    registry = create_registry(ctx.governor_dir)

    rule_id = f"world-{len([a for a in registry.all() if 'world-' in a.id]) + 1}"

    anchor = Anchor(
        id=rule_id,
        anchor_type=AnchorType.DEFINITION,
        description=request.rule,
        severity=Severity.REJECT,
    )
    registry.register(anchor)
    registry.save(ctx.governor_dir / "continuity" / "anchors.json")

    return {
        "success": True,
        "message": "Rule added. I'll keep it consistent.",
        "id": rule_id,
    }


@app.get("/governor/fiction/forbidden")
async def list_forbidden(project_id: str | None = None) -> dict[str, Any]:
    """List all forbidden things for fiction mode."""
    ctx, _ = _resolve_context(project_id)
    if ctx is None:
        return {"forbidden": [], "message": "No governor context initialized."}

    from governor.continuity import AnchorType, create_registry

    registry = create_registry(ctx.governor_dir)
    anchors = registry.all()

    forbidden = []
    for a in anchors:
        if a.anchor_type == AnchorType.PROHIBITION and not a.id.endswith("-wont"):
            forbidden.append(
                {
                    "id": a.id,
                    "description": a.description,
                    "patterns": a.forbidden_patterns,
                }
            )

    return {"forbidden": forbidden}


@app.post("/governor/fiction/forbidden")
async def add_forbidden(request: ForbiddenRequest) -> dict[str, Any]:
    """Add a forbidden thing for fiction mode."""
    ctx, _ = _resolve_context(request.project_id)
    if ctx is None:
        raise HTTPException(status_code=400, detail="No governor context initialized.")

    from governor.continuity import Anchor, AnchorType, Severity, create_registry

    registry = create_registry(ctx.governor_dir)

    forbid_id = f"forbid-{len([a for a in registry.all() if 'forbid-' in a.id]) + 1}"

    anchor = Anchor(
        id=forbid_id,
        anchor_type=AnchorType.PROHIBITION,
        description=request.description,
        forbidden_patterns=request.patterns,
        severity=Severity.REJECT,
    )
    registry.register(anchor)
    registry.save(ctx.governor_dir / "continuity" / "anchors.json")

    return {
        "success": True,
        "message": "I'll watch for that.",
        "id": forbid_id,
    }


# =============================================================================
# Canon Capture (fiction mode — pending promotion pipeline)
# =============================================================================

# Deprecated compatibility mirrors for retained donor tests. Product capture
# state is persisted by CanonReviewStore and never sourced from these values.
_pending_captures: dict[str, dict[str, Any]] = {}
_capture_counter: int = 0


@app.post("/governor/fiction/capture/scan")
async def capture_scan(request: CaptureRequest) -> dict[str, Any]:
    """Scan text for canon-worthy statements. Returns capture candidates."""
    try:
        from fiction_governor.canon_capture import CanonCaptureClassifier
    except ImportError:
        return {"captures": [], "error": "Canon capture classifier not available."}

    classifier = CanonCaptureClassifier()
    items, receipt = classifier.scan(request.text)
    project = _project_record(request.project_id)
    store = _get_canon_review_store(project.id)

    captures = []
    for item in items:
        candidate = store.add(
            kind=item.kind if isinstance(item.kind, str) else item.kind.value,
            confidence=round(item.confidence, 2),
            subject=item.subject_guess or "",
            statement=item.statement,
            field=item.field_guess or "",
            spans=[list(span) for span in item.evidence_spans],
            conversation_id=request.conversation_id,
            message_id=request.message_id,
            draft=item.draft_payload or None,
        )
        captures.append(candidate.model_dump(mode="json"))

    return {
        "captures": captures,
        "receipt": {
            "classifier_version": receipt.classifier_version,
            "content_hash": receipt.content_hash,
            "pattern_hits": receipt.pattern_hits,
        },
    }


@app.get("/governor/fiction/captures")
async def list_pending_captures(
    project_id: str | None = None,
    status: str = "pending",
) -> dict[str, Any]:
    """List durable canon review candidates for one project."""
    if status not in {"pending", "accepted", "dismissed", "all"}:
        raise HTTPException(status_code=422, detail="invalid canon review status")
    project = _project_record(project_id)
    try:
        items = _get_canon_review_store(project.id).list(status=status)
        return {
            "captures": [item.model_dump(mode="json") for item in items],
            "count": len(items),
        }
    except CanonReviewStoreError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.post("/governor/fiction/capture/{capture_id}/accept")
async def accept_capture(capture_id: str, request: CaptureAcceptRequest) -> dict[str, Any]:
    """Promote a pending capture to canon (creates character or world rule anchor)."""
    project = _project_record(request.project_id)
    review_store = _get_canon_review_store(project.id)
    try:
        cap = review_store.get(capture_id)
    except CanonReviewNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Capture not found.") from exc
    if cap.status != "pending":
        raise HTTPException(status_code=400, detail=f"Capture already {cap.status}.")

    ctx, _ = _resolve_context(project.id)
    if ctx is None:
        raise HTTPException(status_code=400, detail="No governor context initialized.")

    from governor.continuity import Anchor, AnchorType, Severity, create_registry

    registry = create_registry(ctx.governor_dir)

    kind = request.capture_type or cap.kind or "character"
    name = request.name or cap.subject
    desc = request.description or cap.statement

    if kind in ("character", "relationship"):
        char_id = f"char-{name.lower().replace(' ', '-')}" if name else f"char-cap-{capture_id}"
        anchor = Anchor(
            id=char_id,
            anchor_type=AnchorType.CANON,
            description=f"{name}: {desc}" if name else desc,
            severity=Severity.REJECT,
        )
        registry.register(anchor)
        registry.save(ctx.governor_dir / "continuity" / "anchors.json")
        review_store.resolve(capture_id, status="accepted", promoted_to=char_id)
        return {"success": True, "message": f"Canon: {name or char_id}", "id": char_id}

    elif kind in ("world_rule", "constraint"):
        rule_count = len([a for a in registry.all() if "rule-" in a.id])
        rule_id = f"rule-{rule_count + 1}"

        if kind == "constraint":
            patterns = [p.strip() for p in desc.split(",") if p.strip()]
            anchor = Anchor(
                id=rule_id,
                anchor_type=AnchorType.PROHIBITION,
                description=desc,
                forbidden_patterns=patterns,
                severity=Severity.REJECT,
            )
        else:
            anchor = Anchor(
                id=rule_id,
                anchor_type=AnchorType.DEFINITION,
                description=desc,
                severity=Severity.WARN,
            )
        registry.register(anchor)
        registry.save(ctx.governor_dir / "continuity" / "anchors.json")
        review_store.resolve(capture_id, status="accepted", promoted_to=rule_id)
        return {"success": True, "message": f"Canon: {desc[:40]}", "id": rule_id}

    raise HTTPException(status_code=400, detail=f"Unknown capture kind: {kind}")


@app.patch("/governor/fiction/capture/{capture_id}")
async def update_capture(
    capture_id: str,
    request: CaptureUpdateRequest,
) -> dict[str, Any]:
    """Let the writer correct a pending suggestion before accepting it."""
    project = _project_record(request.project_id)
    try:
        item = _get_canon_review_store(project.id).update(
            capture_id,
            subject=request.subject,
            statement=request.statement,
            kind=request.kind,
        )
        return item.model_dump(mode="json")
    except CanonReviewNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Capture not found.") from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/governor/fiction/capture/{capture_id}/reject")
async def reject_capture(
    capture_id: str,
    project_id: str | None = None,
) -> dict[str, Any]:
    """Reject a pending capture."""
    project = _project_record(project_id)
    try:
        _get_canon_review_store(project.id).resolve(
            capture_id,
            status="dismissed",
        )
        return {"success": True, "message": "Capture dismissed."}
    except CanonReviewNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Capture not found.") from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/governor/code/decisions")
async def list_decisions() -> dict[str, Any]:
    """List all decisions for code mode."""
    ctx, _ = _resolve_context()
    if ctx is None:
        return {"decisions": [], "message": "No governor context initialized."}

    from governor.ledgers import DecisionLedger

    try:
        ledger = DecisionLedger(ctx.governor_dir)
        decisions = list(ledger.all())

        return {
            "decisions": [
                {
                    "id": str(d.id),
                    "topic": d.topic,
                    "choice": d.choice,
                    "rationale": d.rationale,
                    "created_at": d.created_at.isoformat() if d.created_at else None,
                }
                for d in decisions
            ]
        }
    except Exception:
        return {"decisions": []}


@app.post("/governor/code/decisions")
async def add_decision(request: DecisionRequest) -> dict[str, Any]:
    """Add a decision for code mode."""
    ctx, _ = _resolve_context()
    if ctx is None:
        raise HTTPException(status_code=400, detail="No governor context initialized.")

    from governor.claims import decision as make_decision
    from governor.ledgers import DecisionLedger

    # Parse decision into topic/choice
    if ":" in request.decision:
        topic, choice = request.decision.split(":", 1)
    elif "," in request.decision:
        parts = request.decision.split(",", 1)
        topic = parts[0].strip()
        choice = parts[1].strip() if len(parts) > 1 else topic
    else:
        topic = "architecture"
        choice = request.decision

    topic = topic.strip()
    choice = choice.strip()

    ledger = DecisionLedger(ctx.governor_dir)
    claim = make_decision(topic, choice)
    decision = ledger.add(claim, rationale=request.rationale)

    return {
        "success": True,
        "message": "Decision recorded. I'll catch anything that contradicts it.",
        "id": str(decision.id),
    }


@app.get("/governor/code/constraints")
async def list_constraints() -> dict[str, Any]:
    """List all constraints for code mode."""
    ctx, _ = _resolve_context()
    if ctx is None:
        return {"constraints": [], "message": "No governor context initialized."}

    from governor.continuity import AnchorType, create_registry

    registry = create_registry(ctx.governor_dir)
    anchors = registry.all()

    constraints = []
    for a in anchors:
        if a.anchor_type == AnchorType.PROHIBITION and "constraint-" in a.id:
            constraints.append(
                {
                    "id": a.id,
                    "description": a.description,
                    "patterns": a.forbidden_patterns,
                }
            )

    return {"constraints": constraints}


@app.post("/governor/code/constraints")
async def add_constraint(request: ConstraintRequest) -> dict[str, Any]:
    """Add a constraint for code mode."""
    ctx, _ = _resolve_context()
    if ctx is None:
        raise HTTPException(status_code=400, detail="No governor context initialized.")

    from governor.continuity import Anchor, AnchorType, Severity, create_registry

    registry = create_registry(ctx.governor_dir)

    con_id = f"constraint-{len([a for a in registry.all() if 'constraint-' in a.id]) + 1}"

    anchor = Anchor(
        id=con_id,
        anchor_type=AnchorType.PROHIBITION,
        description=request.constraint,
        forbidden_patterns=request.patterns,
        severity=Severity.REJECT,
    )
    registry.register(anchor)
    registry.save(ctx.governor_dir / "continuity" / "anchors.json")

    return {
        "success": True,
        "message": "Constraint added.",
        "id": con_id,
    }


# ============================================================================
# Code Interferometry Endpoints
# ============================================================================


@app.get("/governor/code/compare/last")
async def code_compare_last() -> dict[str, Any]:
    """Get the latest code divergence report, or null if none exists."""
    ctx, _ = _resolve_context()
    if ctx is None:
        return {"report": None}

    try:
        from governor.interferometry import InterferometryStore
        from governor.code_interferometry import compute_code_divergence
        from governor.continuity import create_registry

        store = InterferometryStore(ctx.governor_dir)
        irun = store.last()
        if irun is None:
            return {"report": None}

        try:
            registry = create_registry(ctx.governor_dir)
            anchors = registry.all()
        except Exception:
            anchors = []

        report = compute_code_divergence(irun, anchors)
        return {"report": report.to_dict()}
    except Exception:
        return {"report": None}


class CompareRequest(BaseModel):
    prompt: str
    backends: str  # comma-separated backend:model pairs


@app.post("/governor/code/compare")
async def code_compare_run(request: CompareRequest) -> dict[str, Any]:
    """Run code interferometry compare. Returns report + run_id."""
    ctx, _ = _resolve_context()
    if ctx is None:
        raise HTTPException(status_code=400, detail="No governor context initialized.")

    import asyncio
    from governor.interferometry import InterferometryStore, run_ensemble
    from governor.code_interferometry import compute_code_divergence
    from governor.continuity import create_registry

    # Parse backend configs
    backend_configs = []
    for pair in request.backends.split(","):
        pair = pair.strip()
        if ":" not in pair:
            raise HTTPException(status_code=400, detail=f"Invalid backend:model pair: {pair}")
        bt, model = pair.split(":", 1)
        config: dict[str, Any] = {"backend_type": bt, "model": model}
        if bt == "ollama":
            config["host"] = OLLAMA_HOST
        elif bt == "anthropic":
            config["api_key"] = ANTHROPIC_API_KEY
        backend_configs.append(config)

    irun = await run_ensemble(request.prompt, backend_configs)

    store = InterferometryStore(ctx.governor_dir)
    store.save(irun)

    try:
        registry = create_registry(ctx.governor_dir)
        anchors = registry.all()
    except Exception:
        anchors = []

    report = compute_code_divergence(irun, anchors)
    return {"run_id": irun.id, "report": report.to_dict()}


# ============================================================================
# Code Builder Endpoints
# ============================================================================


@app.get("/governor/code/project")
async def code_project_state() -> dict[str, Any]:
    """Full project state for sidebar polling."""
    try:
        store = _get_project_store()
        return store.get_state()
    except Exception:
        return {
            "version": 0,
            "intent": {"text": "", "locked": False},
            "contract": {},
            "plan": {"phases": []},
            "files": {},
        }


@app.put("/governor/code/project/intent")
async def code_update_intent(request: IntentUpdateRequest) -> dict[str, Any]:
    """Update intent text + lock."""
    from gov_webui.project_store import StaleVersionError

    store = _get_project_store()
    try:
        intent = store.update_intent(request.text, request.locked, request.expected_version)
        return {"success": True, "intent": intent.model_dump()}
    except StaleVersionError:
        raise HTTPException(409, "Stale version")


@app.put("/governor/code/project/contract")
async def code_update_contract(request: ContractUpdateRequest) -> dict[str, Any]:
    """Update contract fields."""
    from gov_webui.project_store import (
        Contract,
        ContractField,
        StaleVersionError,
        compute_config_hash,
    )

    # Parse input/output dicts into ContractField objects
    inputs = [ContractField(**f) for f in request.inputs]
    outputs = [ContractField(**f) for f in request.outputs]

    # Server always recomputes hash — never trust client hashes
    config = request.config
    config_hash = None
    config_hash_full = None
    if config:
        # Guard against oversized configs (50KB soft cap)
        config_size = len(json.dumps(config))
        if config_size > 50_000:
            raise HTTPException(400, f"Config too large: {config_size} bytes (max 50000)")
        short, full = compute_config_hash(config)
        if request.config_hash and request.config_hash != short:
            raise HTTPException(
                400, f"Config hash mismatch: got {request.config_hash}, expected {short}"
            )
        config_hash = short
        config_hash_full = full

    contract = Contract(
        description=request.description,
        inputs=inputs,
        outputs=outputs,
        constraints=request.constraints,
        transport=request.transport,
        config=config,
        config_hash=config_hash,
        config_hash_full=config_hash_full,
    )
    store = _get_project_store()
    try:
        result = store.update_contract(contract, request.expected_version)
        return {"success": True, "contract": result.model_dump()}
    except StaleVersionError:
        raise HTTPException(409, "Stale version")


@app.post("/governor/code/plan/item")
async def code_add_plan_item(request: PlanItemRequest) -> dict[str, Any]:
    """Add a plan item to a phase."""
    store = _get_project_store()
    try:
        item = store.add_plan_item(request.phase_idx, request.text)
        return {"success": True, "item": item.model_dump()}
    except ValueError as e:
        raise HTTPException(400, str(e))


@app.patch("/governor/code/plan/item/{item_id}")
async def code_update_plan_item(item_id: str, request: PlanItemStatusRequest) -> dict[str, Any]:
    """Update plan item status with state machine validation."""
    from gov_webui.project_store import PlanItemStatus, StaleVersionError

    store = _get_project_store()
    try:
        status = PlanItemStatus(request.status)
    except ValueError:
        raise HTTPException(400, f"Invalid status: {request.status}")

    try:
        item = store.update_item_status(item_id, status, request.expected_version)
        return {"success": True, "item": item.model_dump()}
    except StaleVersionError:
        raise HTTPException(409, "Stale version")
    except ValueError as e:
        raise HTTPException(400, str(e))


@app.post("/governor/code/plan/phase")
async def code_add_phase(request: PhaseRequest) -> dict[str, Any]:
    """Add a new phase to the plan."""
    store = _get_project_store()
    phase = store.add_phase(request.name)
    return {"success": True, "phase": phase.model_dump()}


@app.patch("/governor/code/plan/phase/{idx}")
async def code_update_phase(idx: int, request: PhaseUpdateRequest) -> dict[str, Any]:
    """Update phase name/lock."""
    store = _get_project_store()
    try:
        phase = store.update_phase(idx, request.name, request.locked)
        return {"success": True, "phase": phase.model_dump()}
    except ValueError as e:
        raise HTTPException(400, str(e))


@app.get("/governor/code/files")
async def code_list_files() -> dict[str, Any]:
    """List files with versions + hashes."""
    store = _get_project_store()
    return {"files": store.list_files()}


@app.get("/governor/code/file-prev/{path:path}")
async def code_get_file_prev(path: str) -> dict[str, Any]:
    """Get previous version for client-side diff."""
    store = _get_project_store()
    try:
        content = store.get_file_prev(path)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"filepath": path, "content": content}


@app.get("/governor/code/files/{path:path}")
async def code_get_file(path: str) -> dict[str, Any]:
    """Get file content."""
    store = _get_project_store()
    try:
        content = store.get_file_content(path)
    except ValueError as e:
        raise HTTPException(400, str(e))
    if content is None:
        raise HTTPException(404, f"File not found: {path}")
    return {"filepath": path, "content": content}


@app.put("/governor/code/files/{path:path}")
async def code_put_file(path: str, request: FileUpdateRequest) -> dict[str, Any]:
    """Accept file, returns version + hash."""
    store = _get_project_store()
    try:
        entry = store.put_file(path, request.content, request.turn_id)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {
        "success": True,
        "filepath": path,
        "version": entry.version,
        "content_hash": entry.content_hash,
    }


@app.post("/governor/code/run")
async def code_run(request: RunRequest) -> dict[str, Any]:
    """Execute project files in tempdir, returns output."""
    import subprocess
    import sys
    import tempfile

    store = _get_project_store()
    state = store.get_state()
    files = state.get("files", {})

    if not files:
        raise HTTPException(400, "No files in project")

    if request.filepath not in files:
        raise HTTPException(404, f"Entrypoint not found: {request.filepath}")

    # Constraint pre-flight check
    preflight_violations: list[str] = []
    try:
        ctx, _ = _resolve_context()
        if ctx is not None:
            from governor.continuity import AnchorType, create_registry

            registry = create_registry(ctx.governor_dir)
            anchors = registry.all()
            prohibitions = [a for a in anchors if a.anchor_type == AnchorType.PROHIBITION]

            # Check all project files against prohibition patterns
            for fpath in files:
                content = store.get_file_content(fpath)
                if content is None:
                    continue
                for anchor in prohibitions:
                    for pattern in anchor.forbidden_patterns:
                        if pattern.lower() in content.lower():
                            preflight_violations.append(
                                f"{fpath}: matches '{pattern}' ({anchor.description})"
                            )
    except Exception:
        pass  # Pre-flight is best-effort

    if preflight_violations and not request.force:
        return {
            "success": False,
            "preflight_violations": preflight_violations,
            "preflight_hit": True,
            "forced": False,
        }

    # Execute in tempdir
    tmpdir = None
    try:
        tmpdir = tempfile.mkdtemp(prefix="gov-code-run-")
        tmpdir_path = Path(tmpdir)

        # Write all accepted files into tempdir
        for fpath in files:
            content = store.get_file_content(fpath)
            if content is None:
                continue
            dest = tmpdir_path / fpath
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_text(content)

        entrypoint = request.filepath
        timeout = min(max(request.timeout, 1), 120)  # clamp 1-120s

        result = subprocess.run(
            [sys.executable, entrypoint],
            cwd=tmpdir,
            input=request.stdin or None,
            capture_output=True,
            timeout=timeout,
            text=True,
        )

        stdout = result.stdout or ""
        stderr = result.stderr or ""

        # Cap combined output at 100KB
        max_output = 100 * 1024
        combined_len = len(stdout) + len(stderr)
        if combined_len > max_output:
            # Truncate proportionally
            ratio = max_output / combined_len
            stdout = stdout[: int(len(stdout) * ratio)] + "\n…(truncated)"
            stderr = stderr[: int(len(stderr) * ratio)] + "\n…(truncated)"

        return {
            "success": result.returncode == 0,
            "returncode": result.returncode,
            "stdout": stdout,
            "stderr": stderr,
            "filepath": request.filepath,
            "preflight_hit": bool(preflight_violations),
            "forced": request.force and bool(preflight_violations),
        }

    except subprocess.TimeoutExpired:
        return {
            "success": False,
            "returncode": -1,
            "stdout": "",
            "stderr": f"Process killed: exceeded {request.timeout}s timeout",
            "filepath": request.filepath,
            "preflight_hit": bool(preflight_violations),
            "forced": request.force and bool(preflight_violations),
        }
    except Exception as e:
        return {
            "success": False,
            "returncode": -1,
            "stdout": "",
            "stderr": str(e),
            "filepath": request.filepath,
            "preflight_hit": bool(preflight_violations),
            "forced": request.force and bool(preflight_violations),
        }
    finally:
        if tmpdir:
            shutil.rmtree(tmpdir, ignore_errors=True)


# ============================================================================
# Research Mode Endpoints
# ============================================================================


@app.get("/governor/research/state")
async def research_state() -> dict[str, Any]:
    """Full research state + ED score."""
    store = _get_research_store()
    return store.get_state()


@app.post("/governor/research/claims")
async def add_research_claim(request: ClaimRequest) -> dict[str, Any]:
    """Add a research claim."""
    store = _get_research_store()
    claim = store.add_claim(content=request.content, scope=request.scope)
    return {"success": True, "claim": claim.to_dict()}


@app.delete("/governor/research/claims/{claim_id}")
async def delete_research_claim(claim_id: str) -> dict[str, Any]:
    """Delete a research claim."""
    store = _get_research_store()
    if not store.delete_claim(claim_id):
        raise HTTPException(status_code=404, detail="Claim not found")
    return {"success": True}


@app.patch("/governor/research/claims/{claim_id}/status")
async def change_claim_status(claim_id: str, request: StatusChangeRequest) -> dict[str, Any]:
    """Change a claim's status."""
    from governor.research_store import ClaimStatus as RClaimStatus

    try:
        status = RClaimStatus(request.status)
    except ValueError:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid status: {request.status}. Valid: {[s.value for s in RClaimStatus]}",
        )

    store = _get_research_store()
    try:
        claim = store.update_claim_status(claim_id, status)
    except KeyError:
        raise HTTPException(status_code=404, detail="Claim not found")
    return {"success": True, "claim": claim.to_dict()}


@app.post("/governor/research/assumptions")
async def add_research_assumption(request: AssumptionRequest) -> dict[str, Any]:
    """Add a research assumption."""
    store = _get_research_store()
    assumption = store.add_assumption(content=request.content)
    return {"success": True, "assumption": assumption.to_dict()}


@app.delete("/governor/research/assumptions/{assumption_id}")
async def delete_research_assumption(assumption_id: str) -> dict[str, Any]:
    """Delete a research assumption."""
    store = _get_research_store()
    if not store.delete_assumption(assumption_id):
        raise HTTPException(status_code=404, detail="Assumption not found")
    return {"success": True}


@app.patch("/governor/research/assumptions/{assumption_id}/status")
async def change_assumption_status(
    assumption_id: str, request: StatusChangeRequest
) -> dict[str, Any]:
    """Change an assumption's status."""
    from governor.research_store import AssumptionStatus

    try:
        status = AssumptionStatus(request.status)
    except ValueError:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid status: {request.status}. Valid: {[s.value for s in AssumptionStatus]}",
        )

    store = _get_research_store()
    try:
        assumption = store.update_assumption_status(assumption_id, status)
    except KeyError:
        raise HTTPException(status_code=404, detail="Assumption not found")
    return {"success": True, "assumption": assumption.to_dict()}


@app.post("/governor/research/uncertainties")
async def add_research_uncertainty(request: UncertaintyRequest) -> dict[str, Any]:
    """Add a research uncertainty."""
    store = _get_research_store()
    uncertainty = store.add_uncertainty(content=request.content, attached_to=request.attached_to)
    return {"success": True, "uncertainty": uncertainty.to_dict()}


@app.delete("/governor/research/uncertainties/{uncertainty_id}")
async def delete_research_uncertainty(uncertainty_id: str) -> dict[str, Any]:
    """Delete a research uncertainty."""
    store = _get_research_store()
    if not store.delete_uncertainty(uncertainty_id):
        raise HTTPException(status_code=404, detail="Uncertainty not found")
    return {"success": True}


@app.patch("/governor/research/uncertainties/{uncertainty_id}/status")
async def change_uncertainty_status(
    uncertainty_id: str, request: StatusChangeRequest
) -> dict[str, Any]:
    """Change an uncertainty's status."""
    from governor.research_store import UncertaintyStatus

    try:
        status = UncertaintyStatus(request.status)
    except ValueError:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid status: {request.status}. Valid: {[s.value for s in UncertaintyStatus]}",
        )

    store = _get_research_store()
    try:
        uncertainty = store.update_uncertainty_status(uncertainty_id, status)
    except KeyError:
        raise HTTPException(status_code=404, detail="Uncertainty not found")
    return {"success": True, "uncertainty": uncertainty.to_dict()}


@app.post("/governor/research/links")
async def add_research_link(request: LinkRequest) -> dict[str, Any]:
    """Add a typed link between research items."""
    from governor.research_store import LinkType

    try:
        link_type = LinkType(request.link_type)
    except ValueError:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid link type: {request.link_type}. Valid: {[t.value for t in LinkType]}",
        )

    store = _get_research_store()
    link = store.add_link(
        link_type=link_type,
        from_id=request.from_id,
        to_id=request.to_id,
        subtype=request.subtype,
    )
    return {"success": True, "link": link.to_dict()}


@app.delete("/governor/research/links/{link_id}")
async def delete_research_link(link_id: str) -> dict[str, Any]:
    """Delete a research link."""
    store = _get_research_store()
    if not store.remove_link(link_id):
        raise HTTPException(status_code=404, detail="Link not found")
    return {"success": True}


# =============================================================================
# Research Capture (pending promotion pipeline — claims, citations, source refs)
# =============================================================================

# In-memory pending research captures (per process; cleared on restart)
_pending_research_captures: dict[str, dict[str, Any]] = {}
_research_capture_counter: int = 0


@app.post("/governor/research/capture/scan")
async def research_capture_scan(request: CaptureRequest) -> dict[str, Any]:
    """Scan text for claims, citations, and structured source refs."""
    global _research_capture_counter

    try:
        from governor.capture import ResearchCaptureClassifier
    except ImportError:
        return {"captures": [], "error": "Research capture classifier not available."}

    classifier = ResearchCaptureClassifier()
    items, receipt = classifier.scan(request.text, message_id=request.message_id)

    captures = []
    for item in items:
        _research_capture_counter += 1
        cap_id = f"rcap-{_research_capture_counter}"
        cap = {
            "id": cap_id,
            "kind": item.kind if isinstance(item.kind, str) else item.kind.value,
            "confidence": round(item.confidence, 2),
            "subject": item.subject_guess or "",
            "statement": item.statement,
            "field": item.field_guess or "",
            "spans": [list(s) for s in item.evidence_spans],
            "message_id": request.message_id,
            "status": "pending",
        }
        if item.draft_payload:
            cap["draft"] = item.draft_payload
        _pending_research_captures[cap_id] = cap
        captures.append(cap)

    return {
        "captures": captures,
        "receipt": {
            "classifier_version": receipt.classifier_version,
            "content_hash": receipt.content_hash,
            "pattern_hits": receipt.pattern_hits,
        },
    }


@app.get("/governor/research/captures")
async def list_pending_research_captures() -> dict[str, Any]:
    """List all pending (unresolved) research captures."""
    pending = [c for c in _pending_research_captures.values() if c["status"] == "pending"]
    return {"captures": pending, "count": len(pending)}


@app.post("/governor/research/capture/{capture_id}/accept")
async def accept_research_capture(capture_id: str) -> dict[str, Any]:
    """Promote a pending research capture to the claim ledger."""
    if capture_id not in _pending_research_captures:
        raise HTTPException(status_code=404, detail="Capture not found.")

    cap = _pending_research_captures[capture_id]
    if cap["status"] != "pending":
        raise HTTPException(status_code=400, detail=f"Capture already {cap['status']}.")

    store = _get_research_store()

    kind = cap.get("kind", "claim")
    statement = cap.get("statement", "")
    draft = cap.get("draft", {})
    source_ref = draft.get("source_ref", "")

    if kind in ("claim", "experiment"):
        claim = store.add_claim(
            content=statement,
            source_ref=source_ref,
            captured_from=capture_id,
        )
        cap["status"] = "accepted"
        cap["promoted_to"] = claim.id
        return {"success": True, "message": f"Claim: {statement[:40]}", "id": claim.id}

    elif kind == "citation":
        # Citations with source_ref → claim with ref provenance
        label = source_ref or statement
        claim = store.add_claim(
            content=label,
            source_ref=source_ref,
            captured_from=capture_id,
        )
        cap["status"] = "accepted"
        cap["promoted_to"] = claim.id
        return {"success": True, "message": f"Source: {label[:40]}", "id": claim.id}

    elif kind == "assumption":
        assumption = store.add_assumption(content=statement)
        cap["status"] = "accepted"
        cap["promoted_to"] = assumption.id
        return {"success": True, "message": f"Assumption: {statement[:40]}", "id": assumption.id}

    raise HTTPException(status_code=400, detail=f"Unknown capture kind: {kind}")


@app.post("/governor/research/capture/{capture_id}/reject")
async def reject_research_capture(capture_id: str) -> dict[str, Any]:
    """Reject a pending research capture."""
    if capture_id not in _pending_research_captures:
        raise HTTPException(status_code=404, detail="Capture not found.")

    cap = _pending_research_captures[capture_id]
    cap["status"] = "rejected"
    return {"success": True, "message": "Capture dismissed."}


@app.post("/governor/research/why")
async def research_why_overlay(request: Request) -> dict[str, Any]:
    """Per-turn Why overlay: what was injected vs what the assistant referenced.

    Expects: {"text": "assistant response text"}
    Returns: WhyOverlay dict with injected/referenced/floating/matched.
    """
    from governor.research_why import build_why_overlay

    body = await request.json()
    text = body.get("text", "")

    ctx, _ = _resolve_context()
    accepted_sources: list[str] = []
    accepted_claim_ids: list[str] = []

    if ctx is not None:
        try:
            from governor.research_store import ResearchStore

            store = ResearchStore(ctx.governor_dir)
            # Mirror the logic from _build_accepted_context
            active_claims = [
                c
                for c in store.claims.values()
                if c.status.value not in ("retracted", "superseded")
            ]
            active_claims.sort(key=lambda c: c.created_at, reverse=True)
            active_claims = active_claims[:20]

            seen_refs: set[str] = set()
            for claim in active_claims:
                if claim.source_ref and claim.source_ref not in seen_refs:
                    accepted_sources.append(claim.source_ref)
                    seen_refs.add(claim.source_ref)
            accepted_sources = accepted_sources[:25]
            accepted_claim_ids = [c.id for c in active_claims]
        except Exception:
            pass

    overlay = build_why_overlay(text, accepted_sources, accepted_claim_ids)
    return overlay.to_dict()


# ============================================================================
# Research Project Endpoints (structured workflow — parallel to code builder)
# ============================================================================


@app.get("/governor/research/project")
async def research_project_state() -> dict[str, Any]:
    """Full research project state for sidebar polling."""
    try:
        store = _get_research_project_store()
        return store.get_state()
    except Exception:
        return {
            "version": 0,
            "intent": {"text": "", "locked": False},
            "contract": {},
            "plan": {"phases": []},
            "files": {},
        }


@app.put("/governor/research/project/intent")
async def research_update_intent(request: IntentUpdateRequest) -> dict[str, Any]:
    """Update thesis / research question."""
    from gov_webui.project_store import StaleVersionError

    store = _get_research_project_store()
    try:
        intent = store.update_intent(request.text, request.locked, request.expected_version)
        return {"success": True, "intent": intent.model_dump()}
    except StaleVersionError:
        raise HTTPException(409, "Stale version")


@app.put("/governor/research/project/contract")
async def research_update_contract(request: ContractUpdateRequest) -> dict[str, Any]:
    """Update research scope / methodology."""
    from gov_webui.project_store import (
        Contract,
        ContractField,
        StaleVersionError,
        compute_config_hash,
    )

    inputs = [ContractField(**f) for f in request.inputs]
    outputs = [ContractField(**f) for f in request.outputs]

    # Server always recomputes hash — never trust client hashes
    config = request.config
    config_hash = None
    config_hash_full = None
    if config:
        # Guard against oversized configs (50KB soft cap)
        config_size = len(json.dumps(config))
        if config_size > 50_000:
            raise HTTPException(400, f"Config too large: {config_size} bytes (max 50000)")
        short, full = compute_config_hash(config)
        if request.config_hash and request.config_hash != short:
            raise HTTPException(
                400, f"Config hash mismatch: got {request.config_hash}, expected {short}"
            )
        config_hash = short
        config_hash_full = full

    contract = Contract(
        description=request.description,
        inputs=inputs,
        outputs=outputs,
        constraints=request.constraints,
        transport=request.transport,
        config=config,
        config_hash=config_hash,
        config_hash_full=config_hash_full,
    )
    store = _get_research_project_store()
    try:
        result = store.update_contract(contract, request.expected_version)
        return {"success": True, "contract": result.model_dump()}
    except StaleVersionError:
        raise HTTPException(409, "Stale version")


@app.post("/governor/research/project/plan/item")
async def research_add_plan_item(request: PlanItemRequest) -> dict[str, Any]:
    """Add a plan item to a research phase."""
    store = _get_research_project_store()
    try:
        item = store.add_plan_item(request.phase_idx, request.text)
        return {"success": True, "item": item.model_dump()}
    except ValueError as e:
        raise HTTPException(400, str(e))


@app.patch("/governor/research/project/plan/item/{item_id}")
async def research_update_plan_item(item_id: str, request: PlanItemStatusRequest) -> dict[str, Any]:
    """Update research plan item status."""
    from gov_webui.project_store import PlanItemStatus, StaleVersionError

    store = _get_research_project_store()
    try:
        status = PlanItemStatus(request.status)
    except ValueError:
        raise HTTPException(400, f"Invalid status: {request.status}")

    try:
        item = store.update_item_status(item_id, status, request.expected_version)
        return {"success": True, "item": item.model_dump()}
    except StaleVersionError:
        raise HTTPException(409, "Stale version")
    except ValueError as e:
        raise HTTPException(400, str(e))


@app.post("/governor/research/project/plan/phase")
async def research_add_phase(request: PhaseRequest) -> dict[str, Any]:
    """Add a new phase to the research plan."""
    store = _get_research_project_store()
    phase = store.add_phase(request.name)
    return {"success": True, "phase": phase.model_dump()}


@app.patch("/governor/research/project/plan/phase/{idx}")
async def research_update_phase(idx: int, request: PhaseUpdateRequest) -> dict[str, Any]:
    """Update research phase name/lock."""
    store = _get_research_project_store()
    try:
        phase = store.update_phase(idx, request.name, request.locked)
        return {"success": True, "phase": phase.model_dump()}
    except ValueError as e:
        raise HTTPException(400, str(e))


@app.get("/governor/research/project/files")
async def research_list_files() -> dict[str, Any]:
    """List research drafts/notes with versions + hashes."""
    store = _get_research_project_store()
    return {"files": store.list_files()}


@app.get("/governor/research/project/file-prev/{path:path}")
async def research_get_file_prev(path: str) -> dict[str, Any]:
    """Get previous version of a draft for client-side diff."""
    store = _get_research_project_store()
    try:
        content = store.get_file_prev(path)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"filepath": path, "content": content}


@app.get("/governor/research/project/files/{path:path}")
async def research_get_file(path: str) -> dict[str, Any]:
    """Get draft/note content."""
    store = _get_research_project_store()
    try:
        content = store.get_file_content(path)
    except ValueError as e:
        raise HTTPException(400, str(e))
    if content is None:
        raise HTTPException(404, f"File not found: {path}")
    return {"filepath": path, "content": content}


@app.put("/governor/research/project/files/{path:path}")
async def research_put_file(path: str, request: FileUpdateRequest) -> dict[str, Any]:
    """Accept draft/note, returns version + hash."""
    store = _get_research_project_store()
    try:
        entry = store.put_file(path, request.content, request.turn_id)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {
        "success": True,
        "filepath": path,
        "version": entry.version,
        "content_hash": entry.content_hash,
    }


@app.post("/governor/research/project/validate")
async def research_validate(request: RunRequest) -> dict[str, Any]:
    """Validate research drafts: citation checks, claim consistency.

    Uses the same request shape as /code/run for UI symmetry, but instead
    of executing code, runs text-based validation against registered claims.
    """
    store = _get_research_project_store()
    state = store.get_state()
    files = state.get("files", {})

    if not files:
        raise HTTPException(400, "No drafts in project")

    if request.filepath not in files:
        raise HTTPException(404, f"Draft not found: {request.filepath}")

    draft_content = store.get_file_content(request.filepath)
    if draft_content is None:
        raise HTTPException(404, f"Draft content missing: {request.filepath}")

    # -- Validation checks --------------------------------------------------
    findings: list[str] = []

    # 1. Cross-check against registered research claims
    try:
        rstore = _get_research_store()
        rstate = rstore.get_state()
        claims = rstate.get("claims", [])
        assumptions = rstate.get("assumptions", [])

        # Check for floating claims not referenced in draft
        for claim in claims:
            status = claim.get("status", "floating")
            content = claim.get("content", "")
            if status == "floating" and content:
                # Simple heuristic: first 40 chars as search key
                key = content[:40].lower()
                if key not in draft_content.lower():
                    findings.append(
                        f"Floating claim not referenced in draft: "
                        f'"{content[:60]}{"..." if len(content) > 60 else ""}"'
                    )

        # Check for unresolved assumptions
        for assumption in assumptions:
            status = assumption.get("status", "proposed")
            content = assumption.get("content", "")
            if status == "proposed" and content:
                findings.append(
                    f'Unresolved assumption: "{content[:60]}{"..." if len(content) > 60 else ""}"'
                )
    except Exception:
        pass  # Research store may not be initialized — that's fine

    # 2. Check for common citation patterns + config bans
    import re

    contract = state.get("contract", {})
    config = contract.get("config")

    # Build weasel patterns: default set + config bans
    weasel_patterns = [
        r"studies\s+show",
        r"research\s+suggests",
        r"it\s+is\s+widely\s+accepted",
        r"experts\s+agree",
        r"it\s+is\s+well\s+known",
        r"evidence\s+suggests",
    ]

    # Config bans feed the weasel validator as literal (case-insensitive) matches
    ban_literals: list[str] = []
    if config and config.get("bans"):
        for ban in config["bans"]:
            ban_stripped = ban.strip()
            if ban_stripped:
                ban_literals.append(ban_stripped.lower())

    lines = draft_content.split("\n")
    for i, line in enumerate(lines):
        line_lower = line.lower()
        # Check regex weasel patterns
        for pattern in weasel_patterns:
            if re.search(pattern, line_lower):
                # Check if line has a citation marker [N] or (Author, YYYY)
                if not re.search(r"\[\d+\]|\([A-Z]\w+,?\s*\d{4}\)", line):
                    findings.append(
                        f'Line {i + 1}: "{pattern}" without citation — '
                        f'"{line.strip()[:60]}{"..." if len(line.strip()) > 60 else ""}"'
                    )
        # Check literal ban matches
        for ban in ban_literals:
            if ban in line_lower:
                findings.append(
                    f'Line {i + 1}: banned phrase "{ban}" — '
                    f'"{line.strip()[:60]}{"..." if len(line.strip()) > 60 else ""}"'
                )

    # 3. Config-aware typed checks
    if config:
        # Table format check: config.format.tables == false
        fmt = config.get("format", {})
        if isinstance(fmt, dict) and fmt.get("tables") is False:
            # Detect markdown tables: header row + |---| separator row
            for i, line in enumerate(lines):
                if i + 1 < len(lines) and re.search(r"\|.*\|", line):
                    next_line = lines[i + 1]
                    if re.search(r"\|[\s-]+\|", next_line):
                        findings.append(
                            f"Line {i + 1}: markdown table found but tables are disabled"
                        )

        # Length band check
        length_band = config.get("length")
        if length_band and length_band in _LENGTH_BANDS:
            lo, hi = _LENGTH_BANDS[length_band]
            word_count = len(draft_content.split())
            if word_count < lo:
                findings.append(
                    f"Word count {word_count} below minimum {lo} for '{length_band}' band"
                )
            elif word_count > hi:
                findings.append(
                    f"Word count {word_count} above maximum {hi} for '{length_band}' band"
                )

        # Citations-required check (warn only)
        if config.get("citations") == "required":
            if not re.search(r"\[\d+\]|\([A-Z]\w+,?\s*\d{4}\)", draft_content):
                findings.append("Citations required but no citation markers found")

    # 4. Check for constraint violations from research scope
    constraints = contract.get("constraints", [])
    draft_lower = draft_content.lower()
    constraint_hits: list[str] = []
    for constraint in constraints:
        c_lower = constraint.lower()
        if any(neg in c_lower for neg in ["no ", "avoid ", "don't ", "never "]):
            for neg in ["no ", "avoid ", "don't ", "never "]:
                if c_lower.startswith(neg):
                    term = c_lower[len(neg) :].strip().rstrip(".")
                    if term and term in draft_lower:
                        constraint_hits.append(f'Scope constraint may be violated: "{constraint}"')
                    break

    findings.extend(constraint_hits)

    # 5. Strict mode: when strict=true, all findings are hard fails
    strict = config.get("strict", False) if config else False
    success = (
        len(findings) == 0
        if strict
        else (
            len([f for f in findings if "banned phrase" in f or "constraint" in f.lower()]) == 0
            if not strict and findings
            else len(findings) == 0
        )
    )
    # Simplify: strict=true means any finding fails, strict=false means success unless findings
    success = len(findings) == 0

    return {
        "success": success,
        "returncode": 0 if success else 1,
        "stdout": f"Validated {request.filepath}: "
        + (f"{len(findings)} finding(s)" if findings else "no issues found"),
        "stderr": "\n".join(findings) if findings else "",
        "filepath": request.filepath,
        "preflight_hit": False,
        "forced": False,
        "findings": findings,
    }


@app.get("/governor/corrections")
async def list_corrections(limit: int = 20) -> dict[str, Any]:
    """List past corrections/resolutions."""
    ctx, _ = _resolve_context()
    if ctx is None:
        return {"corrections": [], "message": "No governor context initialized."}

    from governor.violation_resolver import ViolationResolver

    resolver = ViolationResolver(
        governor_dir=ctx.governor_dir,
        mode=ctx.mode,
        context_id=ctx.context_id,
    )
    exceptions = resolver.list_exceptions()

    corrections = []
    for exc in exceptions[:limit]:
        corrections.append(
            {
                "id": exc.id,
                "action": exc.action.value,
                "anchor_id": exc.anchor_id,
                "summary": exc.scope,
                "created_at": exc.created_at.isoformat() if exc.created_at else None,
            }
        )

    return {"corrections": corrections}


# ============================================================================
# Sidecar UI
# ============================================================================

_STATIC_DIR = Path(__file__).resolve().parent / "static"


@app.get("/governor/ui", response_class=HTMLResponse)
async def governor_ui() -> HTMLResponse:
    """Serve the single-page Governor UI."""
    html_path = _STATIC_DIR / "governor.html"
    return HTMLResponse(
        content=html_path.read_text(encoding="utf-8"),
        headers={"Cache-Control": "no-cache, must-revalidate"},
    )


# ============================================================================
# Health / Root
# ============================================================================


@app.get("/health/live")
async def health_live() -> dict[str, Any]:
    """Process liveness only; suitable for restart decisions."""
    return {
        "status": "alive",
        "service": "marginalia",
        "deployment": deployment_metadata(),
    }


@app.get("/health")
async def health() -> dict[str, Any]:
    """Health check endpoint."""
    backend: dict[str, Any] = {"type": "unknown", "connected": False}
    contract_ok = False
    provider_reachable = False
    try:
        governed_chat = _get_governed_chat_adapter()
        await governed_chat.contract_info()
        backend = await governed_chat.provider()
        provider_reachable = bool(await governed_chat.models())
        contract_ok = True
    except Exception:
        pass

    cm = _get_context_manager()
    ctx = cm.get(GOVERNOR_CONTEXT_ID)

    return {
        "status": (
            "healthy"
            if contract_ok and backend.get("connected") and provider_reachable
            else "degraded"
        ),
        "backend": {
            "type": backend.get("type", "unknown"),
            "connected": bool(backend.get("connected")) and provider_reachable,
            "authoritative": "agent-governor-daemon",
        },
        "governor": {
            "context_id": GOVERNOR_CONTEXT_ID,
            "mode": GOVERNOR_MODE,
            "initialized": ctx is not None,
            "contract_ok": contract_ok,
            "daemon_dir": GOVERNOR_DAEMON_DIR,
        },
    }


@app.get("/health/ready", response_model=None)
async def health_ready() -> JSONResponse:
    """Readiness includes daemon connectivity and durable-schema validation."""
    runtime = await health()
    preflight = migration_preflight(
        data_root=Path(MARGINALIA_DATA_ROOT),
        default_context_id=GOVERNOR_CONTEXT_ID,
        apply_migrations=False,
    )
    ready = runtime["status"] == "healthy" and bool(preflight["ready"])
    return JSONResponse(
        status_code=200 if ready else 503,
        content={
            "status": "ready" if ready else "not_ready",
            "runtime": runtime,
            "migration": preflight,
            "deployment": deployment_metadata(),
        },
    )


@app.get("/v1/system")
async def system_status() -> dict[str, Any]:
    """Expose operational provenance without changing application state."""
    return {
        "service": "marginalia",
        "deployment": deployment_metadata(),
        "schemas": schema_versions(),
        "migration": migration_preflight(
            data_root=Path(MARGINALIA_DATA_ROOT),
            default_context_id=GOVERNOR_CONTEXT_ID,
            apply_migrations=False,
        ),
        "backup_destination": _get_backup_manager().backup_root_status(),
    }


@app.get("/")
async def root() -> HTMLResponse:
    """Serve the Marginalia fiction-writing room."""
    html_path = _STATIC_DIR / "index.html"
    return HTMLResponse(
        content=html_path.read_text(encoding="utf-8"),
        headers={"Cache-Control": "no-cache, must-revalidate"},
    )


# ============================================================================
# Export / Import
# ============================================================================


@app.get("/governor/export")
async def export_governor_state() -> dict[str, Any]:
    """Export all governor state as a single JSON object for portability."""
    ctx, _ = _resolve_context()
    if ctx is None:
        return {"mode": GOVERNOR_MODE, "anchors": [], "corrections": []}

    from governor.continuity import AnchorType, create_registry

    registry = create_registry(ctx.governor_dir)
    anchors = registry.all()

    # Serialize all anchors with full data
    anchor_list = []
    for a in anchors:
        entry: dict[str, Any] = {
            "id": a.id,
            "anchor_type": a.anchor_type.value
            if hasattr(a.anchor_type, "value")
            else str(a.anchor_type),
            "description": a.description,
            "severity": a.severity.value if hasattr(a.severity, "value") else str(a.severity),
        }
        if a.required_patterns:
            entry["required_patterns"] = a.required_patterns
        if a.forbidden_patterns:
            entry["forbidden_patterns"] = a.forbidden_patterns
        anchor_list.append(entry)

    # Corrections (exception log)
    corrections = []
    try:
        from governor.violation_resolver import ViolationResolver

        resolver = ViolationResolver(
            governor_dir=ctx.governor_dir,
            mode=ctx.mode,
            context_id=ctx.context_id,
        )
        for exc in resolver.list_exceptions():
            corrections.append(
                {
                    "action": exc.action.value if hasattr(exc.action, "value") else str(exc.action),
                    "anchor_id": exc.anchor_id,
                    "scope": exc.scope,
                    "summary": getattr(exc, "summary", ""),
                }
            )
    except Exception:
        pass

    result = {
        "version": 1,
        "mode": GOVERNOR_MODE,
        "exported_at": __import__("datetime").datetime.now().isoformat(),
        "anchors": anchor_list,
        "corrections": corrections,
    }

    # Research mode: include research store data
    if ctx.mode == "research":
        try:
            from governor.research_store import ResearchStore

            store = ResearchStore(ctx.governor_dir)
            result["research"] = store.export_data()
        except Exception:
            pass

    return result


@app.post("/governor/import")
async def import_governor_state(payload: dict[str, Any]) -> dict[str, Any]:
    """Import governor state from an exported JSON object."""
    ctx, _ = _resolve_context()
    if ctx is None:
        raise HTTPException(status_code=400, detail="No governor context initialized.")

    from governor.continuity import Anchor, AnchorType, Severity, create_registry

    registry = create_registry(ctx.governor_dir)

    anchors_data = payload.get("anchors", [])
    imported = 0
    skipped = 0

    type_map = {t.value: t for t in AnchorType}
    sev_map = {s.value: s for s in Severity}

    for entry in anchors_data:
        anchor_id = entry.get("id", "")
        if not anchor_id:
            skipped += 1
            continue

        # Skip if already exists
        if registry.get(anchor_id) is not None:
            skipped += 1
            continue

        anchor_type = type_map.get(entry.get("anchor_type", ""), AnchorType.CANON)
        severity = sev_map.get(entry.get("severity", ""), Severity.REJECT)

        anchor = Anchor(
            id=anchor_id,
            anchor_type=anchor_type,
            description=entry.get("description", ""),
            required_patterns=entry.get("required_patterns", []),
            forbidden_patterns=entry.get("forbidden_patterns", []),
            severity=severity,
        )
        registry.register(anchor)
        imported += 1

    if imported > 0:
        registry.save(ctx.governor_dir / "continuity" / "anchors.json")

    # Research mode: import research store data
    research_imported = 0
    if ctx.mode == "research" and "research" in payload:
        try:
            from governor.research_store import ResearchStore

            store = ResearchStore(ctx.governor_dir)
            research_imported = store.import_data(payload["research"])
        except Exception:
            pass

    total_imported = imported + research_imported

    return {
        "success": True,
        "imported": total_imported,
        "skipped": skipped,
        "message": f"Imported {total_imported} item(s), skipped {skipped} duplicate(s).",
    }


@app.get("/api/info")
async def api_info() -> dict[str, Any]:
    """JSON endpoint with API info and available endpoints."""
    try:
        backend = (await _get_governed_chat_adapter().provider()).get("type", "unknown")
    except Exception:
        backend = "unavailable"
    endpoints = {
        "ui": "/",
        "models": "/v1/models",
        "markdown": "/v1/markdown",
        "chat": "/v1/chat/completions",
        "backends": "/v1/backends",
        "governed_chat_pending": "/v1/governed-chat/pending",
        "governed_chat_resolve": "/v1/governed-chat/resolve",
        "project": "/v1/project",
        "projects": "/v1/projects",
        "workspaces": "/v1/workspaces",
        "project_export": "/v1/project/export",
        "project_export_zip": "/v1/project/export.zip",
        "project_snapshots": "/v1/project/snapshots",
        "project_search": "/v1/search",
        "project_entities": "/v1/entities",
        "manuscript": "/v1/manuscript",
        "health": "/health",
        "health_live": "/health/live",
        "health_ready": "/health/ready",
        "system": "/v1/system",
        "api_info": "/api/info",
        "sessions_list": "/sessions/",
        "sessions_create": "/sessions/",
        "sessions_get": "/sessions/{id}",
        "sessions_delete": "/sessions/{id}",
        "sessions_update": "/sessions/{id}",
        "sessions_append_message": "/sessions/{id}/messages",
        "sessions_fork": "/sessions/{id}/fork",
        "conversation_tree": "/v1/conversations/tree",
        "fiction_characters": "/governor/fiction/characters",
        "fiction_world_rules": "/governor/fiction/world-rules",
        "fiction_forbidden": "/governor/fiction/forbidden",
        "fiction_capture_scan": "/governor/fiction/capture/scan",
        "fiction_captures": "/governor/fiction/captures",
        "artifacts": "/governor/artifacts",
        "workspace_backups": "/v1/workspaces/{workspace_id}/backups",
    }
    if MARGINALIA_ENABLE_DONOR_ROUTES:
        endpoints.update(
            {
                "governor_contexts": "/governor/contexts",
                "governor_status": "/governor/status",
                "governor_now": "/governor/now",
                "governor_why": "/governor/why",
                "governor_history": "/governor/history",
                "governor_detail": "/governor/detail/{item_id}",
                "governor_corrections": "/governor/corrections",
                "governor_ui": "/governor/ui",
                "code_decisions": "/governor/code/decisions",
                "code_constraints": "/governor/code/constraints",
                "research_state": "/governor/research/state",
                "research_claims": "/governor/research/claims",
                "research_assumptions": "/governor/research/assumptions",
                "research_uncertainties": "/governor/research/uncertainties",
                "research_links": "/governor/research/links",
                "governor_export": "/governor/export",
                "governor_import": "/governor/import",
                "v2_runs": "/v2/runs",
                "v2_run_detail": "/v2/runs/{run_id}",
                "v2_run_events": "/v2/runs/{run_id}/events",
                "v2_run_claims": "/v2/runs/{run_id}/claims",
                "v2_run_violations": "/v2/runs/{run_id}/violations",
                "v2_run_report": "/v2/runs/{run_id}/report",
                "v2_run_cancel": "/v2/runs/{run_id}/cancel",
                "v2_runs_compare": "/v2/runs/compare",
                "v2_artifacts": "/v2/artifacts",
                "v2_artifact": "/v2/artifacts/{hash}",
                "v2_controls_schema": "/v2/controls/schema",
                "v2_controls_templates": "/v2/controls/templates",
                "v2_profiles": "/v2/profiles",
                "v2_anchors": "/v2/anchors",
                "v2_backends": "/v2/backends",
                "v2_dashboard_summary": "/v2/dashboard/summary",
                "v2_dashboard_regime": "/v2/dashboard/regime",
                "v2_demos": "/v2/demos",
                "v2_demo_playwright": "/v2/demos/{name}/playwright",
                "dashboard": "/dashboard",
                "v2_intent_templates": "/v2/intent/templates",
                "v2_intent_schema": "/v2/intent/schema/{template_name}",
                "v2_intent_validate": "/v2/intent/validate",
                "v2_intent_compile": "/v2/intent/compile",
                "v2_intent_policy": "/v2/intent/policy",
            }
        )
    return {
        "name": "Marginalia",
        "version": _webui_version(),
        "deployment": deployment_metadata(),
        "schemas": schema_versions(),
        "backend": backend,
        "provider_owner": "agent-governor-daemon",
        "openai_compatible": True,
        "governor_context": GOVERNOR_CONTEXT_ID,
        "governor_mode": GOVERNOR_MODE,
        "endpoints": endpoints,
    }


# ============================================================================
# V2 Dashboard API — Run-centric governance dashboard
# ============================================================================

if MARGINALIA_ENABLE_DONOR_ROUTES:
    from governor.dashboard_ux import (
        BUILTIN_ACTIONS,
        BUILTIN_TEMPLATES,
        CancelRequest,
        DashboardStore,
        RunSummary,
        RunVerdict,
        StreamEvent,
        StreamEventType,
        build_controls_schema,
        generate_report,
        make_heartbeat,
    )
    from governor.instrument import EventWriter, InstrumentSystem, RunManifest

# Lazy-init singletons for v2 dashboard
_dashboard_store: DashboardStore | None = None
_instrument_system: InstrumentSystem | None = None


def _get_dashboard_store() -> DashboardStore:
    global _dashboard_store
    if _dashboard_store is None:
        cm = _get_context_manager()
        ctx = cm.get_or_create(GOVERNOR_CONTEXT_ID, mode=GOVERNOR_MODE)
        _dashboard_store = DashboardStore(ctx.governor_dir)
    return _dashboard_store


def _get_instrument_system() -> InstrumentSystem:
    global _instrument_system
    if _instrument_system is None:
        cm = _get_context_manager()
        ctx = cm.get_or_create(GOVERNOR_CONTEXT_ID, mode=GOVERNOR_MODE)
        _instrument_system = InstrumentSystem(ctx.governor_dir)
    return _instrument_system


# Pydantic models for v2 API


class CreateRunRequest(BaseModel):
    task: str
    profile: str = "established"
    backend: str = ""
    scope: list[str] = Field(default_factory=list)
    seed: int | None = None


class CancelRunResponse(BaseModel):
    run_id: str
    acknowledged_at: str


# Track active cancel requests
_cancel_requests: dict[str, CancelRequest] = {}


# ---- Runs ----


@app.post("/v2/runs")
async def v2_create_run(request: CreateRunRequest) -> dict[str, Any]:
    """Create a new instrumented run."""
    from governor.instrument import Actor, ActorKind, InstrumentProfile, RunInputs

    system = _get_instrument_system()
    store = _get_dashboard_store()

    # Map profile string to InstrumentProfile
    profile_map = {
        "greenfield": InstrumentProfile.GREENFIELD,
        "strict": InstrumentProfile.STRICT,
        "forensic": InstrumentProfile.FORENSIC,
    }
    profile = profile_map.get(request.profile, InstrumentProfile.GREENFIELD)

    manifest, writer = system.start_run(
        actor=Actor(ActorKind.HUMAN, "dashboard"),
        profile=profile,
        task_id=request.task,
    )

    # Record in dashboard store
    summary = RunSummary(
        run_id=manifest.run_id,
        created_at=manifest.created_at,
        model=request.backend or _current_backend_type,
        profile=request.profile,
        verdict=RunVerdict.PENDING,
        task=request.task,
    )
    store.record_run(summary)

    return {
        "run_id": manifest.run_id,
        "created_at": manifest.created_at,
        "profile": request.profile,
        "task": request.task,
    }


@app.get("/v2/runs")
async def v2_list_runs(
    profile: str = "",
    verdict: str = "",
    limit: int = 50,
) -> dict[str, Any]:
    """List runs with optional filters."""
    store = _get_dashboard_store()
    runs = store.list_runs(profile=profile, verdict=verdict, limit=limit)
    return {"runs": [r.to_dict() for r in runs]}


@app.get("/v2/runs/{run_id}")
async def v2_get_run(run_id: str) -> dict[str, Any]:
    """Get run detail (manifest + summary)."""
    system = _get_instrument_system()
    store = _get_dashboard_store()

    manifest = system.run_store.load_manifest(run_id)
    summary = store.get_run(run_id)

    if manifest is None and summary is None:
        raise HTTPException(status_code=404, detail=f"Run not found: {run_id}")

    result: dict[str, Any] = {}
    if manifest:
        result["manifest"] = manifest.to_dict()
    if summary:
        result["summary"] = summary.to_dict()

    return result


@app.get("/v2/runs/{run_id}/events")
async def v2_run_events(run_id: str, stream: bool = False) -> Any:
    """Get events for a run. If stream=true, returns SSE."""
    system = _get_instrument_system()
    run_dir = system.instrument_dir / "runs" / run_id

    if not run_dir.exists():
        raise HTTPException(status_code=404, detail=f"Run not found: {run_id}")

    writer = EventWriter(run_dir, system.artifact_store, system.config.artifact_size_threshold)

    if stream:

        async def event_stream():
            events = writer.read_events()
            for ev in events:
                se = StreamEvent(
                    event_type=StreamEventType.EVENT,
                    data=ev.to_dict(),
                )
                yield se.to_sse() + "\n"
            # End with heartbeat
            yield make_heartbeat().to_sse() + "\n"

        return StreamingResponse(event_stream(), media_type="text/event-stream")

    events = writer.read_events()
    return {"events": [e.to_dict() for e in events]}


@app.get("/v2/runs/{run_id}/claims")
async def v2_run_claims(run_id: str) -> dict[str, Any]:
    """Get claims for a run."""
    from governor.instrument import ClaimExtractor

    system = _get_instrument_system()
    run_dir = system.instrument_dir / "runs" / run_id

    if not run_dir.exists():
        raise HTTPException(status_code=404, detail=f"Run not found: {run_id}")

    extractor = ClaimExtractor(run_dir)
    claims = extractor.read_claims()
    return {"claims": [c.to_dict() for c in claims]}


@app.get("/v2/runs/{run_id}/violations")
async def v2_run_violations(run_id: str) -> dict[str, Any]:
    """Get violations for a run (policy decisions with non-pass verdict)."""
    system = _get_instrument_system()
    run_dir = system.instrument_dir / "runs" / run_id

    if not run_dir.exists():
        raise HTTPException(status_code=404, detail=f"Run not found: {run_id}")

    from governor.instrument import EventKind

    writer = EventWriter(run_dir, system.artifact_store, system.config.artifact_size_threshold)
    events = writer.read_events()

    violations = []
    for ev in events:
        if ev.kind == EventKind.POLICY_DECISION:
            verdict = ev.payload.get("verdict", "")
            if verdict and verdict != "pass":
                violations.append(ev.to_dict())

    return {"violations": violations}


@app.get("/v2/runs/{run_id}/report")
async def v2_run_report(run_id: str) -> dict[str, Any]:
    """Generate report for a run."""
    system = _get_instrument_system()
    store = _get_dashboard_store()

    manifest = system.run_store.load_manifest(run_id)
    if manifest is None:
        raise HTTPException(status_code=404, detail=f"Run not found: {run_id}")

    from governor.instrument import ClaimExtractor

    run_dir = system.instrument_dir / "runs" / run_id
    writer = EventWriter(run_dir, system.artifact_store, system.config.artifact_size_threshold)
    events = writer.read_events()

    extractor = ClaimExtractor(run_dir)
    claims = extractor.read_claims()

    report = generate_report(
        run_id=run_id,
        manifest=manifest.to_dict(),
        events=[e.to_dict() for e in events],
        claims=[c.to_dict() for c in claims],
    )

    store.save_report(report)
    return report.to_dict()


@app.post("/v2/runs/{run_id}/cancel")
async def v2_cancel_run(run_id: str) -> dict[str, Any]:
    """Cancel an active run."""
    cancel = CancelRequest(run_id=run_id)
    cancel.acknowledge()
    _cancel_requests[run_id] = cancel

    return {
        "run_id": run_id,
        "acknowledged_at": cancel.acknowledged_at,
    }


@app.post("/v2/runs/compare")
async def v2_compare_runs() -> dict[str, Any]:
    """Placeholder for interferometry comparison."""
    return {"status": "not_implemented", "message": "Use /governor/code/compare instead."}


# ---- Artifacts ----


@app.get("/v2/artifacts/{artifact_hash}")
async def v2_get_artifact(artifact_hash: str) -> Any:
    """Retrieve a content-addressed artifact blob."""
    system = _get_instrument_system()
    data = system.artifact_store.retrieve(artifact_hash)
    if data is None:
        raise HTTPException(status_code=404, detail=f"Artifact not found: {artifact_hash}")

    from fastapi.responses import Response

    return Response(content=data, media_type="application/octet-stream")


@app.get("/v2/artifacts")
async def v2_list_artifacts(run_id: str = "") -> dict[str, Any]:
    """List artifacts for a run."""
    if not run_id:
        return {"artifacts": []}

    system = _get_instrument_system()
    run_dir = system.instrument_dir / "runs" / run_id
    if not run_dir.exists():
        return {"artifacts": []}

    writer = EventWriter(run_dir, system.artifact_store, system.config.artifact_size_threshold)
    receipts = writer.read_receipts()
    return {"artifacts": [r.to_dict() for r in receipts]}


# ---- Controls ----


@app.get("/v2/controls/schema")
async def v2_controls_schema() -> dict[str, Any]:
    """Return controls schema for the dashboard left panel."""
    return build_controls_schema()


@app.get("/v2/controls/templates")
async def v2_controls_templates() -> dict[str, Any]:
    """Return built-in run templates."""
    return {"templates": [t.to_dict() for t in BUILTIN_TEMPLATES]}


@app.get("/v2/profiles")
async def v2_list_profiles() -> dict[str, Any]:
    """Return available governance profiles."""
    from governor.profiles import BUILTIN_PROFILES

    return {"profiles": list(BUILTIN_PROFILES.keys())}


@app.get("/v2/anchors")
async def v2_list_anchors() -> dict[str, Any]:
    """Return active anchors (read-only)."""
    ctx, _ = _resolve_context()
    if ctx is None:
        return {"anchors": []}

    from governor.continuity import create_registry

    try:
        registry = create_registry(ctx.governor_dir)
        anchors = registry.all()
        return {
            "anchors": [
                {
                    "id": a.id,
                    "type": a.anchor_type.value
                    if hasattr(a.anchor_type, "value")
                    else str(a.anchor_type),
                    "description": a.description,
                    "severity": a.severity.value
                    if hasattr(a.severity, "value")
                    else str(a.severity),
                }
                for a in anchors
            ]
        }
    except Exception:
        return {"anchors": []}


@app.get("/v2/backends")
async def v2_list_backends() -> dict[str, Any]:
    """List available backends (delegates to v1)."""
    return await list_backends()


@app.post("/v2/backends/switch")
async def v2_switch_backend(request: BackendSwitchRequest) -> dict[str, Any]:
    """Switch backend (delegates to v1)."""
    return await switch_backend(request)


# ---- Dashboard ----


@app.get("/v2/dashboard/summary")
async def v2_dashboard_summary() -> dict[str, Any]:
    """Return aggregate dashboard statistics."""
    store = _get_dashboard_store()
    summary = store.dashboard_summary()
    return summary.to_dict()


@app.get("/v2/dashboard/regime")
async def v2_dashboard_regime() -> dict[str, Any]:
    """Return current regime state."""
    ctx, _ = _resolve_context()
    if ctx is None:
        return {"regime": None}

    vm = _build_vm_for_context(ctx)
    return {
        "regime": vm.regime.name if vm.regime else None,
        "session": vm.session.to_dict() if vm.session else {},
    }


# ============================================================================
# V2 Intent Compiler — Structured hypothesis-collapse for governance sessions
# ============================================================================


class IntentValidateRequest(BaseModel):
    schema_id: str
    values: dict[str, Any]
    escape_text: str | None = None


class IntentCompileRequest(BaseModel):
    schema_id: str
    values: dict[str, Any]
    escape_text: str | None = None
    template_name: str = "session_start"


@app.get("/v2/intent/templates")
async def v2_intent_templates() -> dict[str, Any]:
    """List available intent form templates."""
    from governor.intent_compiler import list_templates

    return {"templates": list_templates()}


@app.get("/v2/intent/schema/{template_name}")
async def v2_intent_schema(template_name: str) -> dict[str, Any]:
    """Build and return an IntentFormSchema for the current mode."""
    from governor.intent_compiler import build_form_schema

    ctx, _ = _resolve_context()
    mode = ctx.mode if ctx else GOVERNOR_MODE

    try:
        schema = build_form_schema(template_name, mode=mode)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))

    return schema.to_dict()


@app.post("/v2/intent/validate")
async def v2_intent_validate(request: IntentValidateRequest) -> dict[str, Any]:
    """Validate a response against its schema."""
    from governor.intent_compiler import (
        IntentFormResponse,
        IntentFormSchema,
        build_form_schema,
        validate_response,
    )

    ctx, _ = _resolve_context()
    mode = ctx.mode if ctx else GOVERNOR_MODE

    response = IntentFormResponse(
        schema_id=request.schema_id,
        values=request.values,
        escape_text=request.escape_text,
    )

    # Try to rebuild the schema to validate against
    # The caller should have gotten schema from /v2/intent/schema/{template}
    # We need the template_name to rebuild — check all templates
    from governor.intent_compiler import BUILTIN_TEMPLATES

    schema = None
    for tname in BUILTIN_TEMPLATES:
        try:
            candidate = build_form_schema(tname, mode=mode)
            if candidate.schema_id == request.schema_id:
                schema = candidate
                break
        except ValueError:
            continue

    if schema is None:
        return {
            "valid": False,
            "errors": [f"Schema ID '{request.schema_id}' not found for mode '{mode}'"],
        }

    errors = validate_response(response, schema)
    return {"valid": len(errors) == 0, "errors": errors}


@app.post("/v2/intent/compile")
async def v2_intent_compile(request: IntentCompileRequest) -> dict[str, Any]:
    """Compile a form response into governance intent + constraints."""
    from governor.intent_compiler import (
        IntentFormResponse,
        build_form_schema,
        compile_intent,
    )

    ctx, _ = _resolve_context()
    mode = ctx.mode if ctx else GOVERNOR_MODE
    governor_dir = ctx.governor_dir if ctx else None

    try:
        schema = build_form_schema(request.template_name, mode=mode)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    response = IntentFormResponse(
        schema_id=request.schema_id,
        values=request.values,
        escape_text=request.escape_text,
    )

    result = compile_intent(response, schema, governor_dir=governor_dir)
    return result.to_dict()


@app.get("/v2/intent/policy")
async def v2_intent_policy() -> dict[str, Any]:
    """Return the current form policy for the active mode."""
    from governor.intent_compiler import get_form_policy

    ctx, _ = _resolve_context()
    mode = ctx.mode if ctx else GOVERNOR_MODE
    policy = get_form_policy(mode)
    return {"mode": mode, "policy": policy.value}


# ---- Dashboard UI ----

# ---- Demos ----


@app.get("/v2/demos")
async def v2_list_demos() -> dict[str, Any]:
    """List demo scenarios with freshness status."""
    from governor.webui_demo import DemoStore, BUILTIN_DEMOS

    cm = _get_context_manager()
    ctx = cm.get(GOVERNOR_CONTEXT_ID)
    gov_dir = ctx.governor_dir if ctx else Path(".governor")

    store = DemoStore(governor_dir=gov_dir)
    freshness = store.check_freshness()

    demos = []
    for demo in BUILTIN_DEMOS:
        fr = next((f for f in freshness if f["name"] == demo.name), None)
        demos.append(
            {
                "name": demo.name,
                "description": demo.description,
                "surface": demo.surface.value,
                "tags": demo.tags,
                "step_count": len(demo.steps),
                "screenshot_count": len(demo.screenshot_paths),
                "status": fr["status"] if fr else "missing",
            }
        )

    return {"demos": demos}


@app.get("/v2/demos/{name}/playwright")
async def v2_demo_playwright(name: str) -> dict[str, Any]:
    """Return generated Playwright spec text for a demo scenario."""
    from governor.webui_demo import BUILTIN_DEMOS, DemoStore, generate_playwright_spec

    # Search built-in demos first
    scenario = next((d for d in BUILTIN_DEMOS if d.name == name), None)

    # Fall back to custom scenarios in store
    if scenario is None:
        cm = _get_context_manager()
        ctx = cm.get(GOVERNOR_CONTEXT_ID)
        gov_dir = ctx.governor_dir if ctx else Path(".governor")
        store = DemoStore(governor_dir=gov_dir)
        for s in store.list_scenarios():
            if s.name == name:
                scenario = s
                break

    if scenario is None:
        raise HTTPException(status_code=404, detail=f"Demo scenario not found: {name}")

    spec_text = generate_playwright_spec(scenario)
    return {"name": name, "spec": spec_text}


@app.get("/dashboard", response_class=HTMLResponse)
async def dashboard_ui() -> HTMLResponse:
    """Serve the v2 governance dashboard."""
    html_path = _STATIC_DIR / "dashboard.html"
    if not html_path.exists():
        raise HTTPException(status_code=404, detail="Dashboard HTML not found")
    return HTMLResponse(
        content=html_path.read_text(encoding="utf-8"),
        headers={"Cache-Control": "no-cache, must-revalidate"},
    )


# ============================================================================
# Artifact Engine — response helpers + routes
# ============================================================================


def _artifact_meta_to_dict(
    meta: Any,
    *,
    include_versions: bool = False,
    project_id: str = "",
) -> dict:
    """Convert ArtifactMeta to dict, optionally including version history."""
    d: dict[str, Any] = {
        "id": meta.id,
        "title": meta.title,
        "kind": meta.kind,
        "artifact_type": meta.artifact_type,
        "project_id": meta.project_id or project_id,
        "provenance": meta.provenance.model_dump(mode="json"),
        "status": meta.status,
        "tags": list(meta.tags),
        "trashed_at": meta.trashed_at,
        "working_copy_updated_at": meta.working_copy_updated_at,
        "working_copy_base_version": meta.working_copy_base_version,
        "language": meta.language,
        "current_version": meta.current_version,
        "created_at": meta.created_at,
        "updated_at": meta.updated_at,
    }
    if include_versions:
        d["versions"] = [
            {
                "version": v.version,
                "created_at": v.created_at,
                "content_hash": v.content_hash,
                "source": v.source,
                "message_id": v.message_id,
                "source_turn_seq": v.source_turn_seq,
            }
            for v in meta.versions
        ]
    return d


def _artifact_detail_response(
    *,
    meta: Any,
    content: str,
    index_version: int,
    style_corrections: list[dict] | None = None,
    style_status: dict | None = None,
    project_id: str = "",
) -> dict:
    resp: dict = {
        "ok": True,
        "index_version": index_version,
        "artifact": _artifact_meta_to_dict(
            meta, include_versions=True, project_id=project_id
        ),
        "content": content,
        "word_count": _word_count(content),
    }
    if style_status is not None:
        resp["style_status"] = style_status
        resp["style_corrections"] = style_corrections or []
    return resp


def _apply_style_policy(content: str) -> tuple[str, list[dict], dict | None]:
    """Style-check/fix content based on GOVERNOR_MODE.

    Returns (possibly_fixed_content, corrections_list, style_status).
    style_status is None if mode has no profile.
    """
    from gov_webui.style_policy import (
        action_for_mode,
        check,
        corrections_to_dicts,
        fix,
        profile_for_mode,
    )

    profile = profile_for_mode(GOVERNOR_MODE)
    if profile is None:
        return content, [], None

    action = action_for_mode(GOVERNOR_MODE)
    if action == "fix":
        fixed, corrections = fix(content, profile)
        return (
            fixed,
            corrections_to_dicts(corrections),
            {
                "profile": profile,
                "action": action,
                "corrections_applied": len(corrections) > 0,
                "correction_count": len(corrections),
            },
        )
    else:
        # warn mode — check only, don't modify
        corrections = check(content, profile)
        return (
            content,
            corrections_to_dicts(corrections),
            {
                "profile": profile,
                "action": action,
                "corrections_applied": False,
                "correction_count": len(corrections),
            },
        )


def _artifact_list_response(
    *, summaries: list, index_version: int, project_id: str = ""
) -> dict:
    return {
        "ok": True,
        "index_version": index_version,
        "artifacts": [
            {
                "id": s.id,
                "title": s.title,
                "kind": s.kind,
                "artifact_type": s.artifact_type,
                "project_id": s.project_id or project_id,
                "provenance": s.provenance.model_dump(mode="json"),
                "status": s.status,
                "tags": list(s.tags),
                "trashed_at": s.trashed_at,
                "working_copy_updated_at": s.working_copy_updated_at,
                "working_copy_base_version": s.working_copy_base_version,
                "language": s.language,
                "current_version": s.current_version,
                "created_at": s.created_at,
                "updated_at": s.updated_at,
            }
            for s in summaries
        ],
    }


def _artifact_error(
    *, status_code: int, code: str, message: str, details: dict | None = None
) -> JSONResponse:
    body: dict[str, Any] = {
        "ok": False,
        "error": {"code": code, "message": message},
    }
    if details:
        body["error"]["details"] = details
    return JSONResponse(content=body, status_code=status_code)


def _artifact_exception_response(exc: Exception) -> JSONResponse:
    """Map artifact store exceptions to structured JSON error responses."""
    from gov_webui.artifact_store import (
        ArtifactContentMissingError,
        ArtifactNotFoundError,
        ArtifactValidationError,
        ArtifactVersionNotFoundError,
        StaleArtifactVersionError,
    )

    if isinstance(exc, ArtifactNotFoundError):
        return _artifact_error(
            status_code=404,
            code="artifact_not_found",
            message=str(exc),
            details={"artifact_id": exc.artifact_id},
        )
    if isinstance(exc, ArtifactVersionNotFoundError):
        return _artifact_error(
            status_code=404,
            code="artifact_version_not_found",
            message=str(exc),
            details={"artifact_id": exc.artifact_id, "version": exc.version},
        )
    if isinstance(exc, StaleArtifactVersionError):
        return _artifact_error(
            status_code=409,
            code="stale_version",
            message=str(exc),
            details={
                "artifact_id": exc.artifact_id,
                "expected_current_version": exc.expected_current_version,
                "current_version": exc.current_version,
                "index_version": exc.index_version,
            },
        )
    if isinstance(exc, ArtifactValidationError):
        return _artifact_error(
            status_code=422,
            code="validation_error",
            message=str(exc),
        )
    if isinstance(exc, ArtifactContentMissingError):
        return _artifact_error(
            status_code=500,
            code="artifact_content_missing",
            message=str(exc),
            details={
                "artifact_id": exc.artifact_id,
                "version": exc.version,
                "path": exc.path,
            },
        )
    # Fallback for unexpected errors
    return _artifact_error(status_code=500, code="internal_error", message=str(exc))


@app.get("/governor/artifacts")
async def artifacts_list(
    project_id: str | None = None,
    view: str = "all",
    q: str = "",
    status: str | None = None,
    tag: str | None = None,
) -> dict:
    """List/filter/search artifacts without mutating their durable records."""
    from gov_webui.artifact_store import ArtifactStoreError

    try:
        project = _project_record(project_id)
        store = _get_artifact_store(project.id)
        if view not in {"active", "trash", "all"}:
            raise HTTPException(status_code=422, detail="invalid artifact view")
        summaries, idx_ver = store.list_all()
        if view == "active":
            summaries = [item for item in summaries if item.trashed_at is None]
        elif view == "trash":
            summaries = [item for item in summaries if item.trashed_at is not None]
        if status is not None:
            summaries = [item for item in summaries if item.status == status]
        if tag is not None:
            wanted_tag = tag.casefold()
            summaries = [
                item for item in summaries
                if any(value.casefold() == wanted_tag for value in item.tags)
            ]
        query = q.strip().casefold()
        if query:
            matching = []
            for item in summaries:
                _, content, _ = store.get(item.id)
                searchable = "\n".join([item.title, content, " ".join(item.tags)]).casefold()
                if query in searchable:
                    matching.append(item)
            summaries = matching
        response = _artifact_list_response(
            summaries=summaries,
            index_version=idx_ver,
            project_id=project.id,
        )
        for item in response["artifacts"]:
            try:
                _, content, _ = store.get(item["id"])
                item["word_count"] = _word_count(content)
            except ArtifactStoreError:
                item["word_count"] = 0
        return response
    except ArtifactStoreError as exc:
        return _artifact_exception_response(exc)


@app.post("/governor/artifacts", status_code=201)
async def artifacts_create(request: ArtifactCreateRequest) -> JSONResponse:
    """Create a new artifact."""
    from gov_webui.artifact_store import ArtifactStoreError

    try:
        project = _project_record(request.project_id)
        store = _get_artifact_store(project.id)
        source_message_ids = list(request.source_message_ids)
        if request.message_id and request.message_id not in source_message_ids:
            source_message_ids.append(request.message_id)
        if request.conversation_id:
            lifecycle, _, session = _conversation_location(request.conversation_id)
            if lifecycle.project_id != project.id:
                raise HTTPException(
                    status_code=422,
                    detail="artifact provenance conversation belongs to another project",
                )
            known_ids = {message.id for message in session.messages}
            missing = [item for item in source_message_ids if item not in known_ids]
            if missing:
                raise HTTPException(
                    status_code=422,
                    detail=f"artifact provenance messages not found: {', '.join(missing)}",
                )
        styled_content, style_corr, style_status = _apply_style_policy(request.content)
        meta, content, idx_ver = store.create(
            title=request.title,
            content=styled_content,
            kind=request.kind,
            artifact_type=request.artifact_type,
            project_id=project.id,
            status=request.status,
            tags=request.tags,
            language=request.language,
            message_id=request.message_id,
            conversation_id=request.conversation_id,
            source_message_ids=source_message_ids,
            source=request.source,
            source_turn_seq=request.source_turn_seq,
        )
        return JSONResponse(
            content=_artifact_detail_response(
                meta=meta,
                content=content,
                index_version=idx_ver,
                style_corrections=style_corr,
                style_status=style_status,
                project_id=project.id,
            ),
            status_code=201,
        )
    except ArtifactStoreError as exc:
        return _artifact_exception_response(exc)


@app.post("/governor/artifacts/{artifact_id}/canon-proposal", status_code=201)
async def artifacts_propose_canon(
    artifact_id: str,
    request: ArtifactCanonProposalRequest,
) -> dict[str, Any]:
    """Place artifact text in the review queue; this never writes canon directly."""
    from gov_webui.artifact_store import ArtifactStoreError

    if request.kind not in {"character", "relationship", "world_rule", "constraint"}:
        raise HTTPException(status_code=422, detail="invalid canon proposal kind")
    try:
        project = _project_record(request.project_id)
        artifact, content, _ = _get_artifact_store(project.id).get(artifact_id)
        statement = (request.statement if request.statement is not None else content).strip()
        if not statement:
            raise HTTPException(status_code=422, detail="canon proposal must not be empty")
        source_messages = artifact.provenance.message_ids
        candidate = _get_canon_review_store(project.id).add(
            kind=request.kind,
            confidence=1.0,
            subject=request.subject.strip() or (
                artifact.title if request.kind in {"character", "relationship"} else ""
            ),
            statement=statement,
            conversation_id=artifact.provenance.conversation_id,
            message_id=source_messages[0] if source_messages else "",
            draft={
                "source": "artifact",
                "artifact_id": artifact.id,
                "artifact_version": artifact.current_version,
                "artifact_type": artifact.artifact_type,
                "source_message_ids": source_messages,
            },
        )
        return {
            "canonical": False,
            "review_required": True,
            "candidate": candidate.model_dump(mode="json"),
        }
    except ArtifactStoreError as exc:
        return _artifact_exception_response(exc)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.get("/governor/artifacts/{artifact_id}/canon-comparison")
async def artifacts_compare_canon(
    artifact_id: str,
    project_id: str | None = None,
    include_working_copy: bool = True,
) -> dict[str, Any]:
    """Run the existing deterministic continuity checker against accepted canon."""
    from gov_webui.artifact_store import ArtifactStoreError
    from governor.continuity import ContinuityChecker, create_registry

    try:
        project = _project_record(project_id)
        store = _get_artifact_store(project.id)
        artifact, committed, _ = store.get(artifact_id)
        working_copy, _ = store.get_working_copy(artifact_id)
        content = working_copy if include_working_copy and working_copy is not None else committed
        context = _get_context_manager().get_or_create(project.context_id, mode="fiction")
        anchors = create_registry(context.governor_dir).all()
        report = ContinuityChecker().check(content, anchors)
        lowered = content.casefold()
        references = []
        for anchor in anchors:
            human_id = re.sub(
                r"^(char|rule|forbid)-",
                "",
                anchor.id,
                flags=re.IGNORECASE,
            ).replace("-", " ")
            terms = [human_id, *anchor.required_patterns, *anchor.forbidden_patterns]
            references.append(
                {
                    "id": anchor.id,
                    "type": anchor.anchor_type.value,
                    "description": anchor.description,
                    "severity": anchor.severity.value,
                    "mentioned": any(
                        term.strip() and term.casefold() in lowered for term in terms
                    ),
                }
            )
        return {
            "ok": True,
            "project_id": project.id,
            "artifact_id": artifact.id,
            "artifact_version": artifact.current_version,
            "working_copy_used": include_working_copy and working_copy is not None,
            "canonical_anchor_count": len(anchors),
            "canonical_references": references,
            "continuity": report.to_dict(),
            "canonical_content_changed": False,
        }
    except ArtifactStoreError as exc:
        return _artifact_exception_response(exc)


@app.get("/governor/artifacts/state")
async def artifacts_state(project_id: str | None = None) -> dict:
    """Quick poll endpoint for artifact index version."""
    from gov_webui.artifact_store import ArtifactStoreError

    try:
        project = _project_record(project_id)
        store = _get_artifact_store(project.id)
        state = store.get_state()
        return {
            "ok": True,
            "index_version": state["version"],
            "updated_at": state["updated_at"],
            "count": state["count"],
        }
    except ArtifactStoreError as exc:
        return _artifact_exception_response(exc)


@app.get("/governor/artifacts/{artifact_id}")
async def artifacts_get(artifact_id: str, project_id: str | None = None) -> dict:
    """Get artifact detail + latest content."""
    from gov_webui.artifact_store import ArtifactStoreError

    try:
        project = _project_record(project_id)
        store = _get_artifact_store(project.id)
        meta, content, idx_ver = store.get(artifact_id)
        # Informational check — never modifies stored content on GET
        _, style_corr, style_status = _apply_style_policy(content)
        if style_status is not None:
            style_status["corrections_applied"] = False
        response = _artifact_detail_response(
            meta=meta,
            content=content,
            index_version=idx_ver,
            style_corrections=style_corr,
            style_status=style_status,
            project_id=project.id,
        )
        working_copy, base_version = store.get_working_copy(artifact_id)
        response["working_copy"] = working_copy
        response["working_copy_base_version"] = base_version
        return response
    except ArtifactStoreError as exc:
        return _artifact_exception_response(exc)


@app.put("/governor/artifacts/{artifact_id}")
async def artifacts_update(
    artifact_id: str,
    request: ArtifactUpdateRequest,
    project_id: str | None = None,
) -> dict:
    """Update artifact content (creates new version)."""
    from gov_webui.artifact_store import ArtifactStoreError

    try:
        project = _project_record(project_id)
        store = _get_artifact_store(project.id)
        styled_content, style_corr, style_status = _apply_style_policy(request.content)
        meta, content, idx_ver = store.update(
            artifact_id,
            content=styled_content,
            title=request.title,
            expected_current_version=request.expected_current_version,
            source=request.source,
            message_id=request.message_id,
            source_turn_seq=request.source_turn_seq,
        )
        return _artifact_detail_response(
            meta=meta,
            content=content,
            index_version=idx_ver,
            style_corrections=style_corr,
            style_status=style_status,
            project_id=project.id,
        )
    except ArtifactStoreError as exc:
        return _artifact_exception_response(exc)


@app.patch("/governor/artifacts/{artifact_id}")
async def artifacts_update_lifecycle(
    artifact_id: str,
    request: ArtifactLifecycleRequest,
    project_id: str | None = None,
) -> dict:
    """Update artifact status, tags, or trash state without a content revision."""
    from gov_webui.artifact_store import ArtifactStoreError

    try:
        project = _project_record(project_id)
        store = _get_artifact_store(project.id)
        meta, idx_ver = store.set_lifecycle(
            artifact_id,
            status=request.status,
            tags=request.tags,
            trashed=request.trashed,
        )
        _, content, _ = store.get(artifact_id)
        return _artifact_detail_response(
            meta=meta,
            content=content,
            index_version=idx_ver,
            project_id=project.id,
        )
    except ArtifactStoreError as exc:
        return _artifact_exception_response(exc)


@app.put("/governor/artifacts/{artifact_id}/working-copy")
async def artifacts_save_working_copy(
    artifact_id: str,
    request: ArtifactWorkingCopyRequest,
    project_id: str | None = None,
) -> dict:
    """Autosave draft text without creating a committed revision."""
    from gov_webui.artifact_store import ArtifactStoreError

    try:
        project = _project_record(project_id)
        store = _get_artifact_store(project.id)
        meta, idx_ver = store.save_working_copy(
            artifact_id,
            content=request.content,
            base_version=request.base_version,
        )
        return {
            "ok": True,
            "index_version": idx_ver,
            "artifact": _artifact_meta_to_dict(meta, project_id=project.id),
        }
    except ArtifactStoreError as exc:
        return _artifact_exception_response(exc)


@app.delete("/governor/artifacts/{artifact_id}/working-copy")
async def artifacts_discard_working_copy(
    artifact_id: str,
    project_id: str | None = None,
) -> dict:
    """Discard only autosaved text; committed revisions are preserved."""
    from gov_webui.artifact_store import ArtifactStoreError

    try:
        project = _project_record(project_id)
        meta, idx_ver = _get_artifact_store(project.id).discard_working_copy(artifact_id)
        return {
            "ok": True,
            "index_version": idx_ver,
            "artifact": _artifact_meta_to_dict(meta, project_id=project.id),
        }
    except ArtifactStoreError as exc:
        return _artifact_exception_response(exc)


@app.delete("/governor/artifacts/{artifact_id}")
async def artifacts_delete(artifact_id: str, project_id: str | None = None) -> dict:
    """Delete artifact from index."""
    from gov_webui.artifact_store import ArtifactStoreError

    try:
        store = _get_artifact_store(project_id)
        _, idx_ver = store.delete(artifact_id)
        return {
            "ok": True,
            "index_version": idx_ver,
            "deleted": {"artifact_id": artifact_id},
        }
    except ArtifactStoreError as exc:
        return _artifact_exception_response(exc)


@app.get("/governor/artifacts/{artifact_id}/version/{version}")
async def artifacts_get_version(
    artifact_id: str,
    version: int,
    project_id: str | None = None,
) -> dict:
    """Get content for a specific artifact version."""
    from gov_webui.artifact_store import ArtifactStoreError

    try:
        store = _get_artifact_store(project_id)
        content = store.get_version(artifact_id, version)
        return {
            "ok": True,
            "artifact_id": artifact_id,
            "version": version,
            "content": content,
        }
    except ArtifactStoreError as exc:
        return _artifact_exception_response(exc)


@app.get("/governor/artifacts/{artifact_id}/compare")
async def artifacts_compare_versions(
    artifact_id: str,
    from_version: int,
    to_version: int,
    project_id: str | None = None,
) -> dict:
    """Return an inert unified diff between two committed revisions."""
    from gov_webui.artifact_store import ArtifactStoreError

    try:
        store = _get_artifact_store(project_id)
        before = store.get_version(artifact_id, from_version)
        after = store.get_version(artifact_id, to_version)
        diff_lines = list(
            difflib.unified_diff(
                before.splitlines(),
                after.splitlines(),
                fromfile=f"v{from_version}",
                tofile=f"v{to_version}",
                lineterm="",
            )
        )
        return {
            "ok": True,
            "artifact_id": artifact_id,
            "from_version": from_version,
            "to_version": to_version,
            "diff": "\n".join(diff_lines),
            "added_lines": sum(
                1 for line in diff_lines if line.startswith("+") and not line.startswith("+++")
            ),
            "removed_lines": sum(
                1 for line in diff_lines if line.startswith("-") and not line.startswith("---")
            ),
        }
    except ArtifactStoreError as exc:
        return _artifact_exception_response(exc)


@app.post("/governor/artifacts/{artifact_id}/version/{version}/restore")
async def artifacts_restore_version(
    artifact_id: str,
    version: int,
    request: ArtifactRestoreRequest,
    project_id: str | None = None,
) -> dict:
    """Restore old text by creating a new revision; history is never rewritten."""
    from gov_webui.artifact_store import ArtifactStoreError

    try:
        project = _project_record(project_id)
        store = _get_artifact_store(project.id)
        content = store.get_version(artifact_id, version)
        meta, restored, idx_ver = store.update(
            artifact_id,
            content=content,
            expected_current_version=request.expected_current_version,
            source="restore",
        )
        return _artifact_detail_response(
            meta=meta,
            content=restored,
            index_version=idx_ver,
            project_id=project.id,
        )
    except ArtifactStoreError as exc:
        return _artifact_exception_response(exc)


# ============================================================================
# Receipt V1 Export / Verify Endpoints
# ============================================================================


def _build_verify_report(dicts: list[dict]) -> dict:
    """Build a verification report from a list of receipt dicts."""
    from receipt_v1.verify import verify_chain, verify_hash, verify_structure

    if not dicts:
        return {
            "ok": True,
            "report": {
                "scheme": "receipt_v1",
                "receipt_count": 0,
                "valid": True,
                "error_count": 0,
                "warning_count": 0,
                "findings": [],
                "summary": "No receipts to verify.",
            },
        }

    findings: list[dict] = []
    receipt_version = dicts[0].get("receipt_version", "unknown")

    # 1. Structure check each receipt
    for i, d in enumerate(dicts):
        sr = verify_structure(d)
        for err in sr.errors:
            findings.append(
                {
                    "level": "error",
                    "code": "structure",
                    "receipt_index": i,
                    "receipt_id": d.get("receipt_id", "?"),
                    "message": err,
                }
            )
        for warn in sr.warnings:
            findings.append(
                {
                    "level": "warning",
                    "code": "structure",
                    "receipt_index": i,
                    "receipt_id": d.get("receipt_id", "?"),
                    "message": warn,
                }
            )

    # 2. Hash verification each receipt
    for i, d in enumerate(dicts):
        hr = verify_hash(d)
        for err in hr.errors:
            findings.append(
                {
                    "level": "error",
                    "code": "hash_mismatch",
                    "receipt_index": i,
                    "receipt_id": d.get("receipt_id", "?"),
                    "message": err,
                }
            )

    # 3. Chain integrity
    cr = verify_chain(dicts)
    for err in cr.errors:
        findings.append(
            {
                "level": "error",
                "code": "chain_break",
                "message": err,
            }
        )
    for warn in cr.warnings:
        findings.append(
            {
                "level": "warning",
                "code": "chain_gap",
                "message": warn,
            }
        )

    errors = [f for f in findings if f["level"] == "error"]
    warnings = [f for f in findings if f["level"] == "warning"]
    valid = len(errors) == 0

    summary_parts = [f"{len(dicts)} receipts"]
    if valid:
        summary_parts.append("chain intact")
    else:
        summary_parts.append(f"{len(errors)} errors")
    if warnings:
        summary_parts.append(f"{len(warnings)} warnings")

    return {
        "ok": True,
        "report": {
            "scheme": "receipt_v1",
            "receipt_version": receipt_version,
            "receipt_count": len(dicts),
            "valid": valid,
            "error_count": len(errors),
            "warning_count": len(warnings),
            "findings": findings,
            "summary": ", ".join(summary_parts),
        },
    }


@app.get("/governor/receipts/export")
async def export_receipt_chain():
    """Export all receipt_v1 records as canonical JSONL."""
    dicts = _load_receipt_v1_dicts()
    if not dicts:
        return JSONResponse(
            {"ok": False, "error": {"code": "no_receipts", "message": "No receipts to export"}},
            status_code=404,
        )
    from receipt_v1.canonical import canonical_json

    lines = []
    for d in dicts:
        lines.append(canonical_json(d, check_floats=False).decode("utf-8"))
    content = "\n".join(lines) + "\n"
    return Response(
        content=content,
        media_type="application/x-ndjson",
        headers={"Content-Disposition": 'attachment; filename="receipts.jsonl"'},
    )


@app.post("/governor/receipts/verify")
async def verify_receipt_chain():
    """Verify the integrity of the current on-disk receipt chain."""
    dicts = _load_receipt_v1_dicts()
    return _build_verify_report(dicts)


@app.post("/governor/receipts/verify-upload")
async def verify_uploaded_receipts(request: Request):
    """Verify an uploaded JSONL file's receipt chain integrity."""
    body = await request.body()
    try:
        text = body.decode("utf-8")
    except UnicodeDecodeError as e:
        return JSONResponse(
            {
                "ok": False,
                "error": {
                    "code": "invalid_jsonl",
                    "message": f"Invalid UTF-8: {e}",
                },
            },
            status_code=400,
        )

    dicts = []
    for line_num, line in enumerate(text.splitlines(), 1):
        line = line.strip()
        if not line:
            continue
        try:
            dicts.append(json.loads(line))
        except json.JSONDecodeError as e:
            return JSONResponse(
                {
                    "ok": False,
                    "error": {
                        "code": "invalid_jsonl",
                        "message": f"Invalid JSON on line {line_num}: {e}",
                    },
                },
                status_code=400,
            )

    return _build_verify_report(dicts)


# ============================================================================
# C2 — Effective Config
# ============================================================================


def _style_policy_section(mode: str) -> dict | None:
    """Build style_policy section for effective config, or None."""
    from gov_webui.style_policy import PROFILES, action_for_mode, profile_for_mode

    profile = profile_for_mode(mode)
    action = action_for_mode(mode)
    if profile is None:
        return None
    return {
        "profile": profile,
        "action": action,
        "available_profiles": sorted(PROFILES.keys()),
    }


def resolve_effective_config() -> dict:
    """Build the full effective-config response for the current session."""
    mode = GOVERNOR_MODE

    base = {
        "ok": True,
        "schema_version": "effective_config_v1",
        "scope": "session_current",
        "mode": mode,
        "backend_type": "agent-governor-daemon",
        "context_id": GOVERNOR_CONTEXT_ID,
    }

    style_section = _style_policy_section(mode)

    if mode not in _CONFIG_DEFAULTS:
        # Mode without typed config (fiction, nonfiction, general)
        resp = {
            **base,
            "has_config": False,
            "has_session_overrides": False,
            "fields": [],
            "effective": {},
            "sources": {},
            "contract_config_hash": None,
            "contract_config_hash_full": None,
            "constraints_hash": None,
            "constraints_hash_full": None,
            "env": {
                "GOVERNOR_MODE": mode,
                "GOVERNOR_CONTEXT_ID": GOVERNOR_CONTEXT_ID,
                "PROVIDER_OWNER": "agent-governor-daemon",
            },
            "diagnostics": {
                "clamped_fields": [],
                "unknown_keys": [],
                "warnings": [],
            },
        }
        if style_section is not None:
            resp["style_policy"] = style_section
        return resp

    fields, contract, diagnostics = _resolve_config_fields(mode)

    effective = {f["key"]: f["value"] for f in fields}
    sources = {f["key"]: f["source"] for f in fields}
    has_session_overrides = bool(contract.get("config"))

    # Get constraints_hash by calling _build_constraints_message (shared path)
    constraints_msg, _meta = _build_constraints_message()
    constraints_hash = None
    constraints_hash_full = None
    if constraints_msg:
        raw = constraints_msg["content"].encode("utf-8")
        full = hashlib.sha256(raw).hexdigest()
        constraints_hash = full[:16]
        constraints_hash_full = full

    resp = {
        **base,
        "has_config": True,
        "has_session_overrides": has_session_overrides,
        "fields": fields,
        "effective": effective,
        "sources": sources,
        "contract_config_hash": contract.get("config_hash") or None,
        "contract_config_hash_full": contract.get("config_hash_full") or None,
        "constraints_hash": constraints_hash,
        "constraints_hash_full": constraints_hash_full,
        "env": {
            "GOVERNOR_MODE": mode,
            "GOVERNOR_CONTEXT_ID": GOVERNOR_CONTEXT_ID,
            "PROVIDER_OWNER": "agent-governor-daemon",
        },
        "diagnostics": diagnostics,
    }
    if style_section is not None:
        resp["style_policy"] = style_section
    return resp


@app.get("/governor/config/effective")
async def get_effective_config():
    """Return the current effective configuration for the active session."""
    try:
        return resolve_effective_config()
    except Exception as e:
        logger.exception("Failed to resolve effective config")
        return JSONResponse(
            {"ok": False, "error": {"code": "store_read_failed", "message": str(e)}},
            status_code=500,
        )


# ============================================================================
# CLI Entry Point
# ============================================================================


def main() -> None:
    """Run the adapter server."""
    import uvicorn

    uvicorn.run(
        "gov_webui.adapter:app",
        host=GOVERNOR_BIND_HOST,
        port=8000,
        reload=True,
    )


if __name__ == "__main__":
    main()

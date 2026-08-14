# SPDX-License-Identifier: Apache-2.0
"""Low-level JSON-RPC client for Marginalia's narrow AG daemon contract."""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
from pathlib import Path
from typing import Any, AsyncIterator

logger = logging.getLogger(__name__)

AUTH_ERROR_CODE = -32001


class DaemonAuthError(RuntimeError):
    """Raised when the daemon reports a backend authentication failure.

    This typically means the Claude Code CLI is logged out and the user
    needs to run `claude /login` to re-authenticate.
    """


# =============================================================================
# Content-Length framing used by the AG daemon RPC transport
# =============================================================================


async def _read_message(reader: asyncio.StreamReader) -> dict | None:
    """Read a Content-Length framed JSON-RPC message."""
    headers: dict[str, str] = {}
    while True:
        line = await reader.readline()
        if not line:
            return None  # EOF
        decoded = line.decode("utf-8")
        if decoded in ("\r\n", "\n"):
            break
        if ":" in decoded:
            key, _, value = decoded.partition(":")
            headers[key.strip()] = value.strip()

    content_length_str = headers.get("Content-Length")
    if content_length_str is None:
        return None

    content_length = int(content_length_str)
    body = await reader.readexactly(content_length)
    return json.loads(body.decode("utf-8"))


async def _write_message(writer: asyncio.StreamWriter, msg: dict) -> None:
    """Write a Content-Length framed JSON-RPC message."""
    json_bytes = json.dumps(msg).encode("utf-8")
    header = f"Content-Length: {len(json_bytes)}\r\n\r\n".encode("utf-8")
    writer.write(header + json_bytes)
    await writer.drain()


# =============================================================================
# Socket path resolution
# =============================================================================


def default_socket_path(governor_dir: Path) -> Path:
    """Compute the default Unix socket path for a governor directory.

    Same algorithm as governor.daemon.default_socket_path.
    """
    xdg = os.environ.get("XDG_RUNTIME_DIR", "/tmp")
    dir_hash = hashlib.sha256(str(governor_dir.resolve()).encode()).hexdigest()[:12]
    return Path(xdg) / f"governor-{dir_hash}.sock"


# =============================================================================
# DaemonChatClient — chat-path only
# =============================================================================


class DaemonChatClient:
    """JSON-RPC 2.0 client for daemon chat methods over Unix socket.

    It intentionally wraps only Marginalia's governed-chat contract: handshake,
    provider discovery, chat, pending resolution, and receipt lookup.
    """

    def __init__(self, socket_path: str | Path) -> None:
        self._socket_path = Path(socket_path)
        self._reader: asyncio.StreamReader | None = None
        self._writer: asyncio.StreamWriter | None = None
        self._request_id: int = 0
        # One framed socket cannot safely multiplex concurrent request loops.
        self._request_lock = asyncio.Lock()

    @property
    def socket_path(self) -> Path:
        return self._socket_path

    async def connect(self) -> None:
        """Open the Unix socket connection."""
        if self._writer is not None and not self._writer.is_closing():
            return  # Already connected
        self._reader, self._writer = await asyncio.open_unix_connection(
            str(self._socket_path)
        )

    async def close(self) -> None:
        """Close the connection."""
        if self._writer is not None:
            self._writer.close()
            try:
                await self._writer.wait_closed()
            except Exception:
                pass
            self._writer = None
            self._reader = None

    def _next_id(self) -> int:
        self._request_id += 1
        return self._request_id

    async def _call(self, method: str, params: dict | None = None) -> Any:
        """Send a JSON-RPC request and return the result."""
        async with self._request_lock:
            await self.connect()
            assert self._reader is not None and self._writer is not None

            request_id = self._next_id()
            msg = {
                "jsonrpc": "2.0",
                "method": method,
                "id": request_id,
                "params": params or {},
            }
            await _write_message(self._writer, msg)

            # Read responses, skipping notifications until we get our response
            while True:
                resp = await _read_message(self._reader)
                if resp is None:
                    raise ConnectionError("Connection closed by daemon")

                # Skip notifications (no id)
                if "id" not in resp:
                    continue

                if resp.get("id") != request_id:
                    continue

                if "error" in resp:
                    err = resp["error"]
                    code = err.get("code", 0)
                    message = err.get("message", "unknown error")
                    if code == AUTH_ERROR_CODE:
                        raise DaemonAuthError(message)
                    raise RuntimeError(f"RPC error {code}: {message}")
                return resp.get("result")

    # ========================================================================
    # Chat methods
    # ========================================================================

    async def chat_send(
        self,
        messages: list[dict[str, str]],
        model: str = "",
        context_id: str = "default",
    ) -> dict:
        """Non-streaming governed chat. Returns daemon result dict.

        Result shape: {content, model, usage, violations, footer, pending}
        """
        return await self._call(
            "chat.send",
            {"messages": messages, "model": model, "context_id": context_id},
        )

    async def chat_stream(
        self,
        messages: list[dict[str, str]],
        model: str = "",
        context_id: str = "default",
    ) -> AsyncIterator[tuple[str | None, dict | None]]:
        """Streaming governed chat via daemon.

        Yields (delta_content, None) for each chunk, then
        (None, final_result) when the stream completes.

        The final_result has the same shape as chat_send's return value.
        """
        async with self._request_lock:
            await self.connect()
            assert self._reader is not None and self._writer is not None

            request_id = self._next_id()
            msg = {
                "jsonrpc": "2.0",
                "method": "chat.stream",
                "id": request_id,
                "params": {
                    "messages": messages,
                    "model": model,
                    "context_id": context_id,
                },
            }
            await _write_message(self._writer, msg)

            while True:
                resp = await _read_message(self._reader)
                if resp is None:
                    raise ConnectionError("Connection closed by daemon")

                # Notification — yield delta content
                if "id" not in resp:
                    if resp.get("method") == "chat.delta":
                        content = resp.get("params", {}).get("content", "")
                        if content:
                            yield (content, None)
                    continue

                # Final response
                if resp.get("id") == request_id:
                    if "error" in resp:
                        err = resp["error"]
                        code = err.get("code", 0)
                        message = err.get("message", "unknown error")
                        if code == AUTH_ERROR_CODE:
                            raise DaemonAuthError(message)
                        raise RuntimeError(f"RPC error {code}: {message}")
                    yield (None, resp.get("result"))
                    return

    async def hello(self) -> dict:
        """Return daemon identity, capabilities, and authoritative state root."""
        return await self._call("governor.hello")

    async def commit_pending(self, context_id: str) -> dict | None:
        """Get pending state for one governed-chat context."""
        return await self._call("commit.pending", {"context_id": context_id})

    async def commit_fix(self, context_id: str, corrected_text: str) -> dict:
        return await self._call(
            "commit.fix",
            {"context_id": context_id, "corrected_text": corrected_text},
        )

    async def commit_revise(
        self, context_id: str, new_anchor_text: str | None = None
    ) -> dict:
        params: dict[str, Any] = {"context_id": context_id}
        if new_anchor_text is not None:
            params["new_anchor_text"] = new_anchor_text
        return await self._call("commit.revise", params)

    async def commit_proceed(
        self,
        context_id: str,
        *,
        reason: str = "",
        scope: str | None = None,
        expiry: str | None = None,
    ) -> dict:
        params: dict[str, Any] = {"context_id": context_id, "reason": reason}
        if scope is not None:
            params["scope"] = scope
        if expiry is not None:
            params["expiry"] = expiry
        return await self._call("commit.proceed", params)

    async def receipt_detail(self, receipt_id: str) -> dict:
        """Fetch a receipt and evidence from AG's authority store."""
        return await self._call("receipts.detail", {"receipt_id": receipt_id})

    async def chat_models(self) -> list[dict[str, str]]:
        """List available models from the backend."""
        result = await self._call("chat.models")
        return result.get("models", [])

    async def chat_backend(self) -> dict:
        """Get current backend info."""
        return await self._call("chat.backend")

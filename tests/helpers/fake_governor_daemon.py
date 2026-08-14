"""Real AG daemon with a deterministic in-process provider for live tests."""

from __future__ import annotations

import argparse
import asyncio
from pathlib import Path

from governor.chat_bridge import ChatBridge, ChatChunk, ChatResponse
from governor.context_manager import GovernorContextManager
from governor.daemon import DaemonState, serve_unix


class DeterministicBackend:
    """No-network provider whose response deterministically violates a test anchor."""

    response = "A quiet sentence containing MARGINALIA_FORBIDDEN."
    model = "deterministic-fiction-model"

    async def chat(self, messages, model, **kwargs):
        return ChatResponse(content=self.response, model=model or self.model)

    async def stream(self, messages, model, **kwargs):
        for part in ("A quiet sentence ", "containing MARGINALIA_FORBIDDEN."):
            yield ChatChunk(content=part)
        yield ChatChunk(content="", finish_reason="stop")

    async def list_models(self):
        return [{"id": self.model, "owned_by": "marginalia-test"}]


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-root", required=True, type=Path)
    parser.add_argument("--socket", required=True, type=Path)
    args = parser.parse_args()

    daemon_dir = args.data_root.resolve() / ".governor"
    daemon_dir.mkdir(parents=True, exist_ok=True)

    state = DaemonState(daemon_dir, mode="fiction")
    contexts = GovernorContextManager(daemon_dir)
    state._context_manager = contexts
    state._chat_bridge = ChatBridge(
        backend=DeterministicBackend(),
        context_manager=contexts,
        show_ok_footer=False,
    )
    state._backend_type = "deterministic"
    state._backend_kwargs = {"default_model": DeterministicBackend.model}
    await serve_unix(args.socket, state)


if __name__ == "__main__":
    asyncio.run(main())

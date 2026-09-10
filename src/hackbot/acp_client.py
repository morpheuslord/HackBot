"""ACP (Agent Communication Protocol, IBM BeeAI `acp-sdk`) client helpers.

Lets the orchestrator - or the user from the TUI - talk to agents running in another
process / machine that speak ACP (for example `uv run hackbot-acp`)."""

from __future__ import annotations

from typing import Callable

from acp_sdk.client import Client
from acp_sdk.models import Message, MessagePart, MessagePartEvent, RunCompletedEvent, RunFailedEvent


async def list_agents(url: str) -> list[dict]:
    async with Client(base_url=url, timeout=15) as client:
        return [
            {"name": a.name, "description": a.description or ""}
            async for a in client.agents()
        ]


def _text_of(messages: list[Message]) -> str:
    return "".join(p.content or "" for m in messages for p in m.parts if p.content).strip()


async def run_agent(url: str, agent: str, prompt: str, on_part: Callable[[str], None] | None = None) -> str:
    """Run a remote agent to completion; streams partial text through on_part when given."""
    text: list[str] = []
    async with Client(base_url=url, timeout=300) as client:
        async for ev in client.run_stream(Message(parts=[MessagePart(content=prompt)]), agent=agent):
            if isinstance(ev, MessagePartEvent) and ev.part.content:
                text.append(ev.part.content)
                if on_part:
                    on_part(ev.part.content)
            elif isinstance(ev, RunFailedEvent):
                err = ev.run.error.message if ev.run.error else "unknown error"
                raise RuntimeError(f"remote run failed: {err}")
            elif isinstance(ev, RunCompletedEvent) and not text:
                text.append(_text_of(ev.run.output))
    return "".join(text).strip()

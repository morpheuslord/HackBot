"""ACP server: publishes the HackBot agents over the Agent Communication Protocol.

    uv run hackbot-acp --port 8000

Any ACP client can then discover (`GET /agents`) and run them. The TUI's Agents page and the
orchestrator's `ask_remote_agent` tool are such clients. Trace steps are streamed as generic
events, the final answer as a message part."""

from __future__ import annotations

import argparse
import asyncio
from collections.abc import AsyncGenerator

from acp_sdk.models import Message, MessagePart
from acp_sdk.server import Context, Server

from .agents import AgentSystem, Event
from .config import Config

server = Server()


def _prompt_of(messages: list[Message]) -> str:
    return "\n".join(p.content for m in messages for p in m.parts if p.content).strip()


async def _serve(agent: str, messages: list[Message], sarif_path: str | None = None, document_path: str | None = None) -> AsyncGenerator:
    cfg = Config.load()
    prompt = _prompt_of(messages)
    q: asyncio.Queue[Event | None] = asyncio.Queue()

    async def work() -> None:
        try:
            async with AgentSystem(cfg) as system:
                outcome = await system.run(prompt, sarif_path=sarif_path, document_path=document_path,
                                           on_event=q.put_nowait, agent=agent)  # fmt: skip
                q.put_nowait(Event("answer", outcome.answer, outcome))
        except Exception as exc:  # noqa: BLE001
            q.put_nowait(Event("error", f"{type(exc).__name__}: {exc}"))
        finally:
            q.put_nowait(None)

    task = asyncio.create_task(work())
    while True:
        ev = await q.get()
        if ev is None:
            break
        if ev.kind == "step" and ev.data is not None:
            step = ev.data
            yield {"step": step.title, "agent": step.agent, "kind": step.kind, "status": step.status}
        elif ev.kind == "answer":
            yield MessagePart(content=ev.text, content_type="text/markdown")
        elif ev.kind == "error":
            yield MessagePart(content=f"error: {ev.text}")
    await task


@server.agent(name="hackbot", description="HackBot orchestrator: plans, spawns researcher/analyst/tutor agents, synthesises a cited answer.")
async def hackbot(input: list[Message], context: Context) -> AsyncGenerator:
    async for item in _serve("orchestrator", input):
        yield item


@server.agent(name="researcher", description="Answers one focused cybersecurity question with web evidence (ResearchBrief JSON).")
async def researcher(input: list[Message], context: Context) -> AsyncGenerator:
    async for item in _serve("researcher", input):
        yield item


@server.agent(name="analyst", description="Triages a SARIF report. Send the path to the .sarif file as the message text.")
async def analyst(input: list[Message], context: Context) -> AsyncGenerator:
    path = _prompt_of(input)
    async for item in _serve("analyst", [Message(parts=[MessagePart(content="Triage the SARIF report.")])], sarif_path=path):
        yield item


@server.agent(name="document_analyst", description="Reviews any text file (code, config, log, JSON ...). Send the file path as the message text.")
async def document_analyst(input: list[Message], context: Context) -> AsyncGenerator:
    path = _prompt_of(input)
    async for item in _serve("document_analyst", [Message(parts=[MessagePart(content="Review the loaded file.")])], document_path=path):
        yield item


@server.agent(name="tutor", description="Explains a cybersecurity concept for students (no web access).")
async def tutor(input: list[Message], context: Context) -> AsyncGenerator:
    async for item in _serve("tutor", input):
        yield item


def main() -> None:
    parser = argparse.ArgumentParser(prog="hackbot-acp", description="Serve HackBot agents over ACP")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()
    server.run(host=args.host, port=args.port, self_registration=False)


if __name__ == "__main__":
    main()

"""Wiring: builds the agent team over live MCP toolsets and runs turns.

* `AgentSystem` - async context: starts the MCP server(s), builds the agents, runs prompts.
* `Runtime`     - owns a background asyncio loop thread so the Rich TUI can stay responsive and
                  stream events while agents work; keeps MCP connections alive between turns.
"""

from __future__ import annotations

import asyncio
import queue
import threading
import time
from contextlib import AsyncExitStack
from dataclasses import dataclass, field
from typing import Any, Callable, Coroutine

from pydantic_ai.messages import ModelMessage, ModelRequest, ModelResponse, TextPart, UserPromptPart
from pydantic_ai.usage import RunUsage, UsageLimits

from ..config import Config
from .orchestrator import build_orchestrator
from .specialists import Deps, build_analyst, build_document_analyst, build_researcher, build_tutor
from .toolsets import DOC_TOOLS, SARIF_TOOLS, WEB_TOOLS, hackbot_mcp_toolset, external_toolsets, only
from .tracing import Event, Tracer, make_event_handler


@dataclass
class Outcome:
    answer: str
    sources: list[dict]
    triage: dict | None
    doc_report: dict | None
    steps: list[dict]
    usage: RunUsage
    messages: list[ModelMessage] = field(default_factory=list)
    seconds: float = 0.0


def history_from_entries(entries: list[dict], limit: int = 20) -> list[ModelMessage]:
    """Rebuild a pydantic-ai message history from saved session text."""
    msgs: list[ModelMessage] = []
    for e in [x for x in entries if x.get("role") in ("user", "assistant")][-limit:]:
        if e["role"] == "user":
            msgs.append(ModelRequest(parts=[UserPromptPart(content=e["content"])]))
        elif e.get("content"):
            msgs.append(ModelResponse(parts=[TextPart(content=e["content"])]))
    return msgs


class AgentSystem:
    """The whole team + its MCP connections. Use as `async with AgentSystem(cfg) as system:`."""

    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.mcp = hackbot_mcp_toolset(cfg)
        self.extra = external_toolsets(cfg)
        self.researcher = build_researcher(cfg, only(self.mcp, WEB_TOOLS))
        self.analyst = build_analyst(cfg, only(self.mcp, SARIF_TOOLS))
        self.tutor = build_tutor(cfg)
        self.document_analyst = build_document_analyst(cfg, only(self.mcp, DOC_TOOLS))
        self.orchestrator = build_orchestrator(cfg, self.researcher, self.analyst, self.tutor, self.document_analyst, self.extra)
        self.agents = {
            "orchestrator": self.orchestrator,
            "researcher": self.researcher,
            "analyst": self.analyst,
            "document_analyst": self.document_analyst,
            "tutor": self.tutor,
        }
        self.tool_names: list[str] = []
        self.external_tools: dict[str, list[str]] = {}
        self._stack: AsyncExitStack | None = None

    async def __aenter__(self) -> "AgentSystem":
        self._stack = AsyncExitStack()
        await self._stack.__aenter__()
        await self._stack.enter_async_context(self.mcp)
        self.tool_names = sorted(t.name for t in await self.mcp.list_tools())
        for ts in self.extra:
            try:
                await self._stack.enter_async_context(ts)
                tools = await ts.get_tools(None) if hasattr(ts, "get_tools") else {}
                self.external_tools[getattr(ts, "id", None) or ts.label] = sorted(tools.keys())
            except Exception as exc:  # noqa: BLE001 - an optional external server must not block start-up
                self.external_tools[getattr(ts, "id", None) or ts.label] = [f"(failed: {exc})"]
        return self

    async def __aexit__(self, *exc) -> None:
        if self._stack:
            await self._stack.__aexit__(*exc)

    async def run(
        self,
        prompt: str,
        *,
        sarif_path: str | None = None,
        document_path: str | None = None,
        document_desc: str = "",
        on_event: Callable[[Event], None] | None = None,
        message_history: list[ModelMessage] | None = None,
        agent: str = "orchestrator",
        tracer: Tracer | None = None,
    ) -> Outcome:
        tracer = tracer or Tracer(on_event)
        if sarif_path and not document_path:
            document_path = sarif_path
        deps = Deps(cfg=self.cfg, tracer=tracer, sarif_path=sarif_path, document_path=document_path, document_desc=document_desc)
        target = self.agents[agent]
        root = tracer.start("agent", f"{agent}: {prompt[:100]}", agent, None, detail=prompt)
        t0 = time.monotonic()
        try:
            result = await target.run(
                prompt,
                deps=deps,
                message_history=message_history or None,
                event_stream_handler=make_event_handler(tracer, agent, root.id, stream_text=(agent == "orchestrator")),
                usage_limits=UsageLimits(request_limit=40, tool_calls_limit=60),
            )
        except Exception as exc:
            tracer.finish(root, detail=f"{type(exc).__name__}: {exc}", status="failed")
            raise
        output = result.output
        answer = output if isinstance(output, str) else output.model_dump_json(indent=1)
        if not isinstance(output, str) and hasattr(output, "items"):
            tracer.triage = output.model_dump()
        if not isinstance(output, str) and hasattr(output, "findings"):
            tracer.doc_report = output.model_dump()
        if not isinstance(output, str) and hasattr(output, "sources"):
            tracer.add_sources([s.model_dump() for s in output.sources])
        tracer.finish(root, detail=answer[:2000])
        usage = result.usage() if callable(result.usage) else result.usage
        messages = result.all_messages() if callable(result.all_messages) else result.all_messages
        return Outcome(
            answer=answer,
            sources=list(tracer.sources),
            triage=tracer.triage,
            doc_report=tracer.doc_report,
            steps=tracer.to_list(),
            usage=usage,
            messages=list(messages),
            seconds=time.monotonic() - t0,
        )


@dataclass
class Totals:
    input_tokens: int = 0
    output_tokens: int = 0
    requests: int = 0
    tool_calls: int = 0
    turns: int = 0

    def add(self, u: RunUsage) -> None:
        self.input_tokens += u.input_tokens or 0
        self.output_tokens += u.output_tokens or 0
        self.requests += u.requests or 0
        self.tool_calls += u.tool_calls or 0
        self.turns += 1


class Runtime:
    """Background asyncio loop hosting an AgentSystem; the TUI talks to it through a queue of Events."""

    def __init__(self, cfg: Config, events: "queue.Queue[Event]"):
        self.cfg = cfg
        self.events = events
        self.system: AgentSystem | None = None
        self.ready = False
        self.error: str | None = None
        self.busy = False
        self.tracer: Tracer | None = None  # live tracer of the current/last turn
        self.totals = Totals()
        self.messages: list[ModelMessage] = []
        self.last: Outcome | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._jobs: asyncio.Queue | None = None
        self._thread: threading.Thread | None = None

    # ------------------------------------------------------------- lifecycle
    def start(self) -> None:
        self.ready, self.error = False, None
        self._thread = threading.Thread(target=self._thread_main, name="hackbot-runtime", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        if self._loop and self._jobs and not self._loop.is_closed():
            try:
                self._loop.call_soon_threadsafe(self._jobs.put_nowait, None)
            except RuntimeError:  # loop already gone (start-up failure)
                pass
        if self._thread:
            self._thread.join(timeout=5)
        self._thread, self._loop, self._jobs = None, None, None
        self.ready = False

    def restart(self, cfg: Config) -> None:
        self.stop()
        self.cfg = cfg
        self.messages = []
        self.start()

    def _thread_main(self) -> None:
        asyncio.run(self._serve())

    async def _serve(self) -> None:
        self._loop = asyncio.get_running_loop()
        self._jobs = asyncio.Queue()
        try:
            async with AgentSystem(self.cfg) as system:
                self.system = system
                self.ready = True
                self.events.put(Event("ready", f"{len(system.tool_names)} MCP tools", system.tool_names))
                while True:
                    job = await self._jobs.get()
                    if job is None:
                        break
                    await job
        except Exception as exc:  # noqa: BLE001 - surface start-up failures in the UI
            self.error = f"{type(exc).__name__}: {exc}"
            self.events.put(Event("error", f"agent runtime failed: {self.error}"))
            self.events.put(Event("done"))
        finally:
            self.ready = False
            self.system = None

    # ------------------------------------------------------------- work
    def submit(self, prompt: str, sarif_path: str | None, agent: str = "orchestrator",
               document_path: str | None = None, document_desc: str = "") -> bool:  # fmt: skip
        if not (self.ready and self._loop and self._jobs) or self.busy:
            return False
        self.busy = True
        self.tracer = None
        coro = self._turn(prompt, sarif_path, agent, document_path, document_desc)
        self._loop.call_soon_threadsafe(self._jobs.put_nowait, coro)
        return True

    async def _turn(self, prompt: str, sarif_path: str | None, agent: str, document_path: str | None, document_desc: str) -> None:
        assert self.system is not None
        self.tracer = Tracer(self.events.put)  # live tracer: the UI renders it while the turn runs
        try:
            outcome = await self.system.run(
                prompt,
                sarif_path=sarif_path,
                document_path=document_path,
                document_desc=document_desc,
                message_history=self.messages[-2 * self.cfg.history_window :],
                agent=agent,
                tracer=self.tracer,
            )
            self.totals.add(outcome.usage)
            self.messages = outcome.messages
            self.last = outcome
            self.events.put(Event("answer", outcome.answer, outcome))
        except Exception as exc:  # noqa: BLE001
            self.events.put(Event("error", f"{type(exc).__name__}: {exc}"))
        finally:
            self.busy = False
            self.events.put(Event("done"))

    def run_coro(self, coro: Coroutine[Any, Any, Any]) -> "asyncio.Future[Any] | None":
        """Schedule an arbitrary coroutine (e.g. an ACP client call) on the runtime loop."""
        if not self._loop:
            coro.close()
            return None
        return asyncio.run_coroutine_threadsafe(coro, self._loop)

    def reset_history(self, entries: list[dict]) -> None:
        self.messages = history_from_entries(entries, self.cfg.history_window)

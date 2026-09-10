"""Execution trace of a multi-agent run: a tree of steps (agents, plans, thoughts, tool calls)
plus a stream of UI events. Framework events from pydantic-ai are translated here."""

from __future__ import annotations

import itertools
import json
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable

from pydantic_ai import RunContext
from pydantic_ai.messages import (
    FunctionToolCallEvent,
    FunctionToolResultEvent,
    PartDeltaEvent,
    PartStartEvent,
    RetryPromptPart,
    TextPart,
    TextPartDelta,
    ToolReturnPart,
)

_ids = itertools.count(1)


@dataclass
class Event:
    kind: str  # ready | step | delta | reset | answer | error | done | remote
    text: str = ""
    data: Any = None


@dataclass
class Step:
    id: str
    parent: str | None
    agent: str
    kind: str  # agent | plan | thought | tool | result | error
    title: str
    detail: str = ""
    status: str = "running"  # running | done | failed
    started: float = field(default_factory=time.monotonic)
    ended: float | None = None
    data: Any = None

    @property
    def elapsed(self) -> float:
        return (self.ended or time.monotonic()) - self.started

    def to_dict(self, depth: int = 0) -> dict:
        return {
            "id": self.id,
            "parent": self.parent,
            "agent": self.agent,
            "kind": self.kind,
            "title": self.title,
            "detail": self.detail[:4000],
            "status": self.status,
            "elapsed": round(self.elapsed, 2),
            "depth": depth,
        }


class Tracer:
    """Collects steps for one turn and forwards UI events. Safe to append from several tasks."""

    def __init__(self, emit: Callable[[Event], None] | None = None):
        self._emit = emit or (lambda e: None)
        self._lock = threading.Lock()
        self.steps: list[Step] = []
        self.sources: list[dict] = []
        self.triage: dict | None = None
        self.doc_report: dict | None = None

    # ---------------------------------------------------------------- steps
    def start(self, kind: str, title: str, agent: str, parent: str | None = None, detail: str = "", data: Any = None) -> Step:
        step = Step(id=str(next(_ids)), parent=parent, agent=agent, kind=kind, title=title[:160], detail=detail, data=data)
        with self._lock:
            self.steps.append(step)
        self.emit("step", step.title, step)
        return step

    def finish(self, step: Step | None, detail: str | None = None, status: str = "done", data: Any = None) -> None:
        if step is None:
            return
        step.ended = time.monotonic()
        step.status = status
        if detail is not None:
            step.detail = detail
        if data is not None:
            step.data = data
        self.emit("step", step.title, step)

    def note(self, kind: str, title: str, agent: str, parent: str | None = None, detail: str = "", data: Any = None) -> Step:
        step = self.start(kind, title, agent, parent, detail, data)
        self.finish(step)
        return step

    def running_tool(self, agent: str) -> Step | None:
        with self._lock:
            for step in reversed(self.steps):
                if step.agent == agent and step.kind == "tool" and step.status == "running":
                    return step
        return None

    def add_sources(self, sources: list[dict]) -> None:
        seen = {s["url"] for s in self.sources}
        for s in sources:
            if s.get("url") and s["url"] not in seen:
                self.sources.append({"title": s.get("title") or s["url"], "url": s["url"]})
                seen.add(s["url"])

    def tree(self) -> list[tuple[int, Step]]:
        with self._lock:
            steps = list(self.steps)
        children: dict[str | None, list[Step]] = {}
        for s in steps:
            children.setdefault(s.parent, []).append(s)
        out: list[tuple[int, Step]] = []

        def walk(parent: str | None, depth: int) -> None:
            for s in children.get(parent, []):
                out.append((depth, s))
                walk(s.id, depth + 1)

        walk(None, 0)
        return out

    def to_list(self) -> list[dict]:
        return [s.to_dict(depth) for depth, s in self.tree()]

    # ---------------------------------------------------------------- events
    def emit(self, kind: str, text: str = "", data: Any = None) -> None:
        self._emit(Event(kind, text, data))


def fmt_args(args: dict | None, limit: int = 70) -> str:
    if not args:
        return ""
    parts = []
    for k, v in args.items():
        s = json.dumps(v, ensure_ascii=False) if not isinstance(v, str) else v
        parts.append(f"{k}={s[:limit] + '...' if len(s) > limit else s}")
    return ", ".join(parts)


def to_text(content: Any, limit: int = 8000) -> str:
    if isinstance(content, str):
        text = content
    else:
        try:
            text = json.dumps(content, indent=1, ensure_ascii=False, default=str)
        except Exception:  # noqa: BLE001
            text = str(content)
    return text if len(text) <= limit else text[:limit] + "\n... (truncated)"


def make_event_handler(tracer: Tracer, agent: str, parent: str | None, stream_text: bool):
    """Build a pydantic-ai `event_stream_handler` that records tool calls / thoughts as steps
    and (optionally) streams text deltas to the UI."""

    async def handler(ctx: RunContext, events) -> None:  # noqa: ANN001 - AsyncIterable[AgentStreamEvent]
        pending: list[str] = []
        open_tools: dict[str, Step] = {}
        async for ev in events:
            if isinstance(ev, PartStartEvent) and isinstance(ev.part, TextPart):
                if ev.part.content:
                    pending.append(ev.part.content)
                    if stream_text:
                        tracer.emit("delta", ev.part.content)
            elif isinstance(ev, PartDeltaEvent) and isinstance(ev.delta, TextPartDelta):
                pending.append(ev.delta.content_delta)
                if stream_text:
                    tracer.emit("delta", ev.delta.content_delta)
            elif isinstance(ev, FunctionToolCallEvent):
                thought = "".join(pending).strip()
                if thought:  # text emitted before a tool call = the model's reasoning
                    tracer.note("thought", thought.splitlines()[0][:120], agent, parent, detail=thought)
                    pending.clear()
                    if stream_text:
                        tracer.emit("reset")
                part = ev.part
                args = part.args_as_dict() if part.args is not None else {}
                step = tracer.start("tool", f"{part.tool_name}({fmt_args(args)})", agent, parent, data={"args": args})
                open_tools[part.tool_call_id] = step
            elif isinstance(ev, FunctionToolResultEvent):
                step = open_tools.pop(ev.part.tool_call_id, None)
                if isinstance(ev.part, RetryPromptPart):
                    tracer.finish(step, detail=to_text(ev.part.content), status="failed")
                elif isinstance(ev.part, ToolReturnPart):
                    tracer.finish(step, detail=to_text(ev.part.content))
                else:
                    tracer.finish(step, detail=to_text(getattr(ev.part, "content", "")))
        for step in open_tools.values():  # should not happen, but never leave spinners running
            tracer.finish(step, status="done")

    return handler

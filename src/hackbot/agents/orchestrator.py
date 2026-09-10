"""The orchestrator agent: plans, spawns specialist agents (in parallel), and composes the answer.

This is the "one agent spins up multiple agents" part of the demo. The orchestrator has NO
direct research tools - it can only plan, delegate and synthesise (agent delegation pattern).
"""

from __future__ import annotations

import asyncio
import json

from pydantic_ai import Agent, RunContext
from pydantic_ai.toolsets import AbstractToolset

from ..config import Config
from .models import DocumentReport, ResearchBrief, TriageReport
from .specialists import SAFETY, Deps, make_model
from .tracing import make_event_handler

ORCHESTRATOR_PROMPT = (
    "You are HackBot, the orchestrator of a small team of cybersecurity research agents helping students.\n"
    "You never research directly; you plan, delegate to specialists via your tools, then synthesise.\n\n"
    "Decide per request:\n"
    "- Conceptual / educational question -> ask_tutor (or answer directly if trivial).\n"
    "- Needs current facts (CVE, advisory, product version, news, 'latest', comparison of real tools) -> "
    "call plan(...) then spawn_researchers with 1-4 focused, non-overlapping sub-questions (they run in parallel).\n"
    "- Anything about the loaded file: if it is a SARIF report -> analyse_sarif; any other file (code, config, "
    "log, JSON, CSV, notes, scan output) -> analyse_document. Never guess file contents yourself.\n"
    "- If a remote ACP agent is configured and clearly relevant, ask_remote_agent.\n\n"
    "Always call plan() first with 1-4 short steps so the user can follow your reasoning.\n"
    "Final answer: concise markdown, headings + bullets, cite sources as 'Title - URL' when researchers "
    "provided them, and finish with a one-line 'Next step' suggestion. Under ~350 words unless asked.\n" + SAFETY
)


def build_orchestrator(
    cfg: Config,
    researcher: Agent[Deps, ResearchBrief],
    analyst: Agent[Deps, TriageReport],
    tutor: Agent[Deps, str],
    document_analyst: Agent[Deps, DocumentReport] | None = None,
    extra_toolsets: list[AbstractToolset] | None = None,
) -> Agent[Deps, str]:
    orchestrator: Agent[Deps, str] = Agent(
        make_model(cfg),
        name="orchestrator",
        deps_type=Deps,
        instructions=ORCHESTRATOR_PROMPT,
        toolsets=list(extra_toolsets or []),
        retries=2,
    )

    @orchestrator.instructions
    def loaded_file(ctx: RunContext[Deps]) -> str:
        d = ctx.deps
        if d.sarif_path:
            return f"A SARIF report is loaded: {d.document_desc or d.sarif_path}. Use analyse_sarif for it."
        if d.document_path:
            return f"A file is loaded: {d.document_desc or d.document_path}. Use analyse_document for questions about it."
        return "No file is loaded. If the user asks about 'the file' or 'the report', tell them to open one (Ctrl+O)."

    def _child(ctx: RunContext[Deps], parent_id: str | None) -> Deps:
        d = ctx.deps
        return Deps(cfg=d.cfg, tracer=d.tracer, sarif_path=d.sarif_path, document_path=d.document_path,
                    document_desc=d.document_desc, parent=parent_id)  # fmt: skip

    @orchestrator.tool
    async def plan(ctx: RunContext[Deps], steps: list[str]) -> str:
        """Record your plan (1-4 short steps) before delegating. Shown to the user as your reasoning."""
        tracer = ctx.deps.tracer
        parent = tracer.running_tool("orchestrator")
        tracer.note("plan", "Plan: " + " | ".join(steps)[:140], "orchestrator", parent.id if parent else None,
                    detail="\n".join(f"{i}. {s}" for i, s in enumerate(steps, 1)))  # fmt: skip
        return "plan recorded"

    @orchestrator.tool
    async def spawn_researchers(ctx: RunContext[Deps], questions: list[str]) -> list[dict]:
        """Spin up one researcher agent per question (max 4) and run them IN PARALLEL.
        Each returns a ResearchBrief (summary, key points, sources)."""
        tracer = ctx.deps.tracer
        questions = [q for q in questions if q.strip()][: ctx.deps.cfg.max_researchers]
        if not questions:
            return [{"error": "no questions given"}]
        parent = tracer.running_tool("orchestrator")
        parent_id = parent.id if parent else None

        async def one(i: int, q: str) -> dict:
            name = f"researcher-{i}"
            step = tracer.start("agent", f"{name}: {q}", name, parent_id, detail=q)
            try:
                res = await researcher.run(
                    q,
                    deps=_child(ctx, step.id),
                    usage=ctx.usage,
                    event_stream_handler=make_event_handler(tracer, name, step.id, stream_text=False),
                )
                brief: ResearchBrief = res.output
                tracer.add_sources([s.model_dump() for s in brief.sources])
                tracer.finish(step, detail=brief.summary, data=brief.model_dump())
                return brief.model_dump()
            except Exception as exc:  # noqa: BLE001 - one failed researcher must not sink the turn
                tracer.finish(step, detail=f"{type(exc).__name__}: {exc}", status="failed")
                return {"question": q, "error": f"{type(exc).__name__}: {exc}"}

        return list(await asyncio.gather(*(one(i, q) for i, q in enumerate(questions, 1))))

    @orchestrator.tool
    async def analyse_sarif(ctx: RunContext[Deps]) -> dict:
        """Delegate to the SARIF analyst agent: loads the user's report, triages every finding
        and returns a TriageReport (summary, per-finding verdict/priority/fix, fix_order)."""
        tracer = ctx.deps.tracer
        if not ctx.deps.sarif_path:
            return {"error": "no SARIF report is loaded; ask the user to open one (Ctrl+O or /sarif <path>)"}
        parent = tracer.running_tool("orchestrator")
        step = tracer.start("agent", "analyst: triage SARIF report", "analyst", parent.id if parent else None,
                            detail=ctx.deps.sarif_path)  # fmt: skip
        try:
            res = await analyst.run(
                "Triage the SARIF report.",
                deps=_child(ctx, step.id),
                usage=ctx.usage,
                event_stream_handler=make_event_handler(tracer, "analyst", step.id, stream_text=False),
            )
            report: TriageReport = res.output
            tracer.triage = report.model_dump()
            tracer.finish(step, detail=report.summary, data=tracer.triage)
            return tracer.triage
        except Exception as exc:  # noqa: BLE001
            tracer.finish(step, detail=f"{type(exc).__name__}: {exc}", status="failed")
            return {"error": f"analyst failed: {type(exc).__name__}: {exc}"}

    @orchestrator.tool
    async def analyse_document(ctx: RunContext[Deps]) -> dict:
        """Delegate to the document analyst agent: reviews the loaded (non-SARIF) file - source code, config,
        log, JSON, CSV, notes, scanner output - and returns a DocumentReport with findings and line numbers."""
        tracer = ctx.deps.tracer
        if document_analyst is None or not ctx.deps.document_path:
            return {"error": "no file is loaded; ask the user to open one (Ctrl+O or /open <path>)"}
        if ctx.deps.sarif_path:
            return {"error": "the loaded file is a SARIF report - use analyse_sarif instead"}
        parent = tracer.running_tool("orchestrator")
        step = tracer.start("agent", f"document_analyst: {ctx.deps.document_desc or ctx.deps.document_path}",
                            "document_analyst", parent.id if parent else None, detail=ctx.deps.document_path)  # fmt: skip
        try:
            res = await document_analyst.run(
                "Review the loaded file for security-relevant issues.",
                deps=_child(ctx, step.id),
                usage=ctx.usage,
                event_stream_handler=make_event_handler(tracer, "document_analyst", step.id, stream_text=False),
            )
            report: DocumentReport = res.output
            tracer.doc_report = report.model_dump()
            tracer.finish(step, detail=report.summary, data=tracer.doc_report)
            return tracer.doc_report
        except Exception as exc:  # noqa: BLE001
            tracer.finish(step, detail=f"{type(exc).__name__}: {exc}", status="failed")
            return {"error": f"document analyst failed: {type(exc).__name__}: {exc}"}

    @orchestrator.tool
    async def ask_tutor(ctx: RunContext[Deps], topic: str) -> str:
        """Delegate a conceptual explanation to the tutor agent (no web access)."""
        tracer = ctx.deps.tracer
        parent = tracer.running_tool("orchestrator")
        step = tracer.start("agent", f"tutor: {topic}", "tutor", parent.id if parent else None, detail=topic)
        try:
            res = await tutor.run(topic, deps=_child(ctx, step.id), usage=ctx.usage)
            tracer.finish(step, detail=res.output)
            return res.output
        except Exception as exc:  # noqa: BLE001
            tracer.finish(step, detail=f"{type(exc).__name__}: {exc}", status="failed")
            return f"tutor failed: {exc}"

    @orchestrator.tool
    async def ask_remote_agent(ctx: RunContext[Deps], agent: str, prompt: str) -> str:
        """Send a prompt to a remote agent over ACP (Agent Communication Protocol) and return its reply.
        Only works when an ACP server URL is configured."""
        from ..acp_client import run_agent  # local import: keeps acp optional at import time

        tracer = ctx.deps.tracer
        url = ctx.deps.cfg.acp_url
        if not url:
            return "ACP is not configured (set HACKBOT_ACP_URL in Settings)."
        parent = tracer.running_tool("orchestrator")
        step = tracer.start("agent", f"acp:{agent} @ {url}", f"acp:{agent}", parent.id if parent else None, detail=prompt)
        try:
            reply = await run_agent(url, agent, prompt)
            tracer.finish(step, detail=reply)
            return reply
        except Exception as exc:  # noqa: BLE001
            tracer.finish(step, detail=f"{type(exc).__name__}: {exc}", status="failed")
            return f"remote agent failed: {type(exc).__name__}: {exc}"

    return orchestrator


def describe_team() -> list[dict]:
    """Static description of the agent team (used by the Agents page and `hackbot agents`)."""
    return [
        {"name": "orchestrator", "role": "Plans, delegates to specialists (in parallel), synthesises the answer",
         "tools": "plan, spawn_researchers, analyse_sarif, analyse_document, ask_tutor, ask_remote_agent (+ external MCP tools)"},
        {"name": "researcher (xN)", "role": "Answers one focused question with web evidence -> ResearchBrief",
         "tools": "MCP: web_search, fetch_page, cwe_lookup"},
        {"name": "analyst", "role": "Loads and triages a SARIF report -> TriageReport",
         "tools": "MCP: load_sarif, sarif_summary, sarif_findings, cwe_lookup"},
        {"name": "document_analyst", "role": "Reviews any loaded file (code, config, log, JSON ...) -> DocumentReport",
         "tools": "MCP: load_document, read_document, search_document, cwe_lookup"},
        {"name": "tutor", "role": "Explains a security concept for students", "tools": "none (pure LLM)"},
    ]  # fmt: skip


def json_default(obj):  # helper for dumping pydantic objects in traces
    return obj.model_dump() if hasattr(obj, "model_dump") else str(obj)


__all__ = ["build_orchestrator", "describe_team", "json_default", "json"]

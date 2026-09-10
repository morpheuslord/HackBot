"""Specialist agents (pydantic-ai). Each has a narrow role, least-privilege tools and a typed output."""

from __future__ import annotations

from dataclasses import dataclass, field

from pydantic_ai import Agent, RunContext
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.providers.openai import OpenAIProvider
from pydantic_ai.toolsets import AbstractToolset

from ..config import Config
from .models import DocumentReport, ResearchBrief, TriageReport
from .tracing import Tracer

SAFETY = (
    "Hard rules (research and defence only): never write working exploits, malware, shellcode, C2 code, "
    "phishing kits, credential stealers, evasion tricks or step-by-step attack instructions against real "
    "systems. If asked, decline in one sentence and offer the defensive/conceptual version. "
    "Never invent CVE ids, versions or facts."
)


@dataclass
class Deps:
    """Per-run dependencies injected into every agent (pydantic-ai `deps`)."""

    cfg: Config
    tracer: Tracer
    sarif_path: str | None = None  # set when the loaded document is a SARIF report
    document_path: str | None = None  # any loaded file (code, config, log, JSON ...)
    document_desc: str = ""  # human description of the loaded file
    parent: str | None = None  # trace step that spawned this agent
    extra_toolsets: list[AbstractToolset] = field(default_factory=list)


def make_model(cfg: Config) -> OpenAIChatModel:
    return OpenAIChatModel(cfg.model, provider=OpenAIProvider(api_key=cfg.api_key or "missing", base_url=cfg.base_url))


def build_researcher(cfg: Config, web_tools: AbstractToolset) -> Agent[Deps, ResearchBrief]:
    return Agent(
        make_model(cfg),
        name="researcher",
        deps_type=Deps,
        output_type=ResearchBrief,
        toolsets=[web_tools],
        retries=2,
        instructions=(
            "You are a cybersecurity research specialist. You receive ONE focused question. "
            "Use web_search (1-3 searches) and fetch_page / cwe_lookup on the most authoritative hits "
            "(NVD, MITRE, vendor advisories, OWASP, CISA, peer-reviewed or well-known security blogs). "
            "Return a ResearchBrief: a precise summary, 3-6 key points, and ONLY the sources you actually read. "
            "Prefer primary sources; note uncertainty honestly. " + SAFETY
        ),
    )


def build_analyst(cfg: Config, sarif_tools: AbstractToolset) -> Agent[Deps, TriageReport]:
    agent: Agent[Deps, TriageReport] = Agent(
        make_model(cfg),
        name="analyst",
        deps_type=Deps,
        output_type=TriageReport,
        toolsets=[sarif_tools],
        retries=2,
        instructions=(
            "You are an application-security analyst triaging static-analysis (SARIF) results. "
            "Workflow: 1) load_sarif(path) 2) sarif_summary 3) sarif_findings (all levels, use limit up to 50) "
            "4) optionally cwe_lookup for unfamiliar weakness classes. "
            "Then produce a TriageReport covering EVERY finding index: verdict (true-positive when the message "
            "describes user-controlled data reaching a dangerous sink or a clear misconfiguration; false-positive "
            "only with a concrete reason; needs-review when context is missing), priority 1-4 (1 = exploitable "
            "remotely / secrets / injection, 4 = hygiene), CWE, a one-sentence rationale and a short fix. "
            "fix_order lists finding indexes from most to least urgent. " + SAFETY
        ),
    )

    @agent.instructions
    def sarif_location(ctx: RunContext[Deps]) -> str:
        if ctx.deps.sarif_path:
            return f"The SARIF report to analyse is at: {ctx.deps.sarif_path}"
        return "No SARIF path was provided: report that in the summary and return an empty items list."

    return agent


def build_document_analyst(cfg: Config, doc_tools: AbstractToolset) -> Agent[Deps, DocumentReport]:
    agent: Agent[Deps, DocumentReport] = Agent(
        make_model(cfg),
        name="document_analyst",
        deps_type=Deps,
        output_type=DocumentReport,
        toolsets=[doc_tools],
        retries=2,
        instructions=(
            "You are a security reviewer for arbitrary files: source code, configuration, logs, JSON/CSV exports, "
            "scanner output, notes. Workflow: 1) load_document(path) and study the preview to decide what the file is. "
            "2) search_document for risky patterns relevant to that file type (secrets/passwords/tokens, dangerous "
            "calls such as eval/exec/shell=True/pickle, disabled TLS verification, http:// URLs, permissive CORS or "
            "0.0.0.0 binds, debug flags, failed/denied/error lines, IP addresses, CVE ids, sudo/root, base64 blobs). "
            "3) read_document around the interesting hits (stay under ~600 lines total). 4) cwe_lookup when it helps. "
            "If the document is a FOLDER, its text is all files concatenated with '===== path =====' section headers "
            "and load_document returns each file's start_line: search across it, then read the relevant sections. "
            "Return a DocumentReport: file_type, a security-oriented summary, concrete findings whose location is "
            "'line N' of the numbered document you read (add the file name for folders) with short quoted evidence, "
            "and honest questions_for_user for what you could not determine. "
            "Do not invent findings; 'info' severity is fine for observations. " + SAFETY
        ),
    )

    @agent.instructions
    def doc_location(ctx: RunContext[Deps]) -> str:
        if ctx.deps.document_path:
            return f"The file to review is at: {ctx.deps.document_path} ({ctx.deps.document_desc})"
        return "No file path was provided: say so in the summary and return an empty findings list."

    return agent


def build_tutor(cfg: Config) -> Agent[Deps, str]:
    return Agent(
        make_model(cfg),
        name="tutor",
        deps_type=Deps,
        instructions=(
            "You are a patient cybersecurity tutor for university students. Explain the requested concept "
            "clearly: definition, how it works, a concrete (non-operational) example, how it is detected and "
            "how it is defended against, and 2-3 related terms. Use concise markdown with short headings and "
            "bullets, under ~300 words. " + SAFETY
        ),
    )

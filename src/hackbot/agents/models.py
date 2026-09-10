"""Structured outputs exchanged between agents (pydantic models = typed contracts)."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class Source(BaseModel):
    title: str = Field(description="Page or document title")
    url: str


class ResearchBrief(BaseModel):
    """What a researcher agent hands back to the orchestrator."""

    question: str
    summary: str = Field(description="2-4 sentence answer to the question")
    key_points: list[str] = Field(default_factory=list, description="3-6 concise bullet points")
    sources: list[Source] = Field(default_factory=list, description="Sources actually used")
    confidence: Literal["low", "medium", "high"] = "medium"


class TriageItem(BaseModel):
    index: int = Field(description="Finding index from sarif_findings")
    rule_id: str
    verdict: Literal["true-positive", "false-positive", "needs-review"]
    priority: int = Field(ge=1, le=4, description="1 = fix first, 4 = lowest")
    cwe: str = Field(default="", description="CWE id like CWE-89, if known")
    rationale: str = Field(description="One or two sentences: why this verdict/priority")
    fix: str = Field(description="Short remediation guidance")


class TriageReport(BaseModel):
    """What the SARIF analyst agent hands back."""

    summary: str = Field(description="Executive summary of the report's risk (3-5 sentences)")
    items: list[TriageItem]
    fix_order: list[int] = Field(default_factory=list, description="Finding indexes in recommended fix order")


class DocFinding(BaseModel):
    title: str
    severity: Literal["critical", "high", "medium", "low", "info"]
    location: str = Field(default="", description="e.g. 'line 42', 'lines 10-30', 'section X'")
    evidence: str = Field(default="", description="Short quoted snippet (<= 200 chars) from the file")
    explanation: str = Field(description="Why this matters (1-2 sentences)")
    recommendation: str = Field(description="What to do about it")
    cwe: str = Field(default="", description="CWE id if applicable")


class DocumentReport(BaseModel):
    """What the document analyst hands back for any non-SARIF file."""

    file_type: str = Field(description="What the file is (e.g. Flask source, nginx config, auth log, Nmap output)")
    summary: str = Field(description="3-5 sentence security-oriented summary")
    findings: list[DocFinding]
    questions_for_user: list[str] = Field(default_factory=list, description="Things you could not determine")

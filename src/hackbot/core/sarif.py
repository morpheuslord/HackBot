"""SARIF 2.1.0 reader, summariser and triage helpers (no framework dependencies)."""

from __future__ import annotations

import json
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

LEVELS = ("error", "warning", "note", "none")
VERDICTS = ("untriaged", "true-positive", "false-positive", "needs-review")


@dataclass
class Finding:
    index: int
    rule_id: str
    rule_name: str
    level: str
    message: str
    file: str
    line: int | None
    tool: str
    description: str = ""
    security_severity: float | None = None
    tags: list[str] = field(default_factory=list)

    @property
    def location(self) -> str:
        return f"{self.file}:{self.line}" if self.line else self.file

    @property
    def cwe(self) -> str:
        for t in self.tags:
            if "cwe" in t.lower():
                digits = "".join(ch for ch in t.split("/")[-1] if ch.isdigit())
                if digits:
                    return f"CWE-{int(digits)}"
        return ""

    def to_dict(self, description_chars: int = 400) -> dict:
        return {
            "index": self.index,
            "rule_id": self.rule_id,
            "rule_name": self.rule_name,
            "level": self.level,
            "security_severity": self.security_severity,
            "cwe": self.cwe,
            "message": self.message,
            "location": self.location,
            "description": self.description[:description_chars],
            "tags": self.tags,
        }


@dataclass
class SarifReport:
    path: str
    tools: list[str]
    findings: list[Finding]

    def by_level(self) -> Counter:
        return Counter(f.level for f in self.findings)

    def by_rule(self) -> Counter:
        return Counter(f.rule_id for f in self.findings)

    def by_file(self) -> Counter:
        return Counter(f.file for f in self.findings)

    def get(self, index: int) -> Finding | None:
        return self.findings[index - 1] if 1 <= index <= len(self.findings) else None


def _rule_lookup(run: dict) -> tuple[list[dict], dict[str, dict]]:
    driver = run.get("tool", {}).get("driver", {})
    rules: list[dict] = list(driver.get("rules", []))
    for ext in run.get("tool", {}).get("extensions", []) or []:
        rules.extend(ext.get("rules", []))
    return rules, {r.get("id", ""): r for r in rules}


def _text(obj: dict | None, *keys: str) -> str:
    if not obj:
        return ""
    for k in keys:
        if obj.get(k):
            return str(obj[k]).strip()
    return ""


def load_sarif_file(path: str | Path) -> SarifReport:
    p = Path(path).expanduser()
    data = json.loads(p.read_text(encoding="utf-8-sig"))
    if "runs" not in data:
        raise ValueError("not a SARIF document: missing 'runs'")
    tools: list[str] = []
    findings: list[Finding] = []
    for run in data["runs"]:
        driver = run.get("tool", {}).get("driver", {})
        tool_name = driver.get("name", "unknown")
        version = driver.get("semanticVersion") or driver.get("version")
        if version:
            tool_name += f" {version}"
        tools.append(tool_name)
        rules_list, rules_by_id = _rule_lookup(run)
        for res in run.get("results", []) or []:
            rule_id = res.get("ruleId") or res.get("rule", {}).get("id") or "unknown"
            idx = res.get("ruleIndex", res.get("rule", {}).get("index"))
            if isinstance(idx, int) and 0 <= idx < len(rules_list):
                rule = rules_list[idx]
            else:
                rule = rules_by_id.get(rule_id, {})
            level = res.get("level") or rule.get("defaultConfiguration", {}).get("level") or "warning"
            props = rule.get("properties", {}) or {}
            sev = props.get("security-severity")
            file, line = "", None
            for loc in res.get("locations", []) or []:
                phys = loc.get("physicalLocation", {})
                file = phys.get("artifactLocation", {}).get("uri", "") or file
                line = phys.get("region", {}).get("startLine", line)
                if file:
                    break
            findings.append(
                Finding(
                    index=len(findings) + 1,
                    rule_id=rule_id,
                    rule_name=rule.get("name", "") or _text(rule.get("shortDescription"), "text"),
                    level=level if level in LEVELS else "warning",
                    message=_text(res.get("message"), "text", "markdown") or "(no message)",
                    file=file or "(unknown)",
                    line=int(line) if isinstance(line, int) else None,
                    tool=tool_name,
                    description=_text(rule.get("fullDescription"), "text")
                    or _text(rule.get("shortDescription"), "text"),
                    security_severity=float(sev) if sev not in (None, "") else None,
                    tags=list(props.get("tags", []) or []),
                )
            )
    order = {lvl: i for i, lvl in enumerate(LEVELS)}
    findings.sort(key=lambda f: (order[f.level], -(f.security_severity or 0), f.file, f.line or 0))
    for i, f in enumerate(findings, 1):
        f.index = i
    return SarifReport(path=str(p.resolve()), tools=tools, findings=findings)


def summary_dict(report: SarifReport) -> dict:
    return {
        "file": report.path,
        "tools": report.tools,
        "total": len(report.findings),
        "by_level": dict(report.by_level()),
        "by_rule": dict(report.by_rule().most_common(15)),
        "top_files": dict(report.by_file().most_common(10)),
        "cwes": sorted({f.cwe for f in report.findings if f.cwe}),
    }


def findings_dict(report: SarifReport, level: str | None = None, rule_id: str | None = None, limit: int = 15) -> dict:
    limit = max(1, min(int(limit or 15), 50))
    rows = [
        f.to_dict()
        for f in report.findings
        if (not level or f.level == level) and (not rule_id or f.rule_id == rule_id)
    ]
    return {"count": len(rows), "returned": min(limit, len(rows)), "findings": rows[:limit]}


# --------------------------------------------------------------------------- triage
def triage_counts(triage: dict[int, dict]) -> Counter:
    return Counter(t.get("verdict", "untriaged") for t in triage.values())


def triage_markdown(report: SarifReport, triage: dict[int, dict]) -> str:
    counts = triage_counts(triage)
    lines = [
        f"# Triage report - {Path(report.path).name}",
        "",
        f"Generated {datetime.now():%Y-%m-%d %H:%M} by HackBot. Tool: {', '.join(report.tools)}.",
        "",
        f"Findings: {len(report.findings)}  |  "
        + "  ".join(f"{v}: {counts.get(v, 0)}" for v in VERDICTS if counts.get(v)),
        "",
        "| # | Level | Rule | CWE | Location | Verdict | Prio | By | Note |",
        "|---|-------|------|-----|----------|---------|------|----|------|",
    ]
    for f in report.findings:
        t = triage.get(f.index, {})
        note = (t.get("note") or "").replace("|", "/").replace("\n", " ")
        lines.append(
            f"| {f.index} | {f.level} | {f.rule_id} | {f.cwe or '-'} | {f.location} | "
            f"{t.get('verdict', 'untriaged')} | {t.get('priority') or '-'} | {t.get('by') or '-'} | {note} |"
        )
    lines.append("")
    lines.append("## Fix order")
    ordered = sorted(
        (f for f in report.findings if triage.get(f.index, {}).get("verdict") == "true-positive"),
        key=lambda f: (triage[f.index].get("priority") or 9, f.index),
    )
    for f in ordered:
        lines.append(f"- P{triage[f.index].get('priority') or '?'}  #{f.index} {f.rule_id} at {f.location}")
    if not ordered:
        lines.append("- (no confirmed true positives yet)")
    return "\n".join(lines) + "\n"

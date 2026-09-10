"""HackBot MCP server: exposes the research / SARIF tools over the Model Context Protocol.

Run it standalone (stdio transport) with `uv run hackbot-mcp`, or point any MCP client
(Claude Desktop, Cursor, another agent) at it. The HackBot agents consume it as MCP clients,
so every tool call in the demo is a real MCP round trip in a separate process.
"""

from __future__ import annotations

import re
import sys

try:  # mcp >= 2.0 renamed FastMCP -> MCPServer
    from mcp.server.mcpserver import MCPServer
except ImportError:  # mcp 1.x
    from mcp.server.fastmcp import FastMCP as MCPServer  # type: ignore[assignment]

from .core import documents as D
from .core import sarif as S
from .core import web as W

server = MCPServer(
    name="hackbot-tools",
    instructions=(
        "Cybersecurity research tools for students: web search / page fetch for CVEs and advisories, "
        "SARIF static-analysis report analysis, and MITRE CWE lookup. Research and defence only."
    ),
)

_state: dict = {"report": None, "document": None}


@server.tool()
def web_search(query: str, max_results: int = 5) -> dict:
    """Search the public web (DuckDuckGo) for cybersecurity information: CVEs, advisories,
    vendor docs, research papers, news. Returns title, url and snippet per hit (max 10)."""
    return W.web_search(query, max_results)


@server.tool()
def fetch_page(url: str, max_chars: int = 4000) -> dict:
    """Fetch a web page and return its readable text (truncated). Use it to read an
    advisory, CVE entry or article found with web_search."""
    return W.fetch_page(url, max_chars)


@server.tool()
def cwe_lookup(cwe_id: str) -> dict:
    """Fetch the MITRE CWE definition for a weakness id such as 'CWE-79' or '79'
    (description, common consequences, mitigations)."""
    digits = re.sub(r"\D", "", cwe_id or "")
    if not digits:
        return {"error": "cwe_id must contain a number, e.g. CWE-79"}
    out = W.fetch_page(f"https://cwe.mitre.org/data/definitions/{int(digits)}.html", 3500)
    out["cwe"] = f"CWE-{int(digits)}"
    return out


@server.tool()
def load_sarif(path: str) -> dict:
    """Load a SARIF (static-analysis results) file from disk so it can be analysed.
    Replaces any previously loaded report and returns its summary."""
    _state["report"] = S.load_sarif_file(path)
    return S.summary_dict(_state["report"])


@server.tool()
def sarif_summary() -> dict:
    """Summarise the loaded SARIF report: tools, totals, counts by level / rule / file, CWEs."""
    rep = _state["report"]
    if rep is None:
        return {"error": "no SARIF report loaded - call load_sarif(path) first"}
    return S.summary_dict(rep)


@server.tool()
def sarif_findings(level: str | None = None, rule_id: str | None = None, limit: int = 15) -> dict:
    """List findings from the loaded SARIF report, optionally filtered by level
    (error|warning|note|none) or exact rule id. Each finding has index, rule, level,
    CWE, message, file:line, description."""
    rep = _state["report"]
    if rep is None:
        return {"error": "no SARIF report loaded - call load_sarif(path) first"}
    return S.findings_dict(rep, level, rule_id, limit)


# --------------------------------------------------------------------------- any file
@server.tool()
def load_document(path: str) -> dict:
    """Load ANY text-like file (source code, config, log, JSON, CSV, markdown, scan output, SARIF)
    OR a whole folder (all its text files are concatenated with '===== path =====' separators)
    for analysis. Returns kind, size, preview and (for folders) the file list with start lines;
    use read_document / search_document next. SARIF files are also loaded for the sarif_* tools."""
    doc = D.load_document(path)
    _state["document"] = doc
    if doc.sarif is not None:
        _state["report"] = doc.sarif
    return doc.info()


@server.tool()
def document_info() -> dict:
    """Describe the currently loaded document (kind, size, preview)."""
    doc = _state["document"]
    if doc is None:
        return {"error": "no document loaded - call load_document(path) first"}
    return doc.info()


@server.tool()
def read_document(start_line: int = 1, num_lines: int = 120) -> dict:
    """Read a range of numbered lines from the loaded document (max 400 per call)."""
    doc = _state["document"]
    if doc is None:
        return {"error": "no document loaded - call load_document(path) first"}
    rows = doc.slice(start_line, num_lines)
    return {"start_line": start_line, "total_lines": doc.lines, "lines": [f"{i:5d}| {t}" for i, t in rows]}


@server.tool()
def search_document(pattern: str, max_hits: int = 30) -> dict:
    """Case-insensitive regex search over the loaded document; returns matching line numbers and text.
    Useful patterns: password|secret|token|api[_-]?key ; eval\\(|exec\\(|shell=True ; http:// ;
    CVE-\\d{4}-\\d+ ; error|failed|denied ; IP addresses."""
    doc = _state["document"]
    if doc is None:
        return {"error": "no document loaded - call load_document(path) first"}
    hits = doc.search(pattern, max_hits)
    return {"pattern": pattern, "count": len(hits), "hits": hits}


def main() -> None:
    import logging

    logging.basicConfig(level=logging.WARNING, stream=sys.stderr)
    for name in ("ddgs", "httpx", "primp", "mcp"):
        logging.getLogger(name).setLevel(logging.WARNING)
    server.run("stdio")


if __name__ == "__main__":
    main()

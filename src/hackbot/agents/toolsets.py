"""MCP toolsets used by the agents.

* `hackbot_mcp_toolset` - the project's own MCP server (`hackbot-mcp`) launched over stdio.
* `external_toolsets` - any extra servers from a standard `mcpServers` JSON config file.
"""

from __future__ import annotations

import sys
from pathlib import Path

from fastmcp.client import Client
from fastmcp.client.transports import StdioTransport
from pydantic_ai.mcp import MCPToolset, load_mcp_toolsets
from pydantic_ai.toolsets import AbstractToolset

from ..config import Config

WEB_TOOLS = frozenset({"web_search", "fetch_page", "cwe_lookup"})
SARIF_TOOLS = frozenset({"load_sarif", "sarif_summary", "sarif_findings", "cwe_lookup"})
DOC_TOOLS = frozenset({"load_document", "document_info", "read_document", "search_document", "cwe_lookup"})


def hackbot_mcp_toolset(cfg: Config) -> MCPToolset:
    cfg.data_dir.mkdir(parents=True, exist_ok=True)
    transport = StdioTransport(
        command=sys.executable,
        args=["-m", "hackbot.mcp_server"],
        cwd=str(Path.cwd()),
        keep_alive=True,
        log_file=cfg.data_dir / "mcp-server.log",  # server stderr must never reach the TUI screen
    )
    return MCPToolset(Client(transport, name="hackbot-mcp"), id="hackbot-mcp", max_retries=1)


def external_toolsets(cfg: Config) -> list[AbstractToolset]:
    if cfg.mcp_config and Path(cfg.mcp_config).exists():
        return list(load_mcp_toolsets(cfg.mcp_config))
    return []


def only(toolset: AbstractToolset, names: frozenset[str]) -> AbstractToolset:
    """Restrict a toolset to the given tool names (least privilege per agent)."""
    return toolset.filtered(lambda ctx, tool: tool.name in names)

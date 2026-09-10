#!/usr/bin/env sh
# HackBot one-shot launcher (macOS/Linux): installs deps, then starts the TUI + MCP server + ACP server.
cd "$(dirname "$0")" || exit 1
uv sync --quiet || exit 1
exec uv run hackbot --acp 8000 "$@"

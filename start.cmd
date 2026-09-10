@echo off
rem HackBot one-shot launcher (Windows): installs deps, then starts the TUI + MCP server + ACP server.
cd /d "%~dp0"
uv sync --quiet || exit /b 1
uv run hackbot --acp 8000 %*

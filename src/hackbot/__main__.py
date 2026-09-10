"""CLI entry point.

    hackbot                                  launch the TUI
    hackbot --file report.sarif|app.py|x.log launch with any text file preloaded (--sarif is an alias)
    hackbot --acp [PORT]                     also run the ACP server (MCP server always starts automatically)
    hackbot ask "question" [--file F] [--agent NAME]   one-shot run (live trace + answer, no TUI)
    hackbot agents                           describe the team and protocols
    hackbot tools                            start the MCP server and list its tools
    hackbot mcp | hackbot acp [--port N]        run the MCP (stdio) / ACP (http) server
"""

from __future__ import annotations

import argparse
import asyncio
import sys

from rich.console import Console
from rich.markdown import Markdown

from .config import Config


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="hackbot", description="HackBot - agentic cybersecurity research team")
    parser.add_argument("--file", "--sarif", dest="file", metavar="FILE",
                        help="file to load at start-up (SARIF, code, config, log, JSON, CSV, text ...)")
    parser.add_argument("--model", metavar="NAME", help="override HACKBOT_MODEL")
    parser.add_argument("--acp", metavar="PORT", nargs="?", const=8000, type=int,
                        help="also start the ACP agent server (default port 8000) and connect the TUI to it")
    sub = parser.add_subparsers(dest="cmd")
    ask = sub.add_parser("ask", help="run one request without the TUI")
    ask.add_argument("question", nargs="+")
    ask.add_argument("--file", "--sarif", dest="file", metavar="FILE")
    ask.add_argument("--model", metavar="NAME")
    ask.add_argument("--agent", default="orchestrator",
                     choices=["orchestrator", "researcher", "analyst", "document_analyst", "tutor"])
    sub.add_parser("agents", help="describe the agent team")
    sub.add_parser("tools", help="start the MCP server and list its tools")
    sub.add_parser("mcp", help="run the MCP server (stdio)")
    acp = sub.add_parser("acp", help="run the ACP server")
    acp.add_argument("--host", default="127.0.0.1")
    acp.add_argument("--port", type=int, default=8000)
    args = parser.parse_args(argv)

    if args.cmd == "mcp":
        from .mcp_server import main as mcp_main

        mcp_main()
        return 0
    if args.cmd == "acp":
        from .acp_server import server

        server.run(host=args.host, port=args.port, self_registration=False)
        return 0

    cfg = Config.load(model=args.model)
    console = Console()

    if args.cmd == "agents":
        from .agents.orchestrator import describe_team

        for a in describe_team():
            console.print(f"[bold cyan]{a['name']:16}[/] {a['role']}\n{'':16} [dim]tools: {a['tools']}[/]")
        console.print("\n[dim]MCP server: uv run hackbot-mcp (stdio)   ACP server: uv run hackbot-acp --port 8000[/]")
        return 0

    if args.cmd == "tools":
        from .agents.toolsets import hackbot_mcp_toolset

        async def list_tools() -> None:
            ts = hackbot_mcp_toolset(cfg)
            async with ts:
                for t in await ts.list_tools():
                    console.print(f"[bold yellow]{t.name}[/]  {(t.description or '').strip()}")

        asyncio.run(list_tools())
        return 0

    if args.cmd == "ask":
        from pathlib import Path

        from .agents import AgentSystem, Event

        from .core.documents import load_document

        question = " ".join(args.question)
        doc = load_document(args.file) if args.file else None

        def on_event(ev: Event) -> None:
            if ev.kind == "step" and ev.data is not None and ev.data.status != "running":
                s = ev.data
                mark = "+" if s.status == "done" else "!"
                console.print(f"[dim]{mark} {s.agent:14} {s.title[:100]}  ({s.elapsed:.1f}s)[/]")
            elif ev.kind == "error":
                console.print(f"[red]error: {ev.text}[/]")

        async def go() -> int:
            async with AgentSystem(cfg) as system:
                console.print(f"[dim]MCP tools: {', '.join(system.tool_names)}[/]")
                out = await system.run(
                    question,
                    sarif_path=doc.sarif.path if doc and doc.sarif else None,
                    document_path=doc.path if doc else None,
                    document_desc=doc.description if doc else "",
                    on_event=on_event,
                    agent=args.agent,
                )
            console.print()
            console.print(Markdown(out.answer))
            if out.sources:
                console.print("\n[bold]Sources[/]")
                for s in out.sources:
                    console.print(f"- {s['title']} - {s['url']}")
            u = out.usage
            console.print(f"\n[dim]{u.requests} LLM requests, {u.tool_calls} tool calls, "
                          f"{(u.input_tokens or 0) + (u.output_tokens or 0)} tokens, {out.seconds:.1f}s[/]")  # fmt: skip
            return 0

        try:
            return asyncio.run(go())
        except Exception as exc:  # noqa: BLE001
            console.print(f"[red]{type(exc).__name__}: {exc}[/]")
            return 1

    if not (sys.stdin.isatty() and sys.stdout.isatty()):
        console.print('[red]hackbot needs an interactive terminal (or use: hackbot ask "...")[/]')
        return 2
    from .ui import App

    App(cfg, file_path=args.file, acp_port=args.acp).run()
    return 0


if __name__ == "__main__":
    sys.exit(main())

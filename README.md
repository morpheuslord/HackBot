# HackBot - agentic cybersecurity research team

```text
     _   _            _    ____        _
    | | | | __ _  ___| | _| __ )  ___ | |_
    | |_| |/ _` |/ __| |/ /  _ \ / _ \| __|
    |  _  | (_| | (__|   <| |_) | (_) | |_
    |_| |_|\__,_|\___|_|\_\____/ \___/ \__|
```

Agentic rewrite of [HackBot](https://github.com/morpheuslord/HackBot): a teaching demo of a real
**agentic AI system** (not an LLM-call wrapper) for cybersecurity students.

* **Multi-agent orchestration** with [pydantic-ai](https://ai.pydantic.dev): an *orchestrator* agent
  plans, spins up specialist agents **in parallel** (researchers, a SARIF analyst, a document analyst,
  a tutor) and synthesises a cited answer. Specialists return typed pydantic models, not free text.
* **MCP** (Model Context Protocol): every tool lives in a separate MCP server process
  (`hackbot-mcp`, stdio). Agents are MCP *clients*. Extra MCP servers can be attached from a
  standard `mcpServers` JSON file.
* **ACP** (Agent Communication Protocol, IBM BeeAI `acp-sdk`): the agents are published by
  `hackbot-acp` and can be discovered / called from any ACP client - including the orchestrator's
  own `ask_remote_agent` tool and the TUI's Agents page.
* **Any file or folder as input**: SARIF reports get structured triage; any other text file (source
  code, config, logs, JSON, CSV, markdown, scanner output) - or a whole folder of them, e.g. a
  Prowler/Nmap output directory - is reviewed by a *document analyst* agent through MCP
  `load_document` / `read_document` / `search_document` tools. In the viewer `b` lists the folder's
  files and jumps between them.
* **Interactive terminal app** built only with Rich: dashboard, chat with streaming answers and a
  live *Activity* tree of agents/tools (inspect any step), result and sources windows, a file viewer
  with search, interactive **triage review** / **file review** windows that open when an analyst
  finishes, history with previews, resizable panes and popups (Ctrl+arrows), settings with a
  first-run wizard, and a Ctrl+P command palette.
* Research and defence only: the system prompts refuse exploit / malware generation.
  Cheapest OpenAI model by default (`gpt-4.1-nano`).

```
 TUI (rich) --events--> Runtime (asyncio thread)
                          orchestrator (pydantic-ai)
                            |- plan()
                            |- spawn_researchers()  -> researcher x N (parallel)  --MCP--> hackbot-mcp: web_search, fetch_page, cwe_lookup
                            |- analyse_sarif()      -> analyst                    --MCP--> hackbot-mcp: load_sarif, sarif_summary, sarif_findings
                            |- analyse_document()   -> document_analyst           --MCP--> hackbot-mcp: load_document, read_document, search_document
                            |- ask_tutor()          -> tutor
                            '- ask_remote_agent()   --ACP--> hackbot-acp / any ACP server
```

## Setup

```bash
uv sync
uv run hackbot
```

On first launch a setup wizard asks for your OpenAI API key and model and writes `.env`
(or copy `.env.example`). The MCP tool server is started automatically by the TUI.
To start **everything** (TUI + MCP server + ACP agent server) with one command:

```bash
uv run hackbot --acp
```

or double-click / run `start.cmd` (Windows) or `./start.sh` (macOS/Linux), which also runs `uv sync`.
`--acp` launches `hackbot-acp` on port 8000 (pass another port with `--acp 8100`), connects the Agents
page to it and stops it when you quit. For the full demo with a report preloaded:

```bash
uv run hackbot --acp --file samples/sample.sarif
```

`--file` (alias `--sarif`) accepts any text file or folder, e.g. `--file src/app.py`, `--file /var/log/auth.log`
or `--file outputs/prowler/aws-prod/2026/06/15`.

Other entry points:

| Command | What it does |
|---------|--------------|
| `uv run hackbot ask "latest on CVE-2024-3094" [--file F] [--agent researcher]` | one-shot run, prints the live agent/tool trace and the answer |
| `uv run hackbot ask --file app.py "review this file"` | document analyst over any text file |
| `uv run hackbot-mcp` (or `hackbot mcp`) | run the MCP tool server on stdio (point Claude Desktop / Cursor / any MCP client at it) |
| `uv run hackbot-acp --port 8000` (or `hackbot acp`) | publish the agents over ACP; `GET /agents` lists them |
| `uv run hackbot tools` | start the MCP server and list its tools |
| `uv run hackbot agents` | describe the team |
| `uv run pytest` | tests (SARIF parser, document loader, the whole team over MCP with a fake model) |

## Demo script (suggested)

1. **Home (F1)** - runtime status: MCP server started, tools discovered.
2. **Chat (F2)** - ask *"What is the latest on CVE-2024-3094 (xz backdoor) and how is it detected?"*.
   Watch the Activity panel: `plan` -> `spawn_researchers` -> `researcher-1..3` running in parallel,
   each doing MCP `web_search` / `fetch_page` calls. Ctrl+E then Enter on any step shows its raw args/result.
   Ctrl+R opens the answer in a window, Ctrl+S the sources (Enter asks the team to summarise one).
3. **File (F4)** - Ctrl+O opens any file or folder. For a SARIF report, `a` runs orchestrator -> analyst ->
   MCP `load_sarif`/`sarif_findings` and the **Triage review** window opens with per-finding verdicts,
   priority and rationale (`a`/`x` accept/reject, `A` all, Enter details, `e` export markdown; `t`, `1-4`,
   `n` let the human override). For any other file (try `src/hackbot/config.py` or a log), `a` runs the
   document analyst and a **File review** window lists findings with line numbers (`g` jumps to the
   line in the viewer; `/` searches the file).
4. **Agents (F5)** - the team, MCP servers and tools; run a specialist directly (Enter).
   With `--acp` the remote agents are already discovered (otherwise start `uv run hackbot-acp` in another
   terminal, set the URL with `u`, `r` to refresh); Enter calls one over ACP. Ask the orchestrator to
   "use the remote tutor agent" to see `ask_remote_agent`.
5. **Settings (F6)** - model, API key, MCP config (`mcp_servers.example.json`), ACP URL; `s` saves to `.env`
   and restarts the runtime.

## Code map

| Path | Role |
|------|------|
| `src/hackbot/agents/orchestrator.py` | orchestrator agent + delegation tools (`spawn_researchers` runs agents with `asyncio.gather`) |
| `src/hackbot/agents/specialists.py` | researcher / analyst / document analyst / tutor agents, least-privilege MCP toolsets, `Deps` |
| `src/hackbot/agents/models.py` | typed contracts: `ResearchBrief`, `TriageReport`, `DocumentReport` |
| `src/hackbot/agents/tracing.py` | step tree + translation of pydantic-ai stream events (tool calls, text deltas, "thoughts") |
| `src/hackbot/agents/runtime.py` | `AgentSystem` (team + MCP connections) and the background `Runtime` used by the TUI |
| `src/hackbot/mcp_server.py` | the MCP server (`MCPServer`/`FastMCP`), tools built on `core/` |
| `src/hackbot/acp_server.py`, `acp_client.py` | ACP server publishing the agents; client used by the TUI and the orchestrator |
| `src/hackbot/core/` | pure SARIF parser / triage export, universal document + folder loader, web helpers |
| `src/hackbot/ui/` | Rich TUI: `keys.py`, `widgets.py` (scroll view, popups, palette, overlay), `pages.py`, `app.py` |

## Keys

| Key | Action |
|-----|--------|
| F1..F7 / Tab | Home, Chat, History, File, Agents, Settings, Help |
| Ctrl+P | command palette (free text is sent to the agents) |
| Ctrl+N / Ctrl+O / Ctrl+Q | new chat / open any file or folder / quit |
| Ctrl+Left / Ctrl+Right | resize the panes of the current page; with a popup open, Ctrl+arrows resize the popup |
| Chat: Ctrl+T / Ctrl+E / Ctrl+R / Ctrl+S | toggle activity / inspect steps / result window / sources |
| Chat commands | `/open <path>` `/agent <name> <prompt>` `/acp <url>` `/analyse` `/new` `/quit` |

## Configuration (`.env`)

| Variable | Default | Purpose |
|----------|---------|---------|
| `OPENAI_API_KEY` | - | required |
| `HACKBOT_MODEL` | `gpt-4.1-nano` | any OpenAI chat model with tool calling |
| `OPENAI_BASE_URL` | OpenAI | OpenAI-compatible endpoint |
| `HACKBOT_MAX_RESEARCHERS` | 3 | parallel researcher agents per question (1-4) |
| `HACKBOT_ACP_URL` | - | remote ACP server, e.g. `http://127.0.0.1:8000` |
| `HACKBOT_MCP_CONFIG` | - | extra MCP servers (`mcpServers` JSON, see `mcp_servers.example.json`) |
| `HACKBOT_DATA_DIR` | `~/.hackbot` | sessions, triage state, reports, MCP server log |
| `HACKBOT_STREAM` / `HACKBOT_SHOW_ACTIVITY` | true | UI defaults |

Sessions are stored as JSON in `~/.hackbot/sessions/`, triage decisions in `~/.hackbot/triage/`, document
analyst reports in `~/.hackbot/reports/`, pane sizes in `~/.hackbot/ui.json`.

## Version pins worth knowing

`acp-sdk 1.0.x` requires `uvicorn<0.35` and `fastapi<0.120`, which in turn pins the `mcp` package to 1.x;
the MCP server import is compatible with both mcp 1.x (`FastMCP`) and 2.x (`MCPServer`).

## Credits

Original HackBot by [morpheuslord](https://github.com/morpheuslord/HackBot). This branch replaces the
single-script chatbot with the multi-agent MCP/ACP architecture above.

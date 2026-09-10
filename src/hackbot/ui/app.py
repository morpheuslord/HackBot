"""Application shell: header tabs, page body, status footer, popups, palette, wizard, event loop."""

from __future__ import annotations

import hashlib
import json
import queue
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from rich.console import Console, Group, RenderableType
from rich.layout import Layout
from rich.live import Live
from rich.markdown import Markdown
from rich.spinner import Spinner
from rich.table import Table
from rich.text import Text

from ..agents import Event, Runtime
from ..config import MODEL_CHOICES, Config
from ..core import documents as D
from ..core import sarif as S
from ..history import SessionStore
from .keys import Key, poll_key, raw_mode
from .pages import (
    AgentsPage,
    ChatPage,
    DocFindingsPopup,
    FilePage,
    HelpPage,
    HistoryPage,
    HomePage,
    Page,
    SettingsPage,
    TriageReviewPopup,
)
from .widgets import Composite, Popup

PAGE_KEYS = {"f1": "home", "f2": "chat", "f3": "history", "f4": "file", "f5": "agents", "f6": "settings", "f7": "help"}
PAGE_ORDER = ["home", "chat", "history", "file", "agents", "settings", "help"]
PAGE_KEYS_INV = {v: k.upper() for k, v in PAGE_KEYS.items()}
DEFAULT_SPLITS = {"home": 40, "chat": 60, "history": 40, "file": 65}


class App:
    def __init__(self, cfg: Config, file_path: str | None = None, acp_port: int | None = None):
        self.cfg = cfg
        self.acp_port = acp_port
        self.acp_proc: subprocess.Popen | None = None
        self._acp_deadline = 0.0
        self._acp_last_try = 0.0
        self.console = Console()
        self.events: queue.Queue[Event] = queue.Queue()
        self.runtime = Runtime(cfg, self.events)
        self.store = SessionStore(cfg.data_dir)
        self.session = self.store.new()
        self.pages: dict[str, Page] = {
            p.name: p for p in (HomePage(self), ChatPage(self), HistoryPage(self), FilePage(self),
                                AgentsPage(self), SettingsPage(self), HelpPage(self))  # fmt: skip
        }
        self.page = "home"
        self.popup: Popup | None = None
        self.running = True
        self.dirty = True
        self.status = ""
        self.pending = ""  # streaming answer text
        self.mcp_tools: list[str] = []
        self.document: D.Document | None = None
        self.sarif: S.SarifReport | None = None
        self.triage: dict[int, dict] = {}
        self.last_triage: dict | None = None
        self.doc_report: dict | None = None
        self.remote_agents: list[dict] = []
        self.remote_agents_error = ""
        self.splits: dict[str, int] = dict(DEFAULT_SPLITS)
        self._load_ui_state()
        if file_path:
            self.open_file(file_path, switch=False)
        self.pages["home"].on_show()

    # ----------------------------------------------------------------- navigation / layout
    @property
    def current(self) -> Page:
        return self.pages[self.page]

    def show(self, name: str) -> None:
        if name == "sarif":
            name = "file"
        self.page = name
        self.current.on_show()
        self.dirty = True

    def notify(self, title: str, body: str, style: str = "cyan") -> None:
        self.popup = Popup(title, body, kind="message", style=style)

    def split(self, page: str) -> int:
        return max(20, min(80, self.splits.get(page, DEFAULT_SPLITS.get(page, 50))))

    def adjust_split(self, delta: int) -> None:
        if self.page in DEFAULT_SPLITS:
            self.splits[self.page] = max(20, min(80, self.split(self.page) + delta))
            self.status = f"{self.current.title} split {self.splits[self.page]}%"
            self._save_ui_state()

    def resize_popup(self, dw: int, dh: int) -> None:
        p = self.popup
        if p is None:
            return
        w, h = self.console.size
        pw, ph = p.size(self.console, w, h)
        p.width = max(30, min(w - 2, pw + dw))
        p.height = max(6, min(h - 1, ph + dh))

    def _ui_state_path(self) -> Path:
        return self.cfg.data_dir / "ui.json"

    def _load_ui_state(self) -> None:
        try:
            data = json.loads(self._ui_state_path().read_text(encoding="utf-8"))
            self.splits.update({k: int(v) for k, v in data.get("splits", {}).items()})
        except Exception:  # noqa: BLE001 - no state yet
            pass

    def _save_ui_state(self) -> None:
        try:
            self._ui_state_path().write_text(json.dumps({"splits": self.splits}), encoding="utf-8")
        except Exception:  # noqa: BLE001
            pass

    def trace_rows(self) -> list[dict]:
        tracer = self.runtime.tracer
        if tracer is not None:
            return tracer.to_list()
        if self.runtime.last:
            return self.runtime.last.steps
        return []

    # ----------------------------------------------------------------- agent turns
    def submit(self, text: str, agent: str = "orchestrator") -> None:
        if text.startswith("/"):
            self.command(text)
            return
        if not self.runtime.ready:
            msg = self.runtime.error or "The agent runtime is still starting (MCP server + agents). Try again in a moment."
            self.notify("Agents not ready", msg, style="yellow")
            return
        if self.runtime.busy:
            self.notify("Busy", "The team is still working on the previous request.", style="yellow")
            return
        if not self.cfg.ready:
            self.start_wizard()
            return
        self.session.add("user", text if agent == "orchestrator" else f"[{agent}] {text}")
        self.pending = ""
        self.pages["chat"].scroll.end()  # type: ignore[attr-defined]
        doc = self.document
        self.runtime.submit(
            text,
            self.sarif.path if self.sarif else None,
            agent=agent,
            document_path=doc.path if doc else None,
            document_desc=doc.description if doc else "",
        )
        if self.page != "chat":
            self.show("chat")

    def analyse_report(self) -> None:
        if not self.document:
            self.open_file_dialog()
            return
        if self.sarif:
            self.submit("Analyse the loaded SARIF report: triage every finding (analyse_sarif), then give me an executive "
                        "summary, the findings grouped by CWE class, what to fix first and why, and remediation guidance.")  # fmt: skip
        else:
            self.submit(f"Review the loaded file {self.document.name} with analyse_document, then summarise what the file is, "
                        "the security-relevant findings ordered by severity with line references, and remediation guidance.")  # fmt: skip

    def drain_events(self) -> None:
        while True:
            try:
                ev = self.events.get_nowait()
            except queue.Empty:
                return
            self.dirty = True
            if ev.kind == "ready":
                self.mcp_tools = list(ev.data or [])
                self.status = f"agents ready - {len(self.mcp_tools)} MCP tools"
                if self.acp_proc is not None:
                    self._acp_deadline = time.monotonic() + 45  # keep probing until the ACP server is up
            elif ev.kind == "delta":
                if self.cfg.stream:
                    self.pending += ev.text
            elif ev.kind == "reset":
                self.pending = ""
            elif ev.kind == "answer":
                out = ev.data
                self.pending = ""
                self.session.add("assistant", ev.text, sources=out.sources if out else [], trace=out.steps if out else [])
                self.status = "answer ready: Ctrl+R result window  Ctrl+S sources"
                if out and out.triage and out.triage.get("items"):
                    self.apply_agent_triage(out.triage)
                if out and out.doc_report and out.doc_report.get("findings") is not None:
                    self.apply_doc_report(out.doc_report)
            elif ev.kind == "error":
                self.pending = ""
                self.session.add("error", ev.text)
            elif ev.kind == "done":
                self.store.save(self.session)

    # ----------------------------------------------------------------- commands / palette
    def command(self, text: str) -> None:
        cmd, _, arg = text[1:].partition(" ")
        cmd, arg = cmd.lower(), arg.strip()
        if cmd in ("quit", "exit", "q"):
            self.confirm_quit()
        elif cmd == "new":
            self.new_session()
        elif cmd in ("open", "file", "sarif", "load"):
            self.open_file(arg) if arg else self.show("file")
        elif cmd == "agent":
            name, _, prompt = arg.partition(" ")
            if name in ("orchestrator", "researcher", "analyst", "document_analyst", "tutor") and prompt:
                self.submit(prompt, agent=name)
            elif name and prompt:
                self.call_remote(name, prompt)
            else:
                self.notify("Usage", "/agent <researcher|analyst|document_analyst|tutor|remote-name> <prompt>", style="yellow")
        elif cmd == "acp":
            self.set_acp_url(arg)
        elif cmd == "tools":
            self.show("agents")
        elif cmd == "analyse":
            self.analyse_report()
        elif cmd in PAGE_ORDER:
            self.show(cmd)
        else:
            self.notify("Unknown command", f"'{text}' - see Help (F7).", style="red")

    def palette_items(self) -> list[tuple[str, str, Any]]:
        items: list[tuple[str, str, Any]] = [
            ("New chat session", "Ctrl+N", self.new_session),
            ("Open file ... (SARIF, code, config, log, JSON, CSV, text)", "Ctrl+O", self.open_file_dialog),
            ("Analyse loaded file with the agent team", "orchestrator -> analyst / document analyst", self.analyse_report),
            ("Review agent triage (SARIF)", "accept / reject verdicts", self.open_triage_review),
            ("Review file findings", "document analyst results", self.open_doc_findings),
            ("Export triage report (markdown)", "File page: e", self.export_triage),
            ("Show last answer in result window", "Ctrl+R", self.open_result_window),
            ("Show sources of last answer", "Ctrl+S", self.open_sources_window),
            ("Toggle activity panel", "Ctrl+T", lambda: self.pages["chat"].handle(Key("ctrl+t"))),
            ("Run a specialist agent directly ...", "researcher / analyst / document_analyst / tutor", lambda: self.run_agent_dialog(None)),
            ("Refresh ACP remote agents", "Agents page: r", self.refresh_remote_agents),
            ("Set ACP server URL ...", "/acp <url>", lambda: self.pages["agents"].handle(Key("char", "u"))),
            ("Reset pane sizes", "Ctrl+Left/Right to resize", self.reset_splits),
            ("Save settings to .env", "Settings page: s", self.save_settings),
            ("Quit", "Ctrl+Q", self.confirm_quit),
        ]
        for name in PAGE_ORDER:
            items.append((f"Go to {self.pages[name].title}", PAGE_KEYS_INV[name], (lambda n=name: self.show(n))))
        return items

    def open_palette(self) -> None:
        def run(value: Any) -> None:
            if callable(value):
                value()
            elif isinstance(value, str) and value.strip():
                self.submit(value.strip())

        self.popup = Popup("Command palette", kind="palette", options=self.palette_items(), on_submit=run, width=84,
                           placeholder="type a command, or a question for the agents", allow_free_text=True)  # fmt: skip

    def reset_splits(self) -> None:
        self.splits = dict(DEFAULT_SPLITS)
        self._save_ui_state()

    # ----------------------------------------------------------------- sessions
    def new_session(self, switch: bool = True, save: bool = True) -> None:
        if save:
            self.store.save(self.session)
        self.session = self.store.new()
        self.runtime.reset_history([])
        self.pending = ""
        self.pages["chat"].scroll.end()  # type: ignore[attr-defined]
        if switch:
            self.show("chat")

    def load_session(self, sid: str) -> None:
        if self.runtime.busy:
            self.notify("Busy", "Wait for the current answer before switching chats.", style="yellow")
            return
        self.store.save(self.session)
        self.session = self.store.load(sid)
        self.runtime.reset_history(self.session.llm_history())
        self.pages["chat"].scroll.end()  # type: ignore[attr-defined]
        self.show("chat")

    # ----------------------------------------------------------------- files (SARIF or anything)
    def _state_path(self, kind: str, key: str) -> Path:
        d = self.cfg.data_dir / kind
        d.mkdir(parents=True, exist_ok=True)
        return d / f"{hashlib.sha1(key.encode()).hexdigest()[:12]}.json"

    def open_file(self, path: str, switch: bool = True) -> None:
        try:
            doc = D.load_document(path)
        except Exception as exc:  # noqa: BLE001 - show the user what went wrong
            self.notify("Could not load file", f"{path}\n\n{type(exc).__name__}: {exc}", style="red")
            return
        self.document, self.sarif = doc, doc.sarif
        self.triage, self.doc_report, self.last_triage = {}, None, None
        if doc.sarif:
            tp = self._state_path("triage", doc.path)
            if tp.exists():
                try:
                    self.triage = {int(k): v for k, v in json.loads(tp.read_text(encoding="utf-8")).items()}
                except Exception:  # noqa: BLE001
                    self.triage = {}
        else:
            rp = self._state_path("reports", doc.path)
            if rp.exists():
                try:
                    self.doc_report = json.loads(rp.read_text(encoding="utf-8"))
                except Exception:  # noqa: BLE001
                    self.doc_report = None
        fp = self.pages["file"]
        fp.sarif_page.selected = 0  # type: ignore[attr-defined]
        fp.doc_page.top, fp.doc_page.matches, fp.doc_page.pattern, fp.doc_page.highlight = 0, [], "", None  # type: ignore[attr-defined]
        self.status = f"loaded {doc.description}"
        self.session.add("note", f"loaded file {doc.description}")
        if switch:
            self.show("file")

    open_sarif = open_file  # backwards-compatible alias

    def open_file_dialog(self) -> None:
        self.popup = Popup("Open file or folder", "Path to ANY text file (SARIF, code, config, log, JSON, CSV ...) or a folder of them",
                           kind="input", width=90, placeholder="samples/sample.sarif",
                           on_submit=lambda p: p and self.open_file(p))  # fmt: skip

    open_sarif_dialog = open_file_dialog

    # ----------------------------------------------------------------- triage (SARIF)
    def save_triage(self) -> None:
        if self.sarif:
            self._state_path("triage", self.sarif.path).write_text(json.dumps(self.triage, indent=1), encoding="utf-8")

    def apply_agent_triage(self, report: dict) -> None:
        self.last_triage = report
        for item in report.get("items", []):
            idx = int(item.get("index", 0))
            if not self.sarif or not self.sarif.get(idx):
                continue
            existing = self.triage.get(idx, {})
            if existing.get("by") == "user":
                continue  # never overwrite a human decision
            self.triage[idx] = {
                "verdict": item.get("verdict", "needs-review"),
                "priority": item.get("priority"),
                "rationale": item.get("rationale", ""),
                "fix": item.get("fix", ""),
                "cwe": item.get("cwe", ""),
                "note": existing.get("note", ""),
                "by": "agent",
                "accepted": False,
            }
        self.save_triage()
        self.open_triage_review()

    def open_triage_review(self) -> None:
        if not self.sarif:
            self.notify("No SARIF report", "Triage review needs a SARIF file (Ctrl+O).", style="yellow")
            return
        self.popup = TriageReviewPopup(self)

    def export_triage(self) -> None:
        if not self.sarif:
            self.notify("No SARIF report", "Load a SARIF report first.", style="yellow")
            return
        out = self.cfg.data_dir / "triage" / (Path(self.sarif.path).stem + ".triage.md")
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(S.triage_markdown(self.sarif, self.triage), encoding="utf-8")
        self.popup = Popup("Triage report exported", Markdown(S.triage_markdown(self.sarif, self.triage)), kind="view",
                           width=110, height=28, hint=f"saved to {out}    Esc close")  # fmt: skip

    # ----------------------------------------------------------------- document findings (any file)
    def apply_doc_report(self, report: dict) -> None:
        self.doc_report = report
        if self.document and not self.sarif:
            self._state_path("reports", self.document.path).write_text(json.dumps(report, indent=1), encoding="utf-8")
        self.open_doc_findings()

    def open_doc_findings(self) -> None:
        if not self.doc_report:
            self.notify("No findings yet", "Load a file (Ctrl+O) and press a on the File page to run the document analyst.", style="yellow")
            return
        self.popup = DocFindingsPopup(self)

    # ----------------------------------------------------------------- result / sources / steps windows
    def _last_assistant(self) -> dict | None:
        for e in reversed(self.session.entries):
            if e["role"] == "assistant":
                return e
        return None

    def open_result_window(self) -> None:
        e = self._last_assistant()
        if not e:
            self.notify("No answer yet", "Ask the team something first.", style="yellow")
            return
        w, h = self.console.size
        self.popup = Popup("Result", Markdown(e["content"]), kind="view", width=w - 6, height=h - 4)

    def open_sources_window(self) -> None:
        e = self._last_assistant()
        sources = (e or {}).get("sources") or []
        if not sources:
            self.notify("No sources", "The last answer did not use web sources.", style="yellow")
            return
        opts = [(f"{s['title'][:60]}  {s['url']}", s["url"]) for s in sources]
        self.popup = Popup("Sources (Enter = ask the team to summarise)", kind="list", options=opts, width=100,
                           on_submit=lambda url: self.submit(f"Summarise this source and what it says about my question: {url}"))  # fmt: skip

    def open_step_window(self, row: dict) -> None:
        body = Group(
            Text.assemble(("Agent   ", "dim"), (row["agent"], "bold cyan"), ("   kind ", "dim"), row["kind"],
                          ("   status ", "dim"), row["status"], ("   ", ""), (f"{row['elapsed']:.2f}s", "dim")),  # fmt: skip
            Text.assemble(("Step    ", "dim"), (row["title"], "bold")),
            Text(""),
            Text("Detail / result", style="bold underline"),
            Text(row.get("detail") or "-"),
        )
        w, h = self.console.size
        self.popup = Popup(f"Step {row['id']}", body, kind="view", width=min(110, w - 6), height=min(30, h - 4), style="yellow")

    # ----------------------------------------------------------------- agents / ACP
    def start_acp_server(self) -> None:
        """Launch `hackbot-acp` as a managed child process (used by `hackbot --acp`)."""
        port = self.acp_port or 8000
        log = self.cfg.data_dir / "acp-server.log"
        self.cfg.data_dir.mkdir(parents=True, exist_ok=True)
        self.acp_proc = subprocess.Popen(
            [sys.executable, "-m", "hackbot.acp_server", "--port", str(port)],
            stdout=open(log, "a", encoding="utf-8"),
            stderr=subprocess.STDOUT,
            cwd=str(Path.cwd()),
        )
        self.cfg.acp_url = f"http://127.0.0.1:{port}"
        self.status = f"ACP server starting on port {port}"

    def poll_acp_server(self) -> None:
        if self.acp_proc is None or self.remote_agents or not self.runtime.ready:
            return
        now = time.monotonic()
        if now > self._acp_deadline or now - self._acp_last_try < 1.5:
            return
        self._acp_last_try = now
        if self.acp_proc.poll() is not None:
            self.remote_agents_error = f"ACP server exited (code {self.acp_proc.returncode}); see acp-server.log"
            self.acp_proc = None
            return
        self.refresh_remote_agents(quiet=True)

    def stop_acp_server(self) -> None:
        if self.acp_proc is not None and self.acp_proc.poll() is None:
            self.acp_proc.terminate()
            try:
                self.acp_proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.acp_proc.kill()
        self.acp_proc = None

    def run_agent_dialog(self, name: str | None) -> None:
        def ask(agent: str) -> None:
            if agent == "analyst":
                self.submit("Triage the SARIF report.", agent="analyst")
                return
            if agent == "document_analyst":
                self.submit("Review the loaded file for security-relevant issues.", agent="document_analyst")
                return
            hint = {"researcher": "one focused question", "tutor": "a concept to explain", "orchestrator": "anything"}.get(agent, "")
            self.popup = Popup(f"Run {agent} directly", hint, kind="input", width=90,
                               on_submit=lambda p: p and self.submit(p, agent=agent))  # fmt: skip

        if name:
            ask(name)
        else:
            opts = [(n, n) for n in ("orchestrator", "researcher", "analyst", "document_analyst", "tutor")]
            self.popup = Popup("Which agent?", kind="list", options=opts, on_submit=ask)

    def set_acp_url(self, url: str) -> None:
        self.cfg.acp_url = url.strip() or None
        self.remote_agents, self.remote_agents_error = [], ""
        if self.cfg.acp_url:
            self.refresh_remote_agents()
        self.dirty = True

    def refresh_remote_agents(self, quiet: bool = False) -> None:
        if not self.cfg.acp_url:
            if not quiet:
                self.notify("ACP", "Set an ACP server URL first (Agents page: u, or /acp <url>).", style="yellow")
            return
        from ..acp_client import list_agents

        async def job() -> None:
            try:
                self.remote_agents = await list_agents(self.cfg.acp_url)  # type: ignore[arg-type]
                self.remote_agents_error = ""
                self.status = f"ACP: {len(self.remote_agents)} remote agents at {self.cfg.acp_url}"
            except Exception as exc:  # noqa: BLE001
                self.remote_agents = []
                self.remote_agents_error = "waiting for ACP server ..." if quiet else f"{type(exc).__name__}: {exc}"
            self.events.put(Event("remote"))

        if self.runtime.run_coro(job()) is None and not quiet:
            self.notify("ACP", "Runtime not started yet.", style="yellow")

    def call_remote_dialog(self, name: str) -> None:
        self.popup = Popup(f"Prompt for remote agent '{name}'", f"via ACP at {self.cfg.acp_url}", kind="input", width=90,
                           on_submit=lambda p: p and self.call_remote(name, p))  # fmt: skip

    def call_remote(self, name: str, prompt: str) -> None:
        if not self.cfg.acp_url:
            self.notify("ACP", "Set an ACP server URL first.", style="yellow")
            return
        from ..acp_client import run_agent

        self.session.add("user", f"[acp:{name}] {prompt}")
        self.status = f"calling remote agent {name} ..."
        self.show("chat")

        async def job() -> None:
            try:
                reply = await run_agent(self.cfg.acp_url, name, prompt)  # type: ignore[arg-type]
                self.events.put(Event("answer", reply, None))
            except Exception as exc:  # noqa: BLE001
                self.events.put(Event("error", f"remote agent {name}: {type(exc).__name__}: {exc}"))
            self.events.put(Event("done"))

        if self.runtime.run_coro(job()) is None:
            self.notify("ACP", "Runtime not started yet.", style="yellow")

    # ----------------------------------------------------------------- settings / wizard
    def save_settings(self) -> None:
        path = self.cfg.save_env()
        self.store = SessionStore(self.cfg.data_dir)
        self.status = f"settings saved to {path.name}; restarting agents"
        self.runtime.restart(self.cfg)
        self.mcp_tools = []
        self.notify("Settings saved", f"{path}\n\nAgent runtime restarting with the new configuration.")

    def reload_settings(self) -> None:
        self.cfg = Config.load()
        self.runtime.restart(self.cfg)
        self.status = "settings reloaded from .env"

    def start_wizard(self) -> None:
        cfg = self.cfg

        def step_model(key: str) -> None:
            if not key:
                self.notify("Setup skipped", "Set the API key later in Settings (F6).", style="yellow")
                return
            cfg.api_key = key

            def finish(model: str) -> None:
                cfg.model = model
                self.save_settings()
                self.show("chat")

            self.popup = Popup("Setup 2/2 - choose a model", "Cheapest first; you can change it later.", kind="list",
                               options=[(m, m) for m in MODEL_CHOICES], on_submit=finish)  # fmt: skip

        def step_key() -> None:
            self.popup = Popup("Setup 1/2 - OpenAI API key", "Paste your key (it is saved to .env in this folder).",
                               kind="input", mask=True, width=80, on_submit=step_model)  # fmt: skip

        self.popup = Popup("Welcome to HackBot", "No API key found. Let's set up the agent team.\n\n"
                           "You will need an OpenAI API key.", kind="message", on_close=step_key)  # fmt: skip

    def confirm_quit(self) -> None:
        self.popup = Popup("Quit HackBot?", "The current chat will be saved.", kind="confirm", style="red",
                           on_submit=lambda _: setattr(self, "running", False))  # fmt: skip

    # ----------------------------------------------------------------- input
    def handle_key(self, key: Key) -> None:
        if self.popup is not None:
            if key.name in ("ctrl+left", "ctrl+right", "ctrl+up", "ctrl+down"):
                dw = {"ctrl+left": -6, "ctrl+right": 6}.get(key.name, 0)
                dh = {"ctrl+up": -2, "ctrl+down": 2}.get(key.name, 0)
                self.resize_popup(dw, dh)
                return
            popup = self.popup
            close = popup.handle(key)
            if close and self.popup is popup:  # a callback may have opened a new popup
                self.popup = None
                if popup.on_close:
                    popup.on_close()
            return
        if key.name in PAGE_KEYS:
            self.show(PAGE_KEYS[key.name])
        elif key.name == "tab":
            self.show(PAGE_ORDER[(PAGE_ORDER.index(self.page) + 1) % len(PAGE_ORDER)])
        elif key.name == "ctrl+left":
            self.adjust_split(-5)
        elif key.name == "ctrl+right":
            self.adjust_split(5)
        elif key.name == "ctrl+p":
            self.open_palette()
        elif key.name in ("ctrl+q", "ctrl+c"):
            self.confirm_quit()
        elif key.name == "ctrl+n":
            self.new_session()
        elif key.name == "ctrl+o":
            self.open_file_dialog()
        elif not self.current.handle(key) and key.name == "escape":
            self.confirm_quit() if self.page == "home" else self.show("home")

    # ----------------------------------------------------------------- rendering
    def header(self) -> Text:
        t = Text(" HackBot ", style="bold black on cyan")
        for key, name in PAGE_KEYS.items():
            t.append(f" {key.upper()} {self.pages[name].title} ", style="bold reverse" if name == self.page else "dim")
        t.append("  Ctrl+P palette", style="dim italic")
        return t

    def footer(self) -> RenderableType:
        left = Text(f" {self.current.hint}", style="dim")
        t = self.runtime.totals
        right = Text()
        if self.status:
            right.append(f"{self.status}  ", style="green")
        if self.document:
            right.append(f"file:{self.document.name}  ", style="yellow")
        right.append(f"{self.cfg.model}  ", style="cyan")
        right.append(f"req {t.requests} tok {t.input_tokens + t.output_tokens} ", style="dim")
        middle: RenderableType | None = None
        if self.runtime.busy:
            middle = Spinner("line", text=Text("agents", style="yellow"), style="yellow")
        elif not self.runtime.ready and not self.runtime.error:
            middle = Spinner("line", text=Text("starting", style="yellow"), style="yellow")
        return _grid(left, right, middle)

    def render(self) -> RenderableType:
        w, h = self.console.size
        root = Layout()
        root.split_column(Layout(self.header(), name="header", size=1), Layout(name="body"), Layout(self.footer(), name="footer", size=1))
        root["body"].update(self.current.render(w, max(3, h - 2)))
        return Composite(root, self.popup)

    # ----------------------------------------------------------------- loop
    def run(self) -> None:
        self.runtime.start()
        if self.acp_port:
            self.start_acp_server()
        if not self.cfg.ready:
            self.start_wizard()
        try:
            with raw_mode(), Live(self.render(), console=self.console, screen=True, auto_refresh=False,
                                  redirect_stdout=False, redirect_stderr=False) as live:  # fmt: skip
                while self.running:
                    key = poll_key(0.05)
                    if key is not None:
                        self.handle_key(key)
                        self.dirty = True
                    self.drain_events()
                    self.poll_acp_server()
                    if self.dirty or self.runtime.busy or not self.runtime.ready:
                        live.update(self.render(), refresh=True)
                        self.dirty = False
        except KeyboardInterrupt:
            pass
        finally:
            self.store.save(self.session)
            self.runtime.stop()
            self.stop_acp_server()


def _grid(left: RenderableType, right: RenderableType, middle: RenderableType | None = None) -> Table:
    grid = Table.grid(expand=True, padding=(0, 1))
    grid.add_column(ratio=1, no_wrap=True, overflow="ellipsis")
    if middle is not None:
        grid.add_column(width=10)
    grid.add_column(justify="right", no_wrap=True)
    grid.add_row(*(x for x in (left, middle, right) if x is not None))
    return grid

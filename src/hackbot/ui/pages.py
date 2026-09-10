"""Pages of the TUI: Home, Chat, History, File (SARIF or any document), Agents, Settings, Help -
plus the triage-review and document-findings windows."""

from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from rich import box
from rich.align import Align
from rich.console import Group, RenderableType
from rich.layout import Layout
from rich.markdown import Markdown
from rich.panel import Panel
from rich.spinner import Spinner
from rich.table import Table
from rich.text import Text

from ..agents.orchestrator import describe_team
from ..config import MODEL_CHOICES
from ..core import sarif as S
from .keys import Key
from .widgets import FixedHeight, Popup, ScrollState, ScrollView, TextInput

if TYPE_CHECKING:
    from .app import App

LEVEL_STYLE = {"error": "bold red", "warning": "yellow", "note": "blue", "none": "dim"}
VERDICT_STYLE = {"untriaged": "dim", "true-positive": "bold red", "false-positive": "green", "needs-review": "yellow"}
SEVERITY_STYLE = {"critical": "bold red", "high": "red", "medium": "yellow", "low": "blue", "info": "dim"}
STATUS_MARK = {"running": ("~", "yellow"), "done": ("+", "green"), "failed": ("!", "red")}
KIND_STYLE = {"agent": "bold cyan", "tool": "yellow", "plan": "magenta", "thought": "italic white", "error": "red"}
BORDER = "bright_black"
RESIZE_HINT = "Ctrl+Left/Right resize"


# ------------------------------------------------------------------ shared renderers
def render_trace(rows: list[dict], selected: int | None = None, compact: bool = False) -> RenderableType:
    if not rows:
        return Text("no activity yet", style="dim italic")
    lines: list[Text] = []
    for i, r in enumerate(rows):
        mark, mstyle = STATUS_MARK.get(r["status"], ("?", "dim"))
        t = Text("  " * r["depth"])
        t.append(f"{mark} ", style=mstyle)
        t.append(f"{r['agent']} ", style=KIND_STYLE.get(r["kind"], ""))
        title = r["title"]
        if r["kind"] == "agent" and ":" in title:
            title = title.split(":", 1)[1].strip()
        t.append(title, style="dim" if r["kind"] == "thought" else "")
        if r["status"] != "running" or not compact:
            t.append(f"  {r['elapsed']:.1f}s", style="dim")
        if selected is not None and i == selected:
            t.stylize("reverse")
        t.no_wrap = True
        t.overflow = "ellipsis"
        lines.append(t)
    return Group(*lines)


def render_transcript(entries: list[dict], pending: str = "", busy: bool = False) -> RenderableType:
    parts: list[RenderableType] = []
    turn = 0
    for e in entries:
        role, content = e["role"], e.get("content", "")
        if role == "user":
            turn += 1
            parts.append(Text(f"  turn {turn}", style="dim"))
            parts.append(Panel(Text(content), title="You", title_align="left", border_style="green", box=box.ROUNDED))
        elif role == "assistant":
            trace = [r for r in e.get("trace", []) if r["kind"] in ("agent", "plan") and 1 <= r["depth"] <= 2][:10]
            if trace:
                parts.append(render_trace(trace, compact=True))
            parts.append(Panel(Markdown(content or "*(empty reply)*"), title="HackBot", title_align="left", border_style="cyan", box=box.ROUNDED))
            if e.get("sources"):
                src = Text("  sources: ", style="dim")
                src.append(" | ".join(s["title"][:40] for s in e["sources"][:4]), style="dim underline")
                parts.append(src)
            parts.append(Text(""))
        elif role == "note":
            parts.append(Text(f"  -- {content}", style="dim italic"))
        else:
            parts.append(Panel(Text(content, style="red"), title="Error", title_align="left", border_style="red", box=box.ROUNDED))
    if pending:
        parts.append(Panel(Markdown(pending), title="HackBot (streaming)", title_align="left", border_style="dim cyan", box=box.ROUNDED))
    elif busy:
        parts.append(Spinner("dots", text=Text(" agents working - see Activity panel", style="dim cyan"), style="cyan"))
    if not parts:
        parts.append(Align.center(Text.from_markup(
            "\n[bold cyan]HackBot[/] - agentic cybersecurity research team\n\n"
            "[dim]Ask a question, paste a CVE id, or load any file (Ctrl+O): SARIF, code, config, log, JSON ...\n"
            "Ctrl+P opens the command palette. F1 is Home.[/]"), vertical="middle"))  # fmt: skip
    return Group(*parts)


def panel(body: RenderableType, title: str | None = None, style: str = BORDER) -> Panel:
    return Panel(body, title=f"[bold]{title}[/]" if title else None, title_align="left", border_style=style, box=box.SQUARE)


def when(updated: str) -> str:
    """'today 14:02' / 'yesterday 09:10' / '2026-09-03 18:22' for session timestamps."""
    try:
        dt = datetime.strptime(updated, "%Y-%m-%d %H:%M")
    except ValueError:
        return updated
    days = (datetime.now().date() - dt.date()).days
    if days == 0:
        return f"today {dt:%H:%M}"
    if days == 1:
        return f"yesterday {dt:%H:%M}"
    return updated


def session_preview(s: Any) -> str:
    for e in s.entries:
        if e["role"] == "assistant" and e.get("content"):
            return re.sub(r"[#*`>\n]+", " ", e["content"]).strip()[:90]
    return ""


class Page:
    name = "page"
    title = "Page"
    hint = ""

    def __init__(self, app: "App"):
        self.app = app

    def on_show(self) -> None:
        pass

    def render(self, width: int, height: int) -> RenderableType:
        raise NotImplementedError

    def handle(self, key: Key) -> bool:
        return False


# ------------------------------------------------------------------ home
class HomePage(Page):
    name, title = "home", "Home"
    hint = f"Up/Dn select  Enter open  Ctrl+P palette  {RESIZE_HINT}"

    def __init__(self, app: "App"):
        super().__init__(app)
        self.selected = 1
        self.items: list[tuple[str, str, Any]] = []  # (label, hint, action | None for headers)

    def on_show(self) -> None:
        app = self.app
        items: list[tuple[str, str, Any]] = [
            ("QUICK ACTIONS", "", None),
            ("New chat", "start a fresh session with the agent team", lambda: app.new_session()),
            ("Open file", "SARIF, source code, config, log, JSON, CSV, markdown ...", app.open_file_dialog),
        ]
        if app.document:
            items.append(("Analyse loaded file", f"{app.document.name}: orchestrator -> " +
                          ("analyst -> triage window" if app.document.is_sarif else "document analyst -> findings"), app.analyse_report))  # fmt: skip
        if any(t.get("by") == "agent" for t in app.triage.values()):
            items.append(("Review agent triage", "accept / reject the analyst's verdicts", app.open_triage_review))
        if app.doc_report and not (app.document and app.document.is_sarif):
            items.append(("Review file findings", "document analyst results", app.open_doc_findings))
        items += [
            ("Agents & protocols", "team, MCP servers, ACP remote agents", lambda: app.show("agents")),
            ("Settings", "model, API key, ACP/MCP endpoints", lambda: app.show("settings")),
            ("Help", "keys and architecture", lambda: app.show("help")),
            ("RECENT CHATS", "", None),
        ]
        sessions = app.store.list()[:10]
        for s in sessions:
            items.append((s.title, f"{when(s.updated)}  |  {len(s.entries)} entries  |  {session_preview(s)}",
                          (lambda sid=s.id: app.load_session(sid))))  # fmt: skip
        if not sessions:
            items.append(("(no previous chats yet)", "", None))
        self.items = items
        self.selected = min(max(self.selected, 1), len(items) - 1)
        if self.items[self.selected][2] is None:
            self._move(1)

    def _move(self, delta: int) -> None:
        i = self.selected
        for _ in range(len(self.items)):
            i = (i + delta) % len(self.items)
            if self.items[i][2] is not None:
                self.selected = i
                return

    def render(self, width: int, height: int) -> RenderableType:
        app = self.app
        rt = app.runtime
        grid = Table.grid(padding=(0, 1))
        grid.add_column(style="dim", no_wrap=True)
        grid.add_column()

        def row(k: str, v: RenderableType) -> None:
            grid.add_row(k, v)

        row("API key", Text("configured", style="green") if app.cfg.ready else Text("missing - press Enter on Settings", style="red"))
        row("Model", Text(app.cfg.model, style="cyan"))
        if rt.error:
            row("Runtime", Text(f"failed: {rt.error[:60]}", style="red"))
        elif rt.ready:
            row("Runtime", Text(f"ready - {len(app.mcp_tools)} MCP tools via hackbot-mcp (stdio)", style="green"))
        else:
            row("Runtime", Spinner("dots", text=Text(" starting MCP server + agents", style="yellow"), style="yellow"))
        ext = rt.system.external_tools if rt.system else {}
        row("External MCP", Text(", ".join(f"{k} ({len(v)})" for k, v in ext.items()) or "none (Settings > MCP config)", style="dim" if not ext else ""))
        acp = app.cfg.acp_url or "not configured"
        if app.remote_agents:
            acp += f"  ({len(app.remote_agents)} agents)"
        row("ACP remote", Text(acp, style="" if app.cfg.acp_url else "dim"))
        if app.document:
            row("File", Text(app.document.description))
            if app.sarif:
                tc = S.triage_counts(app.triage)
                row("Triage", Text("  ".join(f"{k} {v}" for k, v in tc.items() if k != "untriaged") or "not started", style="dim"))
            elif app.doc_report:
                row("Findings", Text(f"{len(app.doc_report.get('findings', []))} from document analyst", style="dim"))
        else:
            row("File", Text("none loaded (Ctrl+O)", style="dim"))
        t = rt.totals
        row("Usage", Text(f"{t.turns} turns  {t.requests} LLM requests  {t.tool_calls} tool calls  "
                          f"{t.input_tokens + t.output_tokens} tokens", style="dim"))  # fmt: skip
        status = panel(grid, "Status")

        table = Table.grid(expand=True, padding=(0, 1))
        table.add_column(no_wrap=True, overflow="ellipsis", ratio=2)
        table.add_column(no_wrap=True, overflow="ellipsis", ratio=3)
        visible = max(1, height - 4)
        top = max(0, min(self.selected - visible // 2, len(self.items) - visible))
        for i, (label, hint, action) in enumerate(self.items[top : top + visible], start=top):
            if action is None:
                table.add_row(Text(label, style="bold cyan" if label.isupper() else "dim italic"), Text(""))
                continue
            style = "reverse" if i == self.selected else ""
            table.add_row(Text("  " + label, style=style or ("bold" if i > 0 and self.items[i - 1][2] is None or True else "")),
                          Text(hint, style=style or "dim"))  # fmt: skip
        menu = panel(table, "Start")
        banner = Text.from_markup("[bold cyan]HackBot[/] [dim]agentic cybersecurity research team  -  pydantic-ai + MCP + ACP[/]")
        layout = Layout()
        layout.split_column(Layout(Align.center(banner), size=1), Layout(name="body"))
        split = app.split("home")
        layout["body"].split_row(Layout(menu, ratio=split), Layout(status, ratio=100 - split))
        return layout

    def handle(self, key: Key) -> bool:
        if key.name == "up":
            self._move(-1)
        elif key.name == "down":
            self._move(1)
        elif key.name == "enter" and self.items and self.items[self.selected][2]:
            self.items[self.selected][2]()
        else:
            return False
        return True


# ------------------------------------------------------------------ chat
class ChatPage(Page):
    name, title = "chat", "Chat"
    hint = f"Enter send  Ctrl+T activity  Ctrl+E inspect  Ctrl+R result  Ctrl+S sources  {RESIZE_HINT}"

    def __init__(self, app: "App"):
        super().__init__(app)
        self.scroll = ScrollState(stick_bottom=True)
        self.input = TextInput(placeholder="Ask the agent team ...  (/help for commands, Ctrl+P palette)")
        self.activity_focus = False
        self.activity_sel = 0
        self.activity_scroll = ScrollState()

    def render(self, width: int, height: int) -> RenderableType:
        app = self.app
        transcript = render_transcript(app.session.entries, pending=app.pending, busy=app.runtime.busy)
        log_panel = panel(ScrollView(transcript, self.scroll), app.session.title)
        prompt = Text.assemble(("> ", "bold green"), self.input.render(width - 6, focused=not self.activity_focus))
        input_panel = Panel(prompt, border_style="yellow" if app.runtime.busy else ("green" if not self.activity_focus else BORDER), box=box.SQUARE)
        left = Layout()
        left.split_column(Layout(log_panel, name="log"), Layout(input_panel, name="input", size=3))
        if not app.cfg.show_activity:
            return left
        rows = app.trace_rows()
        self.activity_sel = min(self.activity_sel, max(0, len(rows) - 1))
        tree = render_trace(rows, self.activity_sel if self.activity_focus else None)
        title = "Activity" + ("  [yellow]running[/]" if app.runtime.busy else "") + ("  [dim]Ctrl+E to leave[/]" if self.activity_focus else "")
        if self.activity_focus:
            self.activity_scroll.offset = max(0, self.activity_sel - max(1, self.activity_scroll.page) // 2)
            self.activity_scroll.stick_bottom = False
        else:
            self.activity_scroll.stick_bottom = app.runtime.busy
        right = panel(ScrollView(tree, self.activity_scroll), title, style="cyan" if self.activity_focus else BORDER)
        layout = Layout()
        split = app.split("chat")
        layout.split_row(Layout(left, ratio=split), Layout(right, ratio=100 - split, minimum_size=24))
        return layout

    def handle(self, key: Key) -> bool:
        app = self.app
        if key.name == "ctrl+t":
            app.cfg.show_activity = not app.cfg.show_activity
            self.activity_focus = self.activity_focus and app.cfg.show_activity
            return True
        if key.name == "ctrl+e":
            self.activity_focus = not self.activity_focus and app.cfg.show_activity
            return True
        if key.name == "ctrl+r":
            app.open_result_window()
            return True
        if key.name == "ctrl+s":
            app.open_sources_window()
            return True
        if self.activity_focus:
            rows = app.trace_rows()
            if key.name == "up":
                self.activity_sel = max(0, self.activity_sel - 1)
            elif key.name == "down":
                self.activity_sel = min(max(0, len(rows) - 1), self.activity_sel + 1)
            elif key.name == "enter" and rows:
                app.open_step_window(rows[self.activity_sel])
            elif key.name == "escape":
                self.activity_focus = False
            else:
                return False
            return True
        if key.name in ("up", "down", "pageup", "pagedown"):
            return self.scroll.handle(key)
        if key.name == "enter":
            text = self.input.text.strip()
            if text:
                self.input.clear()
                app.submit(text)
            return True
        return self.input.handle(key)


# ------------------------------------------------------------------ history
class HistoryPage(Page):
    name, title = "history", "History"
    hint = f"Up/Dn select  Enter open  d delete  n new  PgUp/PgDn scroll preview  {RESIZE_HINT}"

    def __init__(self, app: "App"):
        super().__init__(app)
        self.sessions = []
        self.selected = 0
        self.preview = ScrollState()

    def on_show(self) -> None:
        self.app.store.save(self.app.session)
        self.sessions = self.app.store.list()
        self.selected = min(self.selected, max(0, len(self.sessions) - 1))
        self.preview.home()

    def render(self, width: int, height: int) -> RenderableType:
        table = Table(box=box.SIMPLE, show_lines=True, expand=True, show_header=False, padding=(0, 1), border_style=BORDER)
        table.add_column(width=17, no_wrap=True)
        table.add_column(ratio=1, no_wrap=True, overflow="ellipsis")
        visible = max(1, (height - 4) // 3)
        top = max(0, min(self.selected - visible // 2, len(self.sessions) - visible))
        for i, s in enumerate(self.sessions[top : top + visible], start=top):
            current = s.id == self.app.session.id
            sel = i == self.selected
            left = Text(when(s.updated), style="reverse" if sel else "dim")
            left.append(f"\n{len(s.entries)} entries", style="reverse" if sel else "dim")
            right = Text(("* " if current else "") + s.title, style="reverse bold" if sel else "bold")
            right.append("\n" + (session_preview(s) or "(no answer yet)"), style="reverse" if sel else "dim italic")
            table.add_row(left, right)
        if not self.sessions:
            table.add_row("", Text("no saved chats yet", style="dim"))
        left_panel = panel(table, f"Chats ({len(self.sessions)})")
        if self.sessions:
            s = self.sessions[self.selected]
            right_panel = panel(ScrollView(render_transcript(s.entries), self.preview), f"{s.title}  [dim]{s.created}[/]")
        else:
            right_panel = panel(Align.center(Text("nothing to preview", style="dim"), vertical="middle"), "Preview")
        layout = Layout()
        split = self.app.split("history")
        layout.split_row(Layout(left_panel, ratio=split), Layout(right_panel, ratio=100 - split))
        return layout

    def handle(self, key: Key) -> bool:
        if key.name == "up":
            self.selected = max(0, self.selected - 1)
            self.preview.home()
        elif key.name == "down":
            self.selected = min(max(0, len(self.sessions) - 1), self.selected + 1)
            self.preview.home()
        elif key.name in ("pageup", "pagedown", "home", "end"):
            return self.preview.handle(key)
        elif key.name == "enter" and self.sessions:
            self.app.load_session(self.sessions[self.selected].id)
        elif key.name == "char" and key.char.lower() == "d" and self.sessions:
            s = self.sessions[self.selected]
            self.app.popup = Popup("Delete chat?", f"{s.title}\n({len(s.entries)} entries, updated {s.updated})",
                                   kind="confirm", style="red", on_submit=lambda _: self._delete(s.id))  # fmt: skip
        elif key.name == "char" and key.char.lower() == "n":
            self.app.new_session()
        else:
            return False
        return True

    def _delete(self, sid: str) -> None:
        self.app.store.delete(sid)
        if sid == self.app.session.id:
            self.app.new_session(switch=False, save=False)
        self.on_show()


# ------------------------------------------------------------------ SARIF + triage
class SarifPage(Page):
    name, title = "file", "File"
    hint = "Enter details  a analyse  T review  t verdict  1-4 prio  n note  f/v filter  e export  o open"
    LEVEL_FILTERS = (None, "error", "warning", "note")
    VERDICT_FILTERS = (None,) + S.VERDICTS

    def __init__(self, app: "App"):
        super().__init__(app)
        self.selected = 0
        self.level_idx = 0
        self.verdict_idx = 0

    @property
    def findings(self) -> list[S.Finding]:
        rep = self.app.sarif
        if rep is None:
            return []
        lvl = self.LEVEL_FILTERS[self.level_idx]
        ver = self.VERDICT_FILTERS[self.verdict_idx]
        return [
            f for f in rep.findings
            if (lvl is None or f.level == lvl)
            and (ver is None or self.app.triage.get(f.index, {}).get("verdict", "untriaged") == ver)
        ]  # fmt: skip

    def render(self, width: int, height: int) -> RenderableType:
        app = self.app
        rep = app.sarif
        assert rep is not None
        counts = rep.by_level()
        tc = S.triage_counts(app.triage)
        summary = Text.assemble(
            ("SARIF ", "dim"), (Path(rep.path).name, "bold"), ("   Tool ", "dim"), (", ".join(rep.tools), "bold"),
            ("   Findings ", "dim"), (str(len(rep.findings)), "bold"), "   ",
            *[part for lvl in S.LEVELS if counts.get(lvl) for part in ((f"{lvl} ", LEVEL_STYLE[lvl]), (f"{counts[lvl]}  ", "bold"))],
            ("  Triage ", "dim"),
            *[part for v in S.VERDICTS[1:] if tc.get(v) for part in ((f"{v} ", VERDICT_STYLE[v]), (f"{tc[v]}  ", "bold"))],
        )  # fmt: skip
        rows = self.findings
        self.selected = min(self.selected, max(0, len(rows) - 1))
        table = Table(box=box.SIMPLE_HEAD, show_edge=False, expand=True, header_style="bold", padding=(0, 1))
        table.add_column("#", width=3, justify="right")
        table.add_column("Level", width=7)
        table.add_column("Rule", width=min(26, width // 5), no_wrap=True, overflow="ellipsis")
        table.add_column("CWE", width=7)
        table.add_column("Location", width=min(28, width // 4), no_wrap=True, overflow="ellipsis")
        table.add_column("Verdict", width=14)
        table.add_column("P", width=1)
        table.add_column("Message", ratio=1, no_wrap=True, overflow="ellipsis")
        visible = max(1, height - 7)
        top = max(0, min(self.selected - visible // 2, len(rows) - visible))
        for i, f in enumerate(rows[top : top + visible], start=top):
            t = app.triage.get(f.index, {})
            verdict = t.get("verdict", "untriaged")
            vtext = Text(verdict, style=VERDICT_STYLE[verdict])
            if t.get("by") == "agent" and not t.get("accepted"):
                vtext.append("?", style="dim")
            table.add_row(str(f.index), Text(f.level, style=LEVEL_STYLE[f.level]), f.rule_id, f.cwe or "-", f.location,
                          vtext, str(t.get("priority") or "-"), f.message, style="reverse" if i == self.selected else "")  # fmt: skip
        if not rows:
            table.add_row("", "", "[dim]no findings match[/]", "", "", "", "", "")
        title = (f"Findings  [dim]level: {self.LEVEL_FILTERS[self.level_idx] or 'all'}  "
                 f"verdict: {self.VERDICT_FILTERS[self.verdict_idx] or 'all'}  ({len(rows)})[/]")  # fmt: skip
        layout = Layout()
        layout.split_column(Layout(panel(summary), size=3), Layout(panel(table, title)))
        return layout

    def handle(self, key: Key) -> bool:
        app = self.app
        rows = self.findings
        cur = rows[self.selected] if rows else None
        ch = key.char if key.name == "char" else ""
        if key.name == "up":
            self.selected = max(0, self.selected - 1)
        elif key.name == "down":
            self.selected = min(max(0, len(rows) - 1), self.selected + 1)
        elif key.name == "pageup":
            self.selected = max(0, self.selected - 10)
        elif key.name == "pagedown":
            self.selected = min(max(0, len(rows) - 1), self.selected + 10)
        elif key.name == "home":
            self.selected = 0
        elif key.name == "end":
            self.selected = max(0, len(rows) - 1)
        elif key.name == "enter" and cur:
            app.popup = finding_popup(cur, app.triage.get(cur.index, {}))
        elif ch == "f":
            self.level_idx = (self.level_idx + 1) % len(self.LEVEL_FILTERS)
            self.selected = 0
        elif ch == "v":
            self.verdict_idx = (self.verdict_idx + 1) % len(self.VERDICT_FILTERS)
            self.selected = 0
        elif ch == "o":
            app.open_file_dialog()
        elif ch == "a":
            app.analyse_report()
        elif ch == "T":
            app.open_triage_review()
        elif ch == "t" and cur:
            t = app.triage.setdefault(cur.index, {})
            cur_v = t.get("verdict", "untriaged")
            t.update(verdict=S.VERDICTS[(S.VERDICTS.index(cur_v) + 1) % len(S.VERDICTS)], by="user", accepted=True)
            app.save_triage()
        elif ch in "1234" and cur:
            app.triage.setdefault(cur.index, {}).update(priority=int(ch), by="user", accepted=True)
            app.save_triage()
        elif ch == "n" and cur:
            idx = cur.index

            def set_note(text: str) -> None:
                app.triage.setdefault(idx, {}).update(note=text, by="user", accepted=True)
                app.save_triage()

            app.popup = Popup(f"Note for finding #{idx}", cur.message[:200], kind="input",
                              initial=app.triage.get(idx, {}).get("note", ""), on_submit=set_note, width=80)  # fmt: skip
        elif ch == "e":
            app.export_triage()
        else:
            return False
        return True


def finding_popup(f: S.Finding, t: dict) -> Popup:
    verdict = t.get("verdict", "untriaged")
    body = Group(
        Text.assemble(("Rule      ", "dim"), (f.rule_id, "bold"), f"  {f.rule_name}"),
        Text.assemble(("Level     ", "dim"), (f.level, LEVEL_STYLE[f.level]), ("   Severity ", "dim"),
                      (str(f.security_severity) if f.security_severity is not None else "-", "bold"),
                      ("   CWE ", "dim"), (f.cwe or "-", "bold")),  # fmt: skip
        Text.assemble(("Location  ", "dim"), (f.location, "bold"), ("   Tool ", "dim"), f.tool),
        Text.assemble(("Triage    ", "dim"), (verdict, VERDICT_STYLE[verdict]), ("   priority ", "dim"),
                      (str(t.get("priority") or "-"), "bold"), ("   by ", "dim"), (t.get("by") or "-", "bold")),  # fmt: skip
        Text(""),
        Text("Message", style="bold underline"), Text(f.message), Text(""),
        Text("Rule description", style="bold underline"), Text(f.description or "-"), Text(""),
        Text("Analyst rationale", style="bold underline"), Text(t.get("rationale") or "-"), Text(""),
        Text("Suggested fix", style="bold underline"), Text(t.get("fix") or "-"), Text(""),
        Text("Note", style="bold underline"), Text(t.get("note") or "-"),
    )
    return Popup(f"Finding #{f.index}", body, kind="view", width=96, height=26, style="yellow")


class TriageReviewPopup(Popup):
    """Interactive window that appears once the analyst agent has finished: accept/reject each verdict."""

    def __init__(self, app: "App"):
        super().__init__("Triage review - analyst proposals", kind="view", width=110, height=26, style="magenta")
        self.app = app
        self.selected = 0

    def items(self) -> list[tuple[S.Finding, dict]]:
        rep = self.app.sarif
        if not rep:
            return []
        return [(f, self.app.triage[f.index]) for f in rep.findings if self.app.triage.get(f.index, {}).get("by") == "agent"]

    def hint(self) -> Text:
        return Text("Up/Dn move   a accept   x reject   A accept all   X reject all   Enter details   e export   Esc close", style="dim", justify="right")

    def body_renderable(self, width: int, inner_h: int) -> RenderableType:
        items = self.items()
        self.selected = min(self.selected, max(0, len(items) - 1))
        summary = (self.app.last_triage or {}).get("summary", "")
        table = Table(box=box.SIMPLE_HEAD, show_edge=False, expand=True, header_style="bold", padding=(0, 1))
        for col, w in (("#", 3), ("Rule", 24), ("Verdict", 14), ("P", 1), ("State", 8)):
            table.add_column(col, width=w, no_wrap=True, overflow="ellipsis")
        table.add_column("Rationale", ratio=1, no_wrap=True, overflow="ellipsis")
        rows = max(1, inner_h - 5)
        top = max(0, min(self.selected - rows // 2, len(items) - rows))
        for i, (f, t) in enumerate(items[top : top + rows], start=top):
            state = Text("accepted", style="green") if t.get("accepted") else Text("proposed", style="yellow")
            table.add_row(str(f.index), f.rule_id, Text(t["verdict"], style=VERDICT_STYLE[t["verdict"]]),
                          str(t.get("priority") or "-"), state, t.get("rationale", ""), style="reverse" if i == self.selected else "")  # fmt: skip
        if not items:
            table.add_row("", "[dim]no agent proposals - press a on the File page to run the analyst[/]", "", "", "", "")
        head = Text(summary[:width * 2], style="italic")
        return FixedHeight(Group(head, Text(""), table), inner_h - 1)

    def handle(self, key: Key) -> bool:
        items = self.items()
        ch = key.char if key.name == "char" else ""
        if key.name == "escape":
            return True
        if key.name == "up":
            self.selected = max(0, self.selected - 1)
        elif key.name == "down":
            self.selected = min(max(0, len(items) - 1), self.selected + 1)
        elif ch == "a" and items:
            items[self.selected][1]["accepted"] = True
        elif ch == "x" and items:
            f, _ = items[self.selected]
            self.app.triage[f.index] = {"verdict": "untriaged", "by": "user", "accepted": True}
        elif ch == "A":
            for _, t in items:
                t["accepted"] = True
        elif ch == "X":
            for f, _ in items:
                self.app.triage[f.index] = {"verdict": "untriaged", "by": "user", "accepted": True}
        elif ch == "e":
            self.app.export_triage()
            return True
        elif key.name == "enter" and items:
            f, t = items[self.selected]
            detail = finding_popup(f, t)
            detail.on_close = lambda: setattr(self.app, "popup", self)
            self.app.popup = detail
            return False
        else:
            return False
        self.app.save_triage()
        return False


# ------------------------------------------------------------------ any document
_LINE_RE = re.compile(r"line[s]?\s*(\d+)", re.I)


def finding_line(f: dict) -> int | None:
    m = _LINE_RE.search(f.get("location") or "")
    return int(m.group(1)) if m else None


def doc_finding_popup(f: dict) -> Popup:
    body = Group(
        Text.assemble(("Severity  ", "dim"), (f.get("severity", "info"), SEVERITY_STYLE.get(f.get("severity", "info"), "")),
                      ("   Location ", "dim"), (f.get("location") or "-", "bold"), ("   CWE ", "dim"), (f.get("cwe") or "-", "bold")),  # fmt: skip
        Text(""),
        Text("Evidence", style="bold underline"), Text(f.get("evidence") or "-", style="yellow"), Text(""),
        Text("Why it matters", style="bold underline"), Text(f.get("explanation") or "-"), Text(""),
        Text("Recommendation", style="bold underline"), Text(f.get("recommendation") or "-"),
    )
    return Popup(f.get("title", "Finding"), body, kind="view", width=96, height=22, style="yellow")


class DocFindingsPopup(Popup):
    """Window shown when the document analyst finishes: browse findings, jump to lines."""

    def __init__(self, app: "App"):
        super().__init__("File review - document analyst findings", kind="view", width=110, height=26, style="magenta")
        self.app = app
        self.selected = 0

    def hint(self) -> Text:
        return Text("Up/Dn move   Enter details   g go to line   Esc close", style="dim", justify="right")

    def body_renderable(self, width: int, inner_h: int) -> RenderableType:
        rep = self.app.doc_report or {}
        items = rep.get("findings", [])
        self.selected = min(self.selected, max(0, len(items) - 1))
        table = Table(box=box.SIMPLE_HEAD, show_edge=False, expand=True, header_style="bold", padding=(0, 1))
        table.add_column("Sev", width=8)
        table.add_column("Location", width=14, no_wrap=True, overflow="ellipsis")
        table.add_column("Finding", ratio=2, no_wrap=True, overflow="ellipsis")
        table.add_column("Recommendation", ratio=3, no_wrap=True, overflow="ellipsis")
        rows = max(1, inner_h - 6)
        top = max(0, min(self.selected - rows // 2, len(items) - rows))
        for i, f in enumerate(items[top : top + rows], start=top):
            sev = f.get("severity", "info")
            table.add_row(Text(sev, style=SEVERITY_STYLE.get(sev, "")), f.get("location") or "-", f.get("title", ""),
                          f.get("recommendation", ""), style="reverse" if i == self.selected else "")  # fmt: skip
        if not items:
            table.add_row("", "", "[dim]no findings - press a on the File page to run the document analyst[/]", "")
        head = Text.assemble((rep.get("file_type", ""), "bold"), ("  ", ""), (rep.get("summary", "")[: width * 2], "italic"))
        qs = rep.get("questions_for_user") or []
        tail = Text("Open questions: " + " | ".join(qs)[: width * 2], style="dim") if qs else Text("")
        return FixedHeight(Group(head, Text(""), table, tail), inner_h - 1)

    def handle(self, key: Key) -> bool:
        items = (self.app.doc_report or {}).get("findings", [])
        ch = key.char if key.name == "char" else ""
        if key.name == "escape":
            return True
        if key.name == "up":
            self.selected = max(0, self.selected - 1)
        elif key.name == "down":
            self.selected = min(max(0, len(items) - 1), self.selected + 1)
        elif key.name == "enter" and items:
            detail = doc_finding_popup(items[self.selected])
            detail.on_close = lambda: setattr(self.app, "popup", self)
            self.app.popup = detail
        elif ch == "g" and items:
            line = finding_line(items[self.selected])
            page = self.app.pages["file"].doc_page  # type: ignore[attr-defined]
            page.focus = "findings"
            page.sel = self.selected
            if line:
                page.goto(line)
            self.app.show("file")
            return True
        return False


class DocumentPage(Page):
    name, title = "file", "File"
    hint = f"Up/Dn PgUp/PgDn scroll  / search  n/N next  b files  a analyse  f findings  Enter details  o open  {RESIZE_HINT}"

    def __init__(self, app: "App"):
        super().__init__(app)
        self.top = 0
        self.visible = 20
        self.pattern = ""
        self.matches: list[int] = []
        self.match_i = -1
        self.focus = "text"  # text | findings
        self.sel = 0
        self.highlight: int | None = None

    def on_show(self) -> None:
        self.top = min(self.top, max(0, (self.app.document.lines if self.app.document else 1) - 1))

    def goto(self, line: int) -> None:
        self.highlight = line
        self.top = max(0, line - 1 - self.visible // 2)

    def do_search(self, pattern: str) -> None:
        doc = self.app.document
        self.pattern = pattern
        self.matches = [h["line"] for h in doc.search(pattern, 500)] if (doc and pattern) else []
        self.match_i = -1
        if self.matches:
            self.next_match(1)
        else:
            self.app.status = f"no matches for '{pattern}'" if pattern else ""

    def next_match(self, step: int) -> None:
        if not self.matches:
            return
        self.match_i = (self.match_i + step) % len(self.matches)
        self.goto(self.matches[self.match_i])
        self.app.status = f"match {self.match_i + 1}/{len(self.matches)} for '{self.pattern}'"

    def render(self, width: int, height: int) -> RenderableType:
        app = self.app
        doc = app.document
        if doc is None:
            msg = Text.from_markup("[bold]No file loaded[/]\n\nPress [bold]o[/] (or Ctrl+O) to open ANY text file or a whole folder:\n"
                                   "SARIF report, source code, config, log, JSON, CSV, markdown, scanner output ...\n"
                                   "[dim]Try: samples/sample.sarif  or  src/hackbot/config.py[/]")  # fmt: skip
            return panel(Align.center(msg, vertical="middle"))
        split = app.split("file")
        lines = doc.line_list()
        self.visible = max(1, height - 2)
        self.top = max(0, min(self.top, len(lines) - 1))
        match_set = set(self.matches)
        num_w = len(str(len(lines)))
        rows: list[Text] = []
        for n in range(self.top + 1, min(len(lines), self.top + self.visible) + 1):
            t = Text(f"{n:>{num_w}} ", style="dim")
            t.append("| ", style=BORDER)
            body = lines[n - 1].replace("\t", "    ")
            t.append(body, style="bold yellow" if n in match_set else "")
            if n == self.highlight:
                t.stylize("reverse")
            t.no_wrap = True
            t.overflow = "ellipsis"
            rows.append(t)
        title = f"{doc.description}   [dim]lines {self.top + 1}-{min(len(lines), self.top + self.visible)}[/]"
        if doc.is_folder:
            cur_file = doc.file_at(self.top + 1)
            if cur_file:
                title = (f"[bold]{cur_file}[/]   [dim]{doc.name}/ {len(doc.files)} files   "
                         f"lines {self.top + 1}-{min(len(lines), self.top + self.visible)}   b = file list[/]")
        if self.pattern:
            title += f"   [yellow]/{self.pattern} ({len(self.matches)})[/]"
        viewer = panel(Group(*rows), title, style="cyan" if self.focus == "text" else BORDER)

        rep = app.doc_report or {}
        items = rep.get("findings", [])
        self.sel = min(self.sel, max(0, len(items) - 1))
        ftable = Table.grid(expand=True, padding=(0, 1))
        ftable.add_column(width=8, no_wrap=True)
        ftable.add_column(ratio=1, no_wrap=True, overflow="ellipsis")
        parts: list[RenderableType] = []
        if rep:
            parts.append(Text(rep.get("file_type", ""), style="bold"))
            parts.append(Text(rep.get("summary", ""), style="italic dim"))
            parts.append(Text(""))
        for i, f in enumerate(items):
            sev = f.get("severity", "info")
            style = "reverse" if (i == self.sel and self.focus == "findings") else ""
            ftable.add_row(Text(sev, style=style or SEVERITY_STYLE.get(sev, "")),
                           Text(f"{f.get('location') or '-'}  {f.get('title', '')}", style=style))  # fmt: skip
        if not items:
            ftable.add_row("", Text("no findings yet - press a to run the document analyst", style="dim italic"))
        parts.append(ftable)
        findings = panel(Group(*parts), f"Findings ({len(items)})", style="cyan" if self.focus == "findings" else BORDER)
        layout = Layout()
        layout.split_row(Layout(viewer, ratio=split), Layout(findings, ratio=100 - split, minimum_size=24))
        return layout

    def handle(self, key: Key) -> bool:
        app = self.app
        doc = app.document
        ch = key.char if key.name == "char" else ""
        if ch == "o":
            app.open_file_dialog()
            return True
        if doc is None:
            return False
        items = (app.doc_report or {}).get("findings", [])
        if ch == "b" and doc.is_folder:
            self.file_browser()
        elif ch == "f":
            self.focus = "findings" if self.focus == "text" else "text"
        elif ch == "/":
            app.popup = Popup("Search in file", "regex or text (case-insensitive)", kind="input", initial=self.pattern,
                              on_submit=self.do_search, width=70)  # fmt: skip
        elif ch == "n":
            self.next_match(1)
        elif ch == "N":
            self.next_match(-1)
        elif ch == "a":
            app.analyse_report()
        elif ch == "F" or (key.name == "enter" and self.focus == "text"):
            app.open_doc_findings()
        elif self.focus == "findings" and key.name == "up":
            self.sel = max(0, self.sel - 1)
            self._jump(items)
        elif self.focus == "findings" and key.name == "down":
            self.sel = min(max(0, len(items) - 1), self.sel + 1)
            self._jump(items)
        elif self.focus == "findings" and key.name == "enter" and items:
            app.popup = doc_finding_popup(items[self.sel])
        elif key.name == "up":
            self.top = max(0, self.top - 1)
        elif key.name == "down":
            self.top = min(max(0, doc.lines - 1), self.top + 1)
        elif key.name == "pageup":
            self.top = max(0, self.top - self.visible + 1)
        elif key.name == "pagedown":
            self.top = min(max(0, doc.lines - 1), self.top + self.visible - 1)
        elif key.name == "home":
            self.top = 0
        elif key.name == "end":
            self.top = max(0, doc.lines - self.visible)
        elif key.name == "escape" and self.focus == "findings":
            self.focus = "text"
        else:
            return False
        return True

    def file_browser(self) -> None:
        doc = self.app.document
        if not doc or not doc.files:
            return
        opts = []
        for f in doc.files:
            state = f["kind"] or "-"
            if not f["included"]:
                state += "  (binary)" if f["kind"] == "binary" else "  (not loaded)"
            opts.append((f"{f['path']:<60.60} {f['size']:>10,}  {state}", f["line"] if f["included"] else None))

        def jump(line: int | None) -> None:
            if line:
                self.highlight = line
                self.top = max(0, line - 2)  # show the '===== path =====' header at the top
                self.focus = "text"

        w, h = self.app.console.size
        self.app.popup = Popup(f"Files in {doc.name}/  (Enter jumps to the file; Ctrl+O opens one alone)", kind="list",
                               options=opts, on_submit=jump, width=min(110, w - 4), height=min(30, h - 4))  # fmt: skip

    def _jump(self, items: list[dict]) -> None:
        if items:
            line = finding_line(items[self.sel])
            if line:
                self.goto(line)


class FilePage(Page):
    """F4: shows the SARIF triage table when the loaded file is SARIF, otherwise the document viewer."""

    name, title = "file", "File"

    def __init__(self, app: "App"):
        super().__init__(app)
        self.sarif_page = SarifPage(app)
        self.doc_page = DocumentPage(app)

    @property
    def active(self) -> Page:
        return self.sarif_page if self.app.sarif is not None else self.doc_page

    @property
    def hint(self) -> str:  # type: ignore[override]
        return self.active.hint

    def on_show(self) -> None:
        self.active.on_show()

    def render(self, width: int, height: int) -> RenderableType:
        return self.active.render(width, height)

    def handle(self, key: Key) -> bool:
        return self.active.handle(key)


# ------------------------------------------------------------------ agents
class AgentsPage(Page):
    name, title = "agents", "Agents"
    hint = "Up/Dn select  Enter run agent / call remote  r refresh ACP agents  u set ACP url"

    def __init__(self, app: "App"):
        super().__init__(app)
        self.selected = 0
        self.rows: list[tuple[str, str, Any]] = []

    def on_show(self) -> None:
        self.rebuild()

    def rebuild(self) -> None:
        app = self.app
        rows: list[tuple[str, str, Any]] = [("LOCAL TEAM (pydantic-ai)", "", None)]
        for a in describe_team():
            name = a["name"].split(" ")[0]
            rows.append((f"  {a['name']}", f"{a['role']}  [tools: {a['tools']}]", (lambda n=name: app.run_agent_dialog(n))))
        rows.append(("MCP SERVERS (Model Context Protocol)", "", None))
        rows.append(("  hackbot-mcp  (stdio, uv run hackbot-mcp)", ", ".join(app.mcp_tools) or "starting ...", None))
        ext = app.runtime.system.external_tools if app.runtime.system else {}
        for k, v in ext.items():
            rows.append((f"  {k}", ", ".join(v), None))
        if not ext:
            rows.append(("  (no external servers)", "add an mcpServers JSON file in Settings > MCP config", None))
        rows.append(("ACP REMOTE AGENTS (Agent Communication Protocol)", "", None))
        rows.append((f"  server: {app.cfg.acp_url or 'not configured'}", "start one with: uv run hackbot-acp --port 8000  (or hackbot --acp)", None))
        if app.remote_agents_error:
            rows.append(("  (status)", app.remote_agents_error, None))
        for a in app.remote_agents:
            rows.append((f"  {a['name']}", a["description"], (lambda n=a["name"]: app.call_remote_dialog(n))))
        self.rows = rows
        self.selected = min(self.selected, len(rows) - 1)

    def render(self, width: int, height: int) -> RenderableType:
        self.rebuild()
        table = Table.grid(expand=True, padding=(0, 1))
        table.add_column(no_wrap=True, overflow="ellipsis", width=min(44, width // 3))
        table.add_column(no_wrap=True, overflow="ellipsis", ratio=1)
        visible = max(1, height - 2)
        top = max(0, min(self.selected - visible // 2, len(self.rows) - visible))
        for i, (label, detail, action) in enumerate(self.rows[top : top + visible], start=top):
            if action is None and not label.startswith("  "):
                table.add_row(Text(label, style="bold cyan"), "")
                continue
            style = "reverse" if i == self.selected else ("" if action else "dim")
            table.add_row(Text(label, style=style), Text(detail, style=style or "dim"))
        arch = Text.from_markup(
            "[dim]orchestrator -> plan -> spawn_researchers (parallel) / analyse_sarif / analyse_document / ask_tutor / "
            "ask_remote_agent (ACP)   |   specialists call tools through MCP (separate process)[/]")  # fmt: skip
        return panel(Group(arch, Text(""), table), "Agents & protocols")

    def handle(self, key: Key) -> bool:
        app = self.app
        ch = key.char if key.name == "char" else ""
        if key.name == "up":
            self.selected = max(0, self.selected - 1)
        elif key.name == "down":
            self.selected = min(len(self.rows) - 1, self.selected + 1)
        elif key.name == "enter" and self.rows:
            action = self.rows[self.selected][2]
            if action:
                action()
        elif ch == "r":
            app.refresh_remote_agents()
        elif ch == "u":
            app.popup = Popup("ACP server URL", "e.g. http://127.0.0.1:8000", kind="input", initial=app.cfg.acp_url or "",
                              on_submit=lambda u: app.set_acp_url(u))  # fmt: skip
        else:
            return False
        return True


# ------------------------------------------------------------------ settings
class SettingsPage(Page):
    name, title = "settings", "Settings"
    hint = "Up/Dn select  Enter edit  Space toggle  s save to .env (+ restart agents)  r reload"

    FIELDS = [
        ("model", "Model", "list"),
        ("api_key", "OpenAI API key", "secret"),
        ("base_url", "OpenAI base URL (optional)", "text"),
        ("max_researchers", "Parallel researcher agents (1-4)", "int"),
        ("acp_url", "ACP server URL (remote agents)", "text"),
        ("mcp_config", "Extra MCP servers config (mcpServers JSON path)", "text"),
        ("data_dir", "Data directory", "text"),
        ("stream", "Stream answer text", "bool"),
        ("show_activity", "Show activity panel", "bool"),
    ]

    def __init__(self, app: "App"):
        super().__init__(app)
        self.selected = 0
        self.dirty = False

    def value_text(self, name: str, kind: str) -> Text:
        v = getattr(self.app.cfg, name)
        if kind == "secret":
            return Text(("*" * 8 + str(v)[-4:]) if v else "not set", style="green" if v else "red")
        if kind == "bool":
            return Text("on" if v else "off", style="green" if v else "dim")
        return Text(str(v) if v not in (None, "") else "-", style="" if v else "dim")

    def render(self, width: int, height: int) -> RenderableType:
        table = Table.grid(expand=True, padding=(0, 2))
        table.add_column(width=min(46, width // 2), no_wrap=True)
        table.add_column(ratio=1, no_wrap=True, overflow="ellipsis")
        for i, (name, label, kind) in enumerate(self.FIELDS):
            style = "reverse" if i == self.selected else ""
            val = self.value_text(name, kind)
            if style:
                val.stylize(style)
            table.add_row(Text(label, style=style), val)
        note = Text(f"\n.env: {self.app.cfg.env_path}" + ("   [unsaved changes]" if self.dirty else ""), style="yellow" if self.dirty else "dim")
        layout_note = Text(f"pane splits: {self.app.splits}   (Ctrl+Left/Right on any page; Ctrl+Up/Down/Left/Right resize popups)", style="dim")
        return panel(Group(table, note, layout_note), "Settings")

    def handle(self, key: Key) -> bool:
        app = self.app
        name, label, kind = self.FIELDS[self.selected]
        ch = key.char if key.name == "char" else ""
        if key.name == "up":
            self.selected = max(0, self.selected - 1)
        elif key.name == "down":
            self.selected = min(len(self.FIELDS) - 1, self.selected + 1)
        elif key.name == "enter" or (ch == " " and kind == "bool"):
            self.edit(name, label, kind)
        elif ch == "s":
            app.save_settings()
            self.dirty = False
        elif ch == "r":
            app.reload_settings()
            self.dirty = False
        else:
            return False
        return True

    def edit(self, name: str, label: str, kind: str) -> None:
        app = self.app
        cfg = app.cfg

        def set_value(v: Any) -> None:
            if kind == "int":
                try:
                    v = max(1, min(int(v), 4))
                except ValueError:
                    return
            if kind == "text" and name == "data_dir":
                v = Path(v).expanduser()
            if kind in ("text", "secret") and v == "":
                v = None
            setattr(cfg, name, v)
            self.dirty = True

        if kind == "bool":
            setattr(cfg, name, not getattr(cfg, name))
            self.dirty = True
        elif kind == "list" and name == "model":
            opts = [(m, m) for m in MODEL_CHOICES] + [("custom ...", "__custom__")]

            def choose(v: str) -> None:
                if v == "__custom__":
                    app.popup = Popup("Model name", "Any OpenAI chat model with tool calling", kind="input", initial=cfg.model, on_submit=set_value)
                else:
                    set_value(v)

            app.popup = Popup("Choose model", "Cheapest first", kind="list", options=opts, on_submit=choose)
        elif kind == "int":
            app.popup = Popup(label, "", kind="list", options=[(str(i), i) for i in range(1, 5)], on_submit=set_value)
        else:
            current = "" if kind == "secret" else str(getattr(cfg, name) or "")
            app.popup = Popup(label, "Enter a new value (empty clears it)", kind="input", initial=current, mask=(kind == "secret"),
                              on_submit=set_value, width=80)  # fmt: skip


# ------------------------------------------------------------------ help
HELP_MD = """
# HackBot - agentic cybersecurity research team

**Not a chat wrapper.** An *orchestrator* agent plans, spins up specialist agents in parallel
(researchers, a SARIF analyst, a document analyst, a tutor), and synthesises a cited answer.
Every tool the specialists use lives in a separate **MCP server** process (`hackbot-mcp`). Agents can
also be published to / called from other processes over **ACP** (`hackbot-acp`). Framework: pydantic-ai.

## Pages
| Key | Page | What it does |
|-----|------|--------------|
| F1 | Home | dashboard: status, quick actions, recent chats |
| F2 | Chat | talk to the team; Activity panel shows the live agent/tool tree |
| F3 | History | browse, preview, reopen or delete chats |
| F4 | File | any loaded file: SARIF -> triage table; other files -> viewer + analyst findings |
| F5 | Agents | local team, MCP servers/tools, ACP remote agents (run any of them) |
| F6 | Settings | model, API key, ACP/MCP endpoints, saved to .env |
| F7 | Help | this page |

## Global keys
| Key | Action |
|-----|--------|
| Ctrl+P | command palette (type to filter; free text is sent as a question) |
| Ctrl+N / Ctrl+O / Ctrl+Q | new chat / open file / quit |
| Ctrl+Left / Ctrl+Right | resize the panes of the current page (Ctrl+arrows resize an open popup) |
| Tab | next page, Esc = Home |

## Chat keys
| Key | Action |
|-----|--------|
| Ctrl+T | show/hide Activity panel |
| Ctrl+E | focus Activity: Up/Down select a step, Enter inspects its args/result |
| Ctrl+R | open the last answer in a scrollable result window |
| Ctrl+S | sources window (Enter on a source asks the team to summarise it) |
| `/open <path>` `/agent <name> <prompt>` `/acp <url>` `/analyse` `/new` `/quit` | commands |

## File page keys
Any text file can be loaded (Ctrl+O): source code, config, logs, JSON, CSV, markdown, scanner output.
`a` analyses it with the agent team. For SARIF a **Triage review** window opens (`a`/`x` accept/reject,
`A`/`X` all, `t` verdict, `1-4` priority, `n` note, `e` export markdown). For other files a **File review**
window lists the analyst's findings (`g` jumps to the line); in the viewer `/` searches, `n`/`N` cycle
matches, `f` focuses the findings list.

## Architecture
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
"""


class HelpPage(Page):
    name, title = "help", "Help"
    hint = "Up/Dn PgUp/PgDn scroll"

    def __init__(self, app: "App"):
        super().__init__(app)
        self.scroll = ScrollState()

    def render(self, width: int, height: int) -> RenderableType:
        return panel(ScrollView(Markdown(HELP_MD), self.scroll))

    def handle(self, key: Key) -> bool:
        return self.scroll.handle(key)

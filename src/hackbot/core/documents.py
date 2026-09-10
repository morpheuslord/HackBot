"""Universal document loader: any text-like file (code, config, log, JSON, CSV, markdown, scan
output ...) becomes a `Document`; SARIF files additionally get the structured parser."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

from . import sarif as S

MAX_CHARS = 400_000
FOLDER_FILE_CHARS = 120_000
FOLDER_MAX_FILES = 300
SKIP_DIRS = {'.git', '.venv', 'venv', 'node_modules', '__pycache__', '.idea', '.vscode', 'dist', 'build'}
CODE_EXT = {
    ".py": "python", ".js": "javascript", ".ts": "typescript", ".java": "java", ".go": "go", ".rs": "rust",
    ".c": "c", ".h": "c", ".cpp": "cpp", ".cs": "csharp", ".php": "php", ".rb": "ruby", ".sh": "shell",
    ".ps1": "powershell", ".sql": "sql", ".html": "html", ".css": "css", ".tf": "terraform",
}  # fmt: skip
CONFIG_EXT = {".yaml", ".yml", ".toml", ".ini", ".cfg", ".conf", ".env", ".properties", ".xml", ".dockerfile"}
KIND_HELP = {
    "sarif": "static-analysis report (structured triage available)",
    "json": "JSON document",
    "log": "log file",
    "csv": "tabular data",
    "markdown": "notes / documentation",
    "config": "configuration file",
    "text": "plain text",
    "folder": "folder (all text files concatenated, ===== path ===== separators)",
}


@dataclass
class Document:
    path: str
    name: str
    kind: str  # sarif | json | code:<lang> | config | log | csv | markdown | text
    text: str
    chars: int
    lines: int
    truncated: bool = False
    sarif: S.SarifReport | None = None
    files: list[dict] = field(default_factory=list)  # folders: [{path, size, kind, included, line}]
    _lines: list[str] = field(default_factory=list, repr=False)

    @property
    def is_sarif(self) -> bool:
        return self.kind == "sarif"

    @property
    def is_folder(self) -> bool:
        return self.kind == "folder"

    @property
    def description(self) -> str:
        if self.is_folder:
            inc = sum(1 for f in self.files if f["included"])
            return (f"{self.name}/ - folder, {len(self.files)} files ({inc} text files loaded), {self.lines} lines"
                    + (" (truncated)" if self.truncated else ""))
        base = self.kind.split(":", 1)
        label = f"{base[1]} source code" if base[0] == "code" else KIND_HELP.get(self.kind, self.kind)
        return f"{self.name} - {label}, {self.lines} lines, {self.chars:,} chars" + (" (truncated)" if self.truncated else "")

    def line_list(self) -> list[str]:
        if not self._lines:
            self._lines = self.text.splitlines() or [""]
        return self._lines

    def preview(self, chars: int = 1500) -> str:
        return self.text[:chars]

    def slice(self, start_line: int = 1, num_lines: int = 120) -> list[tuple[int, str]]:
        lines = self.line_list()
        start = max(1, int(start_line))
        num = max(1, min(int(num_lines), 400))
        return [(i, lines[i - 1]) for i in range(start, min(len(lines), start + num - 1) + 1)]

    def search(self, pattern: str, max_hits: int = 30) -> list[dict]:
        try:
            rx = re.compile(pattern, re.I)
        except re.error:
            rx = re.compile(re.escape(pattern), re.I)
        hits = []
        for i, line in enumerate(self.line_list(), 1):
            if rx.search(line):
                hits.append({"line": i, "text": line.strip()[:240]})
                if len(hits) >= max(1, min(int(max_hits), 200)):
                    break
        return hits

    def info(self) -> dict:
        d = {"path": self.path, "name": self.name, "kind": self.kind, "chars": self.chars, "lines": self.lines,
             "truncated": self.truncated, "preview": self.preview()}  # fmt: skip
        if self.sarif:
            d["sarif_summary"] = S.summary_dict(self.sarif)
        if self.files:
            d["files"] = [{"path": f["path"], "size": f["size"], "kind": f["kind"], "included": f["included"], "start_line": f["line"]}
                          for f in self.files[:FOLDER_MAX_FILES]]
        return d

    def file_at(self, line: int) -> str:
        """For folders: which file a document line belongs to."""
        name = ""
        for f in self.files:
            if f["included"] and f["line"] <= line:
                name = f["path"]
        return name


def detect_kind(path: Path, text: str) -> str:
    ext = path.suffix.lower()
    if ext == ".sarif":
        return "sarif"
    head = text.lstrip()[:1]
    if ext == ".json" or head in ("{", "["):
        try:
            data = json.loads(text)
            if isinstance(data, dict) and "runs" in data and ("version" in data or "$schema" in data):
                return "sarif"
            return "json"
        except json.JSONDecodeError:
            pass
    if ext in CODE_EXT:
        return f"code:{CODE_EXT[ext]}"
    if ext in CONFIG_EXT or path.name.lower() in ("dockerfile", "makefile"):
        return "config"
    if ext in (".log", ".txt") and re.search(r"\b(error|warn|info|debug|failed|denied)\b", text[:20000], re.I) and ext == ".log":
        return "log"
    if ext == ".log":
        return "log"
    if ext in (".csv", ".tsv"):
        return "csv"
    if ext in (".md", ".markdown", ".rst"):
        return "markdown"
    return "text"


def load_folder(p: Path) -> Document:
    """Concatenate every text file under `p` (with caps) into one navigable document."""
    entries: list[dict] = []
    for f in sorted(x for x in p.rglob("*") if x.is_file()):
        if any(part in SKIP_DIRS for part in f.relative_to(p).parts):
            continue
        entries.append({"path": f.relative_to(p).as_posix(), "abs": f, "size": f.stat().st_size,
                        "kind": "", "included": False, "line": 0})
    if not entries:
        raise ValueError(f"{p} contains no files")
    parts: list[str] = [f"# Folder: {p.resolve()}", f"# {len(entries)} files. Sections start with '===== <path> ====='.", "#"]
    for e in entries[:FOLDER_MAX_FILES]:
        parts.append(f"#   {e['path']}  ({e['size']:,} bytes)")
    if len(entries) > FOLDER_MAX_FILES:
        parts.append(f"#   ... {len(entries) - FOLDER_MAX_FILES} more files not listed")
    total_lines = len(parts)
    used = sum(len(x) + 1 for x in parts)
    truncated = False
    for e in entries[:FOLDER_MAX_FILES]:
        try:
            raw = e["abs"].read_bytes()
        except OSError:
            continue
        if b"\x00" in raw[:8192]:
            e["kind"] = "binary"
            continue
        text = raw.decode("utf-8-sig", errors="replace")
        e["kind"] = detect_kind(e["abs"], text)
        if len(text) > FOLDER_FILE_CHARS:
            text = text[:FOLDER_FILE_CHARS] + "\n... (file truncated)"
        if used + len(text) > MAX_CHARS:
            truncated = True
            break
        header = f"\n===== {e['path']} ====="
        parts.append(header)
        total_lines += 2
        e["included"], e["line"] = True, total_lines
        parts.append(text)
        total_lines += len(text.splitlines())
        used += len(header) + len(text) + 2
    text = "\n".join(parts)
    doc = Document(path=str(p.resolve()), name=p.name, kind="folder", text=text, chars=len(text),
                   lines=len(text.splitlines()) or 1, truncated=truncated)  # fmt: skip
    doc.files = [{k: v for k, v in e.items() if k != "abs"} for e in entries]
    return doc


def clean_path(path: str | Path) -> Path:
    """Accept paths as users paste them: surrounding quotes (Windows 'Copy as path'), file:// prefix, ~."""
    s = str(path).strip()
    if s.lower().startswith("file:///"):
        s = s[8:] if s[9:10] == ":" else s[7:]
    while len(s) >= 2 and s[0] == s[-1] and s[0] in "\"'":
        s = s[1:-1].strip()
    return Path(s).expanduser()


def load_document(path: str | Path) -> Document:
    p = clean_path(path)
    if not p.exists():
        raise FileNotFoundError(f"no such file or folder: {p}")
    if p.is_dir():
        return load_folder(p)
    raw = p.read_bytes()
    if b"\x00" in raw[:8192]:
        raise ValueError(f"{p.name} looks binary (PDF/image/archive); convert it to text first")
    text = raw.decode("utf-8-sig", errors="replace")
    truncated = len(text) > MAX_CHARS
    if truncated:
        text = text[:MAX_CHARS]
    kind = detect_kind(p, text)
    doc = Document(path=str(p.resolve()), name=p.name, kind=kind, text=text, chars=len(text),
                   lines=len(text.splitlines()) or 1, truncated=truncated)  # fmt: skip
    if kind == "sarif":
        doc.sarif = S.load_sarif_file(p)
    return doc

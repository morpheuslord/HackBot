"""Chat session persistence: one JSON file per session under <data_dir>/sessions."""

from __future__ import annotations

import json
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M")


@dataclass
class Session:
    id: str
    title: str = "New session"
    created: str = field(default_factory=_now)
    updated: str = field(default_factory=_now)
    # transcript entries: {"role": user|assistant|error|note, "content": str, ...extra (sources, trace)}
    entries: list[dict] = field(default_factory=list)

    def add(self, role: str, content: str, **extra: Any) -> dict:
        if role == "user" and not any(e["role"] == "user" for e in self.entries):
            self.title = content.strip().splitlines()[0][:48] or "New session"
        entry = {"role": role, "content": content, **extra}
        self.entries.append(entry)
        self.updated = _now()
        return entry

    def llm_history(self) -> list[dict]:
        return [e for e in self.entries if e["role"] in ("user", "assistant")]


class SessionStore:
    def __init__(self, data_dir: Path):
        self.dir = data_dir / "sessions"
        self.dir.mkdir(parents=True, exist_ok=True)

    def new(self) -> Session:
        return Session(id=uuid.uuid4().hex[:10])

    def _path(self, sid: str) -> Path:
        return self.dir / f"{sid}.json"

    def save(self, session: Session) -> None:
        if not session.entries:  # never persist empty sessions
            return
        self._path(session.id).write_text(json.dumps(asdict(session), indent=1, default=str), encoding="utf-8")

    def load(self, sid: str) -> Session:
        data = json.loads(self._path(sid).read_text(encoding="utf-8"))
        return Session(**data)

    def delete(self, sid: str) -> None:
        self._path(sid).unlink(missing_ok=True)

    def list(self) -> list[Session]:
        sessions = []
        for p in self.dir.glob("*.json"):
            try:
                sessions.append(Session(**json.loads(p.read_text(encoding="utf-8"))))
            except Exception:  # noqa: BLE001 - skip corrupt files
                continue
        return sorted(sessions, key=lambda s: s.updated, reverse=True)

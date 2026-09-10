"""Runtime configuration, loaded from environment / .env and saveable back to .env."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

DEFAULT_MODEL = "gpt-4.1-nano"
MODEL_CHOICES = ["gpt-4.1-nano", "gpt-4.1-mini", "gpt-4o-mini", "gpt-5-nano", "gpt-5-mini"]


def _env(name: str) -> str | None:
    """HACKBOT_<name>, falling back to the legacy CSRA_<name> from older .env files."""
    return os.getenv(f"HACKBOT_{name}") or os.getenv(f"CSRA_{name}") or None


def _default_data_dir() -> Path:
    new, old = Path.home() / ".hackbot", Path.home() / ".csra"
    if not new.exists() and old.exists():  # migrate sessions/triage from the old name
        try:
            old.rename(new)
        except OSError:
            return old
    return new


def _bool(value: str | None, default: bool) -> bool:
    if value is None or value == "":
        return default
    return value.strip().lower() in ("1", "true", "yes", "on")


@dataclass
class Config:
    api_key: str | None = None
    model: str = DEFAULT_MODEL
    base_url: str | None = None
    data_dir: Path = field(default_factory=_default_data_dir)
    max_tool_steps: int = 6
    history_window: int = 20  # how many past turns are sent to the model
    web_search: bool = True  # allow web_search / fetch_page tools
    stream: bool = True  # stream tokens into the UI
    show_activity: bool = True  # activity (chain-of-thought) panel visible by default
    max_researchers: int = 3  # parallel researcher agents per question
    acp_url: str | None = None  # remote ACP server, e.g. http://127.0.0.1:8000
    mcp_config: str | None = None  # optional mcpServers JSON config with extra MCP servers
    env_path: Path = field(default_factory=lambda: Path.cwd() / ".env")

    @property
    def ready(self) -> bool:
        return bool(self.api_key)

    @classmethod
    def load(cls, model: str | None = None) -> "Config":
        env_path = Path.cwd() / ".env"
        load_dotenv(env_path)
        data_dir = Path(_env("DATA_DIR") or _default_data_dir()).expanduser()
        data_dir.mkdir(parents=True, exist_ok=True)
        return cls(
            api_key=os.getenv("OPENAI_API_KEY") or None,
            model=model or _env("MODEL") or DEFAULT_MODEL,
            base_url=os.getenv("OPENAI_BASE_URL") or None,
            data_dir=data_dir,
            max_tool_steps=int(_env("MAX_TOOL_STEPS") or 6),
            web_search=_bool(_env("WEB_SEARCH"), True),
            stream=_bool(_env("STREAM"), True),
            show_activity=_bool(_env("SHOW_ACTIVITY"), True),
            max_researchers=max(1, min(int(_env("MAX_RESEARCHERS") or 3), 4)),
            acp_url=_env("ACP_URL") or None,
            mcp_config=_env("MCP_CONFIG") or None,
            env_path=env_path,
        )

    def to_env(self) -> dict[str, str]:
        return {
            "OPENAI_API_KEY": self.api_key or "",
            "HACKBOT_MODEL": self.model,
            "OPENAI_BASE_URL": self.base_url or "",
            "HACKBOT_DATA_DIR": str(self.data_dir),
            "HACKBOT_MAX_TOOL_STEPS": str(self.max_tool_steps),
            "HACKBOT_WEB_SEARCH": "true" if self.web_search else "false",
            "HACKBOT_STREAM": "true" if self.stream else "false",
            "HACKBOT_SHOW_ACTIVITY": "true" if self.show_activity else "false",
            "HACKBOT_MAX_RESEARCHERS": str(self.max_researchers),
            "HACKBOT_ACP_URL": self.acp_url or "",
            "HACKBOT_MCP_CONFIG": self.mcp_config or "",
        }

    def save_env(self) -> Path:
        """Write settings to .env, preserving unrelated lines and comments."""
        values = self.to_env()
        lines: list[str] = []
        seen: set[str] = set()
        if self.env_path.exists():
            for raw in self.env_path.read_text(encoding="utf-8").splitlines():
                key = raw.split("=", 1)[0].strip().lstrip("#").strip() if "=" in raw else ""
                if key in values:
                    if key not in seen:
                        lines.append(f"{key}={values[key]}")
                        seen.add(key)
                    continue
                lines.append(raw)
        for key, val in values.items():
            if key not in seen:
                lines.append(f"{key}={val}")
        self.env_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        for key, val in values.items():  # keep the running process in sync
            os.environ[key] = val
        self.data_dir.mkdir(parents=True, exist_ok=True)
        return self.env_path

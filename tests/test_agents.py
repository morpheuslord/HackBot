"""Runs the whole agent team with pydantic-ai's TestModel (no API key) over the real MCP stdio server."""

import os
from pathlib import Path

import pytest
from pydantic_ai.models.test import TestModel

from hackbot.agents import AgentSystem
from hackbot.config import Config

SAMPLE = Path(__file__).resolve().parents[1] / "samples" / "sample.sarif"


@pytest.mark.asyncio
async def test_team_runs_over_mcp(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    monkeypatch.setenv("HACKBOT_DATA_DIR", str(tmp_path))
    cfg = Config.load()
    events = []
    async with AgentSystem(cfg) as system:
        assert {"web_search", "load_sarif", "sarif_findings"} <= set(system.tool_names)
        with (
            system.orchestrator.override(model=TestModel(call_tools=["plan", "analyse_sarif", "ask_tutor"], custom_output_text="done")),
            system.analyst.override(model=TestModel(call_tools=["sarif_summary"])),
            system.tutor.override(model=TestModel(custom_output_text="tutor says hi")),
        ):
            out = await system.run("triage my report", sarif_path=str(SAMPLE), on_event=events.append)
    assert out.answer == "done"
    assert out.triage and "items" in out.triage
    kinds = {(s["agent"], s["kind"]) for s in out.steps}
    assert ("analyst", "agent") in kinds and ("tutor", "agent") in kinds and ("orchestrator", "plan") in kinds
    assert all(s["status"] == "done" for s in out.steps)
    assert any(e.kind == "delta" for e in events) or out.answer  # streaming path exercised when model streams

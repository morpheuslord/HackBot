from pathlib import Path

from hackbot.core import sarif as S

SAMPLE = Path(__file__).resolve().parents[1] / "samples" / "sample.sarif"


def test_load_sample():
    rep = S.load_sarif_file(SAMPLE)
    assert rep.tools == ["DemoScan 1.4.2"]
    assert len(rep.findings) == 10
    assert rep.by_level()["error"] == 4
    # sorted: errors first, highest security-severity first
    assert rep.findings[0].rule_id == "py/hardcoded-credentials"
    assert rep.findings[0].location == "app/config.py:7"
    assert rep.findings[0].cwe == "CWE-798"


def test_summary_and_findings_dict():
    rep = S.load_sarif_file(SAMPLE)
    summary = S.summary_dict(rep)
    assert summary["total"] == 10 and "CWE-89" in summary["cwes"]
    errors = S.findings_dict(rep, level="error", limit=2)
    assert errors["count"] == 4 and errors["returned"] == 2


def test_triage_markdown():
    rep = S.load_sarif_file(SAMPLE)
    triage = {1: {"verdict": "true-positive", "priority": 1, "by": "agent", "note": "secret in repo"}}
    md = S.triage_markdown(rep, triage)
    assert "| 1 | error | py/hardcoded-credentials | CWE-798 |" in md
    assert "P1  #1 py/hardcoded-credentials" in md

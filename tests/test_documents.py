from pathlib import Path

import pytest

from hackbot.core import documents as D

ROOT = Path(__file__).resolve().parents[1]


def test_sarif_is_detected_and_parsed():
    doc = D.load_document(ROOT / "samples" / "sample.sarif")
    assert doc.kind == "sarif" and doc.sarif is not None and len(doc.sarif.findings) == 10
    assert "structured triage" in doc.description


def test_any_text_file(tmp_path):
    f = tmp_path / "app.py"
    f.write_text("import os\nPASSWORD = 'hunter2'\nos.system(cmd)\n", encoding="utf-8")
    doc = D.load_document(f)
    assert doc.kind == "code:python" and doc.lines == 3 and doc.sarif is None
    assert doc.search("password")[0]["line"] == 2
    assert doc.slice(2, 1) == [(2, "PASSWORD = 'hunter2'")]


def test_json_and_log_kinds(tmp_path):
    (tmp_path / "x.json").write_text('{"a": 1}', encoding="utf-8")
    (tmp_path / "auth.log").write_text("Failed password for root\n", encoding="utf-8")
    assert D.load_document(tmp_path / "x.json").kind == "json"
    assert D.load_document(tmp_path / "auth.log").kind == "log"


def test_binary_rejected(tmp_path):
    f = tmp_path / "blob.bin"
    f.write_bytes(b"\x00\x01\x02")
    with pytest.raises(ValueError):
        D.load_document(f)


def test_folder_loading(tmp_path):
    (tmp_path / "a.py").write_text("x = 1\ny = 2\n", encoding="utf-8")
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "b.log").write_text("Failed password for root\n", encoding="utf-8")
    (tmp_path / "sub" / "img.bin").write_bytes(b"\x00\x01")
    doc = D.load_document(tmp_path)
    assert doc.is_folder and len(doc.files) == 3
    included = [f for f in doc.files if f["included"]]
    assert [f["path"] for f in included] == ["a.py", "sub/b.log"]
    hit = doc.search("Failed password")[0]
    assert doc.file_at(hit["line"]) == "sub/b.log"
    assert "===== a.py =====" in doc.text and doc.info()["files"][0]["start_line"] == included[0]["line"]


def test_quoted_paths_are_accepted(tmp_path):
    f = tmp_path / "raw.sariff"
    f.write_text((ROOT / "samples" / "sample.sarif").read_text(encoding="utf-8"), encoding="utf-8")
    doc = D.load_document(f'"{f}"')  # Windows "Copy as path" style
    assert doc.kind == "sarif" and doc.sarif is not None
    assert D.load_document(f"'{f}'").name == "raw.sariff"

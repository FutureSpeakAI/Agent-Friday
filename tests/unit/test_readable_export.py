"""The human-readable export (north star §36.6-2, §6.10-2, §27.3-2): every
category in plain files, a manifest of hashes, secrets blanked, nothing
written to Friday's data folder but one receipt."""
from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import time
from pathlib import Path

import pytest

SECRET = "sk-test-readable-export-0123456789abcdef"  # pragma: allowlist secret


@pytest.fixture
def home(tmp_path, monkeypatch):
    h = tmp_path / ".friday"
    h.mkdir()
    monkeypatch.setenv("FRIDAY_HOME", str(h))
    from agent_friday import core
    from agent_friday.services import conversations, core_memory, goals, user_model
    monkeypatch.setattr(core, "WIKI_DIR", h / "wiki")
    monkeypatch.setattr(user_model, "DB_PATH", h / "user_model.db")
    monkeypatch.setattr(goals, "GOALS_DIR", h / "goals")
    monkeypatch.setattr(core_memory, "_path", lambda target: h / "core_memory" / core_memory.FILES[target])
    monkeypatch.setattr(conversations, "_root", lambda: h / "conversations")
    (h / "wiki" / "notes").mkdir(parents=True)
    (h / "wiki" / "notes" / "garden.md").write_text("# Garden\n\nTomatoes in May.\n", encoding="utf-8")
    (h / "wiki" / "health.md").write_text("locked", encoding="utf-8")
    (h / "core_memory").mkdir()
    (h / "core_memory" / "ME.md").write_text("- Lives in Leeds\n", encoding="utf-8")
    con = sqlite3.connect(h / "user_model.db")
    con.execute("CREATE TABLE traits(key, value, confidence, updated_ts, evidence)")
    con.execute("CREATE TABLE facts(fact_id, ts, category, text, confidence, source)")
    con.execute("INSERT INTO traits VALUES('tone','warm',0.8,0,'')")
    con.execute("INSERT INTO facts VALUES('f1',?, 'food','Likes ramen',0.9,'chat')", (time.time(),))
    con.commit()
    con.close()
    c = h / "conversations" / "c1"
    c.mkdir(parents=True)
    (c / "conversation.json").write_text(json.dumps({"id": "c1", "title": "Trip plans", "created_at": time.time()}),
                                         encoding="utf-8")
    (c / "messages.jsonl").write_text(
        json.dumps({"role": "user", "text": "Book Lisbon?", "ts": time.time()}) + "\n"
        + json.dumps({"role": "assistant", "text": "Two options.", "ts": time.time()}) + "\n", encoding="utf-8")
    (h / "goals").mkdir()
    (h / "goals" / "goal_1.json").write_text(json.dumps({"id": "g1", "title": "Run 10k", "status": "active",
                                                         "steps": [{"title": "Buy shoes", "status": "done"}]}),
                                             encoding="utf-8")
    (h / "people_graph.json").write_text(json.dumps({"people": {"p1": {"name": "Ana", "aliases": ["A"],
                                                                       "domains": ["x.org"]}}}), encoding="utf-8")
    (h / "settings.json").write_text(json.dumps({"theme": "dark", "openai_api_key": SECRET}), encoding="utf-8")
    (h / "security").mkdir()
    (h / "security" / "root.key").write_text(SECRET, encoding="utf-8")
    from agent_friday.services import action_ledger, wiki_engine
    action_ledger.append("decision", {"tool": "search_web", "decision": "allow", "class": "internal"})
    real = wiki_engine.wiki_read_text
    monkeypatch.setattr(wiki_engine, "wiki_read_text",
                        lambda p: wiki_engine.VAULT_LOCKED_PLACEHOLDER if Path(p).name == "health.md" else real(p))
    return h


def _snapshot(h: Path) -> dict:
    from agent_friday.services import action_ledger
    ledger = Path(action_ledger.ledger_dir()).resolve()
    out = {}
    for p in h.rglob("*"):
        if p.is_file() and ledger not in p.resolve().parents:
            out[str(p)] = (p.stat().st_size, p.stat().st_mtime_ns)
    return out


def _run(home, tmp_path, **kw):
    from agent_friday.services import readable_export
    return readable_export.export(tmp_path / "out", **kw)


def test_every_category_is_written_in_plain_files(home, tmp_path):
    r = _run(home, tmp_path)
    f = Path(r["folder"])
    assert (f / "wiki" / "notes" / "garden.md").read_text(encoding="utf-8").startswith("# Garden")
    assert "Lives in Leeds" in (f / "memories" / "about-you.md").read_text(encoding="utf-8")
    prof = (f / "memories" / "profile.md").read_text(encoding="utf-8")
    assert "Likes ramen" in prof and "warm" in prof
    conv = next((f / "conversations").glob("* Trip plans.md")).read_text(encoding="utf-8")
    assert "**You**" in conv and "Book Lisbon?" in conv and "Two options." in conv
    assert "Run 10k" in (f / "goals" / "goals.md").read_text(encoding="utf-8")
    assert "search_web" in (f / "receipts" / "receipts.md").read_text(encoding="utf-8")
    assert (f / "receipts" / "receipts.jsonl").read_text(encoding="utf-8").strip()
    assert "**Yes.**" in (f / "receipts" / "verification.md").read_text(encoding="utf-8")
    assert "Ana" in (f / "contacts" / "contacts.csv").read_text(encoding="utf-8")
    assert json.loads((f / "settings" / "settings.json").read_text(encoding="utf-8"))["theme"] == "dark"
    assert all(r["counts"][c] >= 1 for c in r["counts"])


def test_the_manifest_hashes_every_file(home, tmp_path):
    f = Path(_run(home, tmp_path)["folder"])
    man = json.loads((f / "manifest.json").read_text(encoding="utf-8"))
    assert "README.md" in man["files"]
    for rel, meta in man["files"].items():
        assert hashlib.sha256((f / rel).read_bytes()).hexdigest() == meta["sha256"], rel


def test_no_secret_and_no_key_file_is_in_an_export(home, tmp_path):
    f = Path(_run(home, tmp_path)["folder"])
    for p in f.rglob("*"):
        if p.is_file():
            assert SECRET not in p.read_text(encoding="utf-8", errors="replace"), p
    assert not list(f.rglob("*.key")) and not (f / "security").exists()


def test_it_writes_nothing_to_the_data_folder_but_its_receipt(home, tmp_path):
    from agent_friday.services import action_ledger
    before = _snapshot(home)
    _run(home, tmp_path)
    assert _snapshot(home) == before
    kinds = [(e.get("rec") or {}).get("event") for e in action_ledger.entries(["export"])]
    assert kinds == ["readable_export"]


def test_a_page_the_vault_keeps_locked_is_listed_not_written(home, tmp_path):
    r = _run(home, tmp_path)
    f = Path(r["folder"])
    assert not (f / "wiki" / "health.md").exists()
    assert any("health.md" in n and "encrypted section" in n for n in r["notes"])
    assert "health.md" in (f / "README.md").read_text(encoding="utf-8")


def test_a_break_in_the_record_is_written_out(home, tmp_path, monkeypatch):
    from agent_friday.services import action_ledger
    monkeypatch.setattr(action_ledger, "verify", lambda **k: {"ok": False, "records": 1,
                                                               "problems": [{"reason": "a line was changed", "seq": 1}]})
    f = Path(_run(home, tmp_path)["folder"])
    v = (f / "receipts" / "verification.md").read_text(encoding="utf-8")
    assert "**No.**" in v and "a line was changed (entry #1)" in v


def test_off_the_record_messages_are_never_exported(home, tmp_path, monkeypatch):
    from agent_friday.services import conversations
    monkeypatch.setattr(conversations, "messages",
                        lambda cid, *a, **k: [{"role": "user", "text": "OFF RECORD SECRET", "off_record": True}])
    f = Path(_run(home, tmp_path)["folder"])
    for p in (f / "conversations").glob("*.md"):
        assert "OFF RECORD SECRET" not in p.read_text(encoding="utf-8")


def test_inside_the_data_folder_is_refused(home):
    from agent_friday.services import readable_export
    with pytest.raises(readable_export.ExportError):
        readable_export.export(home / "exports")
    with pytest.raises(readable_export.ExportError):
        readable_export.export(Path("relative/folder"))


def test_only_some_categories_and_no_unknown_ones(home, tmp_path):
    from agent_friday.services import readable_export
    r = _run(home, tmp_path, only=["goals"])
    assert list(r["counts"]) == ["goals"] and not (Path(r["folder"]) / "wiki").exists()
    with pytest.raises(readable_export.ExportError):
        _run(home, tmp_path, only=["passwords"])


def test_two_exports_never_overwrite_each_other(home, tmp_path):
    a, b = _run(home, tmp_path), _run(home, tmp_path)
    assert a["folder"] != b["folder"] and Path(a["folder"]).exists() and Path(b["folder"]).exists()


def test_the_command_line(home, tmp_path, capsys):
    from agent_friday import cli
    assert cli.cmd_export_readable(out=str(tmp_path / "cli"), only="contacts,settings") == 0
    out = capsys.readouterr().out
    assert "Exported" in out and "contacts" in out
    assert len(list((tmp_path / "cli").glob("friday-readable-*"))) == 1

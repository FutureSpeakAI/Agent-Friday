"""Every search record and every cited answer carries the encoder, the menu
wording and the calibration that made it, so an old citation can be traced to
the version behind it."""
from __future__ import annotations

import json
import sqlite3

import pytest

from tests.library_fixtures import (install_fake_encoder, isolate_library, make_pdf, release_library, write_docs)


@pytest.fixture(autouse=True)
def _env(tmp_path, monkeypatch):
    fg, lstore = isolate_library(tmp_path, monkeypatch)
    install_fake_encoder(monkeypatch)
    from agent_friday.services import agent
    from agent_friday.services.library import shelf
    monkeypatch.setattr(shelf, "_vault_key", lambda: None)
    monkeypatch.setattr(shelf, "tier_of", lambda title, sample: 1)
    monkeypatch.setattr(agent, "_CURRENT_PROVIDER", type("V", (), {"get": staticmethod(lambda: "local")}))
    yield
    release_library(fg, lstore)


def _lib(tmp_path):
    from agent_friday.services.library import grants, indexer
    from agent_friday.services.library.store import store_for
    root = tmp_path / "Lib"
    write_docs(root, {"lease.pdf": make_pdf([["# Lease", "Either party may end the lease with ninety days notice."]])})
    grants.add_scope("owner", str(root))
    st = store_for("owner")
    indexer.sweep_scope(st, root, allowed=lambda p: grants.allowed("owner", p))
    return st


def test_a_stamp_names_the_encoder_wording_calibration_and_index():
    from agent_friday.services.library import embed, versions
    s = versions.stamp()
    assert s["encoder"] == embed.stamp_id() and s["encoder"].startswith("sentence-transformers/all-MiniLM-L6-v2@")
    assert s["wording"].startswith("w") and s["calibration"].startswith("c") and s["index"] >= 1
    assert "laya" not in s, "Laya is named only when it answered a menu"
    assert versions.stamp(laya_used=True)["laya"]


def test_the_wording_and_calibration_ids_follow_their_content(monkeypatch):
    from agent_friday.services.library import route, versions
    w0, c0 = versions.wording_version(), versions.calibration_id()
    assert versions.wording_version() == w0 and versions.calibration_id() == c0
    monkeypatch.setattr(route, "ROUTE_INSTRUCTIONS", route.ROUTE_INSTRUCTIONS + " Think carefully.")
    assert versions.wording_version() != w0
    cfg = route.config()
    cfg["floor"] = 0.5
    assert versions.calibration_id(cfg) != c0


def test_every_search_record_carries_the_stamp(tmp_path):
    from agent_friday.services.library import search, versions
    st = _lib(tmp_path)
    res = search.run("how much notice to end the lease")
    assert res["stamp"] == versions.stamp(cfg=route_cfg())
    rec = json.loads(st.q("SELECT data FROM receipts WHERE search_id=?", (res["receipt"],))[0]["data"])
    assert rec["stamp"]["wording"] == versions.wording_version() and rec["stamp"]["encoder"] == res["stamp"]["encoder"]


def route_cfg():
    from agent_friday.services.library import route
    return route.config()


def test_a_cited_answer_is_stamped_and_points_at_its_search_record(tmp_path):
    from agent_friday.services.library import cite, tools
    st = _lib(tmp_path)
    out = tools.search_library({"question": "how much notice to end the lease"})
    meta = json.loads(out.split("\n", 1)[0])
    assert meta["stamp"]["wording"] and meta["receipt"]
    trace = [{"name": "search_library", "input": {}, "result": out[:2000]}]
    reply, _ = cite.finish("Ninety days [1.1].", trace, conversation_id="c1")
    assert "lib:" in reply
    row = st.q("SELECT stamp, receipt_id FROM cited_in")[0]
    assert json.loads(row["stamp"]) == meta["stamp"] and row["receipt_id"] == meta["receipt"]
    assert st.q("SELECT count(*) n FROM receipts WHERE search_id=?", (row["receipt_id"],))[0]["n"] == 1


def test_a_written_answer_is_stamped_with_the_search_it_came_from(tmp_path, monkeypatch):
    from agent_friday.services import local_call
    from agent_friday.services.library import answer, search
    st = _lib(tmp_path)
    res = search.run("how much notice to end the lease")
    monkeypatch.setattr(local_call, "call", lambda *a, **k: "Ninety days [1.1].")
    out = answer.write("how much notice", res["evidence"], model="m", stamp=res["stamp"], receipt=res["receipt"])
    assert out["ok"] and out["stamp"] == res["stamp"] and "lib:" in out["text"]
    row = st.q("SELECT stamp, receipt_id FROM cited_in")[0]
    assert json.loads(row["stamp"]) == res["stamp"] and row["receipt_id"] == res["receipt"]


def test_off_the_record_no_stamped_row_is_written(tmp_path, monkeypatch):
    from agent_friday.services import off_record
    from agent_friday.services.library import cite, tools
    st = _lib(tmp_path)
    monkeypatch.setattr(off_record, "active", lambda settings=None: True)
    out = tools.search_library({"question": "how much notice to end the lease"})
    cite.finish("Ninety days [1.1].", [{"name": "search_library", "input": {}, "result": out[:2000]}])
    assert st.q("SELECT count(*) n FROM cited_in")[0]["n"] == 0


def test_an_index_made_before_the_stamp_gains_the_columns(tmp_path):
    from agent_friday.services.library.store import Store
    p = tmp_path / "old" / "library.sqlite"
    p.parent.mkdir()
    db = sqlite3.connect(str(p))
    db.execute("CREATE TABLE cited_in(block_id INTEGER NOT NULL, doc_id INTEGER NOT NULL, conversation_id TEXT, "
               "message_id TEXT, ts REAL)")
    db.commit()
    db.close()
    st = Store(p)
    st.add_citation(1, 1, "c", None, '{"wording":"w1"}', "r1")
    assert dict(st.q("SELECT stamp, receipt_id FROM cited_in")[0]) == {"stamp": '{"wording":"w1"}', "receipt_id": "r1"}


def test_the_search_screen_can_show_what_a_search_was_made_with():
    from pathlib import Path
    src = (Path(__file__).resolve().parents[2] / "static" / "library_ws.js").read_text(encoding="utf-8")
    assert "run.stamp" in src and "stamp: e.stamp" in src

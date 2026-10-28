"""The owner's corrections over HTTP (north star §29.9-1, §15.6-1, §36.2-8):
a wiki correction is recorded as one event that keeps the prior text (the wiki
has no page history of its own); the Corrections list is owner-only and
bounded; "keep as a test" stores the owner's own golden; both UI files carry
the list."""
from __future__ import annotations

from pathlib import Path

import pytest
from flask import Flask, g

from agent_friday.services import core_memory as cm
from agent_friday.services import corrections as co
from agent_friday.services import prompt_freeze


@pytest.fixture(autouse=True)
def _iso(tmp_path, monkeypatch):
    monkeypatch.setattr(cm, "_dir", lambda: tmp_path / "core_memory")
    monkeypatch.setattr(cm, "_MIGRATION_CHECKED", {str(tmp_path / "core_memory"): True})
    monkeypatch.setattr(co, "_dir", lambda: tmp_path / "corrections")
    prompt_freeze.reset()
    yield
    prompt_freeze.reset()


@pytest.fixture
def scoped():
    """The memory-card blueprint alone, with the request principal settable."""
    from agent_friday.routes import core_memory as route
    app = Flask("t")
    app.secret_key = "test"                 # login_required keeps a session on loopback
    principal = ["user"]

    @app.before_request
    def _who():
        g.friday_principal = principal[0]
    app.register_blueprint(route.core_memory_bp)
    c = app.test_client()
    c.principal = principal
    return c


def test_a_wiki_correction_is_one_event_that_keeps_the_prior_text(client, server_module):
    section = server_module.WIKI_DIR / "corrzone"
    section.mkdir(parents=True, exist_ok=True)
    page = section / "home.md"
    page.write_text("# Home\n\n## Where\n\nLives at 14 Elmwood Close QZX, Harwich.\n", encoding="utf-8")
    try:
        r = client.post("/api/wiki/correct", json={"old_text": "14 Elmwood Close QZX",
                                                   "new_text": "3 Quay Lane QZX", "reason": "I moved"})
        d = r.get_json()
        assert r.status_code == 200 and d["status"] == "ok" and d.get("correction_id")
        assert "3 Quay Lane QZX" in page.read_text(encoding="utf-8")
        ev = co.get(d["correction_id"])
        assert ev["store"] == "wiki" and ev["action"] == "corrected" and ev["by"] == "owner"
        assert ev["claim"] == "14 Elmwood Close QZX" and ev["correction"] == "3 Quay Lane QZX"
        assert ev["reason"] == "I moved"
        assert ev["sources"] == ["wiki:corrzone/home.md#Where"]
        assert "wiki:corrzone/home.md" in ev["affected"]
        assert ev["history"] == [{"path": "corrzone/home.md", "section": "Where",
                                  "before": ["Lives at 14 Elmwood Close QZX, Harwich."]}], \
            "the original line stays in history"
        assert ev["repair"]["pages_changed"] == 1 and ev["repair"]["occurrences_replaced"] == 1
        assert ev["repair"]["graph_reindex_queued"] is True
        none = client.post("/api/wiki/correct", json={"old_text": "NOTHING_MATCHES_QZX_0001", "new_text": "x"})
        assert "correction_id" not in none.get_json() and len(co.events()) == 1
    finally:
        page.unlink(missing_ok=True)
        section.rmdir()


def test_the_list_is_newest_first_bounded_and_shortened(scoped):
    for i in range(co.LIST_MAX + 5):
        co.record("card", "corrected", claim=f"old {i}", correction=f"new {i}", sources=[f"card:user#{i}"])
    co.record("card", "corrected", claim="x" * 1000, correction="y")
    d = scoped.get("/api/memory/corrections?n=500").get_json()
    assert d["ok"] and len(d["corrections"]) == co.LIST_MAX
    first = d["corrections"][0]
    assert len(first["claim"]) <= 200 and first["claim"].startswith("xxx")
    assert d["corrections"][1]["claim"] == f"old {co.LIST_MAX + 4}"
    assert len(scoped.get("/api/memory/corrections?n=2").get_json()["corrections"]) == 2
    assert len(scoped.get("/api/memory/corrections?n=bad").get_json()["corrections"]) == 50


def test_undo_is_offered_only_until_the_messages_are_put_back(scoped):
    orig = co.record("card", "marked_wrong", claim="Allergic to peanuts.", correction=co.REMOVED,
                     sources=["card:user#0", "msg:m-1"], affected=["card:user#0", "msg:m-1"])
    row = scoped.get("/api/memory/corrections").get_json()["corrections"][0]
    assert row["undo_ids"] == ["m-1"]
    co.record("card", "restored", sources=["msg:m-1"], affected=["msg:m-1"], restores=[orig])
    rows = {r["id"]: r for r in scoped.get("/api/memory/corrections").get_json()["corrections"]}
    assert rows[orig]["undo_ids"] == [] and rows[orig]["restored_by"]


def test_keep_as_a_test_route(scoped):
    eid = co.record("card", "corrected", claim="Dentist is Dr Okafor.", correction="Dentist is Dr Adeyemi.")
    r = scoped.post(f"/api/memory/corrections/{eid}/test",
                    json={"on": True, "question": "Who is my dentist?", "expected": "Dr Adeyemi"})
    assert r.status_code == 200 and r.get_json()["regression_test"] is True
    row = scoped.get("/api/memory/corrections").get_json()["corrections"][0]
    assert row["regression_test"] is True
    assert row["golden"] == {"question": "Who is my dentist?", "expected": "Dr Adeyemi"}
    assert scoped.post(f"/api/memory/corrections/{eid}/test", json={"on": False}).status_code == 200
    assert scoped.get("/api/memory/corrections").get_json()["corrections"][0]["regression_test"] is False
    assert scoped.post("/api/memory/corrections/nope/test", json={"on": True}).status_code == 400
    assert scoped.post("/api/memory/corrections/corr-000000000000/test", json={"on": True}).status_code == 404
    assert scoped.post(f"/api/memory/corrections/{eid}/test",
                       json={"on": True, "question": "q" * (co.QUESTION_MAX + 1)}).status_code == 400


def test_the_corrections_routes_are_owner_only(scoped):
    eid = co.record("card", "corrected", claim="Dentist is Dr Okafor.", correction="Dentist is Dr Adeyemi.")
    scoped.principal[0] = "observer"
    r = scoped.get("/api/memory/corrections")
    assert r.status_code == 403 and "Okafor" not in r.get_data(as_text=True)
    assert scoped.post(f"/api/memory/corrections/{eid}/test", json={"on": True}).status_code == 403
    assert co.get(eid)["regression_test"] is False


def test_keep_as_a_test_with_a_locked_keystore_is_503_not_500(scoped, monkeypatch):
    from agent_friday.services import credential_store as cs
    from agent_friday.services.keystore import KeystoreLocked
    eid = co.record("card", "corrected", claim="Dentist is Dr Okafor.", correction="Dentist is Dr Adeyemi.")

    def _locked(_data):
        raise KeystoreLocked("locked")
    monkeypatch.setattr(cs, "protect", _locked)
    r = scoped.post(f"/api/memory/corrections/{eid}/test",
                    json={"on": True, "question": "Who is my dentist?", "expected": "Dr Adeyemi"})
    assert r.status_code == 503 and r.get_json()["locked"] is True
    assert co.get(eid)["regression_test"] is False, "nothing was kept"


def test_the_owner_can_delete_a_correction_record(scoped, monkeypatch):
    unlinked = []
    monkeypatch.setattr(cm, "unlink_correction", lambda eid, msgs=None: unlinked.append((eid, list(msgs or []))))
    eid = co.record("card", "marked_wrong", claim="Allergic to peanuts.", correction=co.REMOVED,
                    sources=["card:user#0", "msg:m-1"], affected=["card:user#0", "msg:m-1"])
    other = co.record("card", "corrected", claim="Dentist is Dr Okafor.", correction="Dentist is Dr Adeyemi.")
    assert scoped.post("/api/memory/corrections/nope/delete").status_code == 400
    assert scoped.post("/api/memory/corrections/corr-000000000000/delete").status_code == 404
    scoped.principal[0] = "observer"
    assert scoped.post(f"/api/memory/corrections/{eid}/delete").status_code == 403
    assert co.get(eid), "an observer deletes nothing"
    scoped.principal[0] = "user"
    r = scoped.post(f"/api/memory/corrections/{eid}/delete")
    assert r.status_code == 200 and r.get_json()["ok"]
    assert [c["id"] for c in scoped.get("/api/memory/corrections").get_json()["corrections"]] == [other]
    assert unlinked == [(eid, ["m-1"])], "the card journal and the retired messages stop pointing at it"


def test_deleting_a_record_unlinks_the_card_journal(scoped, monkeypatch):
    monkeypatch.setattr(cm, "_supersede_turns", lambda line, why: [])   # no conversation store here
    cm.add("user", "Dentist is Dr Okafor at Riverside.", actor="owner")
    eid = cm.owner_set("user", 0, "Dentist is Dr Adeyemi at Harbour.")["correction_id"]
    assert "[correction:" in cm.render_prompt()
    assert scoped.post(f"/api/memory/corrections/{eid}/delete").status_code == 200
    journal = cm._journal_path().read_text(encoding="utf-8")
    assert eid not in journal and "line_ref" not in journal
    assert "[correction:" not in cm.render_prompt(), "a deleted record is no longer cited"


def test_both_ui_files_have_the_corrections_list():
    root = Path(__file__).resolve().parents[2]
    panels = []
    for f in ("index.html", "ui_parts/app.html"):
        text = (root / f).read_text(encoding="utf-8")
        panel = text.split("function MemoryCardPanel(", 1)[1].split("// End memory card panel", 1)[0]
        assert "/api/memory/corrections" in panel and "data-memory-corrections" in panel, f
        assert "Keep as a test" in panel and "/test'" in panel and "undo_ids" in panel, f
        assert "Delete this record" in panel and "/delete'" in panel and "data-memory-correction-delete" in panel, f
        panels.append(panel)
    assert panels[0] == panels[1], "the shared panel is identical in both UI files"

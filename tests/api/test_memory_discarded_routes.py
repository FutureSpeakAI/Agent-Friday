"""Discarded suggestions (owner ruling 2026-10-28): GET /api/memory/discarded and
POST /api/memory/discarded/<id>/undo (routes/memory_proposals).

A suggestion the owner discarded on the memory card, or a proposal they
rejected, stays blocked for good and is listed here with its sealed label
opened, for the owner only. "Allow again" removes the block; it does not put
the suggestion back in the queue. Login required; principal "user" only;
bounded (a limit, an id of at most 32 hex characters).
"""
from __future__ import annotations

import pytest
from flask import Flask, g

from agent_friday.services import core_memory as cm
from agent_friday.services import tombstones


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("FRIDAY_HOME", str(tmp_path))
    monkeypatch.setattr(cm, "_dir", lambda: tmp_path / "core_memory")
    monkeypatch.setattr(cm, "_MIGRATION_CHECKED", {str(tmp_path / "core_memory"): True})
    tombstones._reset_for_tests()
    from agent_friday.routes import memory_proposals as route
    app = Flask("t")
    app.secret_key = "test"                 # login_required keeps a session on loopback
    principal = ["user"]

    @app.before_request
    def _who():
        g.friday_principal = principal[0]
    app.register_blueprint(route.memory_proposals_bp)
    c = app.test_client()
    c.principal = principal
    yield c
    tombstones._reset_for_tests()


def _discard_on_the_card(text):
    q = cm.queue("user", "add", text=text, source="dream:2026-10-20")
    assert q["ok"], q
    assert cm.decide(q["id"], False)["ok"]


def test_the_owner_sees_the_list_and_allows_one_again(client):
    _discard_on_the_card("Runs on Sundays.")
    assert tombstones.add_discard("proposal", "The user is training for a marathon.")["ok"]
    d = client.get("/api/memory/discarded").get_json()
    assert d["ok"] and "does not put it back" in d["note"]
    labels = {i["label"]: i for i in d["discarded"]}
    assert set(labels) == {"Runs on Sundays.", "The user is training for a marathon."}
    assert labels["Runs on Sundays."]["kind"] == "card_line"

    item = labels["Runs on Sundays."]
    r = client.post("/api/memory/discarded/%s/undo" % item["id"])
    assert r.status_code == 200 and r.get_json()["ok"] and r.get_json()["removed"] == 1
    assert "not put back" in r.get_json()["note"]
    assert cm.pending() == [], "allowing it again put it back in the queue"
    left = client.get("/api/memory/discarded").get_json()["discarded"]
    assert [i["label"] for i in left] == ["The user is training for a marathon."]
    # The card now takes the suggestion again.
    assert cm.queue("user", "add", text="Runs on Sundays.", source="dream:2026-10-22")["ok"]


def test_an_unknown_or_malformed_id_is_refused(client):
    assert client.post("/api/memory/discarded/0123456789abcdef/undo").status_code == 404
    assert client.post("/api/memory/discarded/" + "a" * 40 + "/undo").status_code == 404
    assert client.post("/api/memory/discarded/not-hex/undo").status_code == 404


def test_the_list_is_bounded(client):
    for i in range(5):
        assert tombstones.add_discard("card_line", "Suggestion number %d." % i)["ok"]
    d = client.get("/api/memory/discarded?limit=2").get_json()
    assert len(d["discarded"]) == 2
    d = client.get("/api/memory/discarded?limit=100000").get_json()
    assert len(d["discarded"]) == 5


def test_only_the_owner_principal_is_served(client):
    _discard_on_the_card("Runs on Sundays.")
    [item] = tombstones.list_discarded()
    client.principal[0] = "observer"
    r = client.get("/api/memory/discarded")
    assert r.status_code == 403 and "Sundays" not in r.get_data(as_text=True)
    assert client.post("/api/memory/discarded/%s/undo" % item["id"]).status_code == 403
    assert tombstones.list_discarded(), "a non-owner lifted the block"


def test_login_is_required():
    from agent_friday.core import login_required
    from agent_friday.routes import memory_proposals as route
    probe = login_required(lambda: None)
    for name in ("api_memory_discarded", "api_memory_discarded_undo"):
        fn = getattr(route, name)
        assert fn.__code__ is probe.__code__, name + " is not behind login_required"


def test_the_list_is_in_both_ui_files():
    from pathlib import Path
    root = Path(__file__).resolve().parents[2]
    for f in ("index.html", "ui_parts/app.html"):
        text = (root / f).read_text(encoding="utf-8")
        panel = text.split("function MemoryCardPanel(")[1].split("// End memory card panel")[0]
        assert "/api/memory/discarded" in panel and "Discarded suggestions" in panel, f
        assert "Allow again" in panel and "does not put it back in the queue" in panel, f

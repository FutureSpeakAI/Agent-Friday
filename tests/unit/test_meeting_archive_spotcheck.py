"""The spot-check of a7d370cbd (Meetings, MT-2). Each test fails on that commit."""
from __future__ import annotations

import json
import sqlite3

from agent_friday.services import meeting_archive as ma
from agent_friday.services import meeting_capture as mc
from test_meeting_archive_critic_fixes import (A, B, _mgr, _store, env, proposals_db,  # noqa: F401
                                               record)

CAND = {"text": "Priya came up in a meeting.", "category": "relationship", "span": {"i": 1, "t": 20}}


def test_a_forgotten_name_is_applied_only_to_meetings_that_existed_or_were_under_way(env, monkeypatch):
    from agent_friday.services import forget_person
    m = _mgr(monkeypatch)
    monkeypatch.setattr(mc, "_manager", m)
    under_way = record(A, state="transcribing")
    m._save(under_way)
    monkeypatch.setattr(forget_person, "forgotten_names", lambda: {"will", "priya"})
    assert mc.people_purge({"Will"}, set()) == 0                # nothing settled names Will; A is left for its own save
    later = record(B)
    later["segments"].append({"t": 9, "kind": "speech", "speaker": "You", "text": "I will do it, and so will Priya."})
    m._save(later)                                              # recorded after the forget: never touched
    assert "I will do it, and so will Priya." in json.dumps(mc._load(B["id"]))
    under_way["state"] = "transcribed"
    m._save(under_way)                                          # under way when it happened: redacted now
    assert "[removed] ask Priya" in json.dumps(mc._load(A["id"]))
    assert not mc._pending_read().get(A["id"]), "the entry is used up"


def test_a_suggestion_the_keystore_cannot_open_is_refused_and_the_row_is_left_alone(env, monkeypatch, proposals_db):
    from agent_friday.services import credential_store as cs
    from agent_friday.services import user_model
    from agent_friday.services.knowledge_graph import integration
    kept = []
    monkeypatch.setattr(user_model, "note_fact", lambda *a, **k: kept.append(a) or {"ok": True})
    monkeypatch.setattr(integration, "ingest_fact", lambda *a, **k: kept.append(a))
    proposals_db.propose_from_meeting([CAND], A["id"], "2026-10-09", "m")
    fid = proposals_db.pending()[0]["fact_id"]
    before = sqlite3.connect(str(proposals_db.DB_PATH)).execute("SELECT text, status FROM proposed_facts").fetchall()

    def locked(blob):
        raise OSError("the keystore is locked")
    monkeypatch.setattr(cs, "unprotect", locked)
    out = proposals_db.approve([fid])
    assert out["approved"] == 0 and out["ok"] is False and out["refused"] == [fid] and "cannot be opened" in out["message"]
    assert kept == [], "no empty fact was written"
    after = sqlite3.connect(str(proposals_db.DB_PATH)).execute("SELECT text, status FROM proposed_facts").fetchall()
    assert after == before and after[0][1] == "pending" and after[0][0].startswith("enc:")


def test_elision_digests_go_oldest_first_and_the_file_is_written_whole(env):
    from agent_friday.services.library import cite
    cite._MEETING_REPLIES = dict.fromkeys("d%d" % i for i in range(4000))
    cite._remember_meeting_digest("newest")
    assert "d0" not in cite._MEETING_REPLIES and "d1" in cite._MEETING_REPLIES and "newest" in cite._MEETING_REPLIES
    cite._save_meeting_replies()
    p = cite._meeting_file()
    assert p.exists() and not list(p.parent.glob("*.tmp"))
    cite._MEETING_REPLIES = None
    assert list(cite._load_meeting_replies())[-1] == "newest" and len(cite._load_meeting_replies()) == 4000


def test_off_then_on_quickly_leaves_the_archive_to_be_rebuilt_not_cleared(env):
    ma.index_meeting(record(A))
    n = _store().one("SELECT count(*) n FROM documents")["n"]
    assert n == 2
    env["choice"] = "on"                      # said yes again before the "off" cleanup got its turn
    ma._clear_after_off()
    assert _store().one("SELECT count(*) n FROM documents")["n"] == 2
    assert ma.remove_all(stop=lambda: True) == 0

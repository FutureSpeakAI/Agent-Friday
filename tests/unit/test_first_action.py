"""The first governed action of onboarding (services/first_action; amendment A27, BD-09 I5).

One proposal built from held records, a normal approval card only the owner's press approves,
a receipt when it runs, and a recorded skip. The approvals store, the checkpoint and the reminder
are replaced by recorders here; tests/api/test_first_action_route.py runs the real card. On a
tree without the module each test fails at its fixture with the reason.
"""
from __future__ import annotations

import importlib

import pytest

THREAD = {"account_id": "a1", "thread_id": "t1", "subject": "Depot story", "email": "dana@example.org",
          "name": "Dana", "days": 5, "at": "2026-10-18T09:00:00Z"}


@pytest.fixture
def fa(tmp_path, monkeypatch):
    monkeypatch.setenv("FRIDAY_HOME", str(tmp_path))
    try:
        mod = importlib.import_module("agent_friday.services.first_action")
    except ImportError:
        pytest.fail("onboarding has no first governed action: services/first_action does not exist")
    from agent_friday.services import approvals as ap
    from agent_friday.services import forget_person as fp
    from agent_friday.services import off_record
    from agent_friday.services import relationship_memory as rm
    from agent_friday.governance import action_gate as gate
    rec = {"cards": [], "claims": set(), "used": [], "decided": [], "external": [], "outcomes": [],
           "follow_ups": []}
    monkeypatch.setattr(off_record, "active", lambda settings=None: False)
    monkeypatch.setattr(fp, "forgotten_names", lambda: set())
    monkeypatch.setattr(fp, "forgotten_emails", lambda: set())
    monkeypatch.setattr(rm, "cold_threads", lambda now=None, days=None: [dict(THREAD)])

    def create(**k):
        card = dict(k, approval_id="ap-%d" % (len(rec["cards"]) + 1), status="pending")
        rec["cards"].append(card)
        return card
    monkeypatch.setattr(ap, "create_approval", create)

    def claim(aid):
        if aid in rec["claims"]:
            return False
        rec["claims"].add(aid)
        return True
    monkeypatch.setattr(ap, "claim_for_execution", claim)
    monkeypatch.setattr(ap, "mark_used", lambda aid, actor, detail=None: rec["used"].append((aid, detail)))
    monkeypatch.setattr(ap, "decide_with_outcome",
                        lambda aid, decision, **k: rec["decided"].append((aid, decision, k)) or ({}, True))
    monkeypatch.setattr(gate, "record_external", lambda action, **k: rec["external"].append((action, k)))
    monkeypatch.setattr(gate, "record_outcome", lambda tool, decision, status, **k:
                        rec["outcomes"].append({"tool": tool, "status": status, **k}))
    from agent_friday.services import action_ledger
    monkeypatch.setattr(action_ledger, "records", lambda kind, **k: [
        dict(o, approval=o.get("approval"), _seq=40 + i) for i, o in enumerate(rec["outcomes"])][::-1]
        if kind == "outcome" else [])
    monkeypatch.setattr(rm, "set_follow_up", lambda person, note="", due=None, in_days=None, now=None:
                        rec["follow_ups"].append((person, note, in_days)) or
                        {"ok": True, "follow_up": {"id": "fu_1", "due": "2026-10-25"}})
    mod.rec, mod.rm, mod.gate = rec, rm, gate
    return mod


def _approved(fa, by="owner:ui", source="desktop_press"):
    card = fa.rec["cards"][-1]
    return dict(card, status="approved", decided_by=by, decided_source=source)


def test_the_kind_is_approved_only_by_a_press_on_this_pc(fa):
    from agent_friday.services import approvals as ap
    assert ap.SCREEN_ONLY_KINDS.get("first_action") == frozenset({"owner:ui"})


def test_one_proposal_built_from_a_held_record_waits_for_the_press(fa):
    out = fa.propose("setup")
    assert out["ok"] and out["state"] == "proposed"
    card = fa.rec["cards"][0]
    assert card["kind"] == "first_action" and card["force_gate"] is True
    assert card["action_class"] == "internal"
    assert card["payload"]["person"] == "dana@example.org" and card["payload"]["action"] == "follow_up"
    assert "sends nothing to anyone" in card["description"]
    assert fa.rec["follow_ups"] == [] and fa.rec["external"] == []      # nothing ran
    again = fa.propose("owner:home")
    assert again["approval_id"] == out["approval_id"] and len(fa.rec["cards"]) == 1


def test_with_nothing_held_nothing_is_proposed_and_it_says_so(fa, monkeypatch):
    monkeypatch.setattr(fa.rm, "cold_threads", lambda now=None, days=None: [])
    out = fa.propose("setup")
    assert out["state"] == "nothing_to_propose" and "skip" in out["why"]
    assert fa.rec["cards"] == []


def test_off_the_record_or_a_forgotten_person_proposes_nothing(fa, monkeypatch):
    from agent_friday.services import off_record
    monkeypatch.setattr(off_record, "active", lambda settings=None: True)
    assert fa.propose("setup")["ok"] is False and fa.rec["cards"] == []
    monkeypatch.setattr(off_record, "active", lambda settings=None: False)
    from agent_friday.services import forget_person as fp
    monkeypatch.setattr(fp, "forgotten_emails", lambda: {"dana@example.org"})
    assert fa.propose("setup")["state"] == "nothing_to_propose" and fa.rec["cards"] == []


@pytest.mark.parametrize("by", ["owner", "owner:voice", "owner:typed", "friday", "grant:g1"])
def test_anything_but_the_press_runs_nothing(fa, by):
    fa.propose("setup")
    out = fa.run_approved(_approved(fa, by=by))
    assert out["ok"] is False
    assert fa.rec["follow_ups"] == [] and fa.rec["external"] == [] and fa.rec["outcomes"] == []


def test_the_press_runs_it_once_with_a_decision_receipt_and_an_outcome_receipt(fa):
    fa.propose("setup")
    rec = _approved(fa)
    out = fa.run_approved(rec)
    assert out["ok"] and out["receipt_seq"] == 40
    assert fa.rec["external"][0][0] == "onboarding:first_action"
    assert fa.rec["external"][0][1]["approval_id"] == rec["approval_id"]
    assert fa.rec["follow_ups"] == [("dana@example.org", "You wrote last on 2026-10-18 and there is no reply yet.", 2)]
    assert fa.rec["outcomes"][0]["status"] == "ok" and fa.rec["outcomes"][0]["approval"] == rec["approval_id"]
    assert fa.state()["state"] == "done" and fa.done_receipt() == 40
    assert fa.rec["used"][0][1]["receipt_seq"] == 40                     # the card links its receipt
    assert fa.run_approved(rec)["ok"] is False and len(fa.rec["follow_ups"]) == 1


def test_a_held_checkpoint_sets_nothing(fa, monkeypatch):
    fa.propose("setup")

    def held(action, **k):
        raise fa.gate.Held("integrity check failed")
    monkeypatch.setattr(fa.gate, "record_external", held)
    out = fa.run_approved(_approved(fa))
    assert out["ok"] is False and fa.rec["follow_ups"] == []
    assert fa.state()["state"] == "failed"


def test_skip_declines_the_card_and_is_recorded(fa, monkeypatch):
    from agent_friday.services import activity_ledger
    rows = []
    monkeypatch.setattr(activity_ledger, "record", lambda kind, **f: rows.append((kind, f)) or True)
    out = fa.propose("setup")
    s = fa.skip("owner:ui")
    assert s["ok"] and s["state"] == "skipped" and s.get("skipped_at")
    assert fa.rec["decided"][0][0] == out["approval_id"] and fa.rec["decided"][0][1] == "deny"
    assert rows == [("first_action_skipped", {"how": "skip"})]
    assert fa.skipped() is True and fa.done_receipt() is None


def test_a_skip_is_possible_with_nothing_proposed(fa, monkeypatch):
    monkeypatch.setattr(fa.rm, "cold_threads", lambda now=None, days=None: [])
    fa.propose("setup")
    assert fa.skip("owner:ui")["state"] == "skipped"


def test_the_owners_decline_on_the_card_is_a_skip_but_friday_declining_is_not(fa):
    fa.propose("setup")
    card = fa.rec["cards"][-1]
    fa._on_decision(dict(card, status="denied", decided_by="friday", decided_source="friday"))
    assert fa.state()["state"] == "proposed"
    fa._on_decision(dict(card, status="denied", decided_by="owner", decided_source="page_without_desk_key"))
    assert fa.state()["state"] == "skipped"


def test_an_expired_card_is_not_an_answer_and_can_be_proposed_again(fa):
    first = fa.propose("setup")
    card = fa.rec["cards"][-1]
    fa._on_decision(dict(card, status="expired"))
    assert fa.state()["state"] == "expired" and fa.skipped() is False
    again = fa.propose("owner:home")
    assert again["state"] == "proposed" and again["approval_id"] != first["approval_id"]
    assert fa.rec["cards"][1]["subject_id"] != fa.rec["cards"][0]["subject_id"]


def test_the_end_of_setup_proposes_it(fa, monkeypatch):
    from agent_friday.services import setup_chat
    import threading
    calls = []
    monkeypatch.setattr(fa, "propose", lambda requested_by="setup": calls.append(requested_by) or {"ok": True})

    class Now:
        def __init__(self, target=None, **k):
            self.target = target

        def start(self):
            self.target()
    monkeypatch.setattr(threading, "Thread", Now)
    monkeypatch.setattr(setup_chat, "FIRST_ACTION_IN_TESTS", [True])
    setup_chat._propose_first_action()
    assert calls == ["setup"]

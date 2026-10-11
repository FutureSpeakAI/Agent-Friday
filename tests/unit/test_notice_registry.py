"""The emitter registry: 82 emitters, four places, stable keys, one ledger line per notice."""
from __future__ import annotations

import json

import pytest

from agent_friday import notifications_engine as ne
from agent_friday.services import activity_ledger as al
from agent_friday.services import notice_registry as reg
from agent_friday.services import notification_policy as pol


@pytest.fixture(autouse=True)
def _clean(friday_dir, tmp_path, monkeypatch):
    if ne.NOTIF_FILE.exists():
        ne.NOTIF_FILE.unlink()
    monkeypatch.setattr(pol, "_LAST_TURN", [0.0])             # nobody mid-conversation
    monkeypatch.setattr(al, "LEDGER_FILE", tmp_path / "activity_ledger.jsonl")
    monkeypatch.setattr(pol, "muted_kinds", lambda: set())
    yield


def _notices():
    return [r for r in al.read(limit=500, kind="notice")][::-1]


# ── the registry ─────────────────────────────────────────────────────────────

def test_all_82_census_emitters_are_registered_with_a_place():
    for i in range(1, 83):
        em = reg.get("E%02d" % i)
        assert em is not None, "E%02d is not registered" % i
        assert em.place in reg.PLACES
        assert em.route in (reg.PUSH, reg.POPUP, reg.BUS, reg.TRANSCRIPT, reg.VOICE, reg.TRAY,
                            reg.SMS, reg.AMBIENT, reg.STREAM, reg.DERIVED)
    assert reg.get("E83") is None


def test_the_emitters_live_gained_since_the_census_are_registered():
    """Visible jobs, the Local AI queue, the cloud-spill offer and the in-call voice
    frames are emitters too: each has a place, and none is a safety-critical notice."""
    assert len(reg.LIVE_IDS) == 6
    for eid in reg.LIVE_IDS:
        em = reg.get(eid)
        assert em is not None, eid
        assert em.place in reg.PLACES and not em.safety_critical
    assert reg.get("L01").route == reg.AMBIENT and not reg.get("L01").ledger
    assert reg.get("L03").place == reg.NEEDS_YOU            # it spends money: a card, asked
    assert reg.get("L06").place == reg.RECEIPTS             # the route receipt is pulled


def test_the_first_60_go_through_the_tray_and_the_other_22_were_the_bypasses():
    push_like = [e for e in reg.REGISTRY.values() if e.id.startswith("E") and int(e.id[1:]) <= 60]
    assert len(push_like) == 60
    assert all(e.route in (reg.PUSH, reg.DERIVED) for e in push_like)
    bypass = [e for e in reg.REGISTRY.values() if e.id.startswith("E") and int(e.id[1:]) > 60]
    assert len(bypass) == 22
    assert not any(e.route in (reg.PUSH, reg.DERIVED) for e in bypass)


def test_every_notice_that_can_repeat_has_a_stable_key_template():
    for em in reg.REGISTRY.values():
        if em.route in (reg.AMBIENT, reg.STREAM) or not em.ledger and not em.key:
            continue
        assert em.key, "%s declares no dedupe key" % em.id
        lowered = em.key.lower()
        assert "time" not in lowered and "{title" not in lowered and "stamp" not in lowered, em.key


def test_safety_critical_is_exactly_spend_credential_budget_seat_and_claw():
    flagged = {e.kind for e in reg.REGISTRY.values() if e.safety_critical}
    assert flagged == {"spend_halt", "credential_problem", "budget_alert", "seat_change",
                       "claw_block", "security"}
    assert reg.SAFETY_CRITICAL_KINDS == flagged
    for kind in flagged:
        assert reg.is_safety_critical(kind)
    for kind in ("approval_pending", "front_page", "scheduled_failure", "info"):
        assert not reg.is_safety_critical(kind), kind


def test_receipts_and_traces_live_in_the_receipts_place():
    for kind in ("trace", "receipt", "router_receipt", "egress_receipt", "reasoning_trace"):
        assert reg.place_of(kind) == reg.RECEIPTS
    assert reg.get("E79").place == reg.get("E80").place == reg.get("E81").place == reg.RECEIPTS
    assert reg.get("E23").place == reg.RECEIPTS            # network log-only


def test_an_approval_is_needs_you_and_task_results_are_updates():
    assert reg.get("E01").place == reg.NEEDS_YOU
    assert reg.get("E61").place == reg.NEEDS_YOU
    assert reg.get("E02").place == reg.get("E03").place == reg.UPDATES


def test_a_generic_kind_names_no_emitter_without_a_source():
    assert reg.resolve("info", "system") is None
    assert reg.resolve("info", "podcasts").id == "E15"
    assert reg.resolve("warning", "network").id == "E25"
    assert reg.resolve("anything", "", emitter="E37").id == "E37"


# ── the engine ───────────────────────────────────────────────────────────────

def test_a_card_records_its_place_and_emitter():
    card = ne.push(title="Front Page ready", source="front-page", kind="front_page",
                   dedupe_key="front-page:e1")
    assert card["place"] == reg.UPDATES and card["emitter"] == "E11"
    card = ne.push(title="Approval needed: x", source="approvals", kind="approval_pending",
                   dedupe_key="approval:a1")
    assert card["place"] == reg.NEEDS_YOU and card["emitter"] == "E01"


@pytest.mark.parametrize("kind, source", [
    ("spend_halt", "spend-guard"), ("credential_problem", "credentials"),
    ("budget_alert", "cost-meter"), ("seat_change", "")])
def test_a_safety_critical_notice_is_needs_you_unmuted_and_never_held(monkeypatch, kind, source):
    monkeypatch.setattr(pol, "muted_kinds", lambda: {pol.mute_key(kind, source)})
    monkeypatch.setattr(pol, "_LAST_TURN", [__import__("time").time()])     # mid-conversation
    card = ne.push(title="x", kind=kind, source=source, priority="low", dedupe_key=kind + ":1")
    assert card["tier"] == pol.NEEDS_YOU
    assert not card.get("logged"), "a muted spend halt must still reach the owner"
    assert card["held"] is False and card["safety_critical"] is True
    assert ne.unread_count() == 1


def test_a_trace_or_a_receipt_is_never_pushed():
    for kind in ("trace", "receipt", "router_receipt", "egress_receipt"):
        out = ne.push(title="Why I answered that way", kind=kind, source="router",
                      dedupe_key=kind + ":1")
        assert out.get("pull_only") is True and out["place"] == reg.RECEIPTS
    assert ne.list_notifications(limit=50) == []
    assert ne.unread_count() == 0
    lines = _notices()
    assert len(lines) == 4 and all(l["place"] == "receipts" and l["outcome"] == "held"
                                   for l in lines)


def test_a_receipt_place_emitter_is_not_pushed_even_with_a_plain_kind():
    out = ne.push(title="Back online", source="network", kind="info", emitter="E23",
                  dedupe_key="network-online")
    assert out["logged"] is True and ne.list_notifications(limit=50) == []


# ── seat changes split by the cloud line ─────────────────────────────────────

def _observe(monkeypatch, tmp_path, before, after):
    from agent_friday.services import seat_transparency as st
    monkeypatch.setattr(st, "_STATE_FILE", tmp_path / "seat_state.json")
    monkeypatch.setattr(st.core, "_save_chat_history", lambda *a, **k: None)
    sides = iter([before, after])
    monkeypatch.setattr(st, "_side", lambda settings: next(sides))
    st.observe_seats({"orchestrator_model": "m1"})
    return st.observe_seats({"orchestrator_model": "m2"})


def test_a_seat_change_across_the_cloud_line_is_needs_you_and_safety_critical(monkeypatch, tmp_path):
    for before, after in (("local", "cloud"), ("cloud", "local")):
        if ne.NOTIF_FILE.exists():
            ne.NOTIF_FILE.unlink()
        assert _observe(monkeypatch, tmp_path, before, after)
        cards = ne.list_notifications(limit=50)
        assert len(cards) == 1
        assert cards[0]["kind"] == "seat_change" and cards[0]["tier"] == pol.NEEDS_YOU
        assert cards[0]["place"] == reg.NEEDS_YOU and cards[0]["safety_critical"] is True
        (tmp_path / "seat_state.json").unlink()


@pytest.mark.parametrize("side", ["local", "cloud"])
def test_a_seat_swap_on_the_same_side_is_a_receipt_and_never_pushed(monkeypatch, tmp_path, side):
    assert _observe(monkeypatch, tmp_path, side, side)
    assert ne.list_notifications(limit=50) == []
    line = _notices()[-1]
    assert (line["notice_kind"], line["place"], line["outcome"]) == ("seat_receipt", "receipts", "held")


def test_a_behavioral_anomaly_is_needs_you_and_safety_critical():
    card = ne.push(title="Friday flagged her own behaviour", source="behavioral_monitor",
                   kind="security", emitter="E33", dedupe_key="bmon-s1", priority="low")
    assert card["tier"] == pol.NEEDS_YOU and card["safety_critical"] is True and not card["held"]


# ── the ledger line ──────────────────────────────────────────────────────────

def test_every_notice_writes_one_ledger_line_with_kind_place_and_outcome():
    ne.push(title="Front Page ready", body="secret body words", source="front-page",
            kind="front_page", dedupe_key="front-page:e1")
    ne.push(title="Back online", source="network", kind="info", emitter="E23",
            dedupe_key="network-online")                         # log-only: held
    lines = _notices()
    assert [(l["notice_kind"], l["place"], l["outcome"]) for l in lines] == [
        ("front_page", "updates", "delivered"), ("network_log", "receipts", "held")]
    assert all(l["kind"] == "notice" for l in lines), "a field must not overwrite the record kind"


def test_the_ledger_line_holds_counts_only_no_words():
    ne.push(title="Tidy the notes finished", body="Your private notes about Alice",
            source="system", kind="info", emitter="E02", dedupe_key="task-done:t9",
            meta={"task_id": "t9"})
    raw = al.LEDGER_FILE.read_text(encoding="utf-8")
    for word in ("Tidy", "private", "Alice", "t9"):
        assert word not in raw, word
    rec = json.loads(raw.splitlines()[-1])
    assert set(rec) <= {"kind", "ts", "notice_kind", "place", "outcome", "safety", "emitter"}


def test_a_held_card_is_a_held_line(monkeypatch):
    monkeypatch.setattr(pol, "_LAST_TURN", [__import__("time").time()])     # mid-conversation
    ne.push(title="Front Page ready", source="front-page", kind="front_page",
            dedupe_key="front-page:e2")
    assert _notices()[-1]["outcome"] == "held"


def test_an_unregistered_kind_is_counted_without_its_name():
    ne.push(title="x", source="somewhere", kind="made_up_kind_from_a_new_module",
            dedupe_key="new:1")
    line = _notices()[-1]
    assert line["notice_kind"] == "unregistered"


def test_a_ledger_field_cannot_overwrite_the_records_own_kind(tmp_path):
    assert al.record("notification", kind="info", source="network", tier="log_only")
    got = al.read(limit=5, kind="notification")
    assert got, "the record keeps its kind"
    assert got[0]["kind"] == "notification" and got[0]["notification_kind"] == "info"


def test_no_field_called_kind_or_ts_can_replace_the_records_own():
    assert al.record("notice", kind="evil", ts=1, notice_kind="front_page", place="updates",
                     outcome="delivered")
    rec = al.read(limit=1, kind="notice")[0]
    assert rec["kind"] == "notice" and rec["ts"] > 1000


def test_a_routed_bypass_writer_writes_its_line(monkeypatch):
    sent = []
    from agent_friday.services import desktop_bus
    monkeypatch.setattr(desktop_bus, "send", lambda *a, **k: sent.append(a) or {"delivered": False})
    reg.bus_send("E70", {"type": "notice", "workspace": "media", "card": "c1", "text": "x"})
    assert sent
    line = _notices()[-1]
    assert (line["notice_kind"], line["place"], line["outcome"]) == ("media_job", "activity", "held")


def test_ambient_frames_are_registered_but_are_not_ledger_notices():
    assert reg.note("E63", True) is False
    assert reg.note("E81", True) is False
    assert _notices() == []


# ── keys that used to carry a clock ──────────────────────────────────────────

def test_the_same_interrupted_tasks_are_one_incident_not_one_per_second(monkeypatch):
    from agent_friday.services import task_journal
    items = [{"task_id": "t1", "name": "a"}, {"task_id": "t2", "name": "b"}]
    for _ in range(3):
        task_journal.announce_interrupted(items)
    cards = ne.list_notifications(limit=50)
    assert len(cards) == 1 and cards[0]["count"] == 3
    task_journal.announce_interrupted([{"task_id": "t3", "name": "c"}])
    assert len(ne.list_notifications(limit=50)) == 2, "a different set is a new incident"


def test_the_same_resumable_tasks_are_one_incident(monkeypatch):
    from agent_friday.services import task_resume
    items = [{"task_id": "t1", "name": "a", "resumable": True},
             {"task_id": "t2", "name": "b", "resumable": True}]
    for _ in range(3):
        task_resume.announce(items)
    cards = ne.list_notifications(limit=50)
    assert len(cards) == 1 and cards[0]["count"] == 3


def test_a_tasks_card_is_keyed_by_the_task_not_its_name():
    a = ne.push(title="Task finished: Tidy the notes", source="system", kind="info",
                emitter="E02", dedupe_key="task-done:t1")
    b = ne.push(title="Task finished: Tidy the notes", source="system", kind="info",
                emitter="E02", dedupe_key="task-done:t2")
    assert a["id"] != b["id"] and len(ne.list_notifications(limit=50)) == 2


def test_the_external_endpoint_cannot_claim_a_safety_critical_kind(monkeypatch):
    from flask import Flask
    from agent_friday.routes.notifications import notif_bp
    app = Flask(__name__)
    app.register_blueprint(notif_bp)
    with app.test_client() as c:
        r = c.post("/api/notifications/push", json={"title": "Spend halt", "kind": "spend_halt",
                                                    "priority": "low"})
    assert r.status_code == 200
    card = r.get_json()["notification"]
    assert card.get("safety_critical") is not True
    assert card["kind"] == "external"
    assert card["dedupe_key"] == "external:external:external"

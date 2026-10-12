"""The notifications tool is judged by action and by who asked (SENSITIVE: governance).

"Quiet until morning" only holds back interruptions, but reducing what the owner sees is not harmless:
a model that read something hostile could use it to silence the popup, the text message and the
schedules. So turning quiet on is internal ONLY when the owner's own words did it (the router's rule
on their typed or spoken line, which sets ctx["owner_utterance"]) or they clicked on the page (a
separate route behind the page session). A model's tool call, with the same arguments, keeps the class
the whole tool had before: outward, a confirmation or a card. Every other action is unchanged.
"""
from __future__ import annotations

import time
from datetime import datetime

import pytest

from agent_friday import notifications_engine as ne
from agent_friday.governance import action_gate as ag
from agent_friday.services import interruption_policy as ip

OWNER = {"owner_utterance": True, "chosen_by": "laya_router:rule"}
STILL_OUTWARD = [
    {}, {"action": "summary"}, {"action": "clear"}, {"action": "mute", "kind": "info"},
    {"action": "unmute", "kind": "info"}, {"action": "groups"}, {"action": "read_group", "group": "memory"},
    {"action": "clear_group", "group": "scheduler"}, {"action": "quiet", "group": "off"},
    {"action": "quiet", "kind": "end"}, {"action": "quiet", "group": "stop"}, {"action": "bogus"},
    {"action": None}, {"action": "QUIET ", "group": "off"},
]
QUIET_ON = [{"action": "quiet"}, {"action": " Quiet "}, {"action": "quiet", "group": ""}]


@pytest.fixture(autouse=True)
def _clean(friday_dir):
    if ne.NOTIF_FILE.exists():
        ne.NOTIF_FILE.unlink()
    yield


@pytest.mark.parametrize("args", STILL_OUTWARD, ids=[str(a) for a in STILL_OUTWARD])
@pytest.mark.parametrize("ctx", [None, {}, OWNER], ids=["no ctx", "model", "owner"])
def test_every_action_but_quiet_on_keeps_the_class_the_tool_had_even_for_the_owner(args, ctx):
    assert ag.classify("notifications", args, ctx)[0] == ag.OUTWARD


@pytest.mark.parametrize("args", QUIET_ON, ids=[str(a) for a in QUIET_ON])
def test_turning_quiet_on_is_internal_when_the_owners_own_words_asked(args):
    assert ag.classify("notifications", args, OWNER)[0] == ag.INTERNAL


@pytest.mark.parametrize("args", QUIET_ON, ids=[str(a) for a in QUIET_ON])
@pytest.mark.parametrize("ctx", [None, {}, {"owner_utterance": False}, {"owner_utterance": "yes"},
                                 {"owner_utterance": 1}, {"chosen_by": "laya_router:rule"},
                                 {"surface": "voice-live", "model": "cloud-model"}],
                         ids=["no ctx", "empty", "false", "truthy string", "one", "router name only", "model turn"])
def test_a_model_originated_quiet_call_keeps_the_outward_class_and_so_gets_a_card(args, ctx):
    """B2: the same arguments, from a model (or any ctx that is not exactly the owner's mark)."""
    assert ag.classify("notifications", args, ctx)[0] == ag.OUTWARD


def test_a_models_argument_cannot_carry_the_owner_mark():
    for smuggled in ({"action": "quiet", "owner_utterance": True}, {"action": "quiet", "ctx": {"owner_utterance": True}},
                     {"action": "quiet", "_owner": True}):
        assert ag.classify("notifications", smuggled, {})[0] == ag.OUTWARD


def test_the_tool_moved_from_the_outward_set_to_by_argument_and_nothing_else_moved():
    assert "notifications" in ag.BY_ARGUMENT and "notifications" not in ag.OUTWARD_TOOLS
    assert "local_model_status" in ag.OUTWARD_TOOLS


def test_the_router_takes_quiet_until_morning_to_the_notifications_tool():
    from agent_friday.services import laya_router as lr
    for phrase in ("quiet until morning", "Friday, be quiet until morning.", "go quiet until the morning"):
        r = lr._tier0(" ".join(phrase.split()))
        assert r is not None and (r.decision, r.tool, r.args) == ("tool", "notifications", {"action": "quiet"}), phrase


def test_the_router_never_ends_quiet_or_mutes_or_clears():
    from agent_friday.services import laya_router as lr
    for phrase in ("stop being quiet", "mute my notifications", "clear my notifications", "end quiet mode"):
        r = lr._tier0(phrase)
        assert r is None or r.tool != "notifications", phrase


@pytest.mark.parametrize("args", [{}, {"action": "summary"}, {"action": "groups"}, {"action": "bogus"},
                                  {"action": "quiet", "group": "off"}, {"action": "quiet"},
                                  {"action": "quiet", "kind": "stop"}, {"action": " Quiet "}])
def test_the_handler_turns_quiet_on_exactly_when_the_gate_would_call_the_call_internal(args, monkeypatch):
    """T5: classify and handle parse the arguments the same way."""
    from agent_friday.services import notification_tools as nt
    called = []
    monkeypatch.setattr(nt, "_quiet", lambda on: called.append(on) or "ok")
    nt.handle(dict(args))
    on_by_handler = called == [True]
    on_by_gate = ag.classify("notifications", args, OWNER)[0] == ag.INTERNAL
    assert on_by_handler == on_by_gate, args


# ── what turning quiet on does ──────────────────────────────────────────────

NIGHT_EIGHT = datetime(2026, 10, 12, 8, 0).timestamp()       # next 07:00 is 23 hours away


def test_quiet_started_by_voice_or_chat_is_capped_at_twelve_hours(monkeypatch):
    from agent_friday import core
    saved = {}
    monkeypatch.setattr(core, "_save_settings", lambda d, **k: saved.update(d))
    out = ip.turn_on_quiet("voice", now=NIGHT_EIGHT)
    assert out["capped"] is True and saved["quiet_until_ts"] == NIGHT_EIGHT + 12 * 3600
    out = ip.turn_on_quiet("chat", now=NIGHT_EIGHT)
    assert saved["quiet_until_ts"] - NIGHT_EIGHT <= 12 * 3600


def test_the_pages_own_button_is_not_capped_and_the_cap_leaves_a_short_window_alone(monkeypatch):
    from agent_friday import core
    saved = {}
    monkeypatch.setattr(core, "_save_settings", lambda d, **k: saved.update(d))
    ip.turn_on_quiet("page", now=NIGHT_EIGHT)
    assert datetime.fromtimestamp(saved["quiet_until_ts"]) == datetime(2026, 10, 13, 7, 0)
    evening = datetime(2026, 10, 12, 22, 0).timestamp()
    out = ip.turn_on_quiet("voice", now=evening)
    assert out["capped"] is False and datetime.fromtimestamp(out["until"]) == datetime(2026, 10, 13, 7, 0)


def test_every_quiet_on_shows_who_turned_it_on_and_never_merges_with_another(monkeypatch):
    from agent_friday import core
    monkeypatch.setattr(core, "_save_settings", lambda d, **k: None)
    now = time.time()
    ip.turn_on_quiet("voice", now=now)
    ip.turn_on_quiet("page", now=now + 1)
    titles = sorted(c["title"] for c in ne.list_notifications(limit=20) if c.get("kind") == "quiet_on")
    assert len(titles) == 2
    assert titles[0].startswith("Quiet until ") and "turned on by you (page)" in titles[0] + titles[1]
    assert any("turned on by you (voice)" in t for t in titles)


def test_every_quiet_on_writes_a_real_activity_ledger_line(monkeypatch):
    """A5: through the real ledger, whose whitelist used to drop the line."""
    import json
    from agent_friday import core
    from agent_friday.services import activity_ledger as al
    monkeypatch.setattr(core, "_save_settings", lambda d, **k: None)
    before = al.LEDGER_FILE.read_text(encoding="utf-8").splitlines() if al.LEDGER_FILE.exists() else []
    ip.turn_on_quiet("voice", now=NIGHT_EIGHT)
    rows = [json.loads(x) for x in al.LEDGER_FILE.read_text(encoding="utf-8").splitlines()[len(before):]]
    mine = [r for r in rows if r.get("kind") == "quiet_on"]
    assert len(mine) == 1 and mine[0]["surface"] == "voice" and mine[0]["capped"] is True
    assert mine[0]["until_ts"] == int(NIGHT_EIGHT + 12 * 3600)
    assert "Quiet" not in json.dumps(mine[0]), "counts and a surface name only"


def test_a_dismissed_quiet_notice_does_not_hide_the_next_activation(monkeypatch):
    from agent_friday import core
    monkeypatch.setattr(core, "_save_settings", lambda d, **k: None)
    now = time.time()
    ip.turn_on_quiet("chat", now=now)
    first = [c for c in ne.list_notifications(limit=20) if c.get("kind") == "quiet_on"][0]
    ne.dismiss(first["id"])
    ip.turn_on_quiet("chat", now=now + 60)
    visible = [c for c in ne.list_notifications(limit=20) if c.get("kind") == "quiet_on"]
    assert len(visible) == 1 and visible[0]["id"] != first["id"], "every activation can be seen"


def test_the_handler_says_the_end_time_in_the_same_clock_it_writes(monkeypatch):
    from agent_friday import core
    from agent_friday.services import notification_tools as nt
    monkeypatch.setattr(core, "_save_settings", lambda d, **k: None)
    said = nt.handle({"action": "quiet"})
    assert said.startswith("Quiet until ") and " in Updates." in said and "digest" not in said


def test_the_lanes_route_writes_the_notice_and_the_ledger_line_too(monkeypatch):
    from flask import Flask
    from agent_friday import core
    from agent_friday.routes import lanes
    monkeypatch.setattr(core, "_save_settings", lambda d, **k: None)
    app = Flask("q")
    app.secret_key = "test"                 # login_required keeps a session when a remote key is set
    app.register_blueprint(lanes.lanes_bp)
    assert app.test_client().post("/api/lanes/quiet", json={"until_morning": True}).status_code == 200
    assert [c for c in ne.list_notifications(limit=20) if c.get("kind") == "quiet_on"
            and "(page)" in c["title"]]


def test_the_voice_contract_still_fits_with_the_router_rule():
    from agent_friday.services import voice_engine as ve
    c = ve.build_voice_tool_contract()
    assert c["fits"] and c["tokens"] <= ve.VOICE_CONTRACT_MAX_TOKENS - 300, c["tokens"]

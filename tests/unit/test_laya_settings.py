"""Settings > Laya (services/laya_settings): modes, report card, replay test, bars. All synthetic."""
from __future__ import annotations

import json
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest

from agent_friday.services import laya_learning_tools as llt
from agent_friday.services import laya_router as lr
from agent_friday.services import laya_settings as ls

READS = {"query_calendar", "check_email", "search_email", "get_briefing", "search_files", "search_wiki",
         "search_past_conversations", "queue_status", "search_web", "search_news"}


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setattr("agent_friday.core.FRIDAY_DIR", tmp_path)
    monkeypatch.setattr(lr, "log_path", lambda: tmp_path / "runtime" / "laya2" / "router.jsonl")
    monkeypatch.setattr("agent_friday.services.laya.mutation_class", lambda t: "read" if t in READS else "write")
    ls._cache.update(checked=0.0, mtime=None, path=None, data=None)
    return tmp_path


def _row(at, decision="tool", tool="query_calendar", layer="laya2", conf=0.6, runner=0.2, ms=3.0):
    return {"at": at.strftime("%Y-%m-%dT%H:%M:%S"), "surface": "voice", "text_sha": "0" * 16,
            "routed_by": "laya_router", "layer": layer, "decision": decision, "tool": tool,
            "confidence": conf, "runner_up": ["check_email", runner], "elapsed_ms": ms, "args_keys": []}


def _write_log(home, rows):
    p = home / "runtime" / "laya2" / "router.jsonl"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")


# ── modes ────────────────────────────────────────────────────────────────────

def test_the_approval_check_and_safe_writes_never_show_act(home, monkeypatch):
    from agent_friday.services import laya_backend as lb
    s = dict(lb.MODES["on"])
    monkeypatch.setattr("agent_friday.services.laya_safe_writes.mode", lambda: "on")
    rows = {r["id"]: r for r in ls.modes(s)}
    assert rows["approval_check"]["mode"] == "suggest" and rows["approval_check"]["never_act"]
    assert rows["safe_writes"]["mode"] == "suggest" and rows["safe_writes"]["never_act"]
    for r in rows.values():
        assert r["mode"] in ls.MODES or r["mode"] == "custom"


def test_modes_follow_the_settings_that_exist(home, monkeypatch):
    monkeypatch.setattr("agent_friday.services.laya_safe_writes.mode", lambda: "off")
    rows = {r["id"]: r["mode"] for r in ls.modes({"voice_tool_routing": "model", "chat_tool_routing": "laya",
                                                   "laya_tool_head": "on", "voice_live_reflex": "off",
                                                   "laya_personal_learning": False})}
    assert rows["voice_routing"] == "off" and rows["chat_routing"] == "act"
    assert rows["tool_head"] == "act" and rows["live_reflex"] == "off" and rows["personal_learning"] == "off"
    assert rows["calibration"] == "shadow" and rows["bars"] == "off"


# ── report card from the decision log ────────────────────────────────────────

def test_weekly_take_rate_counts_guesses_and_flags_drift(home):
    now = datetime(2026, 10, 16, 12, 0, 0)
    rows = []
    for wk in range(1, 5):                    # four earlier weeks: 5 of 25 guesses acted on
        d = now - timedelta(weeks=wk)
        rows += [_row(d, "tool")] * 5 + [_row(d, "ask")] * 20
    rows += [_row(now, "tool")] * 15 + [_row(now, "ask")] * 10      # this week: 15 of 25
    rows += [_row(now, "tool", layer="rule")] * 5                     # rules count in all turns only
    tr = ls.take_rate(rows, now=now)
    cur = tr["weeks"][-1]
    assert cur["guesses"] == 25 and cur["rate"] == 60.0 and cur["turns"] == 30 and cur["all_rate"] == round(100 * 20 / 30, 1)
    assert tr["drift"]["flag"] and tr["drift"]["points"] == 40.0 and tr["drift"]["baseline"] == 20.0
    assert len(tr["weeks"]) == ls.WEEKS_SHOWN


def test_drift_is_not_claimed_on_too_few_turns(home):
    now = datetime(2026, 10, 16, 12, 0, 0)
    rows = [_row(now - timedelta(weeks=1), "ask")] * 30 + [_row(now, "tool")] * 5
    assert ls.take_rate(rows, now=now)["drift"]["flag"] is False


def test_speed_is_read_from_the_log(home):
    now = datetime.now()
    _write_log(home, [_row(now, ms=float(i)) for i in range(1, 21)])
    sp = ls.what_runs()["speed"]
    assert sp["turns"] == 20 and sp["p50_ms"] == 10.5 and sp["p95_ms"] == 19.0


def test_the_log_is_read_from_its_tail_and_skips_damaged_lines(home):
    p = home / "runtime" / "laya2" / "router.jsonl"
    p.parent.mkdir(parents=True)
    p.write_text("not json\n" + json.dumps(_row(datetime.now())) + "\n", encoding="utf-8")
    assert len(ls._read_log()) == 1


# ── bars ─────────────────────────────────────────────────────────────────────

def test_reads_may_go_lower_changes_only_up_outward_searches_have_no_bar(home):
    assert ls.set_bars({"query_calendar": 0.40})["ok"]
    res = ls.set_bars({"set_chat_tray": 0.45})
    assert not res["ok"] and "only be raised" in res["why"]
    assert ls.set_bars({"set_chat_tray": 0.70})["ok"]
    assert not ls.set_bars({"search_web": 0.60})["ok"]
    assert not ls.set_bars({"send_email": 0.60})["ok"]
    assert not ls.set_bars({"query_calendar": 0.97})["ok"]
    assert not ls.set_bars({"query_calendar": 0.35})["ok"]
    assert ls.load_bars()["bars"] == {"query_calendar": 0.40, "set_chat_tray": 0.70}


def test_recommended_values_are_labelled_and_never_preselected(home):
    rows = {r["tool"]: r for r in ls.bars_view()["rows"]}
    assert rows["query_calendar"]["recommended"] == ls.RECOMMENDED_READ_BAR and rows["query_calendar"]["yours"] is None
    assert rows["set_chat_tray"]["recommended"] is None and rows["set_chat_tray"]["min"] == lr.ACT_THRESHOLD
    assert rows["search_web"]["class"] == "outward" and rows["search_web"]["min"] is None
    assert ls.bars_view()["mode"] == "shadow"


def test_live_bars_are_empty_in_shadow_and_present_in_act(home):
    ls.set_bars({"query_calendar": 0.45})
    assert ls.live_bars() == {}
    ls.set_bars(mode="act")
    assert ls.live_bars() == {"query_calendar": 0.45}


def test_a_hand_edited_file_cannot_loosen_an_action_that_changes_something(home):
    p = ls.bars_path()
    p.parent.mkdir(parents=True)
    p.write_text(json.dumps({"mode": "act", "bars": {"set_chat_tray": 0.40, "search_web": 0.40,
                                                       "query_calendar": 0.45, "nonsense": 0.5}}), encoding="utf-8")
    assert ls.live_bars() == {"query_calendar": 0.45}


def test_what_counts_as_letting_her_act_more(home):
    ls.set_bars({"query_calendar": 0.50, "set_chat_tray": 0.70})
    assert ls.is_loosening({"query_calendar": 0.45}, None)
    assert not ls.is_loosening({"query_calendar": 0.60}, None)
    assert ls.is_loosening({"set_chat_tray": None}, None)          # clearing a stricter bar loosens
    assert not ls.is_loosening({}, "shadow")
    assert ls.is_loosening({}, "act")


def test_every_bar_change_is_kept_in_an_append_only_history(home):
    ls.set_bars({"query_calendar": 0.45}, actor="owner:ui")
    ls.set_bars({"query_calendar": None})
    lines = ls.bars_history_path().read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2 and json.loads(lines[0])["actor"] == "owner:ui"
    assert json.loads(lines[1])["after"]["bars"] == {}


def test_the_shadow_preview_counts_what_the_bars_would_have_changed(home):
    now = datetime.now()
    rows = [_row(now, "ask", conf=0.50), _row(now, "ask", conf=0.42), _row(now, "tool", conf=0.60),
            _row(now, "ask", tool="query_calendar", layer="rule", conf=0.50)]
    pv = ls.bars_preview(rows, {"query_calendar": 0.45})
    assert pv["would_act_more"] == 1 and pv["would_ask_more"] == 0
    pv = ls.bars_preview(rows, {"query_calendar": 0.65})
    assert pv["would_ask_more"] == 1


# ── replay ───────────────────────────────────────────────────────────────────

def _store_row(i, text, label, weight="gold", off=None):
    r = {"id": "lr_%012d" % i, "at": 1000 + i, "kind": "correction", "weight": weight, "text": text, "label": label}
    if off:
        r["off"] = off
    return r


def _fake_route(table):
    def route(text, profile):
        d, t = table.get(text, ("no_tool", None))
        return SimpleNamespace(decision=d, tool=t, layer="laya2", confidence=0.5, candidates=[(t, 0.5), ("x", 0.1)])
    return route


def test_replay_scores_right_missed_and_wrong_per_action_and_keeps_no_words(home, monkeypatch):
    rows = [_store_row(1, "pull up my week", "query_calendar"),
            _store_row(2, "any new mail", "check_email"),
            _store_row(3, "what's on today", "query_calendar"),
            _store_row(4, "how are you", "conversation"),
            _store_row(5, "read me the morning", "get_briefing", weight="shadow"),
            _store_row(6, "old one", "check_email", off="undo")]
    monkeypatch.setattr("agent_friday.services.laya_personal_store.load_rows", lambda profile: rows)
    table = {"pull up my week": ("tool", "query_calendar"), "any new mail": ("ask", "check_email"),
             "what's on today": ("tool", "check_email"), "how are you": ("no_tool", None)}
    res = ls.run_replay(profile="owner", route_fn=_fake_route(table))
    assert res["ok"]
    p = res["plain"]
    assert (p["of"], p["right"], p["miss"], p["wrong"]) == (4, 2, 1, 1)
    assert p["per_action"]["query_calendar"] == {"right": 1, "miss": 0, "wrong": 1, "safe": 0, "of": 2}
    assert res["waiting_not_scored"] == 1 and res["set_aside_not_scored"] == 1
    assert res["reshuffled"]["of"] == 4 and res["memory_gap"] is not None
    saved = (ls.replay_dir() / "latest.json").read_text(encoding="utf-8")
    for words in ("pull up my week", "any new mail", "how are you"):
        assert words not in saved
    assert len(list(ls.replay_dir().glob("2*.json"))) == 1


def test_the_reshuffled_run_routes_reworded_text(home, monkeypatch):
    rows = [_store_row(1, "Open my calendar.", "query_calendar")]
    monkeypatch.setattr("agent_friday.services.laya_personal_store.load_rows", lambda profile: rows)
    seen = []

    def route(text, profile):
        seen.append(text)
        return SimpleNamespace(decision="tool" if text == "Open my calendar." else "ask", tool="query_calendar",
                               layer="laya2", confidence=0.6, candidates=[])
    res = ls.run_replay(profile="owner", route_fn=route)
    assert seen[0] == "Open my calendar." and seen[1] != seen[0] and "bring up" in seen[1]
    assert res["plain"]["right"] == 1 and res["reshuffled"]["right"] == 0 and res["memory_gap"] == 100.0


def test_reshuffle_is_deterministic_and_changes_the_words():
    a = ls.reshuffle("Play the news!", "lr_1")
    assert a == ls.reshuffle("Play the news!", "lr_1") and a != "Play the news!" and "put on" in a
    assert ls.reshuffle("hello", "x") != "hello"


def test_a_locked_vault_replays_nothing(home, monkeypatch):
    monkeypatch.setattr("agent_friday.services.laya_personal_store.load_rows", lambda profile: None)
    res = ls.run_replay(profile="owner", route_fn=_fake_route({}))
    assert not res["ok"] and "locked" in res["why"]
    assert not ls.replay_dir().exists()


def test_an_empty_history_says_how_to_get_one(home, monkeypatch):
    monkeypatch.setattr("agent_friday.services.laya_personal_store.load_rows", lambda profile: [])
    res = ls.run_replay(profile="owner", route_fn=_fake_route({}))
    assert res["ok"] and res["plain"]["of"] == 0 and "Nothing to replay yet" in res["note"]


def test_replay_shows_what_shadow_bars_would_have_done(home, monkeypatch):
    rows = [_store_row(1, "the thing on thursday", "query_calendar")]
    monkeypatch.setattr("agent_friday.services.laya_personal_store.load_rows", lambda profile: rows)
    ls.set_bars({"query_calendar": 0.45})

    def route(text, profile):
        return SimpleNamespace(decision="ask", tool="query_calendar", layer="laya2", confidence=0.50,
                               candidates=[("query_calendar", 0.50), ("check_email", 0.20)])
    res = ls.run_replay(profile="owner", route_fn=route)
    assert res["plain"]["miss"] == 1
    assert res["with_your_bars"] == {"moved": 1, "right_after": 1, "wrong_after": 0}


def test_the_real_replay_path_writes_no_log_and_reads_no_screen(home, monkeypatch):
    calls = {}

    def fake_route(text, **kw):
        calls.update(kw)
        return lr.Route("no_tool")
    monkeypatch.setattr(lr, "route", fake_route)
    ls._route_one("hello", "owner")
    assert calls["log"] is False and calls["trusted"] is False and calls["head_mode"] == "off"


def test_score_rules():
    assert ls.score(("tool", "a"), ("tool", "a")) == "right"
    assert ls.score(("tool", "a"), ("ask", None)) == "miss"
    assert ls.score(("tool", "a"), ("tool", "b")) == "wrong"
    assert ls.score(("no_tool", None), ("tool", "b")) == "wrong"
    assert ls.score(("defer", None), ("ask", None)) == "safe"


# ── voice ────────────────────────────────────────────────────────────────────

def test_what_have_you_learned_ends_with_the_last_replay(home, monkeypatch):
    assert llt._with_replay_line("I learned two things.") == "I learned two things."
    monkeypatch.setattr("agent_friday.services.laya_personal_store.load_rows",
                        lambda profile: [_store_row(1, "pull up my week", "query_calendar")])
    ls.run_replay(profile="owner", route_fn=_fake_route({"pull up my week": ("tool", "query_calendar")}))
    out = llt._with_replay_line("I learned two things.")
    assert out.startswith("I learned two things. On her last replay test she got 1 of 1")
    assert "pull up my week" not in out

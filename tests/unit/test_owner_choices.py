"""The owner's eight standing choices: each one is the owner's to make, has a default, and is a setting.

The rule (the owner's, 2026-10): no blanket rule decides for them; once informed they decide once, and
Friday does not pester. Each decision below has a behaviour test that fails on the code before this change:

1. an email paragraph about money held back from a cloud model under the cautious consent raises a card
   showing exactly that text (Send / Keep on this PC, remember for thread or sender); Send lets it go,
   Keep keeps today's announced withholding, and nothing is silent;
2. a run missed while Friday was closed: the owner's default (run once / skip) is a setting, and each job
   keeps its own choice (changeable on its Workflows card);
3. a run held because its pinned model was unavailable runs once when that model is back (default), and
   is never moved to another model;
4. the Undo window of an unattended pre-approved email is a setting (2 minutes, 10 s to 10 min): see
   tests/api/test_owner_choices_api.py;
5. trust: the learning rate (the prior's weight) and the Boost/Ban amounts are settings, the start stays
   0.5, and a change re-scores on the next pass and says so;
6. podcasts are solo by default; choosing two hosts with an unnamed co-host asks the owner once, and the
   suggestion is "Agent Friday" only when she has been renamed;
7. showing or hiding a workspace by asking is applied at once with an Undo (no card), logged;
8. deleting a calendar event tells the guests only when "notify guests" is ticked (sendUpdates=all).

And owner_choices.setup_questions() lists the eight for the first-run conversation, every key a setting
with a Settings control. Synthetic names and addresses only.
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def settings(monkeypatch):
    """Saved settings the loader returns and the saver deep-merges into (the store's own merge rule)."""
    import agent_friday.core as core
    box = {"s": {}}

    def merge(base, over):
        for k, v in over.items():
            if isinstance(v, dict) and isinstance(base.get(k), dict):
                merge(base[k], v)
            else:
                base[k] = json.loads(json.dumps(v))

    monkeypatch.setattr(core, "_load_settings", lambda: box["s"])
    monkeypatch.setattr(core, "_save_settings", lambda delta, **kw: merge(box["s"], json.loads(json.dumps(delta))))
    return box


# ── 1. money paragraphs in email under the cautious cloud consent ────────────────

REF = "mail:acct_a:t-offsite"
MONEY = "The deposit of $400 for the hall is due by 1 May, invoice attached."
PLAIN = "Thanks for the update on the spring offsite plans."
LATER = "The coach costs $250 and we pay on the day."


def _mail(*texts, sender="Robin Vale <robin@example.test>"):
    return json.dumps({"source": "gmail", "thread": REF, "read": True, "message_count": len(texts),
                       "messages": [{"n": i + 1, "id": "m%d" % i, "from": sender, "text": t}
                                    for i, t in enumerate(texts)]})


@pytest.fixture
def guarded(settings, monkeypatch, tmp_path):
    import importlib
    from agent_friday.services import approvals, egress_gate as eg
    import agent_friday.core as core
    settings["s"].update({"model_routing": {"mode": "cloud_only", "mode_chosen": "cloud_only",
                                            "cloud_consent": {"answered": True, "choice": "cloud_guarded"}}})
    monkeypatch.setattr(core, "_load_privacy_watchlist", lambda: [])
    monkeypatch.setattr(core, "_owner_emails", lambda: [])
    monkeypatch.setattr(approvals, "APPROVALS_FILE", tmp_path / "approvals.json")
    monkeypatch.setattr(approvals, "FRIDAY_DIR", tmp_path)
    monkeypatch.setattr(approvals, "_notify_pending", lambda rec: None)
    try:
        cloud_release = importlib.import_module("agent_friday.services.cloud_release")
        monkeypatch.setattr(cloud_release, "_path", lambda: tmp_path / "cloud_release.json")
    except ImportError:
        pass                    # the code before this change: no card at all (the tests say so)
    # Money words classify as private, as the shipped financial rules do; the rest is public.
    monkeypatch.setattr(eg, "_classify_cloud", lambda t: eg.Tier.SENSITIVE if re.search(r"deposit|\$", str(t)) else eg.Tier.PUBLIC)
    monkeypatch.setattr(eg, "_local_model_available", lambda: True)
    monkeypatch.setattr(eg, "_run_appeals", lambda *a, **k: 0)
    return settings


def _seal(text):
    from agent_friday.services import egress_gate as eg
    payload = {"messages": [{"role": "user", "content": [{
        "type": "tool_result", "tool_use_id": "call-1", "content": text}]}]}
    return eg.seal_outbound(payload, "anthropic", pii_lookup={})["messages"][0]["content"][0]["content"]


def _cards():
    from agent_friday.services import approvals
    return [a for a in approvals.list_approvals() if (a.get("payload") or {}).get("handler") == "cloud_release"]


def test_a_held_money_paragraph_raises_a_card_with_exactly_that_text_and_the_model_is_told(guarded):
    sent = _seal(_mail(PLAIN + "\n\n" + MONEY))
    data = json.loads(sent)
    assert PLAIN in sent and "$400" not in sent                     # held while the card waits
    cards = _cards()
    assert len(cards) == 1 and cards[0]["status"] == "pending", "no card: the paragraph was held silently"
    p = cards[0]["payload"]
    assert p["what"] == [MONEY], p["what"]                          # exactly the words that would go
    assert p["scope"]["thread"] == REF and p["scope"]["sender"] == "robin@example.test" and p["scope"]["once"]
    assert "money" in data["privacy_gate"] and "approval card" in data["privacy_gate"]
    # The same result again does not raise a second card.
    _seal(_mail(PLAIN + "\n\n" + MONEY))
    assert len(_cards()) == 1


def test_send_lets_the_text_go_and_remember_for_the_thread_covers_its_next_money_paragraph(guarded):
    _seal(_mail(PLAIN + "\n\n" + MONEY))
    assert _cards(), "no card to say Send on: the paragraph was held silently"
    card = _cards()[0]
    from agent_friday.services import cloud_release as cr
    out = cr.decide(card["approval_id"], "approve", {"thread": True, "sender": False})
    assert out["ok"] and out["approval"]["status"] == "approved"
    sent = _seal(_mail(PLAIN + "\n\n" + MONEY))
    assert MONEY in sent, "the owner said Send and the paragraph still did not go"
    assert [r["scope"] for r in cr.remembered()] == ["thread"]
    sent = _seal(_mail(PLAIN + "\n\n" + LATER))                  # a new money paragraph, same thread
    assert LATER in sent and len(_cards()) == 1
    # Forget: the thread is asked about again.
    assert cr.forget(cr.remembered()[0]["id"])
    _seal(_mail(PLAIN + "\n\n" + LATER + " Extra."))
    assert len(_cards()) == 2


def test_keep_on_this_pc_keeps_it_announced_and_does_not_ask_again(guarded):
    _seal(_mail(PLAIN + "\n\n" + MONEY))
    assert _cards(), "no card to say Keep on: the paragraph was held silently"
    from agent_friday.services import cloud_release as cr
    cr.decide(_cards()[0]["approval_id"], "deny")
    sent = _seal(_mail(PLAIN + "\n\n" + MONEY))
    data = json.loads(sent)
    assert "$400" not in sent and "keep them here" in data["privacy_gate"]
    assert len(_cards()) == 1


def test_the_owner_can_choose_to_always_keep_them_without_a_card(guarded):
    guarded["s"]["email_cloud_release"] = "withhold"
    sent = _seal(_mail(PLAIN + "\n\n" + MONEY))
    assert "$400" not in sent and "withheld" in json.loads(sent)["privacy_gate"]
    assert _cards() == []


def test_an_account_number_is_never_offered(guarded):
    _seal(_mail(PLAIN + "\n\n" + "Pay the invoice to account 12345678 sort code 12-34-56, IBAN GB29NWBK60161331926819."))
    for c in _cards():
        assert "GB29NWBK60161331926819" not in json.dumps(c["payload"])


# ── 2. a run missed while closed: the owner's default, and each job's own ───────

@pytest.fixture
def sched(settings, friday_dir, monkeypatch):
    from agent_friday.services import scheduler as s
    for f in (s.SCHEDULES_FILE, s.RUNS_FILE):
        if f.exists():
            f.unlink()
    s._RUNNING.clear()
    for name in ("_reclaim_expired_lease", "_away_drain_tick", "_meeting_retention_tick"):
        monkeypatch.setattr(s, name, lambda: None)
    monkeypatch.setattr(s, "_deferred_for_quiet", lambda rec, now=None: False)
    yield s
    s._RUNNING.clear()


def _job(s, **extra):
    rec = {"name": "Synthetic job", "trigger": "daily", "spec": {"hour": 7, "minute": 0},
           "task": {"kind": "agent_prompt", "prompt": "Summarise the synthetic notes."}, "created": 0}
    rec.update(extra)
    return s.register_schedule(rec)


def test_a_new_job_takes_the_owners_missed_run_default_and_keeps_its_own_choice(sched, settings):
    s = sched
    assert _job(s)["missed"] == "run_once"
    settings["s"]["schedules"] = {"missed_default": "skip"}
    rec = _job(s)
    assert rec["missed"] == "skip", "the owner's default for a missed run is not a setting"
    row = next(r for r in s.list_schedules() if r["id"] == rec["id"])
    assert row["missed_text"].endswith("skipped until its next time.")
    s.update_schedule(rec["id"], {"missed": "run_once"})
    assert s.get_schedule(rec["id"])["missed"] == "run_once"


# ── 3. a held run retries when its pinned model is back ─────────────────────────

def _wait_for_runs(s, sid, n):
    for _ in range(200):
        hist = s.run_history(sid, limit=10)
        if len(hist) >= n and not s._RUNNING:
            return hist
        time.sleep(0.05)
    raise AssertionError("the runs never finished: %r" % s.run_history(sid, limit=10))


def test_a_run_held_by_its_pinned_model_runs_once_when_the_model_is_back(sched, monkeypatch):
    s = sched
    from agent_friday.services import agent, cloud_intent, local_only_guard, visible_jobs
    spawned = []
    monkeypatch.setattr(agent, "_spawn_task", lambda name, prompt, **kw: spawned.append(
        local_only_guard.pinned_model()) or "task_synthetic")
    monkeypatch.setattr(agent, "_task_snapshot", lambda tid: {"status": "complete", "result": "done"})
    monkeypatch.setattr(visible_jobs, "is_local_model", lambda m, provider=None: False)
    up = {"v": False}
    monkeypatch.setattr(cloud_intent, "_cloud_model_ready", lambda m: (up["v"], "" if up["v"] else "no key"))
    rec = _job(s, task={"kind": "agent_prompt", "prompt": "Synthetic digest.", "model": "claude-synthetic-1"})
    assert rec.get("on_held", "retry") == "retry"
    s.dispatch(s.get_schedule(rec["id"]))
    hist = _wait_for_runs(s, rec["id"], 1)
    assert hist[0]["status"] == "held" and spawned == []
    assert "runs once as soon as the model is back" in hist[0]["summary"]
    s._tick()                                   # still unavailable: nothing runs, nothing moves
    time.sleep(0.2)
    assert spawned == [] and len(s.run_history(rec["id"], limit=10)) == 1
    up["v"] = True
    s._tick()                                   # the model is back: it runs once, on that model
    hist = _wait_for_runs(s, rec["id"], 2)
    assert spawned == ["claude-synthetic-1"], spawned
    assert hist[0]["status"] == "complete" and "pinned model was back" in hist[0]["summary"]
    assert not s.get_schedule(rec["id"]).get("held_retry")
    s._tick()
    time.sleep(0.2)
    assert len(spawned) == 1, "the held run ran more than once"


def test_a_job_set_to_wait_does_not_retry(sched, monkeypatch):
    s = sched
    from agent_friday.services import agent, cloud_intent, visible_jobs
    spawned = []
    monkeypatch.setattr(agent, "_spawn_task", lambda name, prompt, **kw: spawned.append(1) or "t")
    monkeypatch.setattr(agent, "_task_snapshot", lambda tid: {"status": "complete", "result": "done"})
    monkeypatch.setattr(visible_jobs, "is_local_model", lambda m, provider=None: False)
    up = {"v": False}
    monkeypatch.setattr(cloud_intent, "_cloud_model_ready", lambda m: (up["v"], "no key"))
    rec = _job(s, on_held="wait", task={"kind": "agent_prompt", "prompt": "x", "model": "claude-synthetic-1"})
    s.dispatch(s.get_schedule(rec["id"]))
    _wait_for_runs(s, rec["id"], 1)
    up["v"] = True
    s._tick()
    time.sleep(0.2)
    assert spawned == []


# ── 5. trust: the learning rate and Boost/Ban amounts are the owner's ───────────

def test_trust_numbers_are_settings_the_start_stays_half_and_a_change_rescored(settings, tmp_path):
    from agent_friday import source_trust_graph as stg
    cache = getattr(stg, "_PARAMS_CACHE", {})
    cache["value"] = None
    g = stg.SourceTrustGraph(friday_dir=tmp_path)
    for _ in range(3):
        g.observe("synthetic-outlet.example", "correction", "correction_behavior", 1.0)
    g.record_user_action("synthetic-outlet.example", "ban")
    rec = g.get("synthetic-outlet.example")
    assert rec["trust_score"] <= 0.15
    before = rec["scores"]["correction_behavior"]
    settings["s"]["trust_scoring"] = {"prior_strength": 12.0, "ban_ceiling": 0.05}
    cache["value"] = None
    g2 = stg.SourceTrustGraph(friday_dir=tmp_path)
    g2.observe("another.example", "correction", "correction_behavior", 1.0)    # the next pass writes
    data = json.loads((tmp_path / "source_trust.json").read_text(encoding="utf-8"))
    rec = data["sources"]["synthetic-outlet.example"]
    assert rec["trust_score"] <= 0.05, "the owner's Ban amount was not used"
    assert rec["scores"]["correction_behavior"] < before, "a slower learning rate did not move it back toward its start"
    assert (data["meta"].get("rescored") or {}).get("why") == "your trust-scoring settings changed"
    fresh = stg.SourceTrustGraph(friday_dir=tmp_path / "fresh")
    fresh.record_article_seen("new-outlet.example")
    assert fresh.explain("new-outlet.example")["prior"]["strength_observations"] == 12.0


# ── 6. podcasts: solo by default, the co-host's name asked once ─────────────────

def test_podcasts_are_solo_by_default_and_choosing_two_hosts_asks_the_cohost_name_once(settings, monkeypatch):
    from agent_friday.services import podcast_engine as pe
    from agent_friday.core import DEFAULT_SETTINGS
    assert set(pe.RECOMMENDED_FORMAT.values()) == {"solo"}
    assert set(DEFAULT_SETTINGS["podcasts"]["format"].values()) == {"solo"}
    assert pe.format_for("weekly") == "solo"
    # Never renamed: the co-host is not "Agent Friday" beside "Friday".
    neutral = pe.settings()["hosts"]["b"]["name"]
    assert neutral != "Agent Friday" and neutral == getattr(pe, "COHOST_NEUTRAL_NAME", None)
    ask = pe.set_format("weekly", "duo")
    assert ask and neutral in ask and "co-host" in ask, "choosing two hosts did not ask the co-host's name"
    assert pe.set_format("briefing", "duo") == "", "the co-host's name was asked twice"
    assert settings["s"]["podcasts"]["cohost_name_asked"] is True


def test_once_she_is_renamed_the_cohost_suggestion_is_agent_friday(settings):
    from agent_friday.services import podcast_engine as pe
    settings["s"]["agent_name"] = "AGENT VEGA"
    assert pe.settings()["hosts"]["b"]["name"] == "Agent Friday"
    ask = pe.set_format("any", "duo")
    assert ask and "Agent Friday" in ask, "choosing two hosts did not ask the co-host's name"


def test_a_named_cohost_is_not_asked_about(settings):
    from agent_friday.services import podcast_engine as pe
    settings["s"]["podcasts"] = {"hosts": {"b": {"name": "Agent Orion"}}}
    assert not pe.set_format("weekly", "duo")
    assert pe.settings()["hosts"]["b"]["name"] == "Agent Orion"
    settings["s"]["podcasts"] = {}
    assert pe.set_format("weekly", "duo"), "an unnamed co-host was not asked about"


# ── 7. dock by asking: applied at once, with an Undo ────────────────────────────

@pytest.fixture
def dock(tmp_path, monkeypatch, friday_dir):
    from agent_friday.core import _save_settings
    from agent_friday.governance import action_gate as ag
    from agent_friday.services import approvals as ap, desktop_bus, setting_proposals as sp, workspace_registry as wr
    from agent_friday.services import dissent_gate as dg
    desktop_bus.reset()
    monkeypatch.setattr(ap, "APPROVALS_FILE", tmp_path / "approvals.json")
    monkeypatch.setattr(dg, "EVENTS_PATH", tmp_path / "dissent_events.jsonl")
    monkeypatch.setattr(ag, "verify_claws", lambda: (True, "ok"))
    monkeypatch.setattr(sp, "_store", lambda: tmp_path / "setting_changes.json")
    pushed = []
    import agent_friday.notifications_engine as ne
    monkeypatch.setattr(ne, "push", lambda **kw: pushed.append(kw))
    ids = [w["id"] for w in wr.workspaces()]
    _save_settings({"dock_custom": {"order": ids, "hidden": ["trust"]}, "show_all_workspaces": True,
                    "dock_by_asking": "apply_with_undo"})
    yield pushed
    _save_settings({"dock_by_asking": "apply_with_undo"})
    desktop_bus.reset()


def test_showing_a_workspace_by_asking_is_done_at_once_with_an_undo(dock):
    from agent_friday.core import _load_settings
    from agent_friday.services import agent, approvals as ap, setting_proposals as sp
    out = agent._tool_set_setting({"path": "settings.display.dock.trust", "value": "show"})
    assert out.startswith("DOCK_SHOWN"), out
    assert "trust" in sp.dock_state(_load_settings())["visible"], "it waited for a card"
    assert [a for a in ap.list_approvals(status="pending")] == []
    rows = sp.changes("settings.display.dock")
    assert rows and rows[0]["by"] == sp.BY_ASKING and rows[0]["undoable"]
    undo = [a for n in dock for a in (n.get("actions") or []) if a.get("label") == "Undo"]
    assert undo and undo[0]["url"] == "/api/settings/changes/%s/undo" % rows[0]["id"]
    # "Undo that", by voice or chat, puts it back.
    back = agent._tool_set_setting({"path": "settings.display.dock.trust", "op": "undo"})
    assert back.startswith("SETTING_UNDONE"), back
    assert "trust" not in sp.dock_state(_load_settings())["visible"]


def test_the_owner_can_keep_the_card_for_dock_changes(dock):
    from agent_friday.core import _save_settings
    from agent_friday.services import agent
    _save_settings({"dock_by_asking": "ask_first"})
    out = agent._tool_set_setting({"path": "settings.display.dock.trust", "value": "show"})
    assert out.startswith("SETTING_NEEDS_YES"), out


# ── 8. calendar delete: guests told only when ticked ────────────────────────────

class _Exec:
    def __init__(self, fn):
        self.fn = fn

    def execute(self):
        return self.fn()


class _Events:
    def __init__(self):
        self.calls = []

    def get(self, calendarId, eventId):
        return _Exec(lambda: {"id": eventId, "summary": "Synthetic review", "status": "confirmed",
                              "start": {"dateTime": "2026-10-21T10:00:00Z"}, "end": {"dateTime": "2026-10-21T11:00:00Z"},
                              "attendees": [{"email": "a@example.test"}, {"email": "b@example.test"}]})

    def delete(self, calendarId, eventId, **kw):
        self.calls.append(kw)
        return _Exec(lambda: "")


@pytest.fixture
def cal(monkeypatch, settings):
    from agent_friday.services import calendar_write as cw
    ev = _Events()
    svc = type("S", (), {"events": lambda self: ev})()
    monkeypatch.setattr(cw, "write_ready", lambda: (True, None))
    monkeypatch.setattr(cw, "writable_accounts", lambda: [])
    monkeypatch.setattr(cw, "_service", lambda account_id=None: (svc, None))
    monkeypatch.setattr(cw, "_drop_calendar_cache", lambda: None)
    return ev


def test_a_delete_tells_the_guests_only_when_notify_guests_is_ticked(cal, settings):
    from agent_friday.services import agent, calendar_write as cw
    out = cw.delete_event("ev1")
    assert cal.calls[-1].get("sendUpdates") == "none", cal.calls
    assert out["guests_notified"] is False and "not told" in out["guests_told"]
    out = cw.delete_event("ev1", notify_guests=True)
    assert cal.calls[-1].get("sendUpdates") == "all"
    assert out["guests_notified"] is True and "sent a cancellation" in out["guests_told"]
    res = json.loads(agent._tool_delete_calendar_event({"event_id": "ev1", "notify_guests": True}))
    assert res["guests_notified"] is True and cal.calls[-1]["sendUpdates"] == "all"
    # The owner's default for the box is a setting.
    settings["s"]["calendar_delete_notify_guests"] = True
    cw.delete_event("ev1")
    assert cal.calls[-1]["sendUpdates"] == "all"


def test_the_delete_card_shows_the_notify_guests_box(cal):
    from agent_friday.services import agent
    off = agent._taint_title("delete_calendar_event", {"event_id": "ev1"})
    on = agent._taint_title("delete_calendar_event", {"event_id": "ev1", "notify_guests": True})
    assert "[ ] notify guests" in off and "not told" in off
    assert "[x] notify guests" in on and "cancellation" in on
    schema = next(t for t in agent.CLAUDE_TOOLS if t["name"] == "delete_calendar_event")["input_schema"]
    assert schema["properties"]["notify_guests"]["type"] == "boolean"


# ── the first-run question hook ─────────────────────────────────────────────────

def _default_at(dotted):
    from agent_friday.core import DEFAULT_SETTINGS
    cur = DEFAULT_SETTINGS
    for part in dotted.split("."):
        assert isinstance(cur, dict) and part in cur, "%s is not in DEFAULT_SETTINGS" % dotted
        cur = cur[part]
    return cur


def _owner_choices():
    try:
        from agent_friday.services import owner_choices as oc
    except ImportError:
        pytest.fail("there is no list of the owner's standing choices to ask at first run, and nothing checks them")
    return oc


def test_setup_questions_list_the_eight_decisions_each_a_setting_with_a_settings_control(settings):
    oc = _owner_choices()
    qs = oc.setup_questions()
    # The eight, and the default account for drafts (round 3).
    assert len(qs) == 9 and qs[-1]["key"] == "mail_default_account"
    controls = json.loads((ROOT / "tests/unit/settings_controls.json").read_text(encoding="utf-8"))
    control_keys = {k for row in controls if row["status"] != "removed" for k in row["keys"]}
    for q in qs:
        assert set(q) >= {"key", "question", "default", "options", "why", "where"}, q
        assert q["question"].endswith("?") and q["why"]
        _default_at(q["key"])
        assert any(k == q["key"] or k.startswith(q["key"] + ".") for k in control_keys), (
            "%s has no Settings control in the inventory" % q["key"])
    assert next(q for q in qs if q["key"] == "unattended_send_undo_s")["default"] == 120


def test_bad_values_are_refused_by_the_settings_check():
    oc = _owner_choices()
    assert oc.validate({"unattended_send_undo_s": 5})
    assert oc.validate({"unattended_send_undo_s": 601})
    assert oc.validate({"unattended_send_undo_s": 600}) is None
    assert oc.validate({"schedules": {"missed_default": "later"}})
    assert oc.validate({"trust_scoring": {"ban_ceiling": 0.9}})
    assert oc.validate({"dock_by_asking": "sometimes"})
    assert oc.validate({"email_cloud_release": "ask"}) is None


# ── the controls on the page (node harness) ─────────────────────────────────────

def _extract(src, start):
    i = src.index(start)
    depth, j = 0, src.index("{", src.index(")", i) if start.startswith("function") else i)
    for k in range(j, len(src)):
        if src[k] == "{":
            depth += 1
        elif src[k] == "}":
            depth -= 1
            if depth == 0:
                return src[i:k + 1]
    raise AssertionError(start)


@pytest.mark.skipif(not shutil.which("node"), reason="node is not on PATH")
def test_the_cards_render_their_choices_under_node():
    src = (ROOT / "index.html").read_text(encoding="utf-8")
    names = ("function CloudReleaseCard(", "function WfJobChoices(", "function CalDeleteConfirm(")
    missing = [n for n in names if n not in src]
    assert not missing, "the page has no %s" % ", ".join(missing)
    parts = [_extract(src, n) for n in names]
    script = "\n".join([
        "let calls = [];",
        "const React = {createElement: (t, p, ...c) => ({t, p: p || {}, c: c.flat().filter(x => x !== null && x !== false && x !== undefined)})};",
        "const useState = v => [v, () => {}];",
        "const apiFetch = (u, o) => { calls.push([u, o && JSON.parse(o.body || 'null')]); return Promise.resolve({json: () => ({})}); };",
        "const fridayName = () => 'Friday'; const fridayToast = () => {}; const fridayApprovalFeed = {drop() {}};",
        *parts,
        "const text = n => typeof n === 'string' ? n : (n.c || []).map(text).join('');",
        "const all = (n, f, out = []) => { if (n && typeof n === 'object') { if (f(n)) out.push(n); (n.c || []).forEach(x => all(x, f, out)); } return out; };",
        "const card = CloudReleaseCard({a: {approval_id: 'appr_1', title: 'Send 1 paragraph', payload: {handler: 'cloud_release', what: ['The deposit is $400.'], why: 'w', scope: {once: true, thread: 'mail:a:t', sender: 'robin@example.test'}}}});",
        "const buttons = all(card, n => n.t === 'button').map(text);",
        "const ticks = all(card, n => n.t === 'label').map(text);",
        "all(card, n => n.t === 'button')[0].p.onClick();",
        "const wf = WfJobChoices({w: {schedule_id: 'sch_1', missed: 'skip', on_held: 'retry', model_pinned: true}});",
        "const sel = all(wf, n => n.t === 'select');",
        "sel[1].p.onChange({target: {value: 'wait'}});",
        "const del = CalDeleteConfirm({e: {id: 'ev1', title: 'Review', attendees: [1, 2]}});",
        "setTimeout(() => console.log(JSON.stringify({buttons, ticks, quote: all(card, n => n.p['data-testid'] === 'cloud-release-text').map(text),",
        "  selects: sel.map(s => s.p.value), calls, del: text(del)})), 10);",
    ])
    cp = subprocess.run([shutil.which("node"), "-e", script], capture_output=True, text=True, encoding="utf-8", timeout=60)
    assert cp.returncode == 0, cp.stderr[-2000:]
    got = json.loads(cp.stdout)
    assert got["buttons"] == ["Send", "Keep on this PC"]
    assert got["quote"] == ["The deposit is $400."]
    assert any("Remember for this thread" in t for t in got["ticks"])
    assert any("Remember for this sender" in t for t in got["ticks"])
    assert got["calls"][0] == ["/api/privacy/cloud-release/appr_1/decide",
                               {"decision": "approve", "remember": {"thread": False, "sender": False,
                                                                    "text": False}}]
    assert got["selects"][:2] == ["skip", "retry"]
    assert got["calls"][1] == ["/api/schedules/sch_1", {"on_held": "wait"}]
    assert got["del"] == "Delete…"

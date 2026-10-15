"""The first-run conversation (first-run spec, Part A): order, choices, records.

Each test runs against a private Friday home with the settings store, the
hardware check, the schedule store and the model readers replaced, so the
conversation is exercised without a model, a network or the shared settings.
New modules are imported inside the tests, so on a tree without them a test
fails on its assertion about behaviour, not on an import at collection.
"""
from __future__ import annotations

import json
import time

import pytest

from agent_friday.services import setup_chat as sc
from agent_friday.services import setup_chat_copy as copy
from agent_friday.services import setup_profile as profile

#: Routines the store holds on a fresh install (seeded on, as the scheduler does).
_SEED = [
    {"id": "sch_news_morning", "name": "Morning news briefing", "trigger": "daily",
     "spec": {"hour": 7, "minute": 0}, "task": {"kind": "builtin", "ref": "news_morning"},
     "enabled": True},
    {"id": "sch_afternoon_briefing", "name": "Afternoon briefing", "trigger": "daily",
     "spec": {"hour": 16, "minute": 0}, "task": {"kind": "builtin", "ref": "afternoon_briefing"},
     "enabled": True},
    {"id": "sch_heartbeat", "name": "Hourly heartbeat", "trigger": "interval",
     "spec": {"every_minutes": 60}, "task": {"kind": "agent_prompt"}, "enabled": True},
]


@pytest.fixture
def home(tmp_path, monkeypatch):
    import agent_friday.core as core
    from agent_friday.services import scheduler, setup_connections, setup_reader, soul, user_model
    monkeypatch.setenv("FRIDAY_HOME", str(tmp_path))
    monkeypatch.setattr(user_model, "DB_PATH", tmp_path / "user_model.db")
    monkeypatch.setattr(soul, "SOUL_FILE", tmp_path / "SOUL.md")
    monkeypatch.setattr(soul, "HISTORY_DIR", tmp_path / "soul_history")
    monkeypatch.setattr(soul, "FRIDAY_DIR", tmp_path)
    soul._invalidate()
    monkeypatch.setattr(core, "FRIDAY_DIR", tmp_path)
    monkeypatch.setattr(core, "_SETUP_MARKER", tmp_path / ".setup_complete")
    settings = {"model_routing": {"mode": "cloud_only", "ollama_url": "http://127.0.0.1:11434"}}
    saved = []

    def _save(delta, **kw):
        saved.append(dict(delta, _kw=kw))
        for k, v in delta.items():
            if k == "model_routing" and isinstance(v, dict):
                block = dict(settings.get(k) or {})
                block.update(v)
                settings[k] = block
            else:
                settings[k] = v
    monkeypatch.setattr(core, "_save_settings", _save)
    monkeypatch.setattr(core, "_load_settings", lambda: json.loads(json.dumps(settings)))
    monkeypatch.setattr(setup_reader, "local_model", lambda: None)
    monkeypatch.setattr(setup_reader, "cloud_model", lambda: None)
    monkeypatch.setattr(setup_connections, "connected_labels", lambda: [])
    monkeypatch.setattr(sc, "RESEARCH_ENGINE", None)
    monkeypatch.setattr(sc, "RESEARCH_SPAWN", None)
    store = tmp_path / "schedules.json"
    store.write_text(json.dumps(_SEED), encoding="utf-8")
    monkeypatch.setattr(scheduler, "SCHEDULES_FILE", store)
    yield {"path": tmp_path, "settings": settings, "saved": saved, "store": store}
    soul._invalidate()


@pytest.fixture
def steps(monkeypatch):
    try:
        from agent_friday.services import setup_steps
    except ImportError:
        # A tree without the conversation's steps: each test then fails on
        # what the conversation does, not on this import.
        from types import SimpleNamespace
        return SimpleNamespace(about_lines=lambda: [])
    monkeypatch.setattr(setup_steps, "INSTALLED_VOICES", lambda: ["af_heart", "am_michael"])
    monkeypatch.setattr(setup_steps, "CAPABILITY", lambda: {
        "capable": False, "roles": {"text": {"ok": False}}, "chain_why": None})
    return setup_steps


def _texts():
    return [e["text"] for e in profile.load_transcript() if e["role"] == "friday"]


def _consents(monkeypatch):
    from agent_friday.privacy import cloud_consent
    got = []

    def _record(choice, **kw):
        if choice == "local_private":
            raise cloud_consent.ConsentRejected("this machine cannot run local models well enough")
        got.append(choice)
        return {"answered": True, "choice": choice}
    monkeypatch.setattr(cloud_consent, "record_consent", _record)
    return got


def _walk(to, **answers):
    """Ordinary answers up to (not including) the step `to`."""
    plan = [("welcome", "type", ""), ("offer", "none", ""), ("agent_name", "Friday", ""),
            ("voice", "later", ""), ("pronouns", "skip", ""), ("framing", "skip", ""),
            ("about_name", None, "Sam"), ("about_city", "skip", ""), ("about_tz", "skip", ""),
            ("basics", {"minor_mode": False, "distribution": "default"}, ""),
            ("posture", "skip", ""), ("never_send", "none", ""),
            ("connect", "done", ""), ("knowledge", "skip", ""), ("reader", "ok", "")]
    sc.begin(answers.get("routing", "cloud_only"), "")
    for stage, value, text in plan:
        if sc.load_state()["stage"] == to:
            return
        value, text = answers.get(stage, (value, text))
        sc.answer(stage, value, text)
    if sc.load_state()["stage"] != to:
        for _ in copy.QUESTIONS:
            if sc.load_state()["stage"] != "questions":
                break
            sc.answer("questions", "skip", "")
        if sc.load_state()["stage"] == "style":
            sc.answer("style", {"action": "skip"}, "")
        for stage, value in (("money", "skip"), ("scheduled_cloud", "skip")):
            if sc.load_state()["stage"] == stage and stage != to:
                sc.answer(stage, value, "")
    assert sc.load_state()["stage"] == to, sc.load_state()["stage"]


# ── the order and the opening ───────────────────────────────────────────────

def test_the_conversation_runs_in_the_spec_order():
    order = list(copy.STAGES)
    want = ["welcome", "offer", "agent_name", "voice", "pronouns", "framing",
            "about_name", "about_city", "about_tz", "posture", "never_send", "connect"]
    assert [s for s in order if s in want] == want
    assert order.index("money") < order.index("routines") < order.index("memory_rules") \
        < order.index("household") < order.index("finish")
    for stage in order:
        if stage != "done":
            assert stage in sc._HANDLERS, stage


def test_the_six_promises_come_first_then_talk_or_type(home):
    v = sc.begin("cloud_only", "")
    first = v["transcript"][0]["text"]
    for line in ("Nothing about you goes to the people who made me. Ever.",
                 "You decide where I think", "I ask before I send, spend or delete anything.",
                 "change or delete it", "I'll remember where we were.",
                 "I'm not a lawyer or a financial adviser"):
        assert line in first
    assert "about 20 minutes" in first and first.rstrip().endswith("You can switch any time.")
    chips = v["prompt"]["chips"]
    assert [c["value"] for c in chips] == ["talk", "type", "screen_reader"]
    assert not any(c.get("recommended") for c in chips)


def test_talk_or_type_accepts_the_spoken_words_and_refuses_anything_else(home):
    sc.begin("cloud_only", "")
    with pytest.raises(sc.Refused):
        sc.answer("welcome", None, "purple")
    v = sc.answer("welcome", None, "I'd rather talk")
    assert v["talk"] == "talk" and not v["screen_reader"]
    assert copy.TALK_OK in _texts() and v["stage"] == "offer"


def test_a_screen_reader_turns_her_speech_off(home):
    sc.begin("cloud_only", "")
    v = sc.answer("welcome", "screen_reader", "")
    assert v["screen_reader"] is True and v["talk"] == "type"
    assert copy.SCREEN_READER_OK in _texts()


def test_the_offer_is_the_inbox_now_and_names_the_other_two_as_coming(home):
    _walk("offer")
    v = sc.view()
    assert [c["value"] for c in v["prompt"]["chips"]] == ["inbox", "none"]
    assert "Finding you money and taking back your data are coming" in copy.OFFER
    v = sc.answer("offer", "inbox", "")
    assert v["offer"] == {"choice": "inbox", "state": "waiting_for_email"}
    assert "Nothing is unsubscribed until you tick it" in copy.OFFER_INBOX


def test_the_inbox_offer_starts_once_connections_are_done_never_before(home, steps, monkeypatch):
    calls = []
    monkeypatch.setattr(sc, "OFFER_STARTER", lambda st: calls.append(st["stage"]) or
                        {"state": "running", "text": "Started on your inbox."})
    _walk("connect", offer=("inbox", ""))
    assert calls == [], "not before her identity, the posture and the lock exist"
    sc.answer("connect", "done", "")
    assert calls == ["connect"] and sc.load_state()["offer_state"] == "running"
    assert "Started on your inbox." in _texts()


# ── her identity ─────────────────────────────────────────────────────────────

def test_her_pronouns_are_asked_never_taken_from_the_voice(home, steps):
    _walk("voice")
    sc.answer("voice", "am_michael", "")
    assert home["settings"]["local_voice_kokoro_voice"] == "am_michael"
    v = sc.view()
    assert v["stage"] == "pronouns"
    assert not any(c.get("recommended") for c in v["prompt"]["chips"])
    sc.answer("pronouns", "skip", "")
    # A male voice chosen, nothing said about pronouns: her name, not "he".
    assert home["settings"]["identity"]["pronouns"] == "name_only"


def test_he_typed_is_he_and_something_else_uses_her_name(home, steps):
    _walk("pronouns")
    sc.answer("pronouns", None, "he")
    assert home["settings"]["identity"]["pronouns"] == "he/him"
    sc.goto("pronouns")
    sc.answer("pronouns", None, "xe/xem")
    assert home["settings"]["identity"]["pronouns"] == "name_only"
    assert copy.PRONOUNS_OTHER in _texts()


# ── who you are ──────────────────────────────────────────────────────────────

def test_about_you_writes_visible_lines_and_shields_the_name(home, steps):
    from agent_friday.services import privacy_shield_store as ps
    _walk("about_name")
    sc.answer("about_name", None, "Sam")
    sc.answer("about_city", None, "Springfield, IL", client_tz="America/Chicago")
    v = sc.view()
    assert v["stage"] == "about_tz" and "Chicago time (America/Chicago)" in _texts()[-1]
    v = sc.answer("about_tz", "yes", "")
    lines = [a["text"] for a in sc.view()["about"] or steps.about_lines()]
    assert lines == ["Goes by Sam", "Lives in Springfield, Illinois", "Time zone: America/Chicago"]
    assert home["settings"]["timezone"] == "America/Chicago"
    assert "Sam" in ps.entries("watchlist") and ps.source_of("watchlist", "Sam") == "setup"
    # the egress scrubber reads the same file
    import agent_friday.core as core
    assert "Sam" in core._load_privacy_watchlist()


def test_a_line_on_the_about_card_can_be_deleted(home, steps):
    _walk("about_name")
    sc.answer("about_name", None, "Sam")
    line = steps.about_lines()[0]
    out = sc.forget_about_line(line["id"])
    assert out["ok"] and out["about"] == []


def test_the_time_zone_is_offered_never_assumed(home, steps):
    _walk("about_city")
    sc.answer("about_city", "skip", "", client_tz="Europe/Paris")
    sc.answer("about_tz", "no", "")
    assert "timezone" not in home["settings"]
    with pytest.raises(sc.Refused):
        sc.answer("about_tz", None, "Mars/Olympus")
    sc.answer("about_tz", None, "Europe/London")
    assert home["settings"]["timezone"] == "Europe/London"


def test_schedules_run_in_the_owners_time_zone(home):
    from agent_friday.services import scheduler
    home["settings"]["timezone"] = "Asia/Tokyo"
    assert str(scheduler._now_central().tzinfo) == "Asia/Tokyo"
    home["settings"]["timezone"] = "Not/AZone"
    assert str(scheduler._now_central().tzinfo) == "America/Chicago"


# ── where I may think ────────────────────────────────────────────────────────

def test_three_postures_nothing_preselected_recommended_labelled(home, steps, monkeypatch):
    _consents(monkeypatch)
    _walk("posture")
    p = sc.view()["prompt"]
    cards = p["posture"]["cards"]
    assert [c["value"] for c in cards] == ["local", "public", "everything"]
    assert [c["recommended"] for c in cards] == [False, True, False]
    local = cards[0]
    assert local["available"] is False and "thinking" in local["reason"]
    assert "This PC can't run my thinking model" in cards[1]["means"]
    assert not any(c.get("selected") or c.get("checked") for c in p["chips"])
    assert [c["value"] for c in p["chips"]] == ["public", "everything", "skip"]
    assert p["chips"][0]["label"].endswith(copy.RECOMMENDED_SUFFIX)


def test_one_posture_answer_writes_consent_routing_and_scheduled_jobs(home, steps, monkeypatch):
    got = _consents(monkeypatch)
    _walk("posture")
    sc.answer("posture", None, "cloud for public things")
    assert got == ["cloud_guarded"]
    routing = [d for d in home["saved"] if "model_routing" in d]
    assert routing[-1]["model_routing"] == {"mode": "cloud_only"}
    assert routing[-1]["_kw"] == {"owner_routing_change": True}
    assert home["settings"]["scheduled_cloud"]["allow"] is True
    st = sc.load_state()
    assert st["posture"] == "public" and st["routing_mode"] == "cloud_only"


def test_only_on_this_pc_is_refused_on_a_pc_that_cannot(home, steps, monkeypatch):
    got = _consents(monkeypatch)
    _walk("posture")
    with pytest.raises(sc.Refused) as e:
        sc.answer("posture", "local", "")
    assert e.value.payload["error"] == "posture_unavailable" and got == []
    assert sc.load_state()["stage"] == "posture"


def test_the_never_send_list_reaches_the_floor_the_egress_gate_reads(home, steps):
    from agent_friday.services import judgment_gate
    _walk("never_send")
    sc.answer("never_send", None, "Project Bluebird, Acme Holdings\nmy sister's clinic")
    toks = judgment_gate.never_send_tokens()
    for t in ("Project Bluebird", "Acme Holdings", "my sister's clinic"):
        assert t in toks
    assert judgment_gate.never_send_hits("notes on project bluebird")


# ── money and routines ───────────────────────────────────────────────────────

def test_money_is_offered_with_ten_labelled_and_nothing_capped_by_default(home, steps):
    _walk("money")
    chips = sc.view()["prompt"]["chips"]
    rec = [c for c in chips if c.get("recommended")]
    assert [c["value"] for c in rec] == ["10"]
    sc.answer("money", "skip", "")
    assert "cost_budget" not in home["settings"]


def test_a_typed_amount_turns_the_monthly_stop_on(home, steps):
    _walk("money")
    sc.answer("money", None, "$12 a month")
    cb = home["settings"]["cost_budget"]
    assert cb["hard_stop_monthly"] == 12.0 and cb["hard_stop_monthly_enabled"] is True
    assert home["settings"]["spending_caps"]["asked"] is True


def test_money_is_not_asked_when_everything_stays_on_this_pc(home, steps):
    _walk("routines", routing="local_only")
    assert not [e for e in profile.load_transcript() if e.get("stage") == "money"]


def test_routines_start_unticked_and_only_ticked_ones_run(home, steps):
    from agent_friday.services import scheduler
    _walk("routines")
    p = sc.view()["prompt"]
    ids = [r["id"] for r in p["routines"]]
    assert "sch_news_morning" in ids and "podcast" in ids
    sc.answer("routines", {"routines": {"sch_news_morning": {"on": True, "time": "06:30"}}}, "")
    recs = {r["id"]: r for r in scheduler.list_schedules()}
    assert recs["sch_news_morning"]["enabled"] is True
    assert recs["sch_news_morning"]["spec"] == {"hour": 6, "minute": 30}
    assert recs["sch_heartbeat"]["enabled"] is False
    assert recs["sch_afternoon_briefing"]["enabled"] is False
    assert home["settings"]["podcasts"]["enabled_for_routines"]["front_page"] is False


def test_set_up_later_on_a_first_setup_leaves_nothing_scheduled(home, steps):
    from agent_friday.services import scheduler
    sc.begin("cloud_only", "")
    sc.skip_all()
    assert not [r for r in scheduler.list_schedules() if r["enabled"]]


# ── the record, recommended answers, resuming, the receipt ──────────────────

def test_every_choice_is_recorded_chained_and_without_the_answers(home, steps, monkeypatch):
    from agent_friday.services import setup_record
    _consents(monkeypatch)
    _walk("money", posture=("public", ""), about_city=(None, "Springfield, IL"))
    entries = setup_record.entries()
    steps_seen = [e["step"] for e in entries]
    for s in ("welcome", "offer", "agent_name", "about_name", "about_city", "posture"):
        assert s in steps_seen
    raw = setup_record.ledger_path().read_text(encoding="utf-8")
    assert "Sam" not in raw and "Springfield" not in raw
    assert setup_record.verify()["ok"] is True
    lines = raw.splitlines()
    rec = json.loads(lines[1])
    rec["decision"] = "tampered"
    lines[1] = json.dumps(rec, sort_keys=True)
    setup_record.ledger_path().write_text("\n".join(lines) + "\n", encoding="utf-8")
    out = setup_record.verify()
    assert out["ok"] is False and out["broken_at"] == 2


def test_use_recommended_fills_and_says_so_and_is_recorded_as_such(home, steps, monkeypatch):
    from agent_friday.services import setup_record
    got = _consents(monkeypatch)
    _walk("posture")
    assert sc.view()["prompt"]["recommended"] is True
    v = sc.use_recommended()
    assert got == ["cloud_guarded"] and v["stage"] == "never_send"
    assert "I used the recommended answer for: Privacy." in _texts()
    assert setup_record.latest_by_step()["posture"]["how"] == "recommended"
    with pytest.raises(sc.Refused):
        sc.use_recommended()                       # never-send has no recommendation


def test_reopening_after_a_long_break_says_where_we_got_to_once(home, steps):
    _walk("about_name")
    st = json.loads(sc.state_path().read_text(encoding="utf-8"))
    st["updated"] = time.time() - 2 * sc.RESUME_AFTER_S
    sc.state_path().write_text(json.dumps(st), encoding="utf-8")   # a long break
    v = sc.view()
    assert v["transcript"][-1]["text"].startswith('We got as far as "You"')
    n = len(v["transcript"])
    assert len(sc.view()["transcript"]) == n


def test_the_receipt_says_who_decided_each_line(home, steps, monkeypatch):
    _consents(monkeypatch)
    _walk("money", posture=("public", ""))
    sc.answer("money", "10", "")
    while sc.load_state()["stage"] != "finish":
        stage = sc.load_state()["stage"]
        sc.answer(stage, {"routines": {}} if stage == "routines" else
                  ("ok" if stage == "memory_rules" else "skip"), "")
    rows = {r["label"]: r for r in sc.view()["receipt"]}
    assert rows["Where I think"]["value"] == "Cloud for public things"
    assert rows["Where I think"]["how"] == "you chose"
    assert rows["Monthly ceiling"]["value"] == "$10.00 a month"
    assert rows["My pronouns"]["how"] == "default because skipped"
    assert rows["Routines"]["value"] == "none scheduled"

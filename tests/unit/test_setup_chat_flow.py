"""The setup chat's state machine: stages, skipping, resuming, finishing.

Each test runs against a private Friday home (FRIDAY_HOME) with the model
readers, the connection checklist and the settings writer replaced, so the
flow is exercised without a model, a network or the shared settings file.
"""
from __future__ import annotations

import json

import pytest

from agent_friday.services import setup_chat as sc
from agent_friday.services import setup_chat_copy as copy
from agent_friday.services import setup_profile as profile


@pytest.fixture
def home(tmp_path, monkeypatch):
    import agent_friday.core as core
    from agent_friday.services import setup_connections, setup_reader, soul
    monkeypatch.setenv("FRIDAY_HOME", str(tmp_path))
    monkeypatch.setattr(soul, "SOUL_FILE", tmp_path / "SOUL.md")
    monkeypatch.setattr(soul, "HISTORY_DIR", tmp_path / "soul_history")
    monkeypatch.setattr(soul, "FRIDAY_DIR", tmp_path)
    soul._invalidate()
    monkeypatch.setattr(core, "FRIDAY_DIR", tmp_path)
    monkeypatch.setattr(core, "_SETUP_MARKER", tmp_path / ".setup_complete")
    saved = []
    monkeypatch.setattr(core, "_save_settings", lambda delta: saved.append(dict(delta)))
    monkeypatch.setattr(core, "_load_settings",
                        lambda: {"model_routing": {"mode": "cloud_only",
                                                   "ollama_url": "http://127.0.0.1:11434"}})
    monkeypatch.setattr(setup_reader, "local_model", lambda: None)
    monkeypatch.setattr(setup_reader, "cloud_model", lambda: None)
    monkeypatch.setattr(setup_connections, "connected_labels", lambda: [])
    monkeypatch.setattr(sc, "RESEARCH_ENGINE", None)
    monkeypatch.setattr(sc, "RESEARCH_SPAWN", None)
    yield {"path": tmp_path, "settings": saved}
    soul._invalidate()


def _walk_to(stage, **kw):
    """Drive a fresh chat to `stage` with ordinary answers."""
    sc.begin(kw.get("routing", "local_only"), "")
    steps = [("welcome", None, "Sam"), ("agent_name", "Friday", ""),
             ("basics", {"minor_mode": False, "distribution": "default"}, ""),
             ("connect", "done", ""), ("reader", "ok", "")]
    for s, v, t in steps:
        if sc.load_state()["stage"] == stage:
            return
        sc.answer(s, v, t)
    assert sc.load_state()["stage"] == stage, sc.load_state()["stage"]


def test_fresh_install_starts_before_consent_with_nothing_said(home):
    v = sc.view()
    assert v["stage"] == "welcome" and v["consent_done"] is False
    assert v["transcript"] == []


def test_begin_opens_the_chat_and_keeps_the_passphrase_out_of_the_state(home, monkeypatch):
    from agent_friday.services import setup_complete
    stored = []
    monkeypatch.setattr(setup_complete, "store_vault_passphrase",
                        lambda p: stored.append(p) or True)
    v = sc.begin("local_preferred", "correct horse battery staple")
    assert v["consent_done"] and v["routing_mode"] == "local_preferred"
    assert v["transcript"][0]["text"] == copy.WELCOME
    assert stored == ["correct horse battery staple"]
    assert "correct horse" not in sc.state_path().read_text(encoding="utf-8")


def test_the_stages_advance_in_order_to_the_questions(home):
    _walk_to("questions")
    st = sc.load_state()
    assert st["agent_name"] == "Friday"
    assert profile.load_profile()["name"] == "Sam"
    # No model on this machine: research is not offered, and the chat says so.
    texts = [e["text"] for e in profile.load_transcript()]
    assert copy.RESEARCH_NO_MODEL in texts
    assert st["research"]["state"] == "unavailable"


def test_an_answer_for_the_wrong_stage_is_a_conflict_not_a_jump(home):
    sc.begin("local_only", "")
    with pytest.raises(sc.Conflict):
        sc.answer("questions", None, "Casual")
    assert sc.load_state()["stage"] == "welcome"


def test_resume_after_a_restart_returns_the_same_stage_and_transcript(home):
    _walk_to("connect")
    before = sc.view()
    # A restart is a fresh read of the same files.
    after = sc.view()
    assert after["stage"] == "connect"
    assert [e["text"] for e in after["transcript"]] == [e["text"] for e in before["transcript"]]


def test_the_transcript_is_encrypted_at_rest(home):
    _walk_to("agent_name")
    raw = profile.transcript_path().read_bytes()
    assert b"Sam" not in raw and b"what should I call you" not in raw


def test_every_question_can_be_skipped_by_typing_skip(home):
    _walk_to("questions")
    for _ in copy.QUESTIONS:
        sc.answer("questions", None, "skip")
    p = profile.load_profile()
    assert all(a["skipped"] for a in p["answers"].values())
    assert sc.load_state()["stage"] == "style"
    assert sc.load_state()["questions_skipped"] is True


def test_the_mother_question_is_last_and_exact(home):
    _walk_to("questions")
    for i, (qid, *_rest) in enumerate(copy.QUESTIONS[:-1]):
        sc.answer("questions", "skip", "")
    v = sc.view()
    assert v["prompt"]["question"] == "mother"
    last_two = [e["text"] for e in v["transcript"][-2:]]
    assert last_two[0] == copy.MOTHER_LEAD_IN
    assert last_two[1] == "What was the relationship with your mother like?"
    assert "Freud" in copy.MOTHER_LEAD_IN
    labels = [c["label"] for c in v["prompt"]["chips"]]
    assert copy.RATHER_NOT in labels and copy.SKIP in labels


def test_answers_become_a_style_and_saving_writes_the_block(home):
    _walk_to("questions")
    for qid, _q, chips, _ph in copy.QUESTIONS:
        if qid == "address":
            sc.answer("questions", "Casual", "")
        elif qid == "detail":
            sc.answer("questions", "Just the answer", "")
        else:
            sc.answer("questions", "skip", "")
    style = profile.load_profile()["style"]
    assert style["sliders"]["formality"] <= 25 and style["sliders"]["verbosity"] <= 20
    assert style["by"]["kind"] == "rules"
    sc.answer("style", {"action": "save"}, "")
    from agent_friday.services import soul
    assert profile.BLOCK_START in soul.load_soul()
    assert sc.load_state()["stage"] == "finish"
    assert {"response_length": "concise", "communication_style": "casual"}.items() \
        <= home["settings"][-1].items()


def test_personality_json_is_written_only_when_setup_finishes(home):
    pfile = home["path"] / "personality.json"
    _walk_to("questions")
    for _ in copy.QUESTIONS:
        sc.answer("questions", "skip", "")
    sc.answer("style", {"action": "skip"}, "")
    assert sc.load_state()["stage"] == "finish"
    assert not pfile.exists(), "personality.json ends first-run setup early"
    assert not (home["path"] / ".setup_complete").exists()
    sc.answer("finish", "finish", "")
    assert pfile.exists()
    assert (home["path"] / ".setup_complete").exists()
    assert sc.load_state()["completed"] is True


def test_finishing_saves_the_routing_mode_the_user_chose(home):
    _walk_to("questions", routing="local_only")
    sc.skip_all()
    final = home["settings"][-1]
    assert final["setup_complete"] is True
    assert final["model_routing"]["mode"] == "local_only"
    # Merged, not replaced: the rest of the block survives.
    assert final["model_routing"]["ollama_url"] == "http://127.0.0.1:11434"


def test_set_up_later_works_from_the_very_first_stage(home):
    sc.begin("cloud_only", "")
    v = sc.skip_all()
    assert v["completed"] and v["stage"] == "done"
    assert (home["path"] / ".setup_complete").exists()
    assert home["settings"][-1]["agent_name"] == "Friday"


def test_a_key_typed_into_the_chat_is_refused_and_never_stored(home):
    sc.begin("cloud_only", "")
    fake = "sk-" + "ant-" + "a1B2c3D4e5F6g7H8i9J0kLmNoPqRsTuV"  # pragma: allowlist secret
    with pytest.raises(sc.Refused) as e:
        sc.answer("welcome", None, fake)
    assert e.value.payload["error"] == "key_shaped"
    assert e.value.payload["shape"]["target"] == "provider:anthropic"
    assert fake not in json.dumps(e.value.payload)
    assert sc.load_state()["stage"] == "welcome"
    assert all(fake not in (x.get("text") or "") for x in profile.load_transcript())
    assert fake not in sc.state_path().read_text(encoding="utf-8")


def test_rerun_keeps_choices_and_starts_again(home):
    _walk_to("questions")
    sc.skip_all()
    sc.skip_connection("provider:openai")
    v = sc.rerun()
    assert v["rerun"] and not v["completed"] and v["stage"] == "welcome"
    assert v["transcript"][0]["text"] == copy.WELCOME_BACK
    st = sc.load_state()
    assert st["skipped_connections"] == ["provider:openai"]
    assert st["routing_mode"] == "local_only"


def test_goto_revisits_a_finished_stage_but_cannot_jump_ahead(home):
    _walk_to("connect")
    with pytest.raises(sc.Conflict):
        sc.goto("style")
    v = sc.goto("agent_name")
    assert v["stage"] == "agent_name"


def test_a_local_model_is_named_before_any_personal_question(home, monkeypatch):
    from agent_friday.services import setup_reader
    monkeypatch.setattr(setup_reader, "local_model", lambda: "gemma-test:4b")
    _walk_to("research_ask")
    said = [e["text"] for e in profile.load_transcript()]
    assert copy.READER_LOCAL.format(model="gemma-test:4b") in said
    assert sc.load_state()["reader"] == {"kind": "local", "model": "gemma-test:4b",
                                         "provider": "this computer"}


def test_a_cloud_reader_needs_the_cloud_mode_and_an_explicit_yes(home, monkeypatch):
    from agent_friday.services import setup_reader
    monkeypatch.setattr(setup_reader, "cloud_model",
                        lambda: {"model": "claude-test", "provider": "Anthropic"})
    # local_only never offers the cloud.
    assert setup_reader.options("local_only")["cloud"] is None
    # cloud mode offers it, but "keep it here" means rules.
    sc.begin("cloud_only", "")
    for s, v, t in [("welcome", "skip", ""), ("agent_name", "Friday", ""),
                    ("basics", {}, ""), ("connect", "done", "")]:
        sc.answer(s, v, t)
    chips = [c["value"] for c in sc.view()["prompt"]["chips"]]
    assert chips == ["cloud", "rules"]
    sc.answer("reader", "rules", "")
    assert sc.load_state()["reader"]["kind"] == "rules"


def test_declining_research_starts_nothing(home, monkeypatch):
    from agent_friday.services import setup_reader, setup_research
    monkeypatch.setattr(setup_reader, "local_model", lambda: "gemma-test:4b")
    started = []
    monkeypatch.setattr(setup_research, "start", lambda *a, **k: started.append(1))
    _walk_to("research_ask")
    sc.answer("research_ask", "no", "")
    assert not started
    assert sc.load_state()["research"]["state"] == "declined"
    assert sc.load_state()["stage"] == "questions"


def test_accepting_research_runs_it_as_a_task_and_goes_on_to_questions(home, monkeypatch):
    from agent_friday.services import setup_reader
    monkeypatch.setattr(setup_reader, "local_model", lambda: "gemma-test:4b")
    spawned = []
    monkeypatch.setattr(sc, "RESEARCH_SPAWN",
                        lambda name, prompt, runner: spawned.append(name) or "task-1")
    _walk_to("research_ask")
    sc.answer("research_ask", "yes", "")
    assert sc.load_state()["stage"] == "research_seeds"
    sc.answer("research_seeds", {"name": "Sam Example", "handles": "@samex",
                                 "sites": "example.org", "employer": "Acme"}, "")
    st = sc.load_state()
    assert st["stage"] == "questions"
    assert st["research"]["state"] == "running" and st["research"]["task_id"] == "task-1"
    assert spawned == ["Looking you up on the public web"]

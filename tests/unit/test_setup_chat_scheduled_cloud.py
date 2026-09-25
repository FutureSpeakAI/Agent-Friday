"""The setup chat's scheduled-jobs question: asked once, only without a local model.

On a PC with no local model the built-in jobs are paused. The setup chat asks,
in plain words and with the estimated monthly cost, whether they may use a
cloud model instead. Yes and No are saved to `scheduled_cloud`; Skip leaves
it unanswered. With a local model the question never appears.
"""
from __future__ import annotations

import pytest

from agent_friday.services import setup_chat as sc
from agent_friday.services import setup_chat_copy as copy


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
    settings = {"model_routing": {"mode": "cloud_only"}}
    saved = []

    def _save(delta):
        saved.append(dict(delta))
        settings.update(delta)
    monkeypatch.setattr(core, "_save_settings", _save)
    monkeypatch.setattr(core, "_load_settings", lambda: dict(settings))
    monkeypatch.setattr(setup_reader, "local_model", lambda: None)
    monkeypatch.setattr(setup_reader, "cloud_model", lambda: None)
    monkeypatch.setattr(setup_connections, "connected_labels", lambda: [])
    monkeypatch.setattr(sc, "RESEARCH_ENGINE", None)
    monkeypatch.setattr(sc, "RESEARCH_SPAWN", None)
    yield {"saved": saved, "settings": settings}
    soul._invalidate()


def _through_style(routing="cloud_only"):
    sc.begin(routing, "")
    for s, v, t in [("welcome", None, "Sam"), ("agent_name", "Friday", ""),
                    ("basics", {"minor_mode": False, "distribution": "default"}, ""),
                    ("connect", "done", ""), ("reader", "ok", "")]:
        sc.answer(s, v, t)
    if sc.load_state()["stage"] == "research_ask":       # a model can read
        sc.answer("research_ask", "no", "")
    for _ in copy.QUESTIONS:
        sc.answer("questions", "skip", "")
    sc.answer("style", {"action": "skip"}, "")
    return sc.load_state()["stage"]


def _answers(home):
    return [d["scheduled_cloud"] for d in home["saved"] if "scheduled_cloud" in d]


def test_without_a_local_model_the_question_is_asked_with_the_cost(home):
    assert _through_style() == "scheduled_cloud"
    v = sc.view()
    question = v["transcript"][-1]["text"]
    for job in ("Morning news", "Evening front page", "Afternoon briefing",
                "Daily creation", "Heartbeat"):
        assert job in question
    assert "Claude Haiku 4.5" in question
    assert "a month in total" in question and "$" in question
    assert "Settings > Spending" in question
    assert [c["value"] for c in v["prompt"]["chips"]] == ["yes", "no", "skip"]
    assert v["groups"][-1]["state"] == "current"


def test_with_a_local_model_the_question_never_appears(home, monkeypatch):
    from agent_friday.services import setup_reader
    monkeypatch.setattr(setup_reader, "local_model", lambda: "gemma4:26b")
    assert _through_style() == "finish"
    assert not any(e.get("stage") == "scheduled_cloud"
                   for e in sc.view()["transcript"])


def test_local_only_routing_already_answers_it(home):
    assert _through_style(routing="local_only") == "finish"


def test_an_existing_answer_is_not_asked_again(home):
    home["settings"]["scheduled_cloud"] = {"answered": True, "allow": False}
    assert _through_style() == "finish"


def test_yes_is_saved_and_the_chat_moves_to_finish(home):
    _through_style()
    sc.answer("scheduled_cloud", "yes", "")
    assert sc.load_state()["stage"] == "finish"
    ans = _answers(home)[-1]
    assert ans["answered"] is True and ans["allow"] is True and ans["at"]
    texts = [e["text"] for e in sc.view()["transcript"]]
    assert copy.SCHEDULED_CLOUD_YES in texts


def test_no_is_saved_as_an_answer(home):
    _through_style()
    sc.answer("scheduled_cloud", "no", "")
    ans = _answers(home)[-1]
    assert ans["answered"] is True and ans["allow"] is False


def test_skip_leaves_it_unanswered(home):
    _through_style()
    sc.answer("scheduled_cloud", "skip", "")
    assert sc.load_state()["stage"] == "finish"
    assert _answers(home) == []


def test_the_stage_resumes_where_it_was(home):
    _through_style()
    n = len(sc.view()["transcript"])
    v = sc.view()                   # reopening the app
    assert v["stage"] == "scheduled_cloud" and len(v["transcript"]) == n


def test_set_up_later_from_this_stage_leaves_it_unanswered(home):
    _through_style()
    v = sc.skip_all()
    assert v["completed"] is True
    assert _answers(home) == []


def test_the_rail_cannot_open_it_on_a_machine_that_does_not_need_it(home, monkeypatch):
    from agent_friday.services import setup_reader
    _through_style()
    sc.answer("scheduled_cloud", "skip", "")
    monkeypatch.setattr(setup_reader, "local_model", lambda: "gemma4:26b")
    assert sc.goto("scheduled_cloud")["stage"] == "finish"

"""The release lead's review of feat/ternary-bonsai-voice at 2c646e7f
(ftv/program/release/bonsai_review_2c646e7f.md), one test per finding. Each
fails on 2c646e7f and passes on the fix.
"""
import json
import threading
import time
from pathlib import Path

import pytest

from agent_friday.services import laya_router as lr
from agent_friday.services import voice_front as vf

BONSAI, Q4, Q17 = "ternary-bonsai:1.7b", "qwen3-4b-instruct-2507", "qwen3-1.7b"


@pytest.fixture
def machine(monkeypatch, tmp_path):
    """Which front files are on disk, and whether the PrismML runtime is."""
    state = {"files": set(), "runtime": True}
    monkeypatch.setattr(vf, "installed", lambda m: m in vf.FRONT_MODELS and m in state["files"])
    from agent_friday.services import model_download as md
    monkeypatch.setattr(md, "runtime_binary",
                        lambda name="prism-fork": (tmp_path / "llama-server.exe") if state["runtime"] else None)
    return state


# ── 1 HIGH: an upgrader keeps the front they have ───────────────────────────

def test_an_upgrade_without_a_saved_choice_keeps_the_installed_qwen_front(machine):
    from agent_friday import core
    machine["files"] = {Q4}
    settings = {"voice_front_model": core.DEFAULT_SETTINGS["voice_front_model"]}
    assert vf.resolve(settings) == (Q4, "")
    assert vf.resolve({}) == (Q4, "")


def test_automatic_prefers_bonsai_when_it_can_run(machine):
    machine["files"] = {BONSAI, Q4}
    assert vf.resolve({"voice_front_model": "auto"}) == (BONSAI, "")


# ── 2 HIGH: Bonsai without the PrismML runtime falls back, visibly ──────────

def test_a_chosen_bonsai_without_its_runtime_falls_back_and_says_so(machine):
    machine["files"], machine["runtime"] = {BONSAI, Q17}, False
    model, notice = vf.resolve({"voice_front_model": BONSAI})
    assert model == Q17
    assert "PrismML runtime" in notice and "Qwen3-1.7B is answering" in notice


def test_bonsai_alone_without_its_runtime_hands_to_the_brain_and_says_so(machine):
    machine["files"], machine["runtime"] = {BONSAI}, False
    model, notice = vf.resolve({"voice_front_model": "auto"})
    assert model is None and "PrismML runtime" in notice and "main model" in notice


def test_arming_tells_the_owner_about_the_fallback(machine, monkeypatch):
    import agent_friday.routes.voice as rv
    machine["files"], machine["runtime"] = {BONSAI}, False
    said = []
    assert rv._arm_voice_front({"voice_front_model": BONSAI}, progress=said.append) is None
    assert said and "PrismML runtime" in said[0]


def test_the_settings_row_says_when_bonsais_runtime_is_missing(machine):
    from agent_friday.services import voice_artifacts as va
    machine["runtime"] = False
    rows = {r["id"]: r for r in va.public_rows()}
    assert rows["voice-front-bonsai-1.7b"]["runtime_ready"] is False
    assert rows["voice-front-4b"]["runtime_ready"] is None


# ── 3 HIGH: voice actions stay reachable in laya mode ───────────────────────

@pytest.mark.parametrize("text,tool,args", [
    ("open my calendar", "navigate_to", {"kind": "workspace", "query": "calendar"}),
    ("show me the news", "navigate_to", {"kind": "workspace", "query": "news"}),
    ("play some music", "media_play", {"query": "music", "kind": "audio"}),
    ("pause the podcast", "podcast_play", {"action": "pause"}),
    ("resume it", "podcast_play", {"action": "resume"}),
    ("speak slower", "voice_preferences", {"action": "set", "scope": "session", "pace": "measured"}),
    ("keep it short", "voice_preferences", {"action": "set", "scope": "session", "depth": "concise"}),
    ("stop the background task", "task_control", {"op": "stop", "target": ""}),
    ("undo that", "undo_action", {}),
])
def test_friday_s_own_screen_player_and_work_are_reachable_by_voice(text, tool, args):
    r = lr.route(text, log=False)
    assert (r.decision, r.tool, r.args) == ("tool", tool, args)


def test_a_spoken_yes_or_no_answers_the_one_waiting_card():
    assert lr.route("yes, go ahead", pending_card="c1", log=False).args == {
        "card_id": "c1", "decision": "approve"}
    assert lr.route("no", pending_card="c1", log=False).args == {"card_id": "c1", "decision": "decline"}
    assert lr.route("yes", log=False).tool != "answer_card", "no card waiting: a yes decides nothing"


def test_every_router_tool_is_internal_or_raises_its_own_card_by_the_gate():
    from agent_friday.governance import action_gate as g
    for t in lr.TOOLS:
        assert t in g.INTERNAL_TOOLS or t in g.SELF_GATED, t
        assert t not in g.OUTWARD_TOOLS or t in g.SELF_GATED, t


# ── 4 MEDIUM: fragments and idioms are not orders ───────────────────────────

@pytest.mark.parametrize("text", ["cancel that", "move on", "make sense?", "set?", "clear it"])
def test_a_fragment_or_an_idiom_spawns_no_background_task(text):
    assert lr.route(text, log=False).decision != "defer"


# ── 8 LOW: a polite compound write is still a write ─────────────────────────

def test_a_polite_compound_write_is_deferred_not_read():
    r = lr.route("Can you please send the email about the meeting to Sam", log=False)
    assert r.decision == "defer"


# ── 5 MEDIUM: no CUDA, no park ──────────────────────────────────────────────

def test_without_cuda_a_co_resident_front_does_not_park_the_brain(monkeypatch):
    import agent_friday.routes.voice as rv
    from agent_friday.services import nemo_voice, voice_workers
    monkeypatch.setattr(nemo_voice, "gpu_status", lambda fresh=False: {"cuda": False})
    monkeypatch.setattr(voice_workers, "admit_gpu", lambda need, stage: pytest.fail("not asked"))
    assert rv._brain_parked_for_call({"voice_brain_during_calls": "auto"}, BONSAI) is False


# ── 6 MEDIUM: a build never holds a turn past its budget ────────────────────

def test_a_turn_does_not_wait_for_the_prototype_build(monkeypatch):
    from agent_friday.services import laya2_encoder
    monkeypatch.setattr(laya2_encoder, "is_available", lambda: True)
    monkeypatch.setattr(lr, "_protos", None)
    gate = threading.Event()
    monkeypatch.setattr(lr, "_build", lambda: (gate.wait(5), (["conversation"], [[1.0]]))[1])
    lr.warm()
    time.sleep(0.05)
    t = time.perf_counter()
    r = lr.route("what is a good name for a goldfish", budget_ms=50, log=False)
    took = (time.perf_counter() - t) * 1000
    gate.set()
    assert took < 400, took
    assert r.layer == "degraded"


# ── 7 LOW-MEDIUM: a guess never searches the web ────────────────────────────

def test_a_tier_one_guess_at_a_web_search_asks_first(monkeypatch):
    monkeypatch.setattr(lr, "_score", lambda text: [("search_web", 0.9, 0.8),
                                                    ("conversation", 0.05, 0.4), ("act", 0.05, 0.3)])
    r = lr._tier1("is my landlord allowed to keep my deposit")
    assert r.decision == "ask" and not r.args


# ── the routed voice turn's own policy (nothing exercised it) ───────────────

def _fixed(decision, **kw):
    return lambda text, **_: lr.Route(decision, **kw)


def test_a_change_goes_to_the_deeper_mind_and_the_receipt_says_so():
    import agent_friday.routes.voice as rv
    plan = rv._voice_route_plan("send the report to the team", ["delegate_to_friday"],
                                route_fn=_fixed("defer", layer="rule", confidence=0.95))
    assert plan["tool"] == "delegate_to_friday" and plan["args"] == {"request": "send the report to the team"}
    assert plan["ack"] == rv.DEFER_ACK and plan["receipt"]["handed_to"] == "delegate_to_friday"


def test_a_change_without_the_deeper_mind_runs_nothing():
    import agent_friday.routes.voice as rv
    plan = rv._voice_route_plan("send it", ["query_calendar"], route_fn=_fixed("defer"))
    assert plan["tool"] is None and plan["receipt"]["handed_to"] is None


def test_the_plan_passes_the_waiting_card_to_the_router():
    import agent_friday.routes.voice as rv
    plan = rv._voice_route_plan("yes", ["answer_card"], pending_card="c9")
    assert (plan["tool"], plan["args"]) == ("answer_card", {"card_id": "c9", "decision": "approve"})


class _Resp:
    def __init__(self, text):
        self._lines = ["data: " + json.dumps({"choices": [{"delta": {"content": text}}]}),
                       "data: " + json.dumps({"choices": [{"delta": {}, "finish_reason": "stop"}]}),
                       "data: [DONE]"]
        self.encoding = "utf-8"

    def raise_for_status(self):
        pass

    def iter_lines(self, decode_unicode=True):
        return iter(self._lines)

    def close(self):
        pass


def _seat(sent):
    seat = vf.FrontSeat(8199)
    seat.model = BONSAI
    seat._post = lambda body, stream: (sent.append(json.loads(json.dumps(body))), _Resp("ok"))[1]
    return seat


def test_a_tool_that_raises_is_reported_not_invented():
    sent = []
    out = _seat(sent).routed_turn("SYS", [{"role": "user", "content": "check my email"}],
                                  tool="check_email", args={}, ack="One moment.",
                                  label="checking your email",
                                  run_tool=lambda n, a: (_ for _ in ()).throw(RuntimeError("boom")))
    # A failure is said in a fixed sentence from code; the model is not asked.
    assert sent == [] and out == "One moment. I couldn't check your email just now."


def test_a_barge_during_the_tool_stops_the_turn():
    from agent_friday.services.model_router import TURN_CANCEL
    sent, cancel = [], threading.Event()
    tok = TURN_CANCEL.set(cancel)
    try:
        def slow(n, a):
            cancel.set()
            time.sleep(0.3)
            return "late"
        out = _seat(sent).routed_turn("SYS", [{"role": "user", "content": "x"}], tool="get_briefing",
                                      args={}, ack="One moment.", run_tool=slow)
    finally:
        TURN_CANCEL.reset(tok)
    assert out == "One moment." and sent == []


def test_a_tool_that_outlives_its_limit_is_called_slow():
    sent, spoken = [], []
    out = _seat(sent).routed_turn("SYS", [{"role": "user", "content": "x"}], tool="search_web",
                                  args={"query": "q"}, ack="One moment.", on_delta=spoken.append,
                                  run_tool=lambda n, a: time.sleep(0.5) or "late", tool_timeout_s=0.1)
    assert "taking longer" in out and sent == []


def test_the_result_rides_in_the_owners_turn_with_its_instruction():
    sent = []
    _seat(sent).routed_turn("SYS", [{"role": "user", "content": "Any urgent emails?"}], tool="check_email",
                            args={}, ack="One moment, checking your email.", label="checking your email",
                            run_tool=lambda n, a: "1 urgent: water shut-off Tuesday")
    msgs = sent[0]["messages"]
    assert [m["role"] for m in msgs] == ["system", "user"], "no assistant/tool turns for the 1.7B to narrate"
    user = msgs[-1]["content"]
    assert user.startswith("Any urgent emails?") and "water shut-off Tuesday" in user
    assert user.rstrip().endswith(vf._SAY_BRIEF), "one instruction line closes the turn"
    # The spoken acknowledgement is not quoted back to the model (a small model
    # said it again); the speaker rule forbids repeating it.
    assert "One moment, checking your email." not in user
    assert sent[0]["tools"] is None

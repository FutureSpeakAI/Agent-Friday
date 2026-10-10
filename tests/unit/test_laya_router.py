"""services/laya_router: system one decides the tool, the generating model speaks.

The contract (ftv/program/reference/laya_router_addendum_2026-10-09.md):
the router decides and never executes; it only ever runs READ-ONLY tools; a
request that changes something is "defer"; a remark that mentions mail or
the calendar is conversation; arguments are lifted from the owner's words,
never invented; every decision carries the layer and confidence that chose
it. The tier-0 tests need no model. The held-out score needs the Laya 2
encoder on disk and is skipped where it is absent (CI); on this machine it
gates the measured numbers (third held-out set, scored untuned: 35/40, no
unwanted action).
"""
import json
from pathlib import Path

import pytest

from agent_friday.services import laya_router as lr

ROOT = Path(__file__).resolve().parents[2]


def r0(text):
    return lr._tier0(" ".join(text.split()))


@pytest.mark.parametrize("text", [
    "send an email to my sister saying I'll be late", "delete the budget file",
    "put lunch with Sam on my calendar for Tuesday", "turn off the notifications",
    "please send the report to the team", "cancel my three o'clock"])
def test_a_request_to_change_something_is_never_run_by_the_router(text):
    r = r0(text)
    assert r is not None and r.decision == "defer" and r.tool is None


@pytest.mark.parametrize("text", [
    "email is exhausting", "my calendar is such a mess this week",
    "I read some news about whales yesterday", "that's great news",
    "don't check my email right now", "are you able to look things up online"])
def test_a_remark_a_negation_or_a_question_about_friday_runs_nothing(text):
    r = r0(text)
    assert r is not None and r.decision == "no_tool"


@pytest.mark.parametrize("text,tool,args", [
    ("Search the web for the Louvre's opening hours.", "search_web", {"query": "Louvre's opening hours"}),
    ("Any urgent emails?", "check_email", {"urgent_only": True}),
    ("What's on my calendar this afternoon?", "query_calendar", {}),
    ("Search my email for the invoice from Acme.", "search_email", {"query": "invoice from Acme"}),
    ("what's in the news today", "search_news", {}),
])
def test_a_read_request_gets_its_tool_and_arguments_from_the_words(text, tool, args):
    r = r0(text)
    assert (r.decision, r.tool, r.args) == ("tool", tool, args)
    assert r.layer == "rule" and r.confidence > 0 and r.label


def test_a_search_with_nothing_to_search_for_asks_instead_of_inventing():
    r = lr._with_args("search_web", {}, 0.9, "rule", "searching the web", "query")
    assert r.decision == "ask" and r.question and not r.args


def test_a_tool_the_session_does_not_hold_is_deferred(monkeypatch):
    r = lr.route("Any urgent emails?", tools=["query_calendar"], log=False)
    assert r.decision == "defer" and "not held" in r.reason


def test_execute_runs_only_a_tool_route_through_the_given_runner():
    ran = []
    runner = lambda name, args: ran.append((name, args)) or "ok"
    assert lr.execute(lr.Route("tool", tool="query_calendar", args={}), runner) == "ok"
    for d in ("no_tool", "ask", "defer"):
        assert lr.execute(lr.Route(d, tool="query_calendar"), runner) is None
    assert ran == [("query_calendar", {})]


def test_the_receipt_names_the_layer_and_confidence():
    r = lr.route("Any urgent emails?", log=False)
    rec = lr.receipt(r)
    assert rec["routed_by"] == "laya_router" and rec["layer"] == "rule"
    assert rec["decision"] == "tool" and rec["tool"] == "check_email" and rec["confidence"] > 0


def test_every_router_tool_is_read_only_and_in_the_voice_contract():
    from agent_friday.services.voice_engine import build_voice_tool_contract
    names = set(build_voice_tool_contract()["names"])
    assert set(lr.TOOLS) <= names
    for t in lr.TOOLS:
        assert not t.startswith(("send", "delete", "create", "update", "organize", "write")), t


def test_the_log_stores_a_hash_never_the_words(tmp_path, monkeypatch):
    monkeypatch.setattr(lr, "log_path", lambda: tmp_path / "router.jsonl")
    lr.route("Search my email for the invoice from Acme.")
    row = json.loads((tmp_path / "router.jsonl").read_text().splitlines()[-1])
    assert "invoice" not in json.dumps(row) and len(row["text_sha"]) == 16
    assert row["layer"] == "rule" and row["tool"] == "search_email"


def _score(name):
    import importlib.util
    spec = importlib.util.spec_from_file_location("ev", ROOT / "tools/laya_router_eval.py")
    ev = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(ev)
    cases = json.loads((ROOT / "tests/fixtures" / name).read_text(encoding="utf-8"))["cases"]
    rows = [(c, lr.route(c["text"], budget_ms=5000, log=False)) for c in cases]
    return rows, [ev.check(c, r) for c, r in rows]


import os
_REAL_ENCODER = Path(os.environ.get("FRIDAY_REAL_HOME") or Path.home()) / ".friday" / "models" / "laya2-encoder"


@pytest.fixture
def encoder(monkeypatch):
    """The real Laya 2 encoder files where this machine has them (read only);
    the test home is a temp folder without them. Skipped where they are
    absent (CI), which is the only place this gate does not run."""
    from agent_friday.services import laya2_encoder
    if not all((_REAL_ENCODER / n).is_file() for n in laya2_encoder.REQUIRED_FILES):
        pytest.skip("the Laya 2 encoder is not on this machine")
    monkeypatch.setattr(laya2_encoder, "artifacts_dir", lambda: _REAL_ENCODER)
    monkeypatch.setattr(lr, "_protos", None)


def test_held_out_routing_never_runs_an_unwanted_action_and_keeps_its_score(encoder):
    rows, why = _score("laya_router_holdout3.json")
    assert all(r.layer != "degraded" for _, r in rows), "the encoder must have run"
    unwanted = [c["text"] for (c, r) in rows if c["decision"] != "tool" and r.decision == "tool"]
    assert unwanted == [], unwanted
    assert sum(not w for w in why) >= 35, [(c["text"], w) for (c, _), w in zip(rows, why) if w]

"""Finances and a home address are never inferred (owner ruling 2026-10-28).

Two families join services/sensitive_categories.special_category, the check
every inferring path already runs (the nightly dreaming pass, the model's
memory proposals and meeting candidates, the background memory review, the
card queue for anything not the owner's, and Friday's own update_core_memory
tool):

* finances: income, salary, debts, loans, a mortgage, a credit score, a bank
  balance, account numbers, investments, net worth, bankruptcy, benefits or
  welfare, tax owed; about the owner or another person. It needs a person or an
  amount tied to one, so "the budget for the project", "a Python bank of
  tests", "the river bank" or "the price of the module" are ordinary.
* home_address: a home framing ("lives at", "my address is", "home is at")
  AND a street address, postcode/ZIP or flat number. "Lives in Leeds" (city
  level) and an office address are ordinary memory.

Same rules as the other families: an inferred candidate is dropped, counted by
category with no text kept, and withheld from logs (loggable); the owner's own
line on the card is theirs and is kept.

Synthetic values are built at run time; no network, no model.
"""
from __future__ import annotations

import copy
import json
import logging
from unittest.mock import patch

import pytest

import agent_friday.core as core
import agent_friday.services.agent as agent
from agent_friday.services import approvals, prompt_freeze, taint
from agent_friday.services import core_memory as cm
from agent_friday.services import core_memory_tools as cmt
from agent_friday.services import memory_dreaming as md
from agent_friday.services import memory_proposals as mp
from agent_friday.services import memory_review as mrev
from agent_friday.services import sensitive_categories as sc
from agent_friday.services import user_model as um

DAY = "2026-08-23"
DREAM_DAY = "2026-10-01"
ORDINARY = "I prefer dark mode and terse answers."
SETTINGS: dict = {}


# ── synthetic values, built at run time ─────────────────────────────────────

def _gbp(n: int, suffix: str = "") -> str:
    return "£" + format(n, ",") + suffix


def _usd(n: int, suffix: str = "") -> str:
    return "$" + format(n, ",") + suffix


def _street(name: str = "Elm", kind: str = "Road") -> str:
    return str(7 * 2) + " " + name + " " + kind


def _postcode() -> str:
    return "LS" + str(3 * 2) + " " + str(1 + 1) + "AB"


def _zip() -> str:
    return str(80000 + 202)


# ── isolation ────────────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def _iso(tmp_path, monkeypatch):
    SETTINGS.clear()
    SETTINGS.update(copy.deepcopy(core.DEFAULT_SETTINGS))
    SETTINGS["off_record"] = False
    monkeypatch.setattr(core, "_load_settings", lambda *a, **k: SETTINGS)
    monkeypatch.setattr(cm, "_dir", lambda: tmp_path / "core_memory")
    monkeypatch.setattr(cm, "_MIGRATION_CHECKED", {str(tmp_path / "core_memory"): True})
    monkeypatch.setattr(um, "FRIDAY_DIR", tmp_path)
    monkeypatch.setattr(um, "DB_PATH", tmp_path / "user_model.db")
    monkeypatch.setattr(mp, "DB_PATH", tmp_path / "proposals.db")
    monkeypatch.setattr(md, "FRIDAY_DIR", tmp_path)
    monkeypatch.setattr(md, "DB_PATH", tmp_path / "dreams.db")
    monkeypatch.setattr(md, "DREAMS_DIR", tmp_path / "dreams")
    monkeypatch.setattr(approvals, "APPROVALS_FILE", tmp_path / "approvals.json")
    monkeypatch.setattr(approvals, "FRIDAY_DIR", tmp_path)
    monkeypatch.setattr(approvals, "_notify_pending", lambda rec: None)
    from agent_friday.services.knowledge_graph import integration as kgi
    monkeypatch.setattr(kgi, "ingest_fact", lambda text, **kw: "fact_x")
    agent._PENDING_CONFIRMATIONS.clear()
    taint.reset()
    prompt_freeze.reset()
    mrev._reset_for_tests()
    sc._reset_for_tests()
    yield
    mrev._reset_for_tests()
    prompt_freeze.reset()
    taint.reset()
    sc._reset_for_tests()


# ── the families: positives and negatives ───────────────────────────────────

def _finance_lines():
    return [
        "Earns " + _gbp(45) + "k a year.",
        "My salary is " + _gbp(40000) + ".",
        "Her salary of " + _usd(90) + "k.",
        "Makes " + _usd(120) + "k a year.",
        "Gets paid " + _usd(30) + " an hour.",
        "Owes his brother " + _gbp(500) + ".",
        "Has " + _gbp(20) + "k in savings.",
        "Has a mortgage of " + _gbp(200) + "k.",
        "Owes the bank money.",
        "Is in debt.",
        "Is deep in debt after the move.",
        "Has a credit score of " + str(700 + 20) + ".",
        "Their net worth is huge.",
        "Filed for bankruptcy.",
        "Is on benefits.",
        "Claims universal credit.",
        "Has a tax bill due in January.",
        "Owes back taxes.",
        "Took out a loan for the car.",
        "Has student loans.",
        "Invests in index funds.",
        "My bank balance is low.",
        "Earns six figures.",
        "Is paying off his credit card.",
        "His income is modest.",
    ]


def _address_lines():
    return [
        "Lives at " + _street() + ".",
        "My address is " + _street("Station") + ", Leeds.",
        "Home is at " + str(221) + "B Baker Street.",
        "Lives in Leeds, " + _postcode() + ".",
        "Lives in Denver, " + _zip() + ".",
        "Home: " + _street("Mill", "Lane") + ".",
        "Lives at number " + str(3 + 4) + ".",
        _street() + " is my home.",
        "His home address is " + _street("Oak", "Avenue") + ".",
        "I live at " + _street() + " with the cats.",
    ]


@pytest.mark.parametrize("text", _finance_lines())
def test_the_finances_family_is_recognised(text):
    assert sc.special_category(text) == "finances"


@pytest.mark.parametrize("text", _address_lines())
def test_the_home_address_family_is_recognised(text):
    assert sc.special_category(text) == "home_address"


@pytest.mark.parametrize("text", [
    # Finances: no person, or no amount tied to one.
    "The budget for the project is tight.", "Keeps a Python bank of tests.",
    "Walks along the river bank.", "The price of the module is " + _usd(5) + ".",
    "Fixed tech debt in the parser.", "Pays with a Visa card.", "Owes me a code review.",
    "Makes 3 commits a day.", "Their investment in CI paid off.",
    "Wants to update my portfolio site.", "Salary band field in the HR app.",
    "Uses the library loan system.", "Bought a " + _usd(40) + " keyboard.",
    "Account settings page is slow.",
    # Home address: city level, an office, a street with no number or framing.
    "Lives in Leeds.", "Lives in Denver.", "Lives in Springfield, Ohio.",
    "Lives on Church Street.", "Office is at " + _street("Downing", "Street") + ".",
    "Works at " + _street("Main", "Street") + ".", "Meets at 4 Church St.",
    "Lives in Leeds, works at " + _street("Park", "Row") + ".",
    "My work address is " + _street("Main", "St") + ".",
    "My email address is in settings.", "Moved to " + str(10000) + " users.",
])
def test_ordinary_lines_are_not_mistaken_for_finances_or_an_address(text):
    assert sc.special_category(text) is None


def test_loggable_withholds_both_families():
    assert sc.loggable("Earns " + _gbp(45) + "k a year.") == "[withheld: it touches finances]"
    said = "Lives at " + _street() + "."
    assert sc.loggable(said) == "[withheld: it touches home address]"
    assert sc.loggable("Lives in Leeds.") == "Lives in Leeds."


# ── every inferring path drops them ─────────────────────────────────────────

class _DayMemory:
    def __init__(self, rows):
        self._rows = rows

    def recent(self, n=2000, roles=None):
        return list(self._rows)[:n]


def _dream(*texts, day=DREAM_DAY):
    rows = [{"text": t, "role": "user", "date": day, "timestamp": f"{day}T10:0{i}:00",
             "topic_keywords": []} for i, t in enumerate(texts)]
    return md.dream(day=day, memory=_DayMemory(rows))


def _dream_lines():
    return [
        ("finances", "I always put half my salary into savings."),
        ("finances", "I never tell anyone that I am in debt."),
        ("home_address", "I never tell anyone I live at " + _street() + "."),
    ]


@pytest.mark.parametrize("category, said", _dream_lines())
def test_the_nightly_pass_drops_them(category, said, tmp_path):
    out = _dream(said, ORDINARY)
    assert out["ok"], out
    assert [p["text"] for p in cm.pending()] == [ORDINARY], cm.pending()
    log = (tmp_path / "dreams" / f"{DREAM_DAY}.md").read_text(encoding="utf-8")
    assert said not in log and ORDINARY in log
    assert said not in json.dumps(md.recent_dreams(1))
    assert sc.dropped_counts().get(f"dreaming.fact:{category}") == 1


def _propose(facts, day=DAY):
    seat = {"assigned": True, "model": "gemma4:12b", "provider": "ollama-local", "reason": ""}
    turns = [{"role": "user", "text": f"turn {i} about the week"} for i in range(5)]
    with patch.object(mp, "seat", return_value=seat), \
         patch.object(mp, "_ask_seat", return_value=json.dumps(facts)), \
         patch("agent_friday.services.memory_dreaming._pull_turns", return_value=(turns, False)):
        return mp.propose(day)


def _all_proposal_text():
    conn = mp._connect()
    try:
        return json.dumps(conn.execute("SELECT text, evidence FROM proposed_facts").fetchall())
    finally:
        conn.close()


@pytest.mark.parametrize("text, evidence", [
    ("The user earns " + _gbp(60) + "k a year.", "my pay"),
    ("Keeps Mondays quiet.", "I keep Mondays quiet because I'm deep in debt"),
    ("The user lives at " + _street() + ".", "my place"),
])
def test_a_proposal_touching_them_is_not_stored(text, evidence):
    keep = {"category": "workflow", "text": "Commits one build at a time.",
            "evidence": "one commit per build"}
    out = _propose([{"category": "bio", "text": text, "evidence": evidence}, keep])
    assert out["ok"] and out["stored"] == 1, out
    assert [p["text"] for p in mp.pending()] == [keep["text"]]
    assert text not in _all_proposal_text() and evidence not in _all_proposal_text()
    assert out.get("dropped_sensitive") == 1


def test_a_meeting_candidate_about_someones_money_or_home_is_not_proposed():
    span = {"i": 0, "t": 1.0}
    stored = mp.propose_from_meeting([
        {"category": "relationship", "text": "Dana's salary is " + _gbp(80) + "k.", "span": span},
        {"category": "relationship", "text": "Dana lives at " + _street() + ".", "span": span},
        {"category": "relationship", "text": "Dana leads the platform team.", "span": span},
    ], "meet-1", DAY, "gemma4:12b")
    assert stored == 1


def test_the_review_drops_them_even_from_the_owners_words():
    said = "I take my coffee black, my salary is " + _gbp(50) + "k and I live at " + _street() + "."
    window = [{"user": said, "friday": "Noted.", "owner": True}]
    plan = {"add": [
        {"target": "user", "text": "Salary is " + _gbp(50) + "k.", "evidence": "my salary is"},
        {"target": "user", "text": "Lives at " + _street() + ".", "evidence": "I live at"},
        {"target": "user", "text": "Takes coffee black.", "evidence": "I take my coffee black"},
    ], "change": [], "remove": []}
    out = mrev.review(window, ask=lambda prompt: json.dumps(plan))
    assert cm.entries("user") == ["Takes coffee black."]
    assert cm.pending() == []
    assert out.get("dropped_sensitive") == 2


@pytest.mark.parametrize("text", [
    "Earns " + _gbp(45) + "k a year.", "Lives at " + _street() + ".",
])
def test_the_card_queue_refuses_them_from_anyone_but_the_owner(text):
    q = cm.queue("user", "add", text=text, source="dream:" + DREAM_DAY)
    assert not q["ok"] and q.get("sensitive") in ("finances", "home_address")
    assert cm.pending() == []
    r = cm.add("user", text, actor="friday")
    assert not r["ok"] and r.get("sensitive")
    assert cm.entries("user") == []


def _friday_tool(text, owner_text, key="conv-r11"):
    ctx = {"session_id": key, "taint_key": key, "surface": "chat", "authenticated": True}
    t1 = agent._CURRENT_TOOL_CONTEXT.set(ctx)
    t2 = agent._CURRENT_OWNER_TEXT.set(owner_text)
    try:
        return cmt._tool_update_core_memory({"action": "add", "target": "user", "text": text})
    finally:
        agent._CURRENT_OWNER_TEXT.reset(t2)
        agent._CURRENT_TOOL_CONTEXT.reset(t1)


@pytest.mark.parametrize("text", [
    "Is in debt.", "Lives at " + _street() + ".",
])
def test_friday_guessing_them_files_nothing(text):
    out = _friday_tool(text, "what's on tomorrow?")
    assert out.startswith("NOT QUEUED"), out
    assert cm.entries("user") == [] and cm.pending() == []


def test_what_was_dropped_is_counted_and_never_logged(caplog):
    salary = "I always put my salary of " + _gbp(52) + "k into savings."
    home = "Lives at " + _street("Rowan") + "."
    with caplog.at_level(logging.DEBUG):
        _dream(salary)
        _friday_tool(home, "what's on tomorrow?")
    counts = sc.dropped_counts()
    assert counts.get("dreaming.fact:finances") == 1
    assert sum(n for k, n in counts.items() if k.endswith(":home_address")) == 1
    for secret in ("52k", "Rowan"):
        assert secret not in caplog.text and secret not in json.dumps(counts)


# ── the owner's own lines are kept ──────────────────────────────────────────

def test_the_owner_adding_them_on_the_card_keeps_them():
    salary = "Salary is " + _gbp(48) + "k."
    home = "Lives at " + _street() + "."
    assert cm.add("user", salary, actor="owner")["ok"]
    assert cm.add("user", home, actor="owner")["ok"]
    assert cm.entries("user") == [salary, home]


def test_the_owner_adding_them_through_the_route_keeps_them():
    from flask import Flask
    from agent_friday.routes.core_memory import core_memory_bp
    app = Flask(__name__)
    app.secret_key = "test"                 # login_required keeps a session on loopback
    app.register_blueprint(core_memory_bp)
    client = app.test_client()
    home = "Home is at " + _street() + "."
    r = client.post("/api/core-memory/add", json={"target": "user", "text": home})
    assert r.status_code == 200 and r.get_json()["ok"], r.get_json()
    assert cm.entries("user") == [home]


def test_friday_saving_the_owners_own_words_waits_for_their_keep():
    out = _friday_tool("Is paying off a mortgage.", "Note that I'm paying off a mortgage.")
    assert out.startswith("NOT SAVED YET"), out
    assert cm.entries("user") == []
    pending = cm.pending()
    assert [p["text"] for p in pending] == ["Is paying off a mortgage."]
    assert cm.decide(pending[0]["id"], True)["ok"]
    assert cm.entries("user") == ["Is paying off a mortgage."]

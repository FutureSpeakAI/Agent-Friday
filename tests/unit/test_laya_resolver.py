"""The item resolver's contract.

A request for one specific item becomes one command built from a template
and that item's stable id, or an ask-back naming two or three candidates, or
the brain. Never a guess, never a command written as free text, never a
choice over more than ten options (laya's 11+ bucket is uncalibrated).

No model is loaded; Laya is faked at `predict`, sources are fakes.
"""
from __future__ import annotations

import threading

import pytest

from agent_friday.services import laya_backend, laya_resolver, laya_runtime
from agent_friday.services.laya_resolver import Candidate


class _Laya:
    def __init__(self, command=0.9, kind="email", pick=None):
        self.command, self.kind, self.pick = command, kind, pick or {}
        self.calls = []

    def predict(self, text, questions):
        self.calls.append({q: dict(v) for q, v in questions.items()})
        out = {}
        for q, spec in questions.items():
            if q == "open_request":
                out[q] = {"choice": "open" if self.command >= 0.5 else "other"}
            elif q == "item_kind":
                out[q] = {"choice": self.kind}
            elif q == "item":
                keys = list(spec["criteria"])
                probs = {k: self.pick.get(k, 0.0) for k in keys}
                out[q] = {"choice": max(probs, key=probs.get), "probabilities": probs}
        return {"answers": out}


def _mail(n):
    return [Candidate("email", "thread%d" % i, "Subject %d" % i, "from Dana", 0.8 - i * 0.01,
                      {"account": "acct1"}) for i in range(n)]


@pytest.fixture(autouse=True)
def _fresh(monkeypatch):
    monkeypatch.setattr(laya_backend, "_scoring",
                        threading.BoundedSemaphore(laya_backend._MAX_SCORING))
    monkeypatch.setattr(laya_runtime, "checkpoint_revision", lambda agent=None: "rev")
    laya_runtime.clear_cache()
    # The cosine shortlist needs a real encoder; keep the lexical order here.
    monkeypatch.setattr(laya_resolver, "shortlist",
                        lambda text, cands, k=laya_resolver.SHORTLIST_K: list(cands[:k]))
    yield
    laya_backend._agent = None
    laya_runtime.clear_cache()


def _with(monkeypatch, laya):
    monkeypatch.setattr(laya_backend, "_agent", laya)
    return laya


def test_a_question_goes_to_the_brain_without_asking_laya(monkeypatch):
    laya = _with(monkeypatch, _Laya())
    r = laya_resolver.resolve("why is the sky blue", sources={"email": lambda t: _mail(3)})
    assert r.status == "brain" and r.reason == "no_open_verb"
    assert laya.calls == []


def test_laya_saying_other_goes_to_the_brain(monkeypatch):
    _with(monkeypatch, _Laya(command=0.1))
    r = laya_resolver.resolve("open up about your day", sources={"email": lambda t: _mail(3)})
    assert r.status == "brain" and r.reason == "not_command"


def test_not_an_item_goes_to_the_brain(monkeypatch):
    _with(monkeypatch, _Laya(kind="other"))
    r = laya_resolver.resolve("open the thing", sources={"email": lambda t: _mail(3)})
    assert r.status == "brain" and r.reason == "not_an_item"


def test_a_missing_laya_is_the_brain_not_a_guess(monkeypatch):
    monkeypatch.setattr(laya_backend, "_agent", None)
    r = laya_resolver.resolve("open the go-live email", sources={"email": lambda t: _mail(3)})
    assert r.status == "brain" and r.command is None


def test_a_confident_pick_is_a_command_built_from_the_item_id(monkeypatch):
    _with(monkeypatch, _Laya(pick={"B": 0.8, "A": 0.1}))
    r = laya_resolver.resolve("open the go-live email from Dana",
                              sources={"email": lambda t: _mail(4)})
    assert r.status == "command"
    assert r.command == {"tool": "navigate_to",
                         "input": {"kind": "email", "id": "thread1", "account": "acct1"}}
    assert r.chosen.id == "thread1" and r.confidence == 0.8


def test_an_unsure_pick_asks_with_named_candidates(monkeypatch):
    _with(monkeypatch, _Laya(pick={"A": 0.45, "C": 0.40, "B": 0.10}))
    r = laya_resolver.resolve("open Dana's email", sources={"email": lambda t: _mail(4)})
    assert r.status == "ask" and r.command is None
    # The index's two best matches first, then Laya's pick if not among them.
    assert [c.id for c in r.choices] == ["thread0", "thread1", "thread2"]
    assert "Subject 0" in r.question and "Subject 2" in r.question
    assert 2 <= len(r.choices) <= 3


def test_a_missing_pick_asks_rather_than_guesses(monkeypatch):
    _with(monkeypatch, _Laya())
    monkeypatch.setattr(laya_runtime, "predict_bounded",
                        lambda *a, **k: {"status": "missing", "result": None,
                                         "reason": "laya busy", "elapsed_ms": 1.0})
    r = laya_resolver.resolve("open Dana's email", sources={"email": lambda t: _mail(4)})
    assert r.status == "ask" and r.command is None


def test_laya_never_chooses_among_more_than_ten(monkeypatch):
    laya = _with(monkeypatch, _Laya(pick={"A": 0.9}))
    laya_resolver.resolve("open the email", sources={"email": lambda t: _mail(30)})
    item_q = [c["item"] for c in laya.calls if "item" in c][0]
    assert len(item_q["criteria"]) <= 10
    assert laya_resolver.SHORTLIST_K <= 10


def test_the_real_shortlist_cuts_to_k_without_an_encoder(monkeypatch):
    monkeypatch.undo()
    monkeypatch.setattr(laya_backend, "_agent", None)
    cands = _mail(30)
    out = laya_resolver.shortlist("dana", cands, k=8)
    assert out == cands[:8]


def test_a_single_exact_match_opens_without_a_choice(monkeypatch):
    laya = _with(monkeypatch, _Laya(kind="workspace"))
    r = laya_resolver.resolve("open the studio", sources={
        "workspace": lambda t: [Candidate("workspace", "studio", "Studio", "workspace", 1.0)]})
    assert r.command == {"tool": "navigate_to", "input": {"kind": "workspace", "workspace": "studio"}}
    assert not [c for c in laya.calls if "item" in c]


def test_a_single_partial_match_is_asked_about(monkeypatch):
    _with(monkeypatch, _Laya(kind="wiki_page"))
    r = laya_resolver.resolve("open the pipeline page", sources={
        "wiki_page": lambda t: [Candidate("wiki_page", "career/pipeline.md", "Pipeline", "", 0.5)]})
    assert r.status == "ask" and r.question == "Did you mean Pipeline?"


def test_a_news_story_opens_its_stored_url(monkeypatch):
    _with(monkeypatch, _Laya(kind="news", pick={"A": 0.9}))
    r = laya_resolver.resolve("open that story about the school board", sources={
        "news": lambda t: [Candidate("news", "ab12", "School board votes", "", 0.9,
                                     {"url": "https://example.org/a"}),
                           Candidate("news", "cd34", "Board game cafe", "", 0.4,
                                     {"url": "https://example.org/b"})]})
    assert r.command == {"tool": "open_url", "input": {"url": "https://example.org/a"}}


def test_nothing_found_says_so(monkeypatch):
    _with(monkeypatch, _Laya())
    r = laya_resolver.resolve("open the email from nobody", sources={"email": lambda t: []})
    assert r.status == "not_found" and r.command is None


def test_the_gate_is_one_pass(monkeypatch):
    laya = _with(monkeypatch, _Laya(command=0.1))
    laya_resolver.resolve("open it", sources={})
    assert sorted(laya.calls[0]) == ["item_kind", "open_request"]


def test_resolve_never_raises(monkeypatch):
    _with(monkeypatch, _Laya())

    def boom(text):
        raise RuntimeError("index down")
    r = laya_resolver.resolve("open the email", sources={"email": boom})
    assert r.status == "brain" and r.reason.startswith("error")


@pytest.mark.parametrize("text,ws", [("open news", "news"), ("Open the Studio workspace for me.", "studio"),
                                     ("open the wiki workspace please", "knowledge"),
                                     ("Yeah, I mean go ahead and open the inbox.", "messages")])
def test_a_workspace_by_name_opens_without_laya(monkeypatch, text, ws):
    laya = _with(monkeypatch, _Laya())
    r = laya_resolver.resolve(text)
    assert r.command == {"tool": "navigate_to", "input": {"kind": "workspace", "workspace": ws}}
    assert laya.calls == []


def test_a_wiki_page_request_is_not_mistaken_for_the_workspace(monkeypatch):
    assert laya_resolver._workspace_named("open the wiki page on the Neurow pitch") is None


def test_the_only_full_match_opens_without_a_choice(monkeypatch):
    laya = _with(monkeypatch, _Laya(kind="email"))
    cands = [Candidate("email", "t1", "Go-live", "from Dana", 1.2),
             Candidate("email", "t2", "Invoice", "from Dana", 0.5)]
    r = laya_resolver.resolve("open the go-live email from Dana", sources={"email": lambda t: cands})
    assert r.command["input"]["id"] == "t1"
    assert not [c for c in laya.calls if "item" in c]


def test_a_named_kind_overrides_laya_saying_other(monkeypatch):
    _with(monkeypatch, _Laya(kind="other"))
    cands = [Candidate("wiki_page", "p/neurow.md", "Neurow pitch", "", 1.0)]
    r = laya_resolver.resolve("pull up my notes on the Neurow pitch",
                              sources={"wiki_page": lambda t: cands})
    assert r.status == "command" and r.command["input"]["id"] == "p/neurow.md"


def test_cue_words_are_not_search_words():
    """"open that story about the school board vote": "story" names the kind,
    it is not in the title, and counting it capped a perfect title at 0.8."""
    words = laya_resolver._words("open that story about the school board vote")
    assert "story" not in words and "open" not in words
    assert {"school", "board", "vote"} <= set(words)


def test_the_same_title_twice_is_one_candidate(monkeypatch):
    laya = _with(monkeypatch, _Laya(kind="news"))
    cands = [Candidate("news", "a1", "School board votes", "Paper A", 1.0, {"url": "https://x/1"}),
             Candidate("news", "a2", "School Board Votes", "Paper A", 1.0, {"url": "https://x/2"}),
             Candidate("news", "b1", "Board game cafe opens", "", 0.4, {"url": "https://x/3"})]
    r = laya_resolver.resolve("open that story about the school board votes",
                              sources={"news": lambda t: cands})
    assert r.status == "command" and r.command["input"]["url"] == "https://x/1"
    assert not [c for c in laya.calls if "item" in c]

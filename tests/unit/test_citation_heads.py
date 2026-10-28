"""Second wave, head 5: the citation support head reads a finished reply in shadow.

Spec: ftv/program/specs/decision_models_STORM_2026-10-24.md (head 5,
evidence.supports). Label only; in shadow it changes nothing and its receipt
holds labels and scores, never a word. On the base branch there is no
services/citation_heads.py: the fixture fails with that, and the route test
fails on its assertion.
"""
from __future__ import annotations

import importlib
import json
from pathlib import Path

import pytest


@pytest.fixture
def ch(monkeypatch):
    try:
        mod = importlib.import_module("agent_friday.services.citation_heads")
    except ImportError as e:
        pytest.fail("services/citation_heads does not exist (%s)" % e)
    mod.clear_cache()
    mod._seen.clear()
    monkeypatch.setattr(mod, "RUN_INLINE", True)
    monkeypatch.setattr(mod, "_off_record", lambda: False)
    monkeypatch.setattr(mod, "_generation", lambda: 1)
    yield mod
    mod.clear_cache()
    mod._seen.clear()
    mod._scorer_override = None


def _heads(monkeypatch, mode="shadow", act=0.9, abstain=0.3):
    from agent_friday.services import decision_heads
    monkeypatch.setattr(decision_heads, "effective",
                        lambda hid: {"id": hid, "mode": mode, "thresholds": {"act": act, "abstain": abstain},
                                     "scorer": "series_one", "budget_ms": 500, "tag": "v1-test"})


URL = "https://example.org/hours"
TRACE = [{"name": "fetch_page", "input": {"url": URL},
          "result": "Opening hours: Monday to Friday 9 to 19. Closed on Sundays."}]


def _lib_trace():
    meta = {"refs": {"1.1": "lib:12#345", "1.2": "lib:12#346"}, "stamp": None, "receipt": None}
    body = ('<evidence-abc label="1.1" doc="Lease">\nThe deposit is 2,000 and is due on signing.\n</evidence-abc>\n'
            '<evidence-abc label="1.2" doc="Lease">\nPets are allowed with consent.\n</evidence-abc>')
    return [{"name": "search_library", "input": {"question": "deposit"}, "result": json.dumps(meta) + "\n\n" + body}]


def test_a_web_footnote_pairs_its_sentence_with_what_the_tool_returned(ch):
    reply = "The library is closed on Sundays [web:%s]. Ask me anything else." % URL
    got = ch.pairs(reply, TRACE)
    assert got == [("web", "The library is closed on Sundays .", TRACE[0]["result"])]


def test_a_library_footnote_pairs_with_its_fenced_passage(ch):
    reply = "The deposit is due when you sign [lib:12#345]."
    got = ch.pairs(reply, _lib_trace())
    assert got == [("lib", "The deposit is due when you sign .", "The deposit is 2,000 and is due on signing.")]


def test_a_footnote_with_no_passage_this_turn_is_left_to_provenance(ch):
    assert ch.pairs("Closed on Sundays [web:https://elsewhere.example/x].", TRACE) == []
    assert ch.pairs("Closed on Sundays [unverified-web:%s]." % URL, TRACE) == []
    assert ch.pairs("No footnotes here at all.", TRACE) == []


@pytest.mark.parametrize("p,label", [(0.95, "supports"), (0.5, "unsure"), (0.1, "does_not_address")])
def test_labels_follow_the_thresholds_and_never_say_contradicts(ch, monkeypatch, p, label):
    _heads(monkeypatch)
    assert ch.label(p) == label


def test_a_reply_leaves_one_receipt_with_labels_and_no_words(ch, monkeypatch):
    from agent_friday.services import laya
    _heads(monkeypatch)
    ch._scorer_override = lambda claim, passage: 0.1 if "Saturday" in claim else 0.95
    got = []
    real = laya.ask
    monkeypatch.setattr(laya, "ask", lambda *a, **k: got.append(real(*a, **k)) or got[-1])
    reply = ("The library is closed on Sundays [web:%s]. It opens late on Saturday [web:%s]."
             % (URL, URL))
    before = (reply, json.dumps(TRACE))
    assert ch.shadow_reply(reply, TRACE, surface="test")
    assert (reply, json.dumps(TRACE)) == before
    a = got[0]["answers"]["evidence.supports"]
    assert a["value"] == "does_not_address"            # the weakest footnote names the reply
    rec = laya.receipt(got[0]["receipt_id"])
    text = json.dumps(rec)
    assert "Sundays" not in text and "Saturday" not in text and "Opening hours" not in text
    assert "1 not addressed" in rec["questions"]["evidence.supports"]["why"]


def test_off_off_the_record_a_moved_generation_or_a_repeat_does_nothing(ch, monkeypatch):
    _heads(monkeypatch)
    ch._scorer_override = lambda claim, passage: 0.5
    reply = "The library is closed on Sundays [web:%s]." % URL
    assert ch.shadow_reply(reply, TRACE, gen=0) is False          # the generation moved since the turn began
    assert ch.shadow_reply(reply, TRACE, gen=1) is True
    assert ch.shadow_reply(reply, TRACE, gen=1) is False          # the same reply read once
    ch._seen.clear()
    monkeypatch.setattr(ch, "_off_record", lambda: True)
    assert ch.shadow_reply(reply, TRACE) is False
    monkeypatch.setattr(ch, "_off_record", lambda: False)
    _heads(monkeypatch, mode="off")
    ch.clear_cache()
    assert ch.shadow_reply(reply, TRACE) is False


def test_a_turn_that_went_off_the_record_before_the_job_ran_leaves_nothing(ch, monkeypatch):
    from agent_friday.services import laya
    _heads(monkeypatch)
    ch._scorer_override = lambda claim, passage: 0.5
    gens = iter([1, 2])
    monkeypatch.setattr(ch, "_generation", lambda: next(gens, 2))
    asked = []
    monkeypatch.setattr(laya, "ask", lambda *a, **k: asked.append(1))
    assert ch.shadow_reply("The library is closed on Sundays [web:%s]." % URL, TRACE)
    assert asked == []


def test_the_head_ships_label_only_in_shadow_with_contradicts_reserved():
    from agent_friday.services import decision_heads as dh
    dh._reset_caches_for_tests()
    spec = dh.shipped("evidence.supports")
    assert spec is not None, dh.problems().get("evidence.supports")
    assert spec["direction"] == "label_only" and spec["mode_default"] == "shadow"
    assert spec["scorer"] == "series_one" and "unsure" in spec["labels"]
    assert spec["thresholds"]["act"] > 0.95                 # out of reach: supports is not said yet
    dh._reset_caches_for_tests()


def test_the_chat_route_hands_the_final_reply_to_the_head_on_both_paths():
    from agent_friday.routes import chat
    src = Path(chat.__file__).read_text(encoding="utf-8")
    assert src.count("_cite_head.shadow_reply(reply, tool_trace") == 2
    # after the Library footnotes are checked, so it reads the reply that is shown
    for i in [n for n in range(len(src)) if src.startswith("_cite_head.shadow_reply(", n)]:
        assert src.rfind("_library_cite.finish(", 0, i) > src.rfind("_mark_unverified_citations(reply", 0, i)

"""Free text is kept as a keyed hash in the ledger, never as the text (Stephen's decision (c) on #868). TEST CREDITS ONLY.

services/ledger_text.py is the pure half: which keys hold owner text (FREE_TEXT_KEYS), how a text becomes a hidden value
("ft1:" + 64 hex, still a string, so every reader's types hold), how to turn it back, and how to find text that was never
hidden. What each check bites on:
  1. every string under a free-text key is replaced, at any depth (a list, a list of dicts, a nested dict), and nothing
     outside those keys is touched (ids, labels, numbers, booleans stay);
  2. hiding twice changes nothing (a hidden value is not hidden again) and the input is not modified;
  3. show() turns what the table knows back into text and leaves what it does not know;
  4. raw_text_key finds text that was written before hiding existed, and finds nothing in hidden or text-free records;
  5. the check can fail: a key set with "summary" removed leaves the canary in the output.
"""
import copy

from agent_friday.services import ledger_text as lt

CANARY = "CANARY-9d41 D:/work/secret plans.txt"


def digest(text):
    import hashlib
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def test_every_string_under_a_free_text_key_is_hidden_at_any_depth():
    rec = {"tool": "write_file", "status": "done", "n": 3, "ok": True, "summary": "Created " + CANARY,
           "items": [{"path": CANARY, "kind": "file", "bytes": 12}, {"path": "b"}],
           "verified": {"critique": CANARY, "ok": False}, "artifacts": [CANARY, CANARY + "2"], "note": CANARY,
           "removed": [CANARY], "what_ran": CANARY}
    hidden, texts = lt.hide(rec, digest)
    assert CANARY not in repr(hidden)
    assert hidden["tool"] == "write_file" and hidden["status"] == "done" and hidden["n"] == 3 and hidden["ok"] is True
    assert hidden["items"][0]["bytes"] == 12, "numbers inside items stay numbers"
    assert lt.is_hidden(hidden["items"][0]["kind"]), "every string under items is free text as a whole"
    assert hidden["verified"]["ok"] is False
    assert all(lt.is_hidden(v) for v in (hidden["summary"], hidden["note"], hidden["what_ran"]))
    assert all(lt.is_hidden(v) for v in hidden["artifacts"] + hidden["removed"])
    assert CANARY in texts.values()


def test_values_outside_the_free_text_keys_are_left_alone():
    rec = {"action_id": "act-1", "tool": "send_email", "label": "work", "reason": "done", "decision_seq": 4}
    hidden, texts = lt.hide(rec, digest)
    assert hidden == rec and texts == {}


def test_hiding_twice_changes_nothing_and_the_input_is_not_modified():
    rec = {"summary": CANARY, "items": [{"path": CANARY}]}
    before = copy.deepcopy(rec)
    once, _ = lt.hide(rec, digest)
    twice, again = lt.hide(once, digest)
    assert rec == before and once == twice and again == {}


def test_show_turns_known_hashes_back_into_text_and_leaves_unknown_ones():
    hidden, texts = lt.hide({"summary": CANARY, "items": ["x"]}, digest)
    known = lt.show(hidden, lambda d: texts.get(d))
    assert known == {"summary": CANARY, "items": ["x"]}
    unknown = lt.show(hidden, lambda d: None)
    assert unknown == hidden


def test_raw_text_key_finds_text_written_before_hiding_and_nothing_else():
    assert lt.raw_text_key({"summary": CANARY}) == "summary"
    assert lt.raw_text_key({"a": {"items": [{"path": CANARY}]}}) == "items"
    assert lt.raw_text_key(lt.hide({"summary": CANARY, "items": [CANARY]}, digest)[0]) is None
    assert lt.raw_text_key({"tool": "x", "n": 1, "summary": ""}) is None


def test_the_check_can_fail_a_key_set_without_summary_leaves_the_canary(monkeypatch):
    monkeypatch.setattr(lt, "FREE_TEXT_KEYS", lt.FREE_TEXT_KEYS - {"summary"})
    hidden, _ = lt.hide({"summary": CANARY}, digest)
    assert CANARY in repr(hidden), "the key set is what decides what is hidden"


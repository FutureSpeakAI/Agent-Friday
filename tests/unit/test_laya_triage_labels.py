"""The owner's label list for the triage heads: what is chosen, what an answer writes, what is scored."""
from __future__ import annotations

import json

import pytest

from agent_friday.services import laya_labels, laya_triage_labels as tl, triage_heads


def _row(head, h, label, incumbent, p=0.9):
    return {"head": head, "h": h, "label": label, "incumbent": incumbent, "p": p}


def _rows(*rows):
    return {r["h"]: r for r in rows}


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setattr(laya_labels, "labels_path", lambda: tmp_path / "laya_labels.jsonl")
    monkeypatch.setattr(tl, "queue_path", lambda: tmp_path / "laya_triage_queue.json")
    monkeypatch.setattr(triage_heads, "shadow_path", lambda: tmp_path / "triage_shadow.jsonl")
    return tmp_path


def _queue(home, *items):
    (home / "laya_triage_queue.json").write_text(json.dumps({"v": 1, "items": list(items)}), encoding="utf-8")


def _item(h, head="notify", head_said="needs_you", incumbent="fyi"):
    return {"id": tl.SUBJECT_PREFIX + h, "head": head, "text": "t " + h, "question": tl.QUESTIONS[head],
            "head_said": head_said, "incumbent_said": incumbent, "p": 0.9, "kind": "disagree"}


# -- choosing ----------------------------------------------------------------

def test_the_list_mixes_both_heads_and_puts_disagreements_before_agreements():
    rows = _rows(_row("notify", "n1", "fyi", "fyi", 0.5), _row("notify", "n2", "needs_you", "fyi"),
                 _row("news", "w1", "promo", "analysis"), _row("news", "w2", "analysis", "analysis", 0.4))
    texts = {h: "words " + h for h in rows}
    got = tl.choose(rows, texts, limit=4)
    assert [i["head"] for i in got] == ["notify", "news", "notify", "news"]
    assert [i["id"] for i in got if i["head"] == "notify"] == ["item:n2", "item:n1"]
    assert [i["kind"] for i in got if i["head"] == "news"] == ["disagree", "edge"]


def test_an_item_whose_words_cannot_be_found_is_never_asked():
    rows = _rows(_row("notify", "n1", "needs_you", "fyi"), _row("notify", "n2", "needs_you", "fyi"))
    assert [i["id"] for i in tl.choose(rows, {"n2": "found"})] == ["item:n2"]


def test_no_one_kind_of_disagreement_fills_the_list():
    big = [_row("news", "a%d" % i, "promo", "analysis", 0.99 - i / 1000) for i in range(10)]
    other = [_row("news", "b1", "promo", "hard"), _row("news", "c1", "other", "service")]
    rows = _rows(*big, *other)
    got = tl.choose(rows, {h: "x" for h in rows}, limit=3)
    assert {(i["incumbent_said"], i["head_said"]) for i in got} == {
        ("analysis", "promo"), ("hard", "promo"), ("service", "other")}


def test_the_notify_head_keeps_to_its_share_and_the_list_to_its_limit():
    notify = [_row("notify", "n%d" % i, "needs_you", "fyi") for i in range(20)]
    news = [_row("news", "w%d" % i, "promo", "analysis") for i in range(40)]
    rows = _rows(*notify, *news)
    got = tl.choose(rows, {h: "x" for h in rows})
    assert len(got) == tl.DEFAULT_LIMIT
    assert sum(1 for i in got if i["head"] == "notify") == tl.NOTIFY_SHARE


def test_the_text_is_one_line_and_clipped():
    rows = _rows(_row("news", "w1", "promo", "analysis"))
    got = tl.choose(rows, {"w1": "first\n\nsecond " + "z" * 500})[0]["text"]
    assert "\n" not in got and len(got) <= tl.LINE_CHARS and got.endswith("…")


# -- finding the words again -------------------------------------------------

def test_words_are_found_by_the_digest_the_head_logged(home, monkeypatch):
    title = "Gift Guide 2026"
    monkeypatch.setattr(tl, "_news_titles", lambda: iter(["Other story", title]))
    monkeypatch.setattr(tl, "_notification_texts", lambda: iter([]))
    h = triage_heads.digest(triage_heads._clip(title))
    assert tl.recover_texts({h: _row("news", h, "promo", "analysis")}) == {h: title}


# -- answering ---------------------------------------------------------------

def test_an_answer_is_written_with_a_receipt_that_chains_to_the_last(home):
    _queue(home, _item("n1"), _item("n2"))
    a = tl.answer("item:n1", "yes")
    b = tl.answer("item:n2", "no")
    lines = [json.loads(x) for x in (home / "laya_labels.jsonl").read_text(encoding="utf-8").splitlines()]
    assert [r["answer"] for r in lines] == ["yes", "no"]
    assert lines[0]["receipt"] == a["receipt"] and lines[1]["receipt"] == b["receipt"]
    assert b["receipt"] == tl.receipt_of(a["receipt"], {k: v for k, v in b.items() if k != "receipt"})
    assert tl.chain_ok()


def test_an_edited_answer_breaks_the_chain(home):
    _queue(home, _item("n1"), _item("n2"))
    tl.answer("item:n1", "yes")
    tl.answer("item:n2", "no")
    p = home / "laya_labels.jsonl"
    p.write_text(p.read_text(encoding="utf-8").replace('"answer": "yes"', '"answer": "no"', 1), encoding="utf-8")
    assert not tl.chain_ok()


def test_an_answer_for_an_item_not_on_the_list_or_not_yes_no_skip_is_refused(home):
    _queue(home, _item("n1"))
    with pytest.raises(ValueError):
        tl.answer("item:zzz", "yes")
    with pytest.raises(ValueError):
        tl.answer("item:n1", "maybe")
    assert not (home / "laya_labels.jsonl").exists()


def test_an_answered_or_skipped_item_leaves_the_waiting_list(home):
    _queue(home, _item("n1"), _item("n2"), _item("n3"))
    tl.answer("item:n1", "skip")
    tl.answer("item:n2", "yes")
    q = tl.queue()
    assert [i["id"] for i in q["items"]] == ["item:n3"] and q["answered"] == 2 and q["total"] == 3


def test_triage_answers_do_not_touch_the_approval_gate_labels(home):
    _queue(home, _item("n1"))
    tl.answer("item:n1", "yes")
    assert laya_labels.labels() == {}


# -- scoring -----------------------------------------------------------------

def test_the_score_counts_misses_false_alarms_and_skips_per_head(home):
    _queue(home,
           _item("a", "notify", "needs_you", "fyi"),      # owner yes: head right, incumbent missed
           _item("b", "notify", "needs_you", "fyi"),      # owner no: head false alarm, incumbent right
           _item("c", "notify", "fyi", "fyi"),            # owner yes: both missed
           _item("d", "news", "promo", "analysis"),       # owner yes: head missed, incumbent right
           _item("e", "news", "promo", "analysis"),       # owner no: incumbent false alarm, head right
           _item("f", "news", "promo", "analysis"))       # skipped
    for h, ans in (("a", "yes"), ("b", "no"), ("c", "yes"), ("d", "yes"), ("e", "no"), ("f", "skip")):
        tl.answer("item:" + h, ans)
    s = tl.score()
    assert s["notify"]["head"] == {"right": 1, "missed": 1, "false_alarm": 1}
    assert s["notify"]["incumbent"] == {"right": 1, "missed": 2, "false_alarm": 0}
    assert s["notify"]["head_only_right"] == 1 and s["notify"]["incumbent_only_right"] == 1
    assert s["news"]["head"] == {"right": 1, "missed": 1, "false_alarm": 0}
    assert s["news"]["incumbent"] == {"right": 1, "missed": 0, "false_alarm": 1}
    assert s["news"]["skipped"] == 1 and s["news"]["answered"] == 2


def test_a_changed_mind_scores_once_by_the_newest_answer(home):
    _queue(home, _item("a", "notify", "needs_you", "fyi"))
    tl.answer("item:a", "no")
    tl.answer("item:a", "yes")
    s = tl.score()["notify"]
    assert s["answered"] == 1 and s["owner_yes"] == 1 and s["head"]["right"] == 1


# -- building ----------------------------------------------------------------

def test_build_skips_items_already_answered_and_writes_the_list(home, monkeypatch):
    rows = [dict(_row("notify", "n1", "needs_you", "fyi"), ts=1), dict(_row("notify", "n2", "needs_you", "fyi"), ts=2)]
    (home / "triage_shadow.jsonl").write_text("\n".join(json.dumps(r) for r in rows), encoding="utf-8")
    monkeypatch.setattr(tl, "recover_texts", lambda wanted: {h: "words " + h for h in wanted})
    tl.build()
    tl.answer("item:n1", "yes")
    out = tl.build()
    assert out["items"] == 1
    got = json.loads((home / "laya_triage_queue.json").read_text(encoding="utf-8"))["items"]
    assert [i["id"] for i in got] == ["item:n2"]


# -- what counts as a disagreement ---------------------------------------------

def test_two_labels_that_mean_the_same_answer_are_not_a_disagreement():
    rows = _rows(_row("notify", "n1", "other", "fyi", 0.3),          # the head abstains: both say "no"
                 _row("news", "w1", "hard", "analysis", 0.9),         # both mean "worth it"
                 _row("news", "w2", "other", "promo", 0.9),           # both mean "not worth it"
                 _row("news", "w3", "promo", "analysis", 0.9))        # opposite
    got = {i["id"]: i["kind"] for i in tl.choose(rows, {h: "x" for h in rows})}
    assert got == {"item:n1": "edge", "item:w1": "edge", "item:w2": "edge", "item:w3": "disagree"}
    assert list(got)[:2] == ["item:w3", "item:n1"] or got["item:w3"] == "disagree"


def test_opposite_answers_are_asked_before_look_alikes_even_when_the_look_alikes_are_surer():
    rows = _rows(_row("news", "w1", "hard", "analysis", 0.99), _row("news", "w2", "promo", "analysis", 0.2))
    assert [i["id"] for i in tl.choose(rows, {h: "x" for h in rows})] == ["item:w2", "item:w1"]


def test_a_negative_limit_asks_nothing():
    rows = _rows(_row("news", "w1", "promo", "analysis"))
    assert tl.choose(rows, {"w1": "x"}, limit=-3) == []


# -- damaged files and a torn line ---------------------------------------------

@pytest.mark.parametrize("damage", ['[1, 2]', '{"items": "no"}', '{"items": [1, {"id": "item:a"}, null]}', 'not json'])
def test_a_damaged_queue_file_asks_nothing_and_never_raises(home, damage):
    (home / "laya_triage_queue.json").write_text(damage, encoding="utf-8")
    assert tl.queue() == {"items": [], "total": 0, "answered": 0}
    with pytest.raises(ValueError):
        tl.answer("item:a", "yes")


def test_one_bad_item_does_not_hide_the_good_ones(home):
    (home / "laya_triage_queue.json").write_text(json.dumps({"items": [{"id": "item:x"}, _item("ok")]}), encoding="utf-8")
    assert [i["id"] for i in tl.queue()["items"]] == ["item:ok"]


def test_a_labels_file_with_odd_rows_still_reads(home):
    _queue(home, _item("n1"))
    (home / "laya_labels.jsonl").write_text('{"question": "triage_notify", "subject": ["x"]}'+ chr(10) + "not json" + chr(10), encoding="utf-8")
    assert [i["id"] for i in tl.queue()["items"]] == ["item:n1"]


def test_an_answer_after_a_torn_last_line_is_its_own_line(home):
    _queue(home, _item("n1"))
    (home / "laya_labels.jsonl").write_bytes(b'{"question": "triage_notify", "sub')
    tl.answer("item:n1", "yes")
    lines = (home / "laya_labels.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2 and json.loads(lines[1])["answer"] == "yes"


# -- the stores the words come from --------------------------------------------

def test_notification_words_are_title_then_body_as_the_head_read_them(home, monkeypatch):
    from agent_friday import core
    monkeypatch.setattr(core, "FRIDAY_DIR", home)
    (home / "notifications.json").write_text(json.dumps({"items": [{"title": "Start My Day", "body": "ran"}, 7]}), encoding="utf-8")
    assert list(tl._notification_texts()) == ["Start My Day. ran"]


def test_news_titles_come_from_the_archive_in_either_shape(home, monkeypatch):
    from agent_friday.services import news_engine
    monkeypatch.setattr(news_engine, "NEWS_ARCHIVE_DIR", home)
    (home / "2026-11-20.json").write_text(json.dumps([{"title": "A"}, {"x": 1}]), encoding="utf-8")
    (home / "2026-11-21.json").write_text(json.dumps({"articles": [{"title": "B"}]}), encoding="utf-8")
    assert list(tl._news_titles()) == ["B", "A"]


# -- what this module may not do ------------------------------------------------

def test_the_module_imports_nothing_that_could_send_a_word_off_the_pc():
    import ast
    import inspect
    tree = ast.parse(inspect.getsource(tl))
    imported = {a.name.split(".")[0] for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}
    imported |= {n.module.split(".")[0] for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) and n.module}
    assert not imported & {"requests", "httpx", "urllib", "http", "socket", "aiohttp", "smtplib", "websockets"}


def test_answering_never_changes_a_head_s_mode(home, monkeypatch):
    _queue(home, _item("n1"))
    monkeypatch.setattr(triage_heads, "mode", lambda name: pytest.fail("a head's mode was read or set"))
    monkeypatch.setattr(triage_heads, "set_mode", lambda *a, **k: pytest.fail("a head's mode was set"), raising=False)
    tl.answer("item:n1", "yes")

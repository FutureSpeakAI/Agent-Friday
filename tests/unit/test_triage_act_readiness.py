"""DM7 gate for the triage heads: what has to hold before any of them may act.

Spec: ftv/program/specs/decision_models_STORM_2026-10-24.md (DM3 owed items,
DM7). Three things, each tested here:

  request path   an act-mode reading on a request or a push never waits for the
                 encoder: a miss with the encoder busy gives no reading now (the
                 incumbent answers) and one is queued, so the next sighting has it
  stale cache    a cached reading older than its freshness, or scored under
                 another head tag, is still answered at once and refreshed behind
  shadow dedupe  one shadow row per (head, text, incumbent, label, tag, day)

The encoder is `hashed_embedder` (a deterministic bag of words); nothing here
loads a model, touches the network or reads the owner's files. On the base
branch none of `cache_stats`, `_append_shadow` or `request_path` exists.
"""
from __future__ import annotations

import json

import pytest

from agent_friday.services import message_triage as mt
from agent_friday.services import notification_policy as pol
from agent_friday.services import triage_heads as th

CAREER_SEED = th.MAIL_SEEDS["career"][0]
URGENT_SEED = th.NOTIFY_SEEDS["needs_you"][3]
NO_RULES = {"lanes": []}
NO_SIGNALS = {"senders": {}}


@pytest.fixture(autouse=True)
def heads(tmp_path, monkeypatch):
    monkeypatch.setattr(th, "runtime_dir", lambda: tmp_path / "laya2")
    monkeypatch.setattr(th, "_off_record", lambda: False)
    monkeypatch.setattr(th, "RUN_INLINE", True)
    monkeypatch.delenv(th.DISABLE_ENV, raising=False)
    th.set_embedder(th.hashed_embedder())
    th._mode_override.clear()
    th.clear_mode_cache()
    th.reset_cache()
    for k in th._counters:
        th._counters[k] = 0
    assert th.warm()
    yield tmp_path
    th._mode_override.clear()
    th.set_embedder(None)
    th.reset_cache()
    th.drain(5.0)


def _classify(subject):
    return mt.classify({"sender": "someone@example.com", "subject": subject, "snippet": ""}, NO_RULES, NO_SIGNALS)


def _rows(tmp_path):
    p = tmp_path / "laya2" / "triage_shadow.jsonl"
    return [json.loads(l) for l in p.read_text("utf-8").splitlines()] if p.exists() else []


def _count_scores(monkeypatch):
    calls = []
    real = th._Head.score

    def counted(self, texts, **kw):
        calls.append((self.name, tuple(texts)))
        return real(self, texts, **kw)
    monkeypatch.setattr(th._Head, "score", counted)
    return calls


# -- the request path --------------------------------------------------------------

def _real_worker(monkeypatch):
    """The real worker thread, and a request budget generous enough that a slow
    CI runner does not abandon the ask before the reading is queued."""
    from agent_friday.services import laya
    monkeypatch.setattr(th, "RUN_INLINE", False)
    monkeypatch.setattr(laya, "TRIAGE_REQUEST_BUDGET_MS", 10000)


def _settle():
    end = __import__("time").time() + 10
    while th._pending and __import__("time").time() < end:
        th.drain(0.2)
    return th.drain(5.0) and not th._pending


def test_an_act_mode_mail_miss_with_a_busy_encoder_waits_for_nothing_and_learns(monkeypatch):
    _real_worker(monkeypatch)
    monkeypatch.setattr(th, "_encoder_free", lambda: False)
    _mode = th._mode_override
    _mode["mail"] = "fallback"
    assert _classify(CAREER_SEED)[0] == mt._fallback_lane()      # no reading yet: the incumbent answers
    assert _settle()
    assert th.cache_stats()["miss_queued"] == 1 and th.cache_stats()["refreshed"] == 1
    assert _classify(CAREER_SEED)[0] == "career"                  # the queued reading is used next time


def test_an_act_mode_notice_with_a_busy_encoder_is_left_as_the_policy_said(monkeypatch):
    _real_worker(monkeypatch)
    monkeypatch.setattr(th, "_encoder_free", lambda: False)
    th._mode_override["notify"] = "escalate"
    assert pol.tier_for("sync", "worker", "low", None, URGENT_SEED) == pol.FYI
    assert _settle()
    assert pol.tier_for("sync", "worker", "low", None, URGENT_SEED) == pol.NEEDS_YOU


def test_with_the_encoder_free_a_request_path_miss_is_read_at_once():
    th._mode_override["mail"] = "fallback"
    assert _classify(CAREER_SEED)[0] == "career"
    assert th.cache_stats()["miss_inline"] == 1


def test_a_request_path_reading_skips_the_extra_embeddings_in_the_laya_preset(monkeypatch):
    from agent_friday.services import laya
    calls = []
    monkeypatch.setattr(laya, "_calibrated_triage", lambda *a, **k: calls.append(1))
    got = laya.triage_read("mail", [th.mail_text(CAREER_SEED, "")], cached=True, request_path=True)
    assert got and max(got[0], key=got[0].get) == "career"        # it did read
    assert calls == []
    laya.triage_read("mail", [th.mail_text(CAREER_SEED, "")], cached=True)
    assert calls == [1]


# -- stale while revalidate ----------------------------------------------------------

def test_a_fresh_hit_is_not_scored_again(monkeypatch):
    calls = _count_scores(monkeypatch)
    th._mode_override["mail"] = "fallback"
    _classify(CAREER_SEED)
    _classify(CAREER_SEED)
    assert len(calls) == 1 and th.cache_stats()["hit"] == 1


def test_a_stale_reading_answers_at_once_and_is_refreshed_behind(monkeypatch):
    calls = _count_scores(monkeypatch)
    th._mode_override["mail"] = "fallback"
    assert _classify(CAREER_SEED)[0] == "career"
    monkeypatch.setattr(th, "_CACHE_FRESH_S", -1.0)                # everything is now stale
    assert _classify(CAREER_SEED)[0] == "career"                   # answered from the stale entry
    assert th.cache_stats()["stale"] == 1 and th.cache_stats()["refreshed"] == 1
    assert len(calls) == 2                                          # the refresh, on the worker


def test_a_new_head_tag_does_not_re_embed_the_scores_only_the_decision_changes(monkeypatch):
    calls = _count_scores(monkeypatch)
    th._mode_override["mail"] = "fallback"
    _classify(CAREER_SEED)
    monkeypatch.setattr(th, "tag", lambda name: "v2-new")
    _classify(CAREER_SEED)
    assert len(calls) == 1 and th.cache_stats()["stale"] == 0


def test_a_refresh_queued_before_going_off_the_record_reads_nothing(monkeypatch):
    calls = _count_scores(monkeypatch)
    text = th.mail_text(CAREER_SEED, "")
    th._cached_score("mail", text)
    monkeypatch.setattr(th, "_CACHE_FRESH_S", -1.0)
    monkeypatch.setattr(th, "_off_record", lambda: True)     # the job runs after the owner went off
    th._cached_score("mail", text)
    assert len(calls) == 1 and th.cache_stats()["refreshed"] == 0


def test_a_reading_that_started_before_a_reset_is_not_stored_after_it(monkeypatch):
    real = th._Head.score

    def reset_midway(self, texts, **kw):
        out = real(self, texts, **kw)
        th.reset_cache()                                      # an encoder or seed change meanwhile
        return out
    monkeypatch.setattr(th._Head, "score", reset_midway)
    th._cached_score("mail", "a message about a job interview next week")
    assert len(th._cache) == 0


def test_the_cache_forgets_its_oldest_reading_not_all_of_them(monkeypatch):
    monkeypatch.setattr(th, "_CACHE_MAX", 2)
    for t in ("first message about a job interview", "second message about a bill", "third about a parcel"):
        th._cached_score("mail", t)
    keys = [k[1].split("|")[0] for k in th._cache]
    assert keys == [th.digest("second message about a bill"), th.digest("third about a parcel")]


# -- shadow-row dedupe ---------------------------------------------------------------

def test_a_message_drawn_twice_leaves_one_shadow_row(heads):
    th._mode_override["mail"] = "shadow"
    _classify(CAREER_SEED)
    _classify(CAREER_SEED)
    rows = _rows(heads)
    assert len(rows) == 1 and th._counters.get("deduped") == 1


def test_a_news_pool_seen_twice_leaves_one_row_per_story(heads):
    pool = [{"title": "Senate passes the budget bill after an overnight vote"},
            {"title": "How to file your taxes before the deadline"}]
    th._mode_override["news"] = "shadow"
    th.news_shadow(pool)
    th.news_shadow(pool)
    assert len(_rows(heads)) == 2


def test_another_incumbent_or_label_is_another_row(heads):
    r1 = th._shadow_row("mail", "same text", "career", "inbox", 0.7)
    r2 = th._shadow_row("mail", "same text", "career", "updates", 0.7)
    r3 = th._shadow_row("mail", "same text", "finance", "inbox", 0.7)
    assert th._append_shadow([r1, r1, r2, r3]) == 3


def test_a_row_that_could_not_be_written_is_tried_again(heads, monkeypatch):
    row = th._shadow_row("mail", "same text", "career", "inbox", 0.7)
    real = th._append
    monkeypatch.setattr(th, "_append", lambda path, rows: False)
    assert th._append_shadow([row]) == 0
    monkeypatch.setattr(th, "_append", real)
    assert th._append_shadow([row]) == 1

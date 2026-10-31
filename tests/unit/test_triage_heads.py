"""DA-6 / DM3: background triage on the shared encoder (services/triage_heads).

Ported from the unlanded b11 track (998e35846, 84e0e7906) for the decision-models
spec's DM3. Modes and thresholds now come from the decision heads
(services/decision_heads); the Settings keys of the b11 version are gone, so
their documentation and UI tests are too. On main before this change there is
no services/triage_heads.py and this module fails at import.

Four small heads sit beside four incumbents. What each may do is the point of
these tests, so every "never" is paired with a positive control that fails when
the head is not wired in at all:

  mail           moves ONLY mail nothing matched, ONLY toward an actionable lane
  news           shadow only: it logs, the order the Front Page uses is untouched
  notifications  raises FYI of an unnamed kind to NEEDS_YOU, nothing else
  memory         pending proposals only; user_model is never written

The encoder is replaced by `hashed_embedder` (a deterministic bag of words), so
nothing here loads a model, touches the network or reads the owner's files.
"""
from __future__ import annotations

import importlib.util
import json
import re
import socket
import sys
from pathlib import Path

import pytest

from agent_friday.services import message_triage as mt
from agent_friday.services import notification_policy as pol
from agent_friday.services import triage_heads as th

ROOT = Path(__file__).resolve().parents[2]
FIXTURES = ROOT / "tests" / "fixtures"

#: Texts that are exactly a seed: under the bag-of-words embedder they sit on top
#: of their class prototype, so the head's reading is not in doubt.
CAREER_SEED = th.MAIL_SEEDS["career"][0]
SUBS_SEED = th.MAIL_SEEDS["subscriptions"][0]
URGENT_SEED = th.NOTIFY_SEEDS["needs_you"][3]
FYI_SEED = th.NOTIFY_SEEDS["fyi"][0]


@pytest.fixture(autouse=True)
def heads(tmp_path, monkeypatch):
    """A hermetic head: fake encoder, inline shadow work, logs under tmp_path."""
    monkeypatch.setattr(th, "runtime_dir", lambda: tmp_path / "laya2")
    monkeypatch.setattr(th, "_off_record", lambda: False)
    monkeypatch.setattr(th, "RUN_INLINE", True)
    monkeypatch.delenv(th.DISABLE_ENV, raising=False)
    th.set_embedder(th.hashed_embedder())
    th._mode_override.clear()
    th.clear_mode_cache()
    for k in th._counters:
        th._counters[k] = 0
    assert th.warm()
    yield tmp_path
    th._mode_override.clear()
    th.set_embedder(None)
    th.reset_cache()


def _mode(**modes):
    th._mode_override.update(modes)


def _rows(tmp_path, name="triage_shadow.jsonl"):
    p = tmp_path / "laya2" / name
    if not p.exists():
        return []
    return [json.loads(l) for l in p.read_text(encoding="utf-8").splitlines() if l.strip()]


NO_RULES = {"lanes": []}
NO_SIGNALS = {"senders": {}}


def _classify(subject, snippet, *, sender="someone@example.com", rules=NO_RULES, signals=NO_SIGNALS):
    return mt.classify({"sender": sender, "subject": subject, "snippet": snippet}, rules, signals)


# ---------------------------------------------------------------------------
#  MAIL
# ---------------------------------------------------------------------------

def test_the_mail_head_files_unmatched_mail_into_an_important_lane_in_fallback_mode():
    _mode(mail="off")
    lane, conf, _ = _classify(CAREER_SEED, "")
    assert (lane, conf) == (mt._fallback_lane(), 0.0)      # nothing matched: the old answer
    _mode(mail="fallback")
    lane, conf, why = _classify(CAREER_SEED, "")
    assert lane == "career"
    assert 0.0 < conf <= 0.5 and "no rule matched" in why[0]


def test_the_mail_head_never_overrides_a_rule_hit():
    rules = {"lanes": [{"id": "finance", "keywords": ["invoice"]}]}
    _mode(mail="off")
    base = _classify("Invoice " + CAREER_SEED, "", rules=rules)
    _mode(mail="fallback")
    got = _classify("Invoice " + CAREER_SEED, "", rules=rules)
    assert base[0] == "finance" and got == base


def test_the_mail_head_never_overrides_a_learned_sender(monkeypatch):
    signals = {"senders": {"boss@example.com": {"lanes": {"family": 3}}}}
    _mode(mail="fallback")
    lane, _, why = _classify(CAREER_SEED, "", sender="boss@example.com", signals=signals)
    assert lane == "family" and "learned" in why[0]


def test_a_penalised_rule_hit_is_still_a_rule_hit_the_head_leaves_alone(monkeypatch):
    # A bulk sender with one body keyword scores below zero, which falls back to
    # the quiet lane. A rule did speak, so the head is not even asked.
    class Asked(BaseException):          # not an Exception: classify must not be able to swallow it
        pass

    def boom(*a, **k):
        raise Asked("the head was asked about a message a rule matched")
    monkeypatch.setattr(th, "mail_fallback", boom)
    _mode(mail="fallback")
    rules = {"lanes": [{"id": "finance", "keywords": ["statement"]}]}
    lane, conf, _ = _classify("Hello", "your statement", sender="noreply@example.com", rules=rules)
    assert (lane, conf) == (mt._fallback_lane(), 0.0)


def test_the_mail_head_only_moves_toward_important_lanes():
    _mode(mail="fallback")
    # a newsletter reading is a quiet lane: the fallback stays
    assert _classify(SUBS_SEED, "")[0] == mt._fallback_lane()


def test_the_mail_head_never_moves_mail_off_an_actionable_fallback(monkeypatch):
    monkeypatch.setattr(mt, "_fallback_lane", lambda: "finance")
    _mode(mail="fallback")
    assert _classify(CAREER_SEED, "")[0] == "finance"


def test_the_mail_head_in_shadow_changes_nothing_and_logs_labels_only(heads):
    _mode(mail="shadow")
    lane, conf, _ = _classify(CAREER_SEED, "")
    assert (lane, conf) == (mt._fallback_lane(), 0.0)
    rows = _rows(heads)
    assert len(rows) == 1 and rows[0]["head"] == "mail" and rows[0]["label"] == "career"
    assert rows[0]["incumbent"] == mt._fallback_lane() and re.fullmatch(r"[0-9a-f]{16}", rows[0]["h"])


def test_a_head_that_is_off_or_has_no_encoder_leaves_classify_as_it_was(monkeypatch):
    _mode(mail="off")
    assert _classify(CAREER_SEED, "")[0] == mt._fallback_lane()
    _mode(mail="fallback")
    th.set_embedder(lambda texts: None)          # an encoder that is not there
    th.reset_cache()
    assert _classify(CAREER_SEED, "")[0] == mt._fallback_lane()


def test_a_mail_correction_is_a_dagger_row_with_hashes_and_labels_only(heads):
    assert th.note_correction("mail", text=th.mail_text(CAREER_SEED, "secret words here"),
                              owner_label="finance", head_label="career", ref="msg-1", source="reclassify")
    row = _rows(heads, "triage_corrections.jsonl")[0]
    assert row["owner_label"] == "finance" and row["head_label"] == "career"
    assert re.fullmatch(r"[0-9a-f]{16}", row["h"]) and re.fullmatch(r"[0-9a-f]{16}", row["ref_h"])
    blob = json.dumps(row)
    assert "secret" not in blob and "msg-1" not in blob and "Interview" not in blob


# ---------------------------------------------------------------------------
#  NEWS (shadow only)
# ---------------------------------------------------------------------------

def _pool():
    return [{"title": "Sponsored: discover the new smartwatch everyone is talking about", "source": "a.example"},
            {"title": "Senate passes the budget bill after an overnight vote", "source": "b.example"},
            {"title": "How to file your taxes before the deadline", "source": "c.example"}]


def test_the_news_head_in_shadow_changes_no_ranking(heads):
    from agent_friday.services import news_seen
    pool = _pool()
    before = [dict(p) for p in pool]
    expected = [p["title"] for p in news_seen.rank(list(pool))]
    _mode(news="shadow")
    assert th.news_shadow(pool)                      # it ran...
    assert pool == before                            # ...read only
    assert [p["title"] for p in news_seen.rank(list(pool))] == expected
    rows = _rows(heads)
    assert len(rows) == 3 and {r["head"] for r in rows} == {"news"}


def test_the_blended_order_is_computed_but_nothing_uses_it():
    pool = _pool()
    inc = ["analysis", "analysis", "analysis"]       # the regex calls them all alike
    scores = th.head("news").score([p["title"] for p in pool], block=True)
    order = th.blended_order(pool, inc, scores)
    assert order[0] == 1 and sorted(order) == [0, 1, 2]     # the head would put the hard story first
    assert pool[0]["title"].startswith("Sponsored")          # and the pool is as it was


def test_news_off_does_nothing(heads):
    _mode(news="off")
    assert not th.news_shadow(_pool())
    assert _rows(heads) == []


# ---------------------------------------------------------------------------
#  NOTIFICATIONS (escalate-only)
# ---------------------------------------------------------------------------

def test_the_notification_head_raises_an_urgent_notice_of_an_unnamed_kind():
    _mode(notify="off")
    assert pol.tier_for("sync", "worker", "low", None, URGENT_SEED) == pol.FYI
    _mode(notify="escalate")
    assert pol.tier_for("sync", "worker", "low", None, URGENT_SEED) == pol.NEEDS_YOU
    assert pol.tier_for("sync", "worker", "low", None, FYI_SEED) == pol.FYI


@pytest.mark.parametrize("mode", ["off", "shadow", "escalate"])
def test_needs_you_is_never_demoted(mode):
    _mode(notify=mode)
    for kind in sorted(pol.NEEDS_YOU_KINDS):
        assert pol.tier_for(kind, "worker", "low", None, FYI_SEED) == pol.NEEDS_YOU
    assert pol.tier_for("approval_pending", "gate", "low", None, FYI_SEED) == pol.NEEDS_YOU
    assert pol.tier_for("info", "worker", "critical", None, FYI_SEED) == pol.NEEDS_YOU
    assert pol.tier_for("warning", "worker", "high", None, FYI_SEED) == pol.NEEDS_YOU
    # an emitter that names NEEDS_YOU keeps it
    assert pol.tier_for("sync", "worker", "low", pol.NEEDS_YOU, FYI_SEED) == pol.NEEDS_YOU


def test_the_head_never_touches_log_only_or_a_tier_the_emitter_named():
    _mode(notify="escalate")
    assert pol.tier_for("build", "lane", "low", None, URGENT_SEED) == pol.LOG_ONLY
    assert pol.tier_for("info", "network", "low", None, URGENT_SEED) == pol.LOG_ONLY
    assert pol.tier_for("sync", "worker", "low", pol.FYI, URGENT_SEED) == pol.FYI
    assert pol.tier_for("sync", "worker", "low", pol.LOG_ONLY, URGENT_SEED) == pol.LOG_ONLY


def test_notification_tier_can_only_move_up_from_fyi():
    _mode(notify="escalate")
    for base in (pol.NEEDS_YOU, pol.FYI, pol.LOG_ONLY):
        for kind in ("sync", "security", "build"):
            for text in (FYI_SEED, URGENT_SEED):
                got = th.notification_tier(base, kind, "worker", "low", text,
                                           needs_you=pol.NEEDS_YOU, known_kinds=pol._named_kinds())
                raised = base == pol.FYI and kind == "sync" and text == URGENT_SEED
                assert got == (pol.NEEDS_YOU if raised else base), (base, kind, text)


def test_the_notification_head_in_shadow_changes_nothing_and_logs(heads):
    _mode(notify="shadow")
    assert pol.tier_for("sync", "worker", "low", None, URGENT_SEED) == pol.FYI
    rows = _rows(heads)
    assert len(rows) == 1 and rows[0]["head"] == "notify" and rows[0]["label"] == "needs_you"
    assert rows[0]["incumbent"] == pol.FYI


def test_a_notice_without_text_is_decided_by_the_policy_alone():
    _mode(notify="escalate")
    assert pol.tier_for("sync", "worker", "low") == pol.FYI


# ---------------------------------------------------------------------------
#  MEMORY (proposals only)
# ---------------------------------------------------------------------------

@pytest.fixture
def proposals(tmp_path, monkeypatch):
    from agent_friday.services import memory_proposals as mp
    monkeypatch.setattr(mp, "DB_PATH", tmp_path / "dreams.db")
    monkeypatch.setattr(mp, "FRIDAY_DIR", tmp_path)
    return mp


TURNS = [{"role": "user", "text": th.MEMORY_SEEDS["preference"][0] + ". " + th.MEMORY_SEEDS["none"][0]},
         {"role": "assistant", "text": th.MEMORY_SEEDS["deadline"][0] + "."},
         {"role": "user", "text": th.MEMORY_SEEDS["deadline"][1]}]


@pytest.fixture
def no_user_model(monkeypatch):
    from agent_friday.services import user_model

    def boom(*a, **k):
        raise AssertionError("the triage head wrote user_model")
    monkeypatch.setattr(user_model, "note_fact", boom)


def test_the_memory_head_proposes_pending_facts_and_never_writes_user_model(proposals, no_user_model):
    _mode(memory="propose")
    rep = th.memory_pass(TURNS, "2026-10-08", [])
    assert rep["mode"] == "propose" and rep["proposed"] == 2
    pend = proposals.pending()
    assert {p["category"] for p in pend} == {"preference", "constraint"}
    assert all(p["confidence"] == proposals.MODEL_CONFIDENCE < 0.6 for p in pend)
    # nothing is durable until the owner approves; the assistant's turn was never read
    assert not any("deadline for the report" in p["text"] for p in pend)


def test_the_memory_head_in_shadow_proposes_nothing(proposals, no_user_model, heads):
    _mode(memory="shadow")
    rep = th.memory_pass(TURNS, "2026-10-08", [])
    assert rep["proposed"] == 0 and proposals.pending() == []
    assert {r["head"] for r in _rows(heads)} == {"memory"}


def test_dream_feeds_the_head_without_giving_it_a_path_to_user_model(proposals, no_user_model, monkeypatch, tmp_path):
    from agent_friday.services import memory_dreaming as md
    monkeypatch.setattr(md, "DB_PATH", tmp_path / "dreams.db")
    monkeypatch.setattr(md, "DREAMS_DIR", tmp_path / "dreams")
    monkeypatch.setattr(md, "FRIDAY_DIR", tmp_path)

    class Memory:
        def recent(self, n=0):
            return [{"role": "user", "date": "2026-10-08", "text": th.MEMORY_SEEDS["deadline"][2]}]
    _mode(memory="propose")
    out = md.dream("2026-10-08", memory=Memory())
    assert out["ok"] and out["consolidated"] == []        # the regex found nothing
    assert [p["category"] for p in proposals.pending()] == ["constraint"]


def test_memory_proposals_dedupe_and_stay_pending(proposals):
    cands = [{"category": "bio", "text": "I work as a nurse at a clinic", "p": 0.9}]
    assert proposals.propose_from_head(cands, "2026-10-08") == 1
    assert proposals.propose_from_head(cands, "2026-10-08") == 0
    assert proposals.state()["pending"] == 1


# ---------------------------------------------------------------------------
#  NO CONTENT, OFF RECORD, NO NETWORK
# ---------------------------------------------------------------------------

def _words(*texts):
    return {w for t in texts for w in re.findall(r"[a-z]{5,}", t.lower())}


def test_no_log_holds_a_word_of_the_content(heads, proposals):
    _mode(mail="shadow", news="shadow", notify="shadow", memory="shadow")
    secret = "Zebrafish quarterly pelican"
    _classify(CAREER_SEED + " " + secret, "")
    th.news_shadow([{"title": "Senate passes the budget bill " + secret, "source": "x"}])
    pol.tier_for("sync", "worker", "low", None, URGENT_SEED + " " + secret)
    th.memory_pass([{"role": "user", "text": th.MEMORY_SEEDS["bio"][0] + " " + secret + " ok"}], "2026-10-08", [])
    th.note_correction("news", text="Senate passes " + secret, owner_label="not_interested")
    seen = 0
    for name in ("triage_shadow.jsonl", "triage_corrections.jsonl"):
        p = heads / "laya2" / name
        text = p.read_text(encoding="utf-8")
        seen += len(text.splitlines())
        low = text.lower()
        for w in _words(secret, CAREER_SEED, URGENT_SEED, th.MEMORY_SEEDS["bio"][0], "senate passes the budget bill"):
            assert w not in low, w
        for line in text.splitlines():
            row = json.loads(line)
            assert re.fullmatch(r"[0-9a-f]{16}", row["h"])
            assert not any(isinstance(v, str) and len(v) > 40 for v in row.values())
    assert seen >= 5


def test_nothing_is_computed_or_written_off_the_record(heads, monkeypatch, proposals):
    monkeypatch.setattr(th, "_off_record", lambda: True)
    _mode(mail="fallback", news="shadow", notify="escalate", memory="propose")
    assert _classify(CAREER_SEED, "")[0] == mt._fallback_lane()
    assert pol.tier_for("sync", "worker", "low", None, URGENT_SEED) == pol.FYI
    assert not th.news_shadow(_pool())
    assert th.memory_pass(TURNS, "2026-10-08", [])["proposed"] == 0
    assert not th.note_correction("mail", text="x" * 10, owner_label="finance")
    assert not (heads / "laya2").exists() and proposals.pending() == []


def test_the_heads_work_with_the_network_cut(monkeypatch, proposals):
    def no_net(*a, **k):
        raise AssertionError("a triage head touched the network")
    monkeypatch.setattr(socket.socket, "connect", no_net)
    monkeypatch.setattr(socket, "create_connection", no_net)
    _mode(mail="fallback", news="shadow", notify="escalate", memory="propose")
    assert _classify(CAREER_SEED, "")[0] == "career"
    assert pol.tier_for("sync", "worker", "low", None, URGENT_SEED) == pol.NEEDS_YOU
    assert th.news_shadow(_pool())
    assert th.memory_pass(TURNS, "2026-10-08", [])["proposed"] == 2


def test_the_module_imports_no_network_library():
    src = (ROOT / "src" / "agent_friday" / "services" / "triage_heads.py").read_text(encoding="utf-8")
    assert not re.search(r"^\s*(?:import|from)\s+(?:requests|urllib|http|socket|httpx|aiohttp)\b", src, re.M)


# ---------------------------------------------------------------------------
#  MODES AND THRESHOLDS COME FROM THE DECISION HEADS (DM3)
# ---------------------------------------------------------------------------

def test_every_head_ships_in_shadow_from_its_decision_head():
    th._mode_override.clear()
    th.clear_mode_cache()
    assert [th.mode(h) for h in th.HEADS] == ["shadow"] * 4
    for h in th.HEADS:
        assert th.tag(h), h                       # a version tag rides every shadow row
    th.clear_mode_cache()


@pytest.mark.parametrize("dmode,expect", [
    ("off", {"mail": "off", "news": "off", "notify": "off", "memory": "off"}),
    ("shadow", {"mail": "shadow", "news": "shadow", "notify": "shadow", "memory": "shadow"}),
    ("suggest", {"mail": "shadow", "news": "shadow", "notify": "shadow", "memory": "propose"}),
    ("act", {"mail": "fallback", "news": "shadow", "notify": "escalate", "memory": "propose"}),
])
def test_a_decision_head_mode_maps_to_each_heads_own_mode(monkeypatch, dmode, expect):
    from agent_friday.services import decision_heads
    th._mode_override.clear()
    th.clear_mode_cache()
    monkeypatch.setattr(decision_heads, "effective",
                        lambda hid: {"id": hid, "mode": dmode, "thresholds": {"act": 0.5, "abstain": 0.2},
                                     "tag": "v1-test"})
    assert {h: th.mode(h) for h in th.HEADS} == expect
    th.clear_mode_cache()


def test_a_missing_or_refused_decision_head_turns_the_head_off(monkeypatch):
    from agent_friday.services import decision_heads
    th._mode_override.clear()
    th.clear_mode_cache()
    monkeypatch.setattr(decision_heads, "effective", lambda hid: None)
    assert [th.mode(h) for h in th.HEADS] == ["off"] * 4
    asked = []
    monkeypatch.setattr(th, "_read", lambda *a, **k: asked.append(a) or [{"career": 0.99}])
    assert _classify(CAREER_SEED, "")[0] == mt._fallback_lane()     # off from effective() itself
    assert asked == [] and _rows_now() == []
    th.clear_mode_cache()


def _rows_now():
    p = th.shadow_path()
    return p.read_text(encoding="utf-8").splitlines() if p.exists() else []


def test_bulk_mail_is_never_lifted_by_the_head(monkeypatch):
    _mode(mail="fallback")
    lane, conf, _ = mt.classify({"sender": "noreply@shop.example", "subject": CAREER_SEED, "snippet": "",
                                 "labels": ["CATEGORY_PROMOTIONS"]}, NO_RULES, NO_SIGNALS)
    assert (lane, conf) == (mt._fallback_lane(), 0.0)


def test_a_reclassify_correction_is_written_off_the_request_with_the_previous_lane(heads):
    _mode(mail="shadow")
    assert th.note_mail_correction(CAREER_SEED, "", owner_lane="finance", previous_lane="career", ref="m-1")
    row = _rows(heads, "triage_corrections.jsonl")[0]
    assert row["owner_label"] == "finance" and row["incumbent"] == "career" and row["head_label"] == "career"
    assert row["source"] == "reclassify" and "m-1" not in json.dumps(row)


def test_a_reclassify_correction_computes_nothing_when_off_or_off_the_record(heads, monkeypatch):
    asked = []
    monkeypatch.setattr(th, "_read", lambda *a, **k: asked.append(a) or None)
    _mode(mail="off")
    assert not th.note_mail_correction(CAREER_SEED, "", owner_lane="finance", ref="m-1")
    _mode(mail="shadow")
    monkeypatch.setattr(th, "_off_record", lambda: True)
    assert not th.note_mail_correction(CAREER_SEED, "", owner_lane="finance", ref="m-1")
    assert asked == [] and not (heads / "laya2").exists()


def test_approving_a_head_proposal_files_it_as_a_day_proposal_not_a_meeting(proposals, monkeypatch):
    from agent_friday.services import user_model
    seen = []
    monkeypatch.setattr(user_model, "note_fact", lambda cat, text, confidence, source: seen.append(source))
    assert proposals.propose_from_head([{"category": "bio", "text": "I work as a nurse at a clinic"}],
                                       "2026-10-08") == 1
    fid = proposals.pending()[0]["fact_id"]
    proposals.approve([fid])
    assert seen == ["proposed:2026-10-08"]


def test_the_act_threshold_in_force_decides_not_the_shipped_constant(monkeypatch):
    from agent_friday.services import decision_heads
    th.clear_mode_cache()
    monkeypatch.setattr(decision_heads, "effective",
                        lambda hid: {"id": hid, "mode": "act", "thresholds": {"act": 0.9999, "abstain": 0.0},
                                     "tag": "v1-test"})
    _mode(mail="fallback")
    assert _classify(CAREER_SEED, "")[0] == mt._fallback_lane()      # not sure enough at 0.9999
    th.clear_mode_cache()
    monkeypatch.setattr(decision_heads, "effective",
                        lambda hid: {"id": hid, "mode": "act", "thresholds": {"act": 0.05, "abstain": 0.0},
                                     "tag": "v1-test"})
    th._cache.clear()
    assert _classify(CAREER_SEED, "")[0] == "career"
    th.clear_mode_cache()


def test_a_reading_below_the_abstain_threshold_is_other_never_a_guess(monkeypatch):
    from agent_friday.services import decision_heads
    th.clear_mode_cache()
    monkeypatch.setattr(decision_heads, "effective",
                        lambda hid: {"id": hid, "mode": "shadow", "thresholds": {"act": 0.6, "abstain": 0.5},
                                     "tag": "v1-test"})
    assert th.decide("mail", {"career": 0.4, "finance": 0.35, "family": 0.25}) == ("other", 0.4)
    assert th.decide("memory", {"bio": 0.3, "none": 0.2}) == ("none", 0.3)
    assert th.decide("mail", {"career": 0.7, "finance": 0.3}) == ("career", 0.7)
    th.clear_mode_cache()


def test_the_laya_triage_preset_answers_with_the_abstain_rule(monkeypatch):
    from agent_friday.services import laya
    seen = []
    monkeypatch.setattr(th, "decide", lambda h, s: seen.append(h) or ("other", 0.1))
    r = laya.ask({"text": CAREER_SEED, "surface": "background",
                  "opts": {"texts": [CAREER_SEED], "block": True, "log": False}},
                 {"triage.mail": {"type": "choice", "preset": "triage.mail"}}, "test.triage", budget_ms=5000)
    assert seen == ["mail"] and r["answers"]["triage.mail"]["value"] == "other"


def test_a_correction_is_not_recorded_for_a_head_that_is_off(heads):
    _mode(mail="off")
    assert not th.note_correction("mail", text=th.mail_text(CAREER_SEED, ""), owner_label="finance")
    assert _rows(heads, "triage_corrections.jsonl") == []


# ---------------------------------------------------------------------------
#  SPEED (fake clock: proves the arithmetic and that the gate can fail)
# ---------------------------------------------------------------------------

class _Clock:
    def __init__(self, step_s):
        self.t, self.step = 0.0, step_s
        self.calls = 0

    def __call__(self):
        self.calls += 1
        if self.calls % 2 == 0:      # the second reading of each pair: the work took `step`
            self.t += self.step
        return self.t


def test_the_speed_gate_passes_a_fast_head_and_fails_a_slow_one():
    texts = ["a short line of text number %d" % i for i in range(32)]
    fast = th.measure_ms_per_item("mail", texts, batch=16, clock=_Clock(0.160))      # 10 ms per item
    slow = th.measure_ms_per_item("mail", texts, batch=16, clock=_Clock(0.480))      # 30 ms per item
    assert fast["ok"] and fast["p95_ms"] == pytest.approx(10.0, abs=0.01) and fast["within_target"]
    assert slow["p95_ms"] == pytest.approx(30.0, abs=0.01) and not slow["within_target"]


def test_percentile_is_nearest_rank():
    assert th.percentile([1, 2, 3, 4, 100], 0.95) == 100
    assert th.percentile([5], 0.95) == 5 and th.percentile([], 0.95) == 0.0


# ---------------------------------------------------------------------------
#  EVAL SETS AND TOOL
# ---------------------------------------------------------------------------

def _tool():
    spec = importlib.util.spec_from_file_location("triage_eval_tool", ROOT / "tools" / "triage_eval.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["triage_eval_tool"] = mod
    spec.loader.exec_module(mod)
    return mod


def test_the_eval_sets_are_labelled_and_the_mail_rule_flags_are_true():
    mail = json.loads((FIXTURES / "triage_eval_mail.json").read_text(encoding="utf-8"))
    assert {i["label"] for i in mail["items"]} <= set(mt.lane_ids())
    _mode(mail="off")
    for it in mail["items"]:
        conf = _classify(it["subject"], it["snippet"], rules=mail["rules"])[1]
        assert (conf > 0) == it["rule_hit"], it["subject"]
    news = json.loads((FIXTURES / "triage_eval_news.json").read_text(encoding="utf-8"))
    assert {i["label"] for i in news["items"]} == set(th.NEWS_LABELS)
    notes = json.loads((FIXTURES / "triage_eval_notifications.json").read_text(encoding="utf-8"))
    assert {i["label"] for i in notes["items"]} == {"needs_you", "fyi", "log_only"}
    for it in notes["items"]:
        if it["label"] != "fyi" or it["kind"] in pol._named_kinds():
            continue
        assert pol.tier_for(it["kind"], it["source"], it["priority"]) == pol.FYI
    mem = json.loads((FIXTURES / "triage_eval_memory.json").read_text(encoding="utf-8"))
    assert {i["label"] for i in mem["items"]} == set(th.MEMORY_LABELS)


def test_the_eval_tool_holds_every_invariant_at_zero_and_counts_against_the_incumbent():
    tool = _tool()
    from agent_friday.services import memory_dreaming as md, news_seen as ns
    mail = tool.eval_mail(th, mt)
    notes = tool.eval_notifications(th, pol)
    assert mail["rule_hits_changed"] == 0 and mail["demotions"] == 0
    assert notes["needs_you_demoted"] == 0
    assert mail["N"] == 32 and notes["N"] == 28 and mail["incumbent"].endswith("/32")
    # a head that did nothing would score the incumbent's number: it must move something
    assert mail["moved"] > 0 and notes["escalated"] > 0
    news = tool.eval_news(th, ns)
    mem = tool.eval_memory(th, md)
    assert news["N"] == 40 and mem["N"] == 42
    assert th._mode_override == {}                  # the tool restores what it forced

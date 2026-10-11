"""Calibration and abstention for every Laya head (swot spec c.4), and the two
invariants that guard what learning and shipping may do (c.2 items 5 and 6).

Pure maths is tested on synthetic data with a fixed seed. The shipped table is
checked for consistency with the code it describes and for holding no words.
Nothing loads a model.
"""
from __future__ import annotations

import inspect
import json
import math
import random
from pathlib import Path

import pytest

from agent_friday.services import laya, laya_calibration as lc, laya_router as lr

ROOT = Path(__file__).resolve().parents[2]
FIX = ROOT / "tests" / "fixtures"


@pytest.fixture(autouse=True)
def clean(tmp_path, monkeypatch):
    laya.reset()
    monkeypatch.setattr(laya, "log_path", lambda: tmp_path / "ask.jsonl")
    lc.set_table(None)
    lc.use_memory_store()
    yield
    lc.set_table(None)
    lc.use_disk_store()
    laya.reset()


def _table(act=0.55):
    return {"version": "cal-test", "heads": {"router": {
        "fitted": True, "tau": 0.06, "q_hat": 0.9, "coverage": 0.9, "thresholds": {"act": act, "margin": 0.10}}}}


def _draw(rng, n, tau, k=6):
    """Cosines for k labels and a true label drawn from softmax(sim / tau):
    a head that is exactly calibrated at temperature ``tau``."""
    rows, truth = [], []
    for _ in range(n):
        sims = [rng.uniform(-0.1, 0.8) for _ in range(k)]
        p = lc.probs_from_sims(sims, tau)
        u, acc, y = rng.random(), 0.0, k - 1
        for i, pi in enumerate(p):
            acc += pi
            if u <= acc:
                y = i
                break
        rows.append(sims)
        truth.append(y)
    return rows, truth


# --- temperature scaling ------------------------------------------------------------

def test_temperature_scaling_recovers_the_true_temperature():
    rows, truth = _draw(random.Random(7), 1500, 0.05)
    tau = lc.fit_temperature(rows, truth, base=0.10)
    assert abs(tau - 0.05) / 0.05 < 0.25, tau
    # and it beats the miscalibrated base temperature on likelihood
    assert lc.nll(rows, truth, tau) < lc.nll(rows, truth, 0.10)


def test_no_data_leaves_the_base_temperature():
    assert lc.fit_temperature([], [], base=0.1) == 0.1


def test_ece_is_small_when_calibrated_and_large_when_overconfident():
    rng = random.Random(3)
    conf = [rng.uniform(0.5, 1.0) for _ in range(4000)]
    calibrated = [rng.random() < c for c in conf]
    assert lc.ece(conf, calibrated) < 0.04
    assert lc.ece([0.99] * 1000, [i % 2 == 0 for i in range(1000)]) > 0.4
    assert lc.ece([], []) == 0.0


def test_fitting_lowers_ece_on_held_out_turns():
    rng = random.Random(11)
    fit_rows, fit_y = _draw(rng, 800, 0.04)
    test_rows, test_y = _draw(rng, 800, 0.04)
    res = lc.fit_head(fit_rows, fit_y, test_rows, test_y, base_tau=0.12)
    assert res["test"]["ece_after"] < res["test"]["ece_before"]
    assert res["n_fit"] == 800 and res["n_test"] == 800


# --- the conformal set ---------------------------------------------------------------

def test_the_conformal_set_covers_at_the_target_rate_on_fresh_turns():
    rng = random.Random(5)
    cal_rows, cal_y = _draw(rng, 600, 0.05)
    q = lc.conformal_quantile([1.0 - lc.probs_from_sims(r, 0.05)[y] for r, y in zip(cal_rows, cal_y)], 0.90)
    rows, ys = _draw(rng, 3000, 0.05)
    hit = 0
    for r, y in zip(rows, ys):
        p = lc.probs_from_sims(r, 0.05)
        hit += y in [i for i, pi in enumerate(p) if 1.0 - pi <= q + 1e-12]
    assert hit / 3000 >= 0.87


def test_too_little_data_means_every_label_is_in_the_set():
    assert lc.conformal_quantile([0.1, 0.2], 0.9) == 1.0
    assert lc.conformal_quantile([], 0.9) == 1.0


def test_set_size_decides_act_ask_escalate_or_none():
    assert lc.decide(["a"], person_present=True) == "act"
    assert lc.decide(["a", "b"], person_present=True) == "ask"
    assert lc.decide(["a", "b"], person_present=False) == "escalate"
    assert lc.decide([], person_present=True) == "none"
    assert lc.prediction_set({"a": 0.9, "b": 0.08, "c": 0.02}, 0.15) == ["a"]
    assert lc.prediction_set({"a": 0.5, "b": 0.45, "c": 0.05}, 0.6) == ["a", "b"]
    assert lc.prediction_set({"a": 0.2, "b": 0.2}, 0.3) == []


def test_a_head_without_a_fitted_entry_has_no_reading():
    lc.set_table({"version": "x", "heads": {"router": {"fitted": False}}})
    assert lc.reading("router", {"a": 0.9, "b": 0.1}) is None
    assert lc.reading("nope", {"a": 0.9}) is None


def test_reading_uses_the_fitted_temperature_and_cutoff():
    lc.set_table(_table())
    r = lc.reading("router", {"a": 0.9, "b": 0.1, "c": 0.0}, person_present=True)
    assert r["label"] == "a" and r["set"][0] == "a" and r["tau"] == 0.06
    assert r["decision"] in ("act", "ask")


# --- only dev sets 1-3 -----------------------------------------------------------------

def test_the_fitter_refuses_set_4_and_set_5():
    lc.check_fit_sources(list(lc.DEV_SETS))
    for bad in ("laya_router_holdout4.json", "laya_router_holdout5.json", "tests/fixtures/set5_sealed.json",
                "/anywhere/laya_router_holdout4.json", "laya_router_eval.json.set4"):
        with pytest.raises(ValueError):
            lc.check_fit_sources(["laya_router_eval.json", bad])


def test_the_fit_tool_names_no_forbidden_set_and_goes_through_the_check():
    src = (ROOT / "tools" / "laya_calibrate.py").read_text(encoding="utf-8")
    assert "holdout4" not in src and "holdout5" not in src
    assert "check_fit_sources" in src


# --- the shipped table ------------------------------------------------------------------

def _table_file():
    return json.loads((ROOT / "src" / "agent_friday" / "resources" / lc.TABLE_NAME).read_text(encoding="utf-8"))


def test_the_shipped_table_matches_the_router_it_describes():
    t = _table_file()
    r = t["heads"]["router"]
    assert r["fitted"] is True and r["shadow"] is True
    assert r["thresholds"] == {"act": lr.ACT_THRESHOLD, "margin": lr.MARGIN_THRESHOLD}
    assert r["base_tau"] == lr.TEMPERATURE
    assert 0.0 < r["tau"] < 1.0 and 0.0 < r["q_hat"] <= 1.0
    assert "conversation" in r["labels"] and "act" in r["labels"]
    assert set(r["labels"]) <= set(lr.TOOLS) | {"conversation", "act"}
    assert r["test"]["ece_after"] < r["test"]["ece_before"]
    assert r["fit"]["ece_after"] < r["fit"]["ece_before"]
    assert "set 5" in r["fitted_on"] and "every labelled turn" in r["fitted_on"]
    for h in ("gate_opinion", "resolve"):
        assert t["heads"][h]["fitted"] is False and t["heads"][h]["reason"]
    # the triage sets were written by the seed author: said so in the table, never independent
    for h in ("triage.mail", "triage.news", "triage.notify", "triage.memory"):
        e = t["heads"][h]
        assert e["fitted"] is True and e["shadow"] is True
        assert e["fitted_on"] == "author-written triage sets (not independent)"
        assert e["set"].startswith("triage_eval_")


def test_the_table_is_what_the_loader_reads():
    assert lc.table_version() == _table_file()["version"] != "none"
    assert lc.head_entry("router")["tau"] == _table_file()["heads"]["router"]["tau"]
    assert lc.shipped_threshold("router", "act") == lr.ACT_THRESHOLD


# --- invariant 5: no user words in shipped weights ---------------------------------------

def _dev_texts():
    out = []
    for name in lc.DEV_SETS:
        out += [c["text"] for c in json.loads((FIX / name).read_text(encoding="utf-8"))["cases"]]
    return out


def leaks(blob: str, texts):
    """The turn texts that appear in ``blob`` (case and spacing folded)."""
    flat = " ".join(blob.lower().split())
    return [t for t in texts if len(t.split()) >= 3 and " ".join(t.lower().split()) in flat]


def test_the_leak_check_can_fail():
    texts = _dev_texts()
    assert leaks(json.dumps({"x": texts[5]}), texts) == [texts[5]]
    assert leaks("nothing here", texts) == []


def test_no_turn_text_is_in_the_shipped_calibration_table_or_seeds():
    texts = _dev_texts()
    assert len(texts) > 100
    blobs = [(ROOT / "src" / "agent_friday" / "resources" / lc.TABLE_NAME).read_text(encoding="utf-8")]
    from agent_friday.services import laya2_seeds
    blobs.append(laya2_seeds.canonical_json())
    try:                                              # the triage heads arrive with their own track
        from agent_friday.services import triage_heads
        blobs.append(json.dumps([triage_heads.MAIL_SEEDS, triage_heads.NOTIFY_SEEDS]))
    except ImportError:
        pass
    for blob in blobs:
        assert leaks(blob, texts) == []


def test_every_string_in_the_table_is_an_id_or_a_short_reason():
    t = _table_file()

    def walk(o, key=""):
        if isinstance(o, dict):
            for k, v in o.items():
                yield from walk(v, k)
        elif isinstance(o, list):
            for v in o:
                yield from walk(v, key)
        elif isinstance(o, str):
            yield key, o

    for key, s in walk(t):
        if key in ("reason", "note", "fitted_on"):
            assert len(s) <= 260, key
        else:
            assert len(s) <= 64 and " " not in s.strip(), (key, s)


# --- invariant 6: learning only tightens ---------------------------------------------------

def _approved(head, name, value, status="approved", kind=lc.APPROVAL_KIND):
    return {"approval_id": "ap1", "status": status, "kind": kind, "payload": {"head": head, "name": name, "value": value}}


@pytest.fixture
def card(monkeypatch):
    """An approvals store holding the cards a test installs."""
    from agent_friday.services import approvals
    store = {}
    monkeypatch.setattr(approvals, "get_approval", lambda i: store.get(i))
    return store


def test_tighten_raises_and_never_lowers():
    lc.set_table(_table(0.55))
    assert lc.effective_threshold("router", "act") == 0.55
    assert lc.tighten("router", "act", 0.70) == 0.70
    assert lc.tighten("router", "act", 0.60) == 0.70          # a lower proposal changes nothing
    assert lc.tighten("router", "act", 0.10) == 0.70
    assert lc.effective_threshold("router", "act") == 0.70


def test_a_refit_cannot_lower_a_shipped_threshold_without_the_owner():
    lc.set_table(_table(0.55))
    assert lc.apply_refit("router", {"act": 0.30}) == {"act": 0.55}
    assert lc.apply_refit("router", {"act": 0.30}, owner_token="ap1") == {"act": 0.55}      # a bare id is not a token
    assert lc.apply_refit("router", {"act": 0.30}, owner_token=None) == {"act": 0.55}
    assert lc.lower("router", "act", 0.30, owner_token="") == 0.55
    assert lc.effective_threshold("router", "act") == 0.55
    assert not lc._read_learned().get("router")               # nothing was even recorded


def test_a_hand_edited_learned_value_below_the_floor_is_ignored():
    lc.set_table(_table(0.55))
    lc.use_memory_store({"router": {"act": {"value": 0.10}}})
    assert lc.effective_threshold("router", "act") == 0.55
    lc.use_memory_store({"router": {"act": {"value": 0.10, "owner_token": "forged"}}})
    assert lc.effective_threshold("router", "act") == 0.55


def test_only_an_approved_card_for_that_exact_value_lowers_a_threshold(card):
    lc.set_table(_table(0.55))
    card["ap1"] = _approved("router", "act", 0.45)
    assert lc.lower("router", "act", 0.45, owner_token="ap1") == 0.45
    assert lc.effective_threshold("router", "act") == 0.45
    lc.use_memory_store()
    for bad in (_approved("router", "act", 0.40),                          # a different value
                _approved("router", "margin", 0.45),                       # a different threshold
                _approved("other", "act", 0.45),                           # a different head
                _approved("router", "act", 0.45, status="pending"),        # not approved
                _approved("router", "act", 0.45, status="denied"),
                _approved("router", "act", 0.45, kind="something_else")):  # a card for another purpose
        card["ap1"] = bad
        assert lc.lower("router", "act", 0.45, owner_token="ap1") == 0.55, bad


def test_a_lowering_whose_card_later_goes_away_stops_applying(card):
    lc.set_table(_table(0.55))
    card["ap1"] = _approved("router", "act", 0.45)
    lc.lower("router", "act", 0.45, owner_token="ap1")
    del card["ap1"]
    assert lc.effective_threshold("router", "act") == 0.55


def test_outward_and_sensitive_tools_never_graduate_from_ask(card):
    card["ap1"] = {"approval_id": "ap1", "status": "approved", "kind": lc.APPROVAL_KIND,
                   "payload": {"head": "graduate", "name": "search_web", "value": 1.0}}
    for tool in ("search_web", "search_news", "check_email", "undo_action", "navigate_to", "no_such_tool"):
        card["ap1"]["payload"]["name"] = tool
        assert lc.may_graduate(tool, owner_token="ap1") is False, tool


def test_a_tool_the_router_asks_before_guessing_never_graduates_even_if_the_table_called_it_local(card, monkeypatch):
    monkeypatch.setattr(lr, "ASK_BEFORE_GUESSING", frozenset(lr.ASK_BEFORE_GUESSING | {"query_calendar"}))
    card["ap1"] = {"approval_id": "ap1", "status": "approved", "kind": lc.APPROVAL_KIND,
                   "payload": {"head": "graduate", "name": "query_calendar", "value": 1.0}}
    assert lc.may_graduate("query_calendar", owner_token="ap1") is False


def test_a_local_read_tool_graduates_only_with_the_owners_card(card):
    assert lc.may_graduate("query_calendar") is False
    assert lc.may_graduate("query_calendar", owner_token="ap1") is False
    card["ap1"] = {"approval_id": "ap1", "status": "approved", "kind": lc.APPROVAL_KIND,
                   "payload": {"head": "graduate", "name": "query_calendar", "value": 1.0}}
    assert lc.may_graduate("query_calendar", owner_token="ap1") is True


def test_every_function_that_can_lower_or_graduate_demands_the_owners_token():
    """No API in this package lowers a shipped threshold without a token: the
    only functions that can are ``lower``, ``apply_refit`` and ``may_graduate``,
    and each takes ``owner_token``. Anything else that touches thresholds is
    ``tighten``, which never lowers (tested above)."""
    for fn in (lc.lower, lc.apply_refit, lc.may_graduate):
        assert "owner_token" in inspect.signature(fn).parameters, fn.__name__
    public = {n for n, f in inspect.getmembers(lc, inspect.isfunction) if not n.startswith("_") and f.__module__ == lc.__name__}
    writers = {n for n in public if any(w in n for w in ("threshold", "lower", "tighten", "refit", "graduate"))}
    assert writers <= {"shipped_threshold", "effective_threshold", "tighten", "lower", "apply_refit", "may_graduate",
                       "token_valid"}, writers
    assert not {n for n, f in inspect.getmembers(laya, inspect.isfunction) if "threshold" in n}


def test_a_triage_read_carries_a_shadow_calibrated_set_and_the_same_label(monkeypatch):
    th = pytest.importorskip("agent_friday.services.triage_heads")            # arrives with the triage track
    th.set_embedder(th.hashed_embedder())
    th.reset_cache()
    lc.set_table({"version": "t", "heads": {"triage.mail": {"fitted": True, "tau": 0.04, "q_hat": 0.5}}})
    try:
        th.head("mail").prototypes(block=True)
        text = th.mail_text(th.MAIL_SEEDS["career"][0], "")
        res = laya.ask({"text": text, "surface": "background", "opts": {"texts": [text], "cached": True, "log": False}},
                       {"triage.mail": {"preset": "triage.mail"}}, "triage.mail", 2000)
        cal = laya.receipt(res["receipt_id"])["questions"]["triage.mail"]["calibrated"]
        assert res["answers"]["triage.mail"]["value"] == "career" and cal["label"] == "career" and cal["set"]
    finally:
        th.set_embedder(None)
        th.reset_cache()


# --- the calibrated decision is shadow --------------------------------------------------------

def test_the_calibrated_decision_is_logged_next_to_the_current_one_and_changes_nothing(monkeypatch):
    lc.set_table(_table())
    # tier 1 decided "tool check_email"; the calibrated set holds two routes, which would be an ask
    monkeypatch.setattr(lr, "route", lambda text, **k: lr.Route("tool", tool="check_email", confidence=0.9,
                                                              layer="laya2", label="x"))
    monkeypatch.setattr(lr, "_score", lambda text: [("check_email", 0.5, 0.60), ("get_briefing", 0.4, 0.58),
                                                    ("conversation", 0.1, 0.1)])
    res = laya.ask({"text": "mail please", "surface": "voice", "opts": {"log": False}}, laya.route_question(), "t", 500)
    assert res["verdict"] == "act" and res["answers"]["route"]["value"] == "tool"       # the current decision stands
    cal = laya.receipt(res["receipt_id"])["questions"]["route"]["calibrated"]
    assert cal["decision"] in ("ask", "act") and cal["current"] == "check_email"
    assert cal["set"] and res["answers"]["route"]["set"] == cal["set"]
    assert "shadow" in laya.receipt(res["receipt_id"])["explain"]


def test_a_rule_decision_has_no_calibrated_reading(monkeypatch):
    lc.set_table(_table())
    monkeypatch.setattr(lr, "route", lambda text, **k: lr.Route("tool", tool="check_email", confidence=0.95, layer="rule"))
    res = laya.ask({"text": "check my email", "surface": "voice", "opts": {"log": False}}, laya.route_question(), "t", 500)
    assert laya.receipt(res["receipt_id"])["questions"]["route"]["calibrated"] is None


def test_the_calibrated_reading_never_changes_the_verdict_whatever_the_table(monkeypatch):
    monkeypatch.setattr(lr, "route", lambda text, **k: lr.Route("no_tool", confidence=0.7, layer="laya2"))
    monkeypatch.setattr(lr, "_score", lambda text: [("conversation", 0.7, 0.5), ("act", 0.3, 0.4)])
    for tau, q in ((0.01, 0.0), (0.06, 0.5), (1.0, 1.0)):
        t = _table()
        t["heads"]["router"].update(tau=tau, q_hat=q)
        lc.set_table(t)
        res = laya.ask({"text": "hm", "surface": "voice", "opts": {"log": False}}, laya.route_question(), "t", 500)
        assert res["verdict"] == "no_tool"

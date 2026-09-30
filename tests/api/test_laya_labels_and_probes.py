"""The owner's labels are the answer key, and probes are not evidence.

Two things the week-one review needed and could not have:

  * PROBES APART. All 125 disagreements came from the Grants screen asking
    the gate about every connector tool with empty arguments, to list what a
    grant could cover. No action was attempted. Those rows now carry
    `purpose: probe:grants_screen` and go to their own file, so the decision
    log is real traffic only.

  * LABELS. "Does this reach outside my machine?" answered yes or no by the
    owner, kept in ~/.friday/laya_labels.jsonl, and scored against: the
    evidence report says how often each scanner agreed with him. A label is
    per tool (argument-independent) or per exact state.
"""
from __future__ import annotations

import hashlib
import json
import threading

import pytest

from agent_friday.services import (approvals, decisions, dissent_gate,
                                   laya_backend, laya_labels)


def _digest(s):
    return hashlib.sha256(s.encode("utf-8")).hexdigest()[:16]


class _Agent:
    def __init__(self, choice):
        self.choice = choice

    def predict(self, text, question):
        return {"answers": {"severity": {"choice": self.choice, "confidence": 0.8}}}


@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch):
    monkeypatch.setattr(decisions, "log_path", lambda: tmp_path / "decisions.jsonl")
    monkeypatch.setattr(decisions, "probe_log_path", lambda: tmp_path / "decisions-probes.jsonl")
    monkeypatch.setattr(laya_labels, "labels_path", lambda: tmp_path / "laya_labels.jsonl")
    monkeypatch.setattr(approvals, "APPROVALS_FILE", tmp_path / "approvals.json")
    monkeypatch.setattr(dissent_gate, "EVENTS_PATH", tmp_path / "dissent.jsonl")
    monkeypatch.setenv("FRIDAY_DECISION_BACKEND", "keyword")
    monkeypatch.setenv("FRIDAY_DECISION_SHADOW", "laya")
    monkeypatch.setattr(decisions, "_SHADOW_SYNC_FOR_TESTS", True)
    laya_backend.register()
    monkeypatch.setattr(laya_backend, "_agent", _Agent("hard"))
    monkeypatch.setattr(laya_backend, "_loading", False)
    monkeypatch.setattr(laya_backend, "_scoring",
                        threading.BoundedSemaphore(laya_backend._MAX_SCORING))
    laya_backend.clear_answer_cache()
    yield tmp_path
    laya_backend.clear_answer_cache()
    laya_backend._agent = None


def _rows(p):
    return [json.loads(x) for x in p.read_text(encoding="utf-8").splitlines() if x.strip()] if p.exists() else []


def test_a_grants_screen_probe_goes_to_its_own_file(_isolate):
    with decisions.purpose("probe:grants_screen"):
        approvals.classify("mcp_github_list_commits {}")
    assert _rows(_isolate / "decisions.jsonl") == []
    probes = _rows(_isolate / "decisions-probes.jsonl")
    assert probes and all(r["context"]["purpose"] == "probe:grants_screen" for r in probes)
    assert any(r.get("shadow") for r in probes)


def test_a_real_action_stays_in_the_decision_log(_isolate):
    approvals.classify("mcp_github_list_commits {}")
    assert _rows(_isolate / "decisions.jsonl")
    assert _rows(_isolate / "decisions-probes.jsonl") == []


def test_the_grants_screen_marks_its_checks_as_probes(_isolate, client):
    r = client.get("/api/governance/outward-tools")
    assert r.status_code == 200
    real = _rows(_isolate / "decisions.jsonl")
    assert all((x.get("context") or {}).get("purpose") for x in real) or real == []


def test_a_tool_label_is_kept_and_read_back(_isolate):
    laya_labels.label(tool="mcp_github_search_users", reaches_outside=True, by="owner")
    laya_labels.label(tool="mcp_github_search_users", reaches_outside=True, by="owner")
    got = laya_labels.labels()
    assert got["tool:mcp_github_search_users"]["reaches_outside"] is True
    assert len(got) == 1


def test_evidence_scores_each_scanner_against_the_owner(_isolate):
    # keyword says internal, Laya says hard: the week-one pattern.
    approvals.classify("mcp_github_search_users {}")
    laya_labels.label(tool="mcp_github_search_users", reaches_outside=True, by="owner")
    ev = laya_labels.evidence()
    assert ev["labelled"] == 1
    assert ev["keyword"] == {"right": 0, "wrong": 1}
    assert ev["laya"] == {"right": 1, "wrong": 0}


def test_evidence_counts_probes_separately_and_matches_by_digest(_isolate):
    # The log blanks long tool names; a probe state is "<tool> {}" and the
    # label still finds it through the digest.
    long_tool = "mcp_higgsfield_show_marketing_studio_generations"
    with decisions.purpose("probe:grants_screen"):
        approvals.classify(long_tool + " {}")
    laya_labels.label(tool=long_tool, reaches_outside=True, by="owner")
    ev = laya_labels.evidence()
    assert ev["probes"]["labelled"] == 1
    assert ev["probes"]["laya"] == {"right": 1, "wrong": 0}
    assert ev["labelled"] == 0


def test_the_label_queue_offers_unlabelled_disagreements_first(_isolate):
    approvals.classify("mcp_github_search_users {}")
    queue = laya_labels.queue()
    assert queue and queue[0]["subject"] == "tool:mcp_github_search_users"
    assert queue[0]["keyword_says_outside"] is False
    assert queue[0]["laya_says_outside"] is True
    assert queue[0]["disagree"] is True
    laya_labels.label(tool="mcp_github_search_users", reaches_outside=True, by="owner")
    assert not [q for q in laya_labels.queue() if q["subject"] == "tool:mcp_github_search_users"]


def test_the_routes_label_and_report(_isolate, client):
    approvals.classify("mcp_github_search_users {}")
    q = client.get("/api/decisions/label_queue").get_json()
    assert q["items"][0]["subject"] == "tool:mcp_github_search_users"
    r = client.post("/api/decisions/labels",
                    json={"subject": "tool:mcp_github_search_users", "reaches_outside": True})
    assert r.status_code == 200
    body = client.get("/api/decisions/gate_status").get_json()
    assert body["evidence"]["laya"]["right"] == 1

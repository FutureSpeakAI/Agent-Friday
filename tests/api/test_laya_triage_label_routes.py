"""The label page and its three routes: local only, one answer per post, a receipt back."""
from __future__ import annotations

import json

import pytest

from agent_friday.services import laya_labels, laya_triage_labels as tl


@pytest.fixture(autouse=True)
def _home(tmp_path, monkeypatch):
    monkeypatch.setattr(laya_labels, "labels_path", lambda: tmp_path / "laya_labels.jsonl")
    monkeypatch.setattr(tl, "queue_path", lambda: tmp_path / "laya_triage_queue.json")
    item = {"id": "item:aaaa1111aaaa1111", "head": "notify", "text": "Start My Day ran", "kind": "edge",
            "question": tl.QUESTIONS["notify"], "head_said": "fyi", "incumbent_said": "fyi", "p": 0.9}
    (tmp_path / "laya_triage_queue.json").write_text(json.dumps({"v": 1, "items": [item]}), encoding="utf-8")
    return tmp_path


def test_the_page_opens_here_and_carries_the_session_token(client):
    r = client.get("/laya/labels")
    assert r.status_code == 200 and b"Teach Laya" in r.data and b"__FRIDAY_API_TOKEN" in r.data
    assert r.headers["Cache-Control"] == "no-store"


def test_the_page_is_one_of_friday_s_own_pages_not_a_sandboxed_document(client):
    r = client.get("/laya/labels")
    assert "allow-same-origin" not in (r.headers.get("Content-Security-Policy") or "")
    assert "sandbox" not in (r.headers.get("Content-Security-Policy") or "")


def test_the_queue_lists_the_waiting_items(client):
    d = client.get("/api/decisions/triage_queue").get_json()
    assert d["status"] == "ok" and [i["id"] for i in d["items"]] == ["item:aaaa1111aaaa1111"]
    assert d["total"] == 1 and d["answered"] == 0 and d["valid_chain"] is True


def test_an_answer_comes_back_with_a_receipt_and_leaves_the_queue(client, _home):
    r = client.post("/api/decisions/triage_labels", json={"subject": "item:aaaa1111aaaa1111", "answer": "yes"})
    d = r.get_json()
    assert r.status_code == 200 and d["status"] == "ok" and len(d["receipt"]) == 16 and d["answer"] == "yes"
    assert client.get("/api/decisions/triage_queue").get_json()["items"] == []
    row = json.loads((_home / "laya_labels.jsonl").read_text(encoding="utf-8").splitlines()[0])
    assert row["receipt"] == d["receipt"] and row["question"] == "triage_notify"


@pytest.mark.parametrize("body", [{"subject": "item:zzzz", "answer": "yes"},
                                  {"subject": "item:aaaa1111aaaa1111", "answer": "maybe"}, {}])
def test_a_bad_answer_is_refused_and_writes_nothing(client, _home, body):
    r = client.post("/api/decisions/triage_labels", json=body)
    assert r.status_code == 400
    assert not (_home / "laya_labels.jsonl").exists()


def test_the_score_route_reports_the_owner_s_answers(client):
    client.post("/api/decisions/triage_labels", json={"subject": "item:aaaa1111aaaa1111", "answer": "yes"})
    s = client.get("/api/decisions/triage_score").get_json()["score"]["notify"]
    assert s["answered"] == 1 and s["owner_yes"] == 1 and s["head"]["missed"] == 1


@pytest.mark.parametrize("method, path", [("get", "/laya/labels"), ("get", "/api/decisions/triage_queue"),
                                          ("post", "/api/decisions/triage_labels"), ("get", "/api/decisions/triage_score")])
def test_every_route_refuses_a_caller_that_is_not_on_this_pc(client, _home, monkeypatch, method, path):
    from agent_friday import core
    monkeypatch.setattr(core, "_is_local_request", lambda: False)
    r = getattr(client, method)(path, **({"json": {"subject": "item:aaaa1111aaaa1111", "answer": "yes"}} if method == "post" else {}))
    assert r.status_code in (302, 401, 403)
    assert not (_home / "laya_labels.jsonl").exists()


def test_a_body_that_is_not_an_object_is_a_bad_request_not_a_crash(client, _home):
    r = client.post("/api/decisions/triage_labels", json=["item:aaaa1111aaaa1111", "yes"])
    assert r.status_code == 400 and not (_home / "laya_labels.jsonl").exists()

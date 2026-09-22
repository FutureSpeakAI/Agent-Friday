"""The conversation endpoints the UI has been calling all along.

`index.html` fetches /api/conversations at six call sites - the switcher, the
transcript loader, the per-chat model picker and the new-chat button. None of
those routes existed, so the switcher silently did nothing and every turn fell
back to Main. These tests pin the contract the UI actually sends, not a
contract invented afterwards.
"""
import json

import pytest

from agent_friday.services import conversations as convs


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(convs, "_root", lambda: tmp_path / "conversations")
    from flask import Flask
    from agent_friday.routes.conversations import conversations_bp
    app = Flask(__name__)
    app.register_blueprint(conversations_bp)
    return app.test_client()


def _json(resp):
    return json.loads(resp.data.decode("utf-8"))


def test_the_list_answers_with_main_even_on_a_fresh_install(client):
    """The UI seeds its current conversation from `main_id`. Without it the
    switcher has nothing to select and the chat panel addresses nothing."""
    d = _json(client.get("/api/conversations"))
    assert d["status"] == "ok"
    assert d["main_id"] == convs.MAIN_ID
    assert any(c["id"] == convs.MAIN_ID for c in d["conversations"])


def test_a_new_chat_comes_back_with_an_id_the_ui_can_open(client):
    """The new-chat button does `setConvId((d.conversation || {}).id)`. An
    id that is absent or nested differently leaves the user on the old
    thread while the UI believes it moved."""
    d = _json(client.post("/api/conversations", json={"title": "Second"}))
    assert d["status"] == "ok"
    cid = d["conversation"]["id"]
    assert cid and cid != convs.MAIN_ID
    assert d["conversation"]["title"] == "Second"
    listed = _json(client.get("/api/conversations"))["conversations"]
    assert cid in [c["id"] for c in listed]


def test_each_conversation_keeps_its_own_transcript(client):
    """Two windows, two threads. If messages leaked between them the whole
    feature is worse than not having it."""
    a = _json(client.post("/api/conversations", json={"title": "A"}))["conversation"]["id"]
    b = _json(client.post("/api/conversations", json={"title": "B"}))["conversation"]["id"]
    convs.append(a, {"role": "user", "text": "only in A"})
    convs.append(b, {"role": "user", "text": "only in B"})
    ma = _json(client.get("/api/conversations/%s/messages" % a))["messages"]
    mb = _json(client.get("/api/conversations/%s/messages" % b))["messages"]
    assert [m["text"] for m in ma] == ["only in A"]
    assert [m["text"] for m in mb] == ["only in B"]


def test_a_seat_binding_sticks_and_is_reported_back(client, monkeypatch):
    """The picker PATCHes a seat and then CHECKS the response, alerting the
    user when what came back is not what it asked for. That check is only
    meaningful if the response reports the conversation as it now is."""
    import agent_friday.routes.conversations as routes
    monkeypatch.setattr(routes, "_why_this_seat_cannot_be_bound",
                        lambda seat: None)
    cid = _json(client.post("/api/conversations", json={"title": "C"}))["conversation"]["id"]
    d = _json(client.patch("/api/conversations/%s" % cid,
                           json={"seat": {"model": "claude-sonnet-5"}}))
    assert d["conversation"]["seat"]["model"] == "claude-sonnet-5"
    again = _json(client.get("/api/conversations/%s" % cid))
    assert again["conversation"]["seat"]["model"] == "claude-sonnet-5"


def test_a_second_local_model_is_refused_with_a_reason(client, monkeypatch):
    """One local model at a time on 12 GB.

    Two 27B seats do not fit - measured 2026-09-18, two bonsai2:27b servers
    held 11,605 MiB of 12,282 between them and every turn hung. Accepting the
    binding and letting the next turn discover it is how a user learns this
    as a hang instead of as a sentence.
    """
    import agent_friday.routes.conversations as routes
    monkeypatch.setattr("agent_friday.services.residency_arbiter."
                        "survey_live_seats",
                        lambda *a, **k: {"bonsai2:27b": (123, 8090)})
    monkeypatch.setattr("agent_friday.services.local_seats._is_local_name",
                        lambda m: ":" in m)
    cid = _json(client.post("/api/conversations", json={"title": "D"}))["conversation"]["id"]
    d = _json(client.patch("/api/conversations/%s" % cid,
                           json={"seat": {"model": "gemma4:12b"}}))
    assert d["conversation"].get("seat") in (None, {}), \
        "the binding must not have been applied"
    assert "bonsai2:27b" in d.get("note", ""), \
        "the refusal must name what is in the way"
    assert routes  # the module under test


def test_refusal_never_fires_when_the_check_cannot_be_made(client,
                                                           monkeypatch):
    """Absence of evidence is not evidence of occupancy.

    A picker that refuses because a probe failed is worse than one that
    occasionally over-promises: the user is denied something that would have
    worked, for a reason that is not true.
    """
    def _boom(*a, **k):
        raise RuntimeError("cannot read the seats")
    monkeypatch.setattr("agent_friday.services.residency_arbiter."
                        "survey_live_seats", _boom)
    monkeypatch.setattr("agent_friday.services.local_seats._is_local_name",
                        lambda m: True)
    cid = _json(client.post("/api/conversations", json={"title": "E"}))["conversation"]["id"]
    d = _json(client.patch("/api/conversations/%s" % cid,
                           json={"seat": {"model": "bonsai2:27b"}}))
    assert d["conversation"]["seat"]["model"] == "bonsai2:27b"
    assert "note" not in d


def test_unbinding_is_always_allowed(client, monkeypatch):
    """Whatever is resident, a user may always take a model OFF a chat."""
    monkeypatch.setattr("agent_friday.services.residency_arbiter."
                        "survey_live_seats",
                        lambda *a, **k: {"bonsai2:27b": (123, 8090)})
    cid = _json(client.post("/api/conversations", json={"title": "F"}))["conversation"]["id"]
    d = _json(client.patch("/api/conversations/%s" % cid, json={"seat": None}))
    assert d["conversation"].get("seat") in (None, {})
    assert "note" not in d


def test_a_missing_conversation_is_a_404_not_an_empty_success(client):
    """An empty 200 tells the UI the thread exists and is blank, which is the
    same shape as a thread whose history was lost."""
    assert client.get("/api/conversations/conv-nope").status_code == 404
    assert client.get("/api/conversations/conv-nope/messages").status_code == 404
    assert client.patch("/api/conversations/conv-nope",
                        json={"title": "x"}).status_code == 404

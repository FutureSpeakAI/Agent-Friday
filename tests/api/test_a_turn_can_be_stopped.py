"""A running turn can be stopped, and the route never pretends it stopped one.

The local seat's round budget went from 50 to parity with the cloud path's 999,
which removed the only thing that had ever ended a long interactive turn early.
The replacements -- loop detection, a wall clock, a token ceiling -- are all
automatic. None of them is the user deciding they have seen enough, and that
decision had no route at all: `task_journal.stop_requested(None)` is always
False and a chat turn carries no task id, so the cap WAS the stop.

Cooperative by design. The round loop checks between rounds, so a stopped turn
keeps its transcript and receipts instead of dying mid-tool-call.
"""


def test_stopping_a_turn_that_is_not_running_says_so(client):
    """A button that silently does nothing is worse than no button."""
    r = client.post("/api/chat/turn/turn-that-never-ran/stop")
    assert r.status_code == 200
    d = r.get_json()
    assert d.get("status") == "ok"
    assert d.get("stopping") is False
    detail = (d.get("detail") or "").lower()
    assert "not running" in detail or "already" in detail, detail


def test_stopping_a_registered_turn_is_accepted(client):
    import agent_friday.core as core
    core.turn_begin("turn-api-stop")
    try:
        r = client.post("/api/chat/turn/turn-api-stop/stop")
        assert r.status_code == 200
        d = r.get_json()
        assert d.get("stopping") is True
        assert d.get("turn_id") == "turn-api-stop"
        assert core.turn_stop_requested("turn-api-stop") is True
    finally:
        core.turn_end("turn-api-stop")


def test_the_stop_is_a_post_only(client):
    """A GET must not stop a turn: anything that ends work needs a method that
    is not followed by a link preview or a retry."""
    r = client.get("/api/chat/turn/whatever/stop")
    assert r.status_code in (404, 405)


def test_stopping_one_turn_leaves_another_alone(client):
    import agent_friday.core as core
    core.turn_begin("turn-keep")
    core.turn_begin("turn-drop")
    try:
        client.post("/api/chat/turn/turn-drop/stop")
        assert core.turn_stop_requested("turn-drop") is True
        assert core.turn_stop_requested("turn-keep") is False
    finally:
        core.turn_end("turn-drop")
        core.turn_end("turn-keep")

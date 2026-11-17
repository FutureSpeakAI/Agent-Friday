"""``GET /tv/v1/events``: one resumable feed for the owner's phone and tablet (PW-G8).

Rules guarded here, each with a case that fails if the check is removed:
* only an owner-bound phone or tablet holding ``pc_read`` may open it; a revoked device is told and closed;
* ``hello`` first; every event has a number and a source name; ``Last-Event-ID`` resumes from the ring, and a device that
  is too far behind, ahead of the PC's numbering (it restarted) or sends nonsense is told ``reset``, never left stale;
* a heartbeat on a quiet stream; a device that cannot keep up is told ``reset``;
* an approval card is cut down for the device the way the cards list is; a source with no shaper publishes nothing and a
  shaper may hide an event from a device;
* at most three streams per device, and a stream that never started gives its slot back.
"""
import collections
import json
import time

import pytest

from agent_friday.services import approval_feed
from agent_friday.services import approvals as ap
from agent_friday.services import device_pairing
from agent_friday.tv_gateway import app as gateway
from agent_friday.tv_gateway import household, pairing, pc_events, pc_read
from tests.unit.test_tv_gateway import FakeTv, auth, open_session


@pytest.fixture(autouse=True)
def _clean(tmp_path, monkeypatch):
    monkeypatch.setattr(ap, "APPROVALS_FILE", tmp_path / "approvals.json")
    monkeypatch.setattr(ap, "_feed", lambda *a, **k: None)
    monkeypatch.setattr(ap, "_notify_pending", lambda rec: None)
    monkeypatch.setattr(household, "_path", lambda: tmp_path / "household.json")
    monkeypatch.setattr(gateway, "_BUCKET", gateway._Bucket())
    monkeypatch.setattr(pc_read, "_AUDITED", {})
    monkeypatch.setattr(pairing, "_OFFERED", {})
    monkeypatch.setattr(pc_events, "_RING", collections.deque(maxlen=pc_events.RING))
    monkeypatch.setattr(pc_events, "_LAST_ID", 0)
    monkeypatch.setattr(pc_events, "_SUBSCRIBERS", set())
    monkeypatch.setattr(pc_events, "_STREAMS", {})
    monkeypatch.setattr(pc_events, "_SHAPERS", dict(pc_events._SHAPERS))
    monkeypatch.setattr(pc_events, "_ensure_pump", lambda: None)
    device_pairing._path().unlink(missing_ok=True)
    device_pairing._reset_for_tests()
    household.ensure_default_members()
    yield
    device_pairing._reset_for_tests()


@pytest.fixture
def client():
    return gateway.create_app().test_client()


def device(kind="phone", member="stephen", rights=("pc_read",)):
    tv = FakeTv()
    device_pairing.add_device(tv.spki, name="A device", kind=kind)
    if member:
        household.bind_device(tv.id, member)
    for right in rights:
        device_pairing.set_right(tv.id, right, True)
    return tv


def open_stream(client, tv, last_event_id=None):
    headers = auth(open_session(client, tv))
    if last_event_id is not None:
        headers["Last-Event-ID"] = str(last_event_id)
    response = client.get("/tv/v1/events", headers=headers, buffered=False)
    return response


def frame(response):
    """The next SSE frame as (event, id, data)."""
    chunk = next(iter(response.response))
    text = chunk.decode("utf-8") if isinstance(chunk, bytes) else chunk
    fields = dict(line.split(": ", 1) for line in text.strip().split("\n"))
    return fields.get("event"), fields.get("id"), json.loads(fields["data"])


def note(approval_id="appr_x"):
    return pc_events.publish("needs_you", {"type": "resolved", "approval_id": approval_id})


def test_hello_comes_first_with_the_current_number(client):
    note()
    event, event_id, data = frame(open_stream(client, device()))
    assert event == "hello" and event_id is None and data["rev"] == 1 and data["resume"] == "ok"


def test_events_carry_a_number_and_a_source_name(client):
    stream = open_stream(client, device())
    frame(stream)
    first, second = note("appr_a"), note("appr_b")
    assert (first, second) == (1, 2)
    assert frame(stream)[:2] == ("needs_you", "1") and frame(stream)[:2] == ("needs_you", "2")


def test_last_event_id_resumes_with_only_what_was_missed(client):
    for number in range(3):
        note("appr_%d" % number)
    stream = open_stream(client, device(), last_event_id=1)
    assert frame(stream)[2]["resume"] == "ok"
    assert [frame(stream)[1], frame(stream)[1]] == ["2", "3"]


@pytest.mark.parametrize("last", ["99", "not-a-number", "-5"])
def test_a_device_ahead_of_the_pc_or_sending_nonsense_is_told_to_reset(client, last):
    note()
    assert frame(open_stream(client, device(), last_event_id=last))[2]["resume"] == "reset"


def test_a_device_further_behind_than_the_ring_is_told_to_reset(client, monkeypatch):
    monkeypatch.setattr(pc_events, "_RING", collections.deque(maxlen=3))
    for number in range(10):
        note("appr_%d" % number)
    assert frame(open_stream(client, device(), last_event_id=2))[2]["resume"] == "reset"


def test_a_device_exactly_at_the_edge_of_the_ring_is_caught_up(client, monkeypatch):
    monkeypatch.setattr(pc_events, "_RING", collections.deque(maxlen=3))
    for number in range(10):
        note("appr_%d" % number)
    stream = open_stream(client, device(), last_event_id=7)
    assert frame(stream)[2]["resume"] == "ok" and [frame(stream)[1] for _ in range(3)] == ["8", "9", "10"]


def test_an_event_published_while_a_device_resumes_is_sent_once(client, monkeypatch):
    """The stream subscribes before it reads the ring, so an event can be in both: it must not be sent twice."""
    real = pc_events._since

    def publish_then_read_the_ring(after):
        note("racer")
        return real(after)
    monkeypatch.setattr(pc_events, "_since", publish_then_read_the_ring)
    monkeypatch.setattr(pc_events, "BEAT_S", 0.05)
    stream = open_stream(client, device(), last_event_id=0)
    assert frame(stream)[0] == "hello" and frame(stream)[:2] == ("needs_you", "1")
    assert frame(stream)[0] == "heartbeat"


def test_a_quiet_stream_has_a_heartbeat(client, monkeypatch):
    monkeypatch.setattr(pc_events, "BEAT_S", 0.05)
    stream = open_stream(client, device())
    frame(stream)
    event, event_id, data = frame(stream)
    assert event == "heartbeat" and event_id is None and "as_of" in data


def test_a_device_that_cannot_keep_up_is_told_to_reset(client, monkeypatch):
    monkeypatch.setattr(pc_events, "QUEUE_MAX", 3)
    stream = open_stream(client, device())
    frame(stream)
    for number in range(10):
        note("appr_%d" % number)
    assert frame(stream)[0] == "reset"


def test_a_revoked_device_is_told_and_closed(client):
    tv = device()
    stream = open_stream(client, tv)
    frame(stream)
    device_pairing.set_right(tv.id, "pc_read", False)
    note()
    assert frame(stream)[0] == "revoked"
    with pytest.raises(StopIteration):
        frame(stream)


def private_card():
    return ap.create_approval(kind="tool_confirm", subject_type="t", subject_id="s1", title="Send the invoice to alex@example.com",
                              description="secret words", action_description="send", force_gate=True)


def test_an_approval_card_is_cut_down_for_a_device_that_cannot_approve(client):
    rec = private_card()
    stream = open_stream(client, device())
    frame(stream)
    pc_events.publish("needs_you", {"type": "pending", "approval": rec})
    _, _, data = frame(stream)
    assert data["type"] == "pending" and data["card"]["description"] == "" and "secret words" not in json.dumps(data)


def test_a_device_that_can_approve_sees_the_card(client, monkeypatch):
    rec = private_card()
    monkeypatch.setattr(device_pairing, "can_approve", lambda dev: True)
    stream = open_stream(client, device())
    frame(stream)
    pc_events.publish("needs_you", {"type": "pending", "approval": rec})
    assert frame(stream)[2]["card"]["description"] == "secret words"


def test_a_source_with_no_shaper_publishes_nothing_and_a_shaper_may_hide_an_event(client):
    stream = open_stream(client, device())
    frame(stream)
    assert pc_events.publish("jobs", {"id": "j1"}) == 0
    pc_events.register_source("hidden", lambda raw, dev: None)
    pc_events.register_source("jobs", lambda raw, dev: {"id": raw["id"]})
    assert pc_events.publish("hidden", {"id": "x"}) == 1
    assert pc_events.publish("jobs", {"id": "j2", "prompt": "SECRET"}) == 2
    event, event_id, data = frame(stream)
    assert (event, event_id, data) == ("jobs", "2", {"id": "j2"})


def test_a_device_at_the_cap_is_refused_and_one_below_it_is_not(client):
    tv = device()
    pc_events._STREAMS[tv.id] = pc_events.MAX_STREAMS_PER_DEVICE
    assert open_stream(client, tv).status_code == 429
    pc_events._STREAMS[tv.id] = pc_events.MAX_STREAMS_PER_DEVICE - 1
    stream = open_stream(client, tv)
    assert stream.status_code == 200
    stream.close()


def test_a_stream_that_was_never_read_gives_its_slot_back_when_closed(client):
    tv = device()
    stream = open_stream(client, tv)
    assert pc_events._STREAMS[tv.id] == 1
    stream.close()
    assert pc_events._STREAMS[tv.id] == 0


def test_a_stream_that_was_read_gives_its_slot_back_when_closed(client):
    tv = device()
    stream = open_stream(client, tv)
    frame(stream)
    stream.close()
    assert pc_events._STREAMS[tv.id] == 0 and not pc_events._SUBSCRIBERS


def test_the_cap_is_per_device(client):
    first, second = device(), device()
    pc_events._STREAMS[first.id] = pc_events.MAX_STREAMS_PER_DEVICE
    stream = open_stream(client, second)
    assert stream.status_code == 200
    stream.close()


def test_without_the_read_right_the_pc_asks_and_nothing_streams(client):
    answer = client.get("/tv/v1/events", headers=auth(open_session(client, device(rights=()))))
    assert answer.status_code == 403 and answer.get_json()["code"] == "needs_right"


@pytest.mark.parametrize("kind,member", [("watch", "stephen"), ("tv", "stephen"), ("phone", "family"), ("phone", None)])
def test_a_watch_a_tv_a_family_or_unbound_device_cannot_open_it(client, kind, member):
    answer = client.get("/tv/v1/events", headers=auth(open_session(client, device(kind, member))))
    assert answer.status_code == 403


def test_the_desktops_approval_feed_reaches_the_hub_through_the_pump(client, monkeypatch):
    monkeypatch.undo()                                     # the pump needs the real one
    pc_events._ensure_pump()
    deadline = time.time() + 5
    while time.time() < deadline and approval_feed.subscribers() == 0:
        time.sleep(0.05)
    before = pc_events._LAST_ID
    approval_feed.publish({"type": "resolved", "approval_id": "appr_pumped"})
    deadline = time.time() + 5
    while time.time() < deadline and pc_events._LAST_ID == before:
        time.sleep(0.05)
    assert pc_events._LAST_ID > before
    assert any(raw.get("approval_id") == "appr_pumped" for _, _, raw in pc_events._RING)

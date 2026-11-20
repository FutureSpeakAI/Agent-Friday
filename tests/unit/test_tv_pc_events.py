"""``GET /tv/v1/events``: one resumable feed for the owner's phone and tablet (PW-G8).

Rules guarded here, each with a case that fails if the check is removed:
* only an owner-bound phone or tablet holding ``pc_read`` may open it; a revoked device is told and closed;
* ``hello`` first; every event id is ``<boot>-<n>`` with a source name; ``Last-Event-ID`` resumes from the ring, and a
  device that is too far behind, ahead of the numbering, names another run of the PC (it restarted) or sends nonsense is
  told ``reset``, never left stale; after a reset it receives every later event;
* events reach a stream in number order however many sources publish at once;
* a heartbeat on a quiet stream; a device that cannot keep up is told ``reset``;
* an approval card is cut down for the device the way the cards list is; a source with no shaper publishes nothing and a
  shaper may hide an event from a device;
* at most three streams per device: a new one ends the oldest, and a closed stream gives its slot back.
"""
import collections
import json
import threading
import time

import pytest

from agent_friday.services import approval_feed
from agent_friday.services import approvals as ap
from agent_friday.services import device_pairing
from agent_friday.tv_gateway import app as gateway
from agent_friday.tv_gateway import household, pairing, pc_events, pc_read
from tests.unit.test_tv_gateway import FakeTv, auth, open_session

REAL_ENSURE_PUMP = pc_events._ensure_pump


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
    monkeypatch.setattr(pc_events, "_LAST", 0)
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
    return client.get("/tv/v1/events", headers=headers, buffered=False)


def frame(response):
    """The next SSE frame as (event, id, data)."""
    chunk = next(iter(response.response))
    text = chunk.decode("utf-8") if isinstance(chunk, bytes) else chunk
    fields = dict(line.split(": ", 1) for line in text.strip().split("\n"))
    return fields.get("event"), fields.get("id"), json.loads(fields["data"])


def note(approval_id="appr_x"):
    return pc_events.publish("needs_you", {"type": "resolved", "approval_id": approval_id})


def eid(number):
    return "%s-%d" % (pc_events._BOOT, number)


def test_hello_comes_first_with_this_runs_boot_and_the_current_number(client):
    note()
    event, event_id, data = frame(open_stream(client, device()))
    assert event == "hello" and event_id is None and data["rev"] == 1 and data["resume"] == "ok"
    assert data["boot"] == pc_events._BOOT


def test_events_carry_an_id_with_the_boot_and_a_source_name(client):
    stream = open_stream(client, device())
    frame(stream)
    assert (note("appr_a"), note("appr_b")) == (1, 2)
    assert frame(stream)[:2] == ("needs_you", eid(1)) and frame(stream)[:2] == ("needs_you", eid(2))


def test_last_event_id_resumes_with_only_what_was_missed(client):
    for number in range(3):
        note("appr_%d" % number)
    stream = open_stream(client, device(), last_event_id=eid(1))
    assert frame(stream)[2]["resume"] == "ok"
    assert [frame(stream)[1], frame(stream)[1]] == [eid(2), eid(3)]


@pytest.mark.parametrize("last", ["99", "not-a-number", "-5", "", "deadbeef-3", "-", "x-1", "1" * 30])
def test_a_device_with_an_id_this_run_did_not_issue_is_told_to_reset(client, last):
    note()
    assert frame(open_stream(client, device(), last_event_id=last))[2]["resume"] == "reset" or last == ""


def test_a_device_ahead_of_this_runs_numbering_is_told_to_reset(client):
    note()
    assert frame(open_stream(client, device(), last_event_id=eid(99)))[2]["resume"] == "reset"


def test_a_resume_after_a_restart_is_a_reset_even_when_the_old_number_is_small(client, monkeypatch):
    """The phone saw event 50 of the previous run; this run has published 60 events. Without the boot in the id the PC
    would answer 'ok' and replay 51 to 60, and the phone would silently miss the first 50 of this run."""
    for number in range(60):
        note("appr_%d" % number)
    old_run = "oldboot00000-50"
    assert pc_events._BOOT != "oldboot00000"
    assert frame(open_stream(client, device(), last_event_id=old_run))[2]["resume"] == "reset"


def test_after_a_reset_every_later_event_is_delivered(client):
    """A phone that sent an id of another run, with a number above this run's current one, still gets what comes next."""
    for number in range(3):
        note("appr_%d" % number)
    stream = open_stream(client, device(), last_event_id="oldboot00000-50")
    assert frame(stream)[2]["resume"] == "reset"
    assert note("appr_later") == 4
    assert frame(stream)[:2] == ("needs_you", eid(4))


def test_a_device_further_behind_than_the_ring_is_told_to_reset(client, monkeypatch):
    monkeypatch.setattr(pc_events, "_RING", collections.deque(maxlen=3))
    for number in range(10):
        note("appr_%d" % number)
    assert frame(open_stream(client, device(), last_event_id=eid(2)))[2]["resume"] == "reset"


def test_a_device_exactly_at_the_edge_of_the_ring_is_caught_up(client, monkeypatch):
    monkeypatch.setattr(pc_events, "_RING", collections.deque(maxlen=3))
    for number in range(10):
        note("appr_%d" % number)
    stream = open_stream(client, device(), last_event_id=eid(7))
    assert frame(stream)[2]["resume"] == "ok" and [frame(stream)[1] for _ in range(3)] == [eid(8), eid(9), eid(10)]


def test_an_event_published_while_a_device_resumes_is_sent_once(client, monkeypatch):
    """The stream subscribes before it reads the ring, so an event can be in both: it must not be sent twice."""
    real = pc_events._since

    def publish_then_read_the_ring(after, readable):
        note("racer")
        return real(after, readable)
    monkeypatch.setattr(pc_events, "_since", publish_then_read_the_ring)
    monkeypatch.setattr(pc_events, "BEAT_S", 0.05)
    stream = open_stream(client, device(), last_event_id=eid(0))
    assert frame(stream)[0] == "hello" and frame(stream)[:2] == ("needs_you", eid(1))
    assert frame(stream)[0] == "heartbeat"


def test_events_from_several_publishers_arrive_in_order_and_none_is_dropped(client):
    stream = open_stream(client, device())
    frame(stream)

    def publisher(prefix):
        for number in range(40):
            note("%s%d" % (prefix, number))
    threads = [threading.Thread(target=publisher, args=(name,)) for name in ("a", "b", "c")]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    numbers = [int(frame(stream)[1].split("-")[1]) for _ in range(120)]
    assert numbers == list(range(1, 121))


def test_a_device_ahead_of_this_runs_numbering_still_gets_every_later_event(client):
    for number in range(3):
        note("appr_%d" % number)
    stream = open_stream(client, device(), last_event_id=eid(50))
    assert frame(stream)[2]["resume"] == "reset"
    assert note("appr_later") == 4
    assert frame(stream)[:2] == ("needs_you", eid(4))


def test_a_slow_delivery_to_one_stream_cannot_let_a_later_event_overtake_it(client, monkeypatch):
    """Numbering and delivery happen under one lock; delivered outside it, event 2 could reach a stream before event 1 and
    the stream would drop 1 as already sent."""
    real = pc_events._deliver

    def slow_for_the_first_event(subscriber, item):
        if item[0] == 1:
            time.sleep(0.3)
        real(subscriber, item)
    monkeypatch.setattr(pc_events, "_deliver", slow_for_the_first_event)
    stream = open_stream(client, device())
    frame(stream)
    first = threading.Thread(target=note, args=("a",))
    first.start()
    time.sleep(0.1)
    second = threading.Thread(target=note, args=("b",))
    second.start()
    first.join()
    second.join()
    assert [frame(stream)[1], frame(stream)[1]] == [eid(1), eid(2)]


def test_a_quiet_stream_has_a_heartbeat(client, monkeypatch):
    monkeypatch.setattr(pc_events, "BEAT_S", 0.05)
    stream = open_stream(client, device())
    frame(stream)
    event, event_id, data = frame(stream)
    assert event == "heartbeat" and event_id is None and "as_of" in data


def test_a_device_that_cannot_keep_up_is_told_to_reset_and_still_gets_what_follows(client, monkeypatch):
    monkeypatch.setattr(pc_events, "QUEUE_MAX", 3)
    stream = open_stream(client, device())
    frame(stream)
    for number in range(10):
        note("appr_%d" % number)
    assert frame(stream)[0] == "reset"                       # what it missed is covered by the refetch it now makes
    assert note("appr_after") == 11
    assert frame(stream)[:2] == ("needs_you", eid(11))


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
    assert frame(stream) == ("jobs", eid(2), {"id": "j2"})


def test_a_device_at_the_cap_ends_its_oldest_stream_instead_of_being_refused(client):
    tv = device()
    streams = [queue_stub() for _ in range(pc_events.MAX_STREAMS_PER_DEVICE)]
    pc_events._STREAMS[tv.id] = list(streams)
    fresh = open_stream(client, tv)
    assert fresh.status_code == 200 and frame(fresh)[0] == "hello"
    assert streams[0].get_nowait()[1] == "superseded" and len(pc_events._STREAMS[tv.id]) == pc_events.MAX_STREAMS_PER_DEVICE
    assert streams[0] not in pc_events._STREAMS[tv.id] and streams[1] in pc_events._STREAMS[tv.id]
    fresh.close()


def test_a_stream_whose_queue_is_full_is_still_ended_when_it_is_evicted(client):
    import queue
    tv = device()
    full = queue.Queue(maxsize=2)
    full.put((5, "needs_you", {}))
    full.put((6, "needs_you", {}))
    pc_events._STREAMS[tv.id] = [full] + [queue.Queue(maxsize=10) for _ in range(pc_events.MAX_STREAMS_PER_DEVICE - 1)]
    fresh = open_stream(client, tv)
    assert fresh.status_code == 200
    assert full.get_nowait() == (0, "superseded", {})
    fresh.close()


@pytest.mark.parametrize("number", ["²", "٣", "１"])
def test_an_id_with_a_digit_that_is_not_an_ascii_digit_is_a_reset_not_a_server_error(client, number):
    answer = open_stream(client, device(), last_event_id="%s-%s" % (pc_events._BOOT, number))
    assert answer.status_code == 200 and frame(answer)[2]["resume"] == "reset"


def queue_stub():
    import queue
    return queue.Queue(maxsize=10)


def test_the_oldest_stream_is_told_it_was_superseded(client, monkeypatch):
    tv = device()
    monkeypatch.setattr(pc_events, "MAX_STREAMS_PER_DEVICE", 1)
    first = open_stream(client, tv)
    frame(first)
    second = open_stream(client, tv)             # ends the first (one request context at a time, so read first afterwards)
    assert frame(first)[0] == "superseded"
    second.close()


def test_a_stream_that_was_never_read_gives_its_slot_back_when_closed(client):
    tv = device()
    stream = open_stream(client, tv)
    assert len(pc_events._STREAMS[tv.id]) == 1
    stream.close()
    assert pc_events._STREAMS[tv.id] == []


def test_a_stream_that_was_read_gives_its_slot_back_when_closed(client):
    tv = device()
    stream = open_stream(client, tv)
    frame(stream)
    stream.close()
    assert pc_events._STREAMS[tv.id] == [] and not pc_events._SUBSCRIBERS


def test_without_the_read_right_the_pc_asks_and_nothing_streams(client):
    answer = client.get("/tv/v1/events", headers=auth(open_session(client, device(rights=()))))
    assert answer.status_code == 403 and answer.get_json()["code"] == "needs_right"


@pytest.mark.parametrize("kind,member", [("watch", "stephen"), ("tv", "stephen"), ("phone", "family"), ("phone", None)])
def test_a_watch_a_tv_a_family_or_unbound_device_cannot_open_it(client, kind, member):
    answer = client.get("/tv/v1/events", headers=auth(open_session(client, device(kind, member))))
    assert answer.status_code == 403


def test_the_desktops_approval_feed_reaches_the_hub_through_the_pump(client, monkeypatch):
    """The real pump thread, stopped at the end so it cannot publish into another test's ring."""
    monkeypatch.setattr(pc_events, "_ensure_pump", REAL_ENSURE_PUMP)      # only the pump is real; the rest stays isolated
    pc_events.stop_pump()
    saved = (pc_events._RING, pc_events._LAST)
    pc_events._RING, pc_events._LAST = collections.deque(maxlen=pc_events.RING), 0
    try:
        pc_events._ensure_pump()
        deadline = time.time() + 5
        while time.time() < deadline and approval_feed.subscribers() == 0:
            time.sleep(0.05)
        approval_feed.publish({"type": "resolved", "approval_id": "appr_pumped"})
        deadline = time.time() + 5
        while time.time() < deadline and pc_events._LAST == 0:
            time.sleep(0.05)
        assert pc_events._LAST >= 1 and any(raw.get("approval_id") == "appr_pumped" for _, _, raw in pc_events._RING)
    finally:
        pc_events.stop_pump()
        pc_events._RING, pc_events._LAST = saved

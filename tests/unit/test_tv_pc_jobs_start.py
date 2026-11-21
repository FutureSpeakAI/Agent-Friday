"""Starting a job from an owner phone or tablet (PW-G12, ``pc_act``): ``POST /tv/v1/jobs``.

* only an owner-bound phone or tablet that holds ``pc_act`` may start one; the prompt is checked here before the
  desktop is asked; the desktop is asked with the device as the origin and nothing else the device sent;
* the desktop's refusal is passed on in its own words; a desktop that fails is a plain 503;
* the receipt names the job and the device and never the prompt.
"""
import json

import pytest

from agent_friday.services import device_pairing
from agent_friday.tv_gateway import pc_act
from tests.ledger_support import rows
from tests.unit import test_tv_pc_act as base
from tests.unit.test_tv_gateway import auth, open_session

_clean, client, device = base._clean, base.client, base.device

CANARY = "the-canary-prompt-text-7731"


class Start:
    def __init__(self):
        self.calls, self.status, self.body, self.boom = [], 201, {"ok": True, "task_id": "job-1"}, None

    def __call__(self, path, body=None):
        self.calls.append((path, body))
        if self.boom:
            raise self.boom
        return self.status, self.body


@pytest.fixture
def desk(monkeypatch):
    stand_in = Start()
    monkeypatch.setattr(pc_act, "_desk_post", stand_in)
    return stand_in


def start(client, tv, **body):
    body.setdefault("prompt", "tidy the downloads folder")
    return client.post("/tv/v1/jobs", headers=auth(open_session(client, tv)), json=body)


def test_an_owner_phone_with_the_right_starts_a_job_and_is_given_its_id(client, desk):
    tv = device()
    answer = start(client, tv, conversation_id="conv_1")
    assert answer.status_code == 201 and answer.get_json() == {"ok": True, "job_id": "job-1"}
    assert desk.calls == [("/api/tasks/start", {"prompt": "tidy the downloads folder", "origin": "device:%s" % tv.id,
                                                "conversation_id": "conv_1"})]


def test_the_device_cannot_choose_the_origin_or_send_anything_else(client, desk):
    tv = device()
    start(client, tv, origin="pc", principal="x", tools=["send_email"], model="m", scope={"a": 1})
    [(_, sent)] = desk.calls
    assert sent == {"prompt": "tidy the downloads folder", "origin": "device:%s" % tv.id}


@pytest.mark.parametrize("kind,member", [("watch", "stephen"), ("tv", "stephen"), ("phone", "family"), ("phone", None)])
def test_a_watch_a_tv_a_family_or_unbound_device_cannot_start_a_job(client, desk, kind, member):
    answer = start(client, device(kind, member))
    assert answer.status_code == 403 and answer.get_json()["code"] == "pc_act_not_for_this_device"
    assert desk.calls == []


def test_without_the_right_the_device_is_told_and_nothing_starts(client, desk):
    answer = start(client, device(rights=("pc_read",)))
    assert answer.status_code == 403 and answer.get_json()["code"] == "needs_right" and desk.calls == []


@pytest.mark.parametrize("prompt", ["", "  ", None, 7, ["a"], "x" * 4001])
def test_a_prompt_that_is_empty_not_text_or_too_long_is_refused_before_the_desktop_is_asked(client, desk, prompt):
    answer = start(client, device(), prompt=prompt)
    assert answer.status_code == 400 and answer.get_json()["code"] == "bad_prompt" and desk.calls == []


@pytest.mark.parametrize("bad", ["..", ".hidden", "a b", "a" * 200, 5, ["x"]])
def test_a_bad_conversation_id_never_reaches_the_desktop(client, desk, bad):
    answer = start(client, device(), conversation_id=bad)
    assert answer.status_code == 400 and desk.calls == []


def test_a_body_that_is_not_an_object_is_a_refusal_not_a_crash(client, desk):
    tv = device()
    answer = client.post("/tv/v1/jobs", headers=auth(open_session(client, tv)), data="[1]",
                         content_type="application/json")
    assert answer.status_code == 400 and desk.calls == []


def test_the_desktops_refusal_is_passed_on_in_its_own_words(client, desk):
    desk.status, desk.body = 409, {"error": "3 jobs from your devices are already running; wait for one to finish"}
    answer = start(client, device())
    assert answer.status_code == 409 and "already running" in answer.get_json()["error"]
    assert "job_id" not in answer.get_json() and rows("device_act")[-1]["outcome"] == 409


@pytest.mark.parametrize("failure,recorded", [("raises", 503), ("answers_5xx", 500)])
def test_a_desktop_that_fails_is_a_plain_503_and_still_a_receipt(client, desk, failure, recorded):
    if failure == "raises":
        desk.boom = RuntimeError("down")
    else:
        desk.status, desk.body = 500, {}
    answer = start(client, device())
    assert answer.status_code == 503 and answer.get_json()["code"] == "pc_unavailable"
    assert rows("device_act")[-1]["outcome"] == recorded


def test_a_desktop_that_says_yes_without_a_job_is_not_called_a_start(client, desk):
    desk.status, desk.body = 201, {"ok": True}
    assert start(client, device()).status_code == 409


def test_the_receipt_names_the_job_and_the_device_and_never_the_prompt(client, desk):
    tv = device()
    start(client, tv, prompt="please do " + CANARY)
    [receipt] = rows("device_act")
    assert (receipt["right"], receipt["action"], receipt["job"], receipt["via"]) == ("pc_act", "start", "job-1", "lan")
    assert tv.id in receipt["principal"]
    assert CANARY not in json.dumps(rows("device_act")) and CANARY not in json.dumps(device_pairing.list_devices())


def test_withdrawing_the_right_ends_it_on_a_live_session(client, desk):
    tv = device()
    token = open_session(client, tv)
    first = client.post("/tv/v1/jobs", headers=auth(token), json={"prompt": "a"})
    device_pairing.set_right(tv.id, "pc_act", False)
    again = client.post("/tv/v1/jobs", headers=auth(token), json={"prompt": "b"})
    assert first.status_code == 201 and again.status_code == 403 and len(desk.calls) == 1

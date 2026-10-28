"""A retried contact save does not make a second contact (NS-33.6-2: zero duplicate outward actions
from retry).

Before: save_google_contact always called createContact, so the model retrying the same save in a
turn (after a timeout, or because it was unsure) put two copies in the owner's Google Contacts. Now
the same details in the same turn or task are one create: a repeat returns the contact already
saved; a repeat after Google did not confirm is not sent again, and says to check Contacts. The
owner's own page (no turn, no task) saves as asked. Google faked; synthetic contacts only.
"""
from __future__ import annotations

import pytest


@pytest.fixture
def gcw(monkeypatch):
    from agent_friday.services import google_contacts_write as mod
    made = []
    box = {"boom": None}

    class _Exec:
        def __init__(self, n):
            self.n = n

        def execute(self):
            if box["boom"] is not None:
                raise box["boom"]
            return {"resourceName": "people/c%d" % self.n}

    class _Svc:
        def people(self):
            return self

        def createContact(self, body=None):   # noqa: N802 (Google's API)
            made.append(body)
            return _Exec(len(made))

    monkeypatch.setattr(mod, "_pick_account", lambda aid: ({"id": "acct_s1", "email": "owner@example.test"}, ""))
    monkeypatch.setattr(mod, "_service", lambda aid: (_Svc(), ""))
    monkeypatch.setattr(mod, "_RECENT", {}, raising=False)
    yield mod, made, box


@pytest.fixture
def in_task():
    from agent_friday.services import agent
    tok = agent._CURRENT_TOOL_CONTEXT.set({"task_id": "task-contacts-1"})
    yield
    agent._CURRENT_TOOL_CONTEXT.reset(tok)


def test_the_same_save_twice_in_a_task_makes_one_contact(gcw, in_task):
    mod, made, _box = gcw
    first = mod.save_contact(name="Sam Synthetic", email="sam@example.test")
    again = mod.save_contact(name="Sam Synthetic", email="sam@example.test")
    assert first["ok"] and again["ok"]
    assert len(made) == 1, "a retried save made a second contact"
    assert again["resource_name"] == first["resource_name"] and again["action"] == "already_saved"


def test_a_retry_after_an_unconfirmed_save_is_not_sent_again(gcw, in_task):
    mod, made, box = gcw
    box["boom"] = TimeoutError("the read timed out")
    first = mod.save_contact(name="Sam Synthetic", email="sam@example.test")
    assert first["ok"] is False and len(made) == 1          # it reached Google
    box["boom"] = None
    again = mod.save_contact(name="Sam Synthetic", email="sam@example.test")
    assert len(made) == 1, "a save Google may already have made was sent again"
    assert again["ok"] is False and "check Contacts" in again["error"]


def test_a_refused_save_can_be_retried(gcw, in_task):
    mod, made, box = gcw

    class _Resp(dict):
        status = 400
        reason = "Bad Request"
    from googleapiclient.errors import HttpError
    box["boom"] = HttpError(_Resp(), b'{"error": {"message": "invalid"}}')
    assert mod.save_contact(name="Sam Synthetic", email="sam@example.test")["ok"] is False
    box["boom"] = None
    assert mod.save_contact(name="Sam Synthetic", email="sam@example.test")["ok"] is True
    assert len(made) == 2


def test_different_details_are_a_different_contact(gcw, in_task):
    mod, made, _box = gcw
    mod.save_contact(name="Sam Synthetic", email="sam@example.test")
    mod.save_contact(name="Pat Synthetic", email="pat@example.test")
    assert len(made) == 2


def test_the_owners_own_saves_outside_a_turn_are_each_made(gcw):
    mod, made, _box = gcw
    mod.save_contact(name="Sam Synthetic", email="sam@example.test")
    mod.save_contact(name="Sam Synthetic", email="sam@example.test")
    assert len(made) == 2

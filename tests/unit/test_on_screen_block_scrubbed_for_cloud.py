"""The rows on the owner's screen (who and subject) reach a guarded cloud turn scrubbed, on
/api/chat/send as on /api/chat; the local seat and an unrestricted cloud get them as they are.
Synthetic row."""
from __future__ import annotations

import inspect

import pytest

ROW = "\n\nON SCREEN\n1. Dana Ortiz <dana.ortiz@example.com> - Lunch on Thursday? [unread]\n"


@pytest.fixture
def rc(monkeypatch):
    from agent_friday.routes import chat as rc
    from agent_friday.services import screen_stage
    monkeypatch.setattr(screen_stage, "chat_tail", lambda cid=None: ROW)
    return rc


@pytest.mark.parametrize("provider", ["cloud", "openai"])
def test_a_guarded_cloud_turn_gets_the_rows_scrubbed(rc, monkeypatch, provider):
    from agent_friday.services import egress_gate
    monkeypatch.setattr(egress_gate, "is_unrestricted_cloud", lambda: False)
    out = rc._screen_block_for(provider, "c1")
    assert out and "dana.ortiz@example.com" not in out


def test_the_local_seat_gets_the_rows_as_they_are(rc):
    assert "dana.ortiz@example.com" in rc._screen_block_for("local", "c1")


def test_an_unrestricted_cloud_gets_the_rows_as_they_are(rc, monkeypatch):
    from agent_friday.services import egress_gate
    monkeypatch.setattr(egress_gate, "is_unrestricted_cloud", lambda: True)
    assert "dana.ortiz@example.com" in rc._screen_block_for("cloud", "c1")


def test_chat_send_builds_its_prompt_with_the_scrubbed_block():
    from agent_friday.routes import chat as rc
    src = inspect.getsource(rc.chat_send)
    assert "_screen_block_for(provider_name" in src and "chat_tail(" not in src

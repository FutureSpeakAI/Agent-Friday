"""A finished task is delivered once, where the owner is.

Before: a card, a chat line and (in a call) speech, every time. Now: in a live
call it is spoken; with the chat open it is one chat line; otherwise it waits in
the panel's Updates. A call that has gone falls to the next place, never to
silence.
"""
from __future__ import annotations

import pytest

from agent_friday.services import agent as ag
from agent_friday.services import conversations, desktop_bus, notice_registry as reg
from agent_friday.services import voice_live_channel


@pytest.fixture
def effects(monkeypatch):
    seen = []
    monkeypatch.setattr(ag, 'TASKS', {'t1': {'task_id': 't1', 'conversation_id': 'conv-x'},
                                      't2': {'task_id': 't2'}})
    monkeypatch.setattr(conversations, 'resolve', lambda cid: cid)
    monkeypatch.setattr(conversations, 'append',
                        lambda cid, msg, **kw: seen.append('chat') or msg)
    import agent_friday.notifications_engine as ne
    monkeypatch.setattr(ne, 'push', lambda **kw: seen.append('panel') or kw)
    monkeypatch.setattr(voice_live_channel, 'deliver', lambda *a, **kw: seen.append('spoken') or True)
    return seen


def _where(monkeypatch, *, call, chat):
    monkeypatch.setattr(voice_live_channel, 'is_live', lambda cid: call)
    monkeypatch.setattr(desktop_bus, 'connected', lambda *a, **k: chat)


@pytest.mark.parametrize('call, chat, expected', [
    (True, True, ['spoken']), (True, False, ['spoken']),
    (False, True, ['chat']), (False, False, ['panel'])],
    ids=['call', 'call-and-chat', 'chat', 'neither'])
def test_one_task_completion_is_one_delivery(monkeypatch, effects, call, chat, expected):
    _where(monkeypatch, call=call, chat=chat)
    ag._report_task_completion('t1', 'Tidy the notes', 'complete', 'Done: 12 notes filed.')
    assert effects == expected


def test_a_task_with_no_conversation_waits_in_the_panel(monkeypatch, effects):
    _where(monkeypatch, call=True, chat=True)
    ag._report_task_completion('t2', 'Tidy the notes', 'complete', 'Done.')
    assert effects == ['panel']


def test_a_failed_task_is_delivered_the_same_way(monkeypatch, effects):
    _where(monkeypatch, call=False, chat=True)
    ag._report_task_completion('t1', 'Tidy the notes', 'failed', '[Error] no seat')
    assert effects == ['chat']


def test_a_call_that_dropped_falls_to_the_chat_then_the_panel(monkeypatch, effects):
    _where(monkeypatch, call=True, chat=True)
    monkeypatch.setattr(voice_live_channel, 'deliver', lambda *a, **kw: False)
    ag._report_task_completion('t1', 'Tidy the notes', 'complete', 'Done.')
    assert effects == ['chat']
    effects.clear()
    _where(monkeypatch, call=True, chat=False)
    ag._report_task_completion('t1', 'Tidy the notes', 'complete', 'Done.')
    assert effects == ['panel']


def test_the_card_for_a_task_is_not_also_a_chat_injection(monkeypatch):
    """The panel card never asks to be injected into the chat as well."""
    pushed = []
    monkeypatch.setattr(ag, 'TASKS', {'t2': {'task_id': 't2'}})
    import agent_friday.notifications_engine as ne
    monkeypatch.setattr(ne, 'push', lambda **kw: pushed.append(kw) or kw)
    _where(monkeypatch, call=False, chat=False)
    ag._report_task_completion('t2', 'Tidy the notes', 'complete', 'Done.')
    assert len(pushed) == 1
    assert not pushed[0].get('proactive_chat')
    assert pushed[0]['dedupe_key'] == 'task-done:t2', 'the key is the task, never its title'


def test_the_target_is_chosen_by_where_the_owner_is(monkeypatch):
    _where(monkeypatch, call=True, chat=True)
    assert reg.completion_target('c') == reg.SPOKEN
    _where(monkeypatch, call=False, chat=True)
    assert reg.completion_target('c') == reg.CHAT
    _where(monkeypatch, call=False, chat=False)
    assert reg.completion_target('c') == reg.PANEL
    assert reg.completion_target(None) == reg.PANEL

"""Letting anyone in the room approve out loud takes an on-screen click (owner ruling, 2026-10-17).

`voice_room_approvals_require_name` False lets anyone the microphone hears approve a card by voice.
Loosening it follows the rule of Laya's mail changes: only the Settings > Voice control's own
owner_action on a click on Friday's page, or the setup chat's chip pressed on the page. A plain
settings save, a script or a model's tool cannot; tightening is always allowed.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from agent_friday import core

KEY = "voice_room_approvals_require_name"
REPO = Path(__file__).resolve().parents[2]


@pytest.fixture
def settings_file():
    path = core.SETTINGS_FILE
    before = path.read_bytes() if path.exists() else None
    core._save_settings({KEY: True})
    yield core
    if before is None:
        try:
            path.unlink()
        except FileNotFoundError:
            pass
    else:
        path.write_bytes(before)
    core._invalidate_settings_cache()


def _stored():
    return core._load_settings_raw().get(KEY)


def test_a_plain_save_cannot_let_anyone_approve(settings_file):
    core._save_settings({KEY: False, "temperature": 0.5})
    assert _stored() is True
    assert core._load_settings_raw().get("temperature") == 0.5, "the rest of the write is kept"


@pytest.mark.parametrize("bad", ["false", 0, None, "anyone"])
def test_a_value_that_is_not_true_or_false_is_dropped(settings_file, bad):
    core._save_settings({KEY: bad}, owner_room_approvals_change=True)
    assert _stored() is True


def test_the_owner_click_loosens_and_tightening_needs_nothing(settings_file):
    core._save_settings({KEY: False}, owner_room_approvals_change=True)
    assert _stored() is False
    core._save_settings({KEY: True})
    assert _stored() is True


def _post(monkeypatch, body, click):
    from flask import Flask
    from agent_friday.routes import core_routes as cr
    from agent_friday.services import screen_click
    saved = {}
    monkeypatch.setattr(cr, "_save_settings", lambda data, **kw: saved.update(data=data, kw=kw) or dict(data))
    monkeypatch.setattr(screen_click, "screen_session", lambda req: click)
    with Flask(__name__).test_request_context("/api/settings", method="POST", json=body):
        resp = cr.api_settings()
    resp, status = (resp if isinstance(resp, tuple) else (resp, 200))
    return status, saved


def test_the_settings_route_needs_the_click_for_the_owner_action(monkeypatch):
    body = {"owner_action": "room_approvals", "settings": {KEY: False}}
    status, saved = _post(monkeypatch, body, click=True)
    assert status == 200 and saved["kw"]["owner_room_approvals_change"] is True
    status, saved = _post(monkeypatch, body, click=False)
    assert status == 403 and not saved
    # without the owner action the save runs, and core drops the loosening
    status, saved = _post(monkeypatch, {"settings": {KEY: False}}, click=True)
    assert status == 200 and saved["kw"]["owner_room_approvals_change"] is False


def test_the_setup_answer_route_says_whether_it_was_a_press_on_the_page(monkeypatch):
    from flask import Flask
    from agent_friday.routes import setup_chat as rsc
    from agent_friday.services import screen_click
    from agent_friday.services import setup_chat as sc
    seen = []
    monkeypatch.setattr(sc, "answer", lambda *a, **k: seen.append(sc.SCREEN_CLICK.get()) or {})
    for click in (True, False):
        monkeypatch.setattr(screen_click, "screen_session", lambda req, c=click: c)
        with Flask(__name__).test_request_context("/api/setup-chat/answer", method="POST",
                                                  json={"stage": "room_voice", "value": "anyone"}):
            rsc.setup_chat_answer()
    assert seen == [True, False]
    assert sc.SCREEN_CLICK.get() is False, "the flag does not outlive the request"


def test_the_settings_control_sends_its_own_owner_action_in_both_ui_sources():
    for name in ("index.html", "ui_parts/app.html"):
        src = (REPO / name).read_text(encoding="utf-8")
        assert "owner_action: 'room_approvals'" in src, name
        assert "fridaySetRoomApprovals(o.k)" in src, name
        assert "save({voice_room_approvals_require_name" not in src.replace(" ", "").replace("\n", ""), name

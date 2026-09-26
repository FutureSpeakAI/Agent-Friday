"""Every surface tells the user about a privacy hold: voice aloud and on screen,
chat on screen, with "send anyway (pattern filters only)" and "wait".

The chat endpoints are driven end to end in tests/api/test_privacy_hold_notice.py.
Here: the voice gates really record holds, the voice frame carries the notice and
both choices, the live handler shows and says it, and both copies of the UI
render the card and send the one-turn override.
"""
import inspect
import pathlib

import pytest

import agent_friday.routes.voice as rv
from agent_friday.services import egress_gate as eg
from agent_friday.services import sensitivity_classifier as sc

ROOT = pathlib.Path(__file__).resolve().parents[2]
TEXT = "she stays with me every other weekend and walks to the school by the park"


@pytest.fixture
def layer3_down(monkeypatch):
    monkeypatch.setattr(sc, "_load_embedder", lambda: None)
    monkeypatch.setattr(sc, "_EXEMPLAR_EMBEDS", None)
    monkeypatch.setattr(sc, "_layer3_expected", lambda: True)
    monkeypatch.setattr(eg, "_rate_limit", lambda: None)
    monkeypatch.setattr(eg, "is_unrestricted_cloud", lambda: False)


def test_the_voice_gates_record_the_hold(layer3_down):
    with sc.layer3_hold_scope() as holds:
        out = rv._gate_voice_text(TEXT)
    assert holds, "voice text was withheld and nothing recorded it"
    assert TEXT not in out
    with sc.layer3_hold_scope(override=True) as holds:
        out = rv._gate_voice_text(TEXT)
    assert not holds and TEXT in out, "send anyway must send, with the pattern filters"


def test_the_voice_frame_carries_the_notice_and_both_choices():
    f = rv._voice_hold_frame(2)
    assert f["type"] == "privacy_hold" and f["voice"] is True and f["held"] == 2
    assert f["text"] == sc.VOICE_HOLD_NOTICE
    assert [o["id"] for o in f["options"]] == ["send_anyway", "wait"]


def test_the_live_handler_shows_and_says_every_hold():
    src = inspect.getsource(rv).replace("\r\n", "\n")
    # A scope for the whole call, opened before the instruction is gated, and
    # closed when the handler ends; the override comes from the socket URL.
    i_scope = src.index("_voice_holds = _hold_scope.__enter__()")
    assert i_scope < src.index("sys_text = _gate_voice_system_instruction(sys_text)")
    assert "request.args.get('privacy_layer3_override')" in src
    assert "_hold_scope.__exit__(None, None, None)" in src
    # Shown after every gate that can hold...
    assert "result = _gate_voice_tool_result(result, fname)\n                            _tell_hold()" in src
    assert "_txt = _gate_voice_text(msg['text'])\n                                    _tell_hold()" in src
    # ...and said aloud in place of the greeting.
    assert "if not _tell_hold() else _sc_hold.VOICE_HOLD_SPOKEN" in src
    assert "button on screen" in sc.VOICE_HOLD_SPOKEN


def test_both_copies_of_the_ui_show_the_card_and_send_the_override():
    served = (ROOT / "index.html").read_text(encoding="utf-8")
    mirror = (ROOT / "ui_parts" / "app.html").read_text(encoding="utf-8")
    for html in (served, mirror):
        assert "function PrivacyHoldCard(" in html
        assert "function PrivacyCheckLayers(" in html
        assert "privacy_hold" in html
    # The one-turn override on both request bodies (App and the conversation window).
    assert served.count("if (opts.privacyOverride) body.privacy_layer3_override = true;") == 2
    assert "if(opts.privacyOverride)body.privacy_layer3_override=true;" in mirror
    # A held turn opens the card in both chat surfaces.
    assert served.count("setPausePending({ kind: 'privacy_hold', message: m, hold: d.privacy_hold })") == 2
    # Voice: the frame opens the same card, and "send anyway" rides the socket URL.
    assert "m.type === 'privacy_hold'" in served
    assert "_qs.push('privacy_layer3_override=1')" in served

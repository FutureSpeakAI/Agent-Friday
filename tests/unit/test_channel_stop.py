"""Stop everything from the phone and the chat bridges (BD-13; NS-20.8-3/-5/-6, NS-19.8-1/-2).

Acceptance: "With a key held by the control lane and the model loop wedged, STOP from the
hotkey, the phone and voice each release the key and halt within one second, with a
receipt." #182 built the stop for the hotkey, voice and chat; these tests are the phone (a
text from the owner's verified cell) and the chat bridges (Telegram, Discord). A turn that is
stuck must not keep the stop waiting, a stranger's words stop nothing, and nothing is ever
resumed from outside Friday's own page. No model, no socket, no Twilio.
"""
from __future__ import annotations

import threading
import time

import pytest

from agent_friday.phone import config, guard, ingress, service, signature, spool
from tests.unit.phone_fakes import (FAKE_ACCOUNT, FAKE_TOKEN, OWNER, PUBLIC, STRANGER,  # noqa: F401
                                    fake_twilio, phone_home, quiet, verify_owner)


@pytest.fixture
def gs(tmp_path, monkeypatch):
    """The real stop, made safe for a test machine (as in test_global_stop)."""
    from agent_friday.services import global_stop, agent, local_image
    monkeypatch.setattr(global_stop, "_path", lambda: tmp_path / "global_stop.json")
    monkeypatch.setattr(agent, "_HAS_PYAUTOGUI", False, raising=False)
    monkeypatch.setattr(local_image, "interrupt_comfy", lambda timeout=10: False)
    had_perm, had_kill = agent._CC_PERMISSION.is_set(), agent._CC_KILL.is_set()
    global_stop._reset_for_tests()
    yield global_stop
    global_stop._reset_for_tests()
    (agent._CC_PERMISSION.set if had_perm else agent._CC_PERMISSION.clear)()
    (agent._CC_KILL.set if had_kill else agent._CC_KILL.clear)()


def _stop_receipts():
    from tests import ledger_support
    return [d for d in ledger_support.decisions() if d.get("tool") == "global_stop"
            and d.get("decision") in ("stop", "stop_again")]


# ── the phone ────────────────────────────────────────────────────────────────

@pytest.fixture
def client(phone_home, monkeypatch):
    monkeypatch.setattr(ingress, "_GUARD", guard.IngressGuard())
    return ingress.create_app().test_client()


def _text(client, frm, body, sid="SM" + "7" * 32):
    p = {"AccountSid": FAKE_ACCOUNT, "MessageSid": sid, "From": frm, "To": "+15125550100",
         "Body": body, "NumMedia": "0", "NumSegments": "1"}
    url = PUBLIC + "/twilio/sms"
    return client.post("/twilio/sms", data=p,
                       headers={"X-Twilio-Signature": signature.compute(FAKE_TOKEN, url, p)})


def test_a_stop_texted_from_the_owner_cell_halts_at_once_without_the_service_loop(
        client, gs, monkeypatch):
    """The service loop that reads texts is not run at all here (as if a turn had it stuck):
    the signed webhook itself stops everything, within a second, with a receipt."""
    verify_owner(monkeypatch)
    t0 = time.time()
    r = _text(client, OWNER, "Stop everything")
    assert r.status_code == 200
    assert gs.active(), "a stop texted from the owner's verified cell did not stop anything"
    assert gs.state()["since"] - t0 < 1.0          # the stop took hold within a second
    assert spool.pending_events() and spool.pending_events()[-1]["data"]["Body"] == "Stop everything"
    rec = _stop_receipts()
    assert rec and rec[-1]["surface"] == "sms" and rec[-1]["chosen_by"] == "owner"


@pytest.mark.parametrize("frm,body", [
    (STRANGER, "Stop everything"),          # a stranger's words stop nothing
    (OWNER, "STOP"),                         # Twilio's own opt-out word, left to Twilio
    (OWNER, "don't stop everything yet"),    # not the command
])
def test_these_texts_do_not_stop_anything(client, gs, monkeypatch, frm, body):
    verify_owner(monkeypatch)
    assert _text(client, frm, body).status_code == 200
    assert not gs.active()


def test_an_unverified_cell_cannot_stop(client, gs):
    # The owner cell is set (phone_home) but never verified.
    assert config.verified_owner_cell() == ""
    _text(client, OWNER, "Stop everything")
    assert not gs.active()


def _event(kind, sid, **data):
    spool.put_event(kind, sid, data)
    return spool.pending_events()[-1]


def test_the_service_stops_too_and_never_asks_the_model(phone_home, fake_twilio, gs,
                                                        monkeypatch, quiet):
    """The event the ingress spooled: if the ingress could not stop, the service does; the model
    is never asked and nothing is texted back (nothing leaves while stopped)."""
    verify_owner(monkeypatch)
    monkeypatch.setattr(config, "load", lambda _orig=config.load: dict(_orig(), sms_conversation=True))
    monkeypatch.setattr(service, "_run_phone_agent",
                        lambda *a: pytest.fail("a stop text reached the model"))
    _event("sms_in", "SM10", From=OWNER, Body="halt all work now", MessageSid="SM10")
    service.process_pending()
    assert gs.active()
    assert fake_twilio.sent_bodies() == []
    assert any("Stopped from your cell" in n.get("title", "") for n in quiet)


def test_while_stopped_an_owner_text_reaches_no_model(phone_home, fake_twilio, gs, monkeypatch, quiet):
    verify_owner(monkeypatch)
    monkeypatch.setattr(config, "load", lambda _orig=config.load: dict(_orig(), sms_conversation=True))
    monkeypatch.setattr(service, "_run_phone_agent",
                        lambda *a: pytest.fail("a text reached the model while everything was stopped"))
    gs.stop(by="owner", source="button")
    _event("sms_in", "SM11", From=OWNER, Body="what's on today?", MessageSid="SM11")
    service.process_pending()
    assert fake_twilio.sent_bodies() == []


# ── the chat bridges ─────────────────────────────────────────────────────────

@pytest.fixture
def chans(tmp_path, monkeypatch):
    from agent_friday.services.channels import manager
    monkeypatch.setattr(manager, "FRIDAY_DIR", tmp_path)
    monkeypatch.setattr(manager, "CONFIG_PATH", tmp_path / "channels.json")
    monkeypatch.setattr(manager, "_ADAPTERS", {})
    cfg = manager.load_config()
    cfg["enabled"] = True
    for ch in ("telegram", "discord"):
        cfg[ch]["enabled"] = True
        cfg[ch]["allowlist"] = ["42"]
    manager.save_config(cfg)
    return manager


@pytest.mark.parametrize("channel", ["telegram", "discord"])
def test_a_stop_from_an_allowlisted_chat_stops_before_any_model(chans, gs, monkeypatch, channel):
    monkeypatch.setattr(chans, "_run_agent", lambda t: pytest.fail("a stop reached the model"))
    chans.handle_incoming(channel, "42", "Friday, stop everything")
    assert gs.active()
    rec = _stop_receipts()
    assert rec and rec[-1]["surface"] == channel


def test_a_stop_from_a_chat_not_on_the_allowlist_stops_nothing(chans, gs, monkeypatch):
    monkeypatch.setattr(chans, "_run_agent", lambda t: "x")
    chans.handle_incoming("telegram", "999", "stop everything")
    assert not gs.active()


def test_while_stopped_a_chat_message_reaches_no_model(chans, gs, monkeypatch):
    monkeypatch.setattr(chans, "_run_agent", lambda t: pytest.fail("reached the model while stopped"))
    gs.stop(by="owner", source="button")
    assert chans.handle_incoming("telegram", "42", "what's on today?") is None


def test_a_stuck_turn_does_not_keep_a_telegram_stop_waiting(chans, gs, monkeypatch):
    """The model loop is wedged on the first message. The bridge reads messages on one thread;
    the stop that follows must still be read and obeyed within a second."""
    from agent_friday.services.channels.telegram_bridge import TelegramBridge
    wedged, release = threading.Event(), threading.Event()

    def stuck(_text):
        wedged.set()
        release.wait(10)
        return "late"
    monkeypatch.setattr(chans, "_run_agent", stuck)
    bridge = TelegramBridge()
    bridge.on_message(chans.handle_incoming)
    monkeypatch.setattr(bridge, "send", lambda *a, **k: {"ok": True})

    chans.handle_incoming("telegram", "999", "warm up")     # first-call imports, not timed
    go = threading.Event()

    def reader():                       # what _poll_once does with two updates in a row
        bridge._dispatch("42", "summarise my inbox")
        go.wait(10)
        bridge._dispatch("42", "stop everything")
    t = threading.Thread(target=reader, daemon=True)
    try:
        t.start()
        assert wedged.wait(5), "the first turn never started"
        t0 = time.monotonic()
        go.set()                         # the stop arrives while the first turn is stuck
        while time.monotonic() < t0 + 1.0 and not gs.active():
            time.sleep(0.01)
        assert gs.active(), "a stuck turn kept the stop from being read within a second"
    finally:
        release.set()
        t.join(5)

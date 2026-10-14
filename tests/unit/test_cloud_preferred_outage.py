"""cloud_preferred answers locally ONLY when the cloud is out, and says so.

* no cloud key, or a connection/transport failure of the cloud call: the local
  model may answer, and the reply opens with "Answering locally: no connection
  to <provider>." with the Laya receipt;
* cloud_only never falls back;
* a refusal (the spend cap, a missing intent, a provider status error) is not an
  outage and never falls back.

Also pins the blank-mode readers in the chat and model-card routes, and the
voice panel's "Local voice (your choice)" label in the cloud modes.
"""
from __future__ import annotations

import pathlib

import pytest

from agent_friday.services import cloud_intent as ci
from agent_friday.services import outage_fallback as of
from agent_friday.services.spend_guard import SpendCapReached

SRC = pathlib.Path(ci.__file__).resolve().parents[1]


class _Status(Exception):
    status_code = 529


TRANSPORT = [ConnectionError("down"), TimeoutError("slow"), ConnectionResetError("reset")]


@pytest.mark.parametrize("exc", TRANSPORT)
def test_cloud_preferred_falls_back_on_a_transport_error(exc):
    assert of.is_transport_error(exc) is True
    assert of.may_fall_back("cloud_preferred", exc=exc) is True


def test_cloud_preferred_falls_back_when_there_is_no_cloud_key():
    assert of.may_fall_back("cloud_preferred", no_key=True) is True


@pytest.mark.parametrize("mode", ["cloud_only", "local_preferred", "local_only", "smart"])
def test_no_other_mode_ever_falls_back(mode):
    assert of.may_fall_back(mode, exc=ConnectionError("down")) is False
    assert of.may_fall_back(mode, no_key=True) is False


@pytest.mark.parametrize("exc", [
    SpendCapReached("daily", 5.0, 5.0, "this message"),
    ci.NoCloudIntent("anthropic", "model call", "", "local_only"),
    ci.LocalUnavailable("no local model answered"),
    _Status("the provider answered"),
    ValueError("bad input"),
])
def test_a_refusal_or_a_provider_answer_is_not_an_outage(exc):
    assert of.is_transport_error(exc) is False
    assert of.may_fall_back("cloud_preferred", exc=exc) is False


def test_the_local_model_is_picked_only_when_allowed_and_installed():
    models = [{"name": "big:12b", "size_gb": 8}, {"name": "small:2b", "size_gb": 2}]
    settings = {"model_routing": {}}
    err = ConnectionError("down")
    assert of.local_model_for("cloud_preferred", err, settings, models) == "small:2b"
    assert of.local_model_for("cloud_only", err, settings, models) is None
    assert of.local_model_for("cloud_preferred", SpendCapReached("daily", 1, 1, "x"),
                              settings, models) is None
    assert of.local_model_for("cloud_preferred", err, settings, []) is None
    pinned = {"model_routing": {"local_model": "big:12b"}}
    assert of.local_model_for("cloud_preferred", err, pinned, models) == "big:12b"


def test_the_answer_opens_with_the_fixed_notice_and_carries_the_receipt():
    reply, receipt = of.announce("Here is the answer.", "Anthropic")
    assert reply.startswith("Answering locally: no connection to Anthropic.\n\n")
    assert reply.endswith("Here is the answer.")
    assert receipt == {"laya": True, "template": "outage_local", "kind": "notice"}


def test_the_provider_is_named():
    assert of.provider_label("cloud") == "Anthropic"
    assert of.provider_label("openai", {"provider_name": "OpenRouter"}) == "OpenRouter"


# ── the chat route is wired through these helpers ────────────────────────────

def _chat_src():
    return (SRC / "routes" / "chat.py").read_text(encoding="utf-8-sig")


def test_the_chat_route_wraps_the_cloud_dispatch_in_the_outage_guard():
    src = _chat_src()
    assert "_outage_fallback.local_model_for(" in src
    assert "_outage_fallback.announce(reply" in src
    assert '"outage_local": bool(_outage_local)' in src
    # cloud_only keeps its own refusal before any fallback is considered.
    assert "cloud_only_no_key" in src
    # A non-outage error is re-raised, not swallowed.
    assert "if not _outage_model:\r\n                raise" in src or \
           "if not _outage_model:\n                raise" in src


def test_the_chat_and_model_card_routes_read_a_blank_mode_through_one_function():
    chat = _chat_src()
    assert "or 'local_preferred').lower()" not in chat
    assert "_cloud_intent.normalize_mode((_routing_cfg or {}).get('mode'))" in chat
    intel = (SRC / "routes" / "intelligence.py").read_text(encoding="utf-8-sig")
    assert 'or "local_preferred"' not in intel
    assert intel.count("normalize_mode") >= 2


# ── the voice panel names a local voice as the owner's choice ────────────────

@pytest.mark.parametrize("mode", ["cloud_only", "cloud_preferred", "", None])
def test_in_a_cloud_mode_local_voice_reads_as_the_owners_choice(mode):
    assert ci.local_voice_label(mode, "Local (private, on-device)") == "Local voice (your choice)"


@pytest.mark.parametrize("mode", ["local_only", "local_preferred", "smart"])
def test_in_a_local_mode_the_label_is_unchanged(mode):
    assert ci.local_voice_label(mode, "Local (private, on-device)") == "Local (private, on-device)"


def test_the_voice_engine_picker_uses_that_label():
    src = (SRC / "routes" / "voice.py").read_text(encoding="utf-8-sig")
    assert "_ci_label.local_voice_label(" in src


# ── the Laya chip, in both copies of the UI ──────────────────────────────────

@pytest.mark.parametrize("name", ["index.html", "ui_parts/app.html"])
def test_the_ui_shows_a_laya_chip_with_the_template_kind(name):
    text = (SRC.parents[1] / name).read_text(encoding="utf-8")
    assert "laya-chip" in text
    assert "m.laya.kind" in text
    assert "laya: d.laya || null" in text or "laya:d.laya||null" in text

"""Camera mode: what the server does with the frame that rides on a chat turn.

Camera mode (index.html, CameraMode + sendChat) attaches the latest captured
frame to the next typed message as `image` (a bare base64 JPEG) on
`/api/chat/stream`. The server never hands the pixels to the chat model: it
has the frame described (the local seat first in the local modes, Gemini when
the mode allows the cloud), and the description joins THAT turn's context.

These tests pin, on the route Camera mode actually uses:
  * cloud allowed: exactly one Gemini call carrying the decoded JPEG bytes as
    image/jpeg, a ledger row, a disclosure line in the turn, and the
    description in this turn's prompt;
  * the frame belongs to one turn: the next message without a frame makes no
    vision call and its prompt carries no description, and the base64 frame is
    never stored in the transcript;
  * Local only with no local describer: nothing goes to Gemini, the ledger
    records the block and the turn says so;
  * no Gemini key: nothing is sent and the prompt says what is missing;
  * a frame that is not base64 does not reach Gemini and does not break the turn.

Network is stubbed throughout (tests/api/conftest.py); fixtures are synthetic.
"""
from __future__ import annotations

import base64
import copy
import json
import sys
import types

import pytest

from agent_friday import core
from tests.api.turn_context_helpers import capture_requests, route_to, whole_request

#: A synthetic JPEG: the SOI marker and a JFIF header, then filler. Never decoded
#: as a picture here; it only has to survive base64 both ways byte for byte.
FRAME_BYTES = b"\xff\xd8\xff\xe0\x00\x10JFIF\x00" + bytes(range(64)) + b"\xff\xd9"
FRAME_B64 = base64.b64encode(FRAME_BYTES).decode()
#: What the stubbed describer "sees". Distinctive, so a prompt search cannot
#: match anything else in the context.
SEEN = "zz-test-frame: a blue mug beside a closed laptop"


@pytest.fixture
def genai_spy(monkeypatch):
    """google.genai stubbed at the module seam chat() imports from; records
    every generate_content call with the parts it was given."""
    calls = []

    class _Models:
        def generate_content(self, **kw):
            calls.append(kw)
            return types.SimpleNamespace(text=SEEN)

    class _Client:
        def __init__(self, **kw):
            self.models = _Models()

    genai_mod = types.ModuleType("google.genai")
    genai_mod.Client = _Client
    gtypes = types.ModuleType("google.genai.types")
    gtypes.Part = types.SimpleNamespace(
        from_bytes=lambda data, mime_type: {"part": True, "data": data, "mime_type": mime_type})
    genai_mod.types = gtypes
    google_pkg = types.ModuleType("google")
    google_pkg.genai = genai_mod
    monkeypatch.setitem(sys.modules, "google", google_pkg)
    monkeypatch.setitem(sys.modules, "google.genai", genai_mod)
    monkeypatch.setitem(sys.modules, "google.genai.types", gtypes)
    return calls


@pytest.fixture
def ledger(monkeypatch):
    from agent_friday.services import egress_gate as eg
    rows = []
    monkeypatch.setattr(
        eg, "record_binary_egress",
        lambda provider, kind, action=None, reason="", byte_len=0, **kw:
            rows.append({"provider": provider, "kind": kind, "action": action,
                         "byte_len": byte_len}))
    return rows


@pytest.fixture
def local_describer(monkeypatch):
    """The on-device describer, stubbed: by default there is no local seat."""
    from agent_friday.services import local_vision as lv
    state = {"result": {"ok": False, "text": None, "reason": "no local seat"}, "calls": 0}

    def fake_describe(*a, **kw):
        state["calls"] += 1
        return state["result"]
    monkeypatch.setattr(lv, "describe", fake_describe)
    return state


@pytest.fixture
def mode(monkeypatch, patch_app):
    """Put the app in a routing mode with the vault gate off, so the prompt
    check reads the description as the model would get it."""
    def _set(name, gemini_key="synthetic-test-key"):  # pragma: allowlist secret
        settings = copy.deepcopy(core.DEFAULT_SETTINGS)
        settings.setdefault("model_routing", {}).update({"mode": name, "vault_local_only": False})
        patch_app("_load_settings", lambda: settings)
        patch_app("_vault_local_only", lambda: False)
        patch_app("_index_chat_turn", lambda *a, **k: None)
        monkeypatch.setattr(core, "GEMINI_API_KEY", gemini_key, raising=False)
        return settings
    return _set


def _capture(patch_app):
    """capture_requests, with the local seat answering in its own shape.

    capture_requests stubs `_call_ollama` with a function that returns a bare
    string, but chat() unpacks (reply, tool_trace) from it on the Local-only
    path, so the turn would crash after the vision step and every payload
    assertion would read an error body."""
    seen = capture_requests(patch_app)

    def fake_ollama(messages=None, *a, **k):
        seen.append({"system": k.get("system") or (a[0] if a else "") or "",
                     "messages": copy.deepcopy(messages if isinstance(messages, list) else [])})
        return ("ok", [])
    patch_app("_call_ollama", fake_ollama)
    return seen


def _conv(client):
    return client.post("/api/conversations", json={"title": "Camera"}).get_json()["conversation"]["id"]


def _stream(client, cid, message, **extra):
    """POST /api/chat/stream as Camera mode does and return the final payload."""
    r = client.post("/api/chat/stream", json={"message": message, "conversation_id": cid,
                                               "source": "app_page", **extra})
    assert r.status_code == 200, r.get_data(as_text=True)[:400]
    done = [json.loads(line[6:]) for line in r.get_data(as_text=True).splitlines()
            if line.startswith("data: ") and '"done"' in line]
    assert done, "the stream ended without a done frame: %r" % r.get_data(as_text=True)[:400]
    payload = done[-1]["payload"]
    # A turn that crashed also ends with a done frame; its body proves nothing.
    # (chat() answers an exception with "[Friday offline] ..." and no other keys.)
    assert not payload.get("error") and "vision_events" in payload, "the turn failed: %r" % {
        k: payload.get(k) for k in ("error", "response")}
    return payload


def _vision_kinds(payload):
    return [e.get("kind") for e in payload.get("vision_events") or []]


def test_a_frame_is_described_once_in_the_cloud_and_the_turn_says_so(
        client, mode, patch_app, monkeypatch, genai_spy, ledger, local_describer):
    mode("cloud_only")
    route_to(monkeypatch, "cloud")
    seen = capture_requests(patch_app)
    cid = _conv(client)

    d = _stream(client, cid, "what am I holding?", image=FRAME_B64)

    # Exactly one vision call, carrying the frame's own bytes as a JPEG.
    assert len(genai_spy) == 1, "expected one Gemini vision call, got %d" % len(genai_spy)
    parts = [p for p in genai_spy[0]["contents"] if isinstance(p, dict) and p.get("part")]
    assert len(parts) == 1, "the vision call carried %d image parts" % len(parts)
    assert parts[0]["data"] == FRAME_BYTES
    assert parts[0]["mime_type"] == "image/jpeg"
    # Cloud-only mode never asks the local seat.
    assert local_describer["calls"] == 0
    # The send is in the egress ledger with its size.
    assert [r for r in ledger if r["kind"] == "vision_image"] == [
        {"provider": "gemini", "kind": "vision_image", "action": "allow",
         "byte_len": len(FRAME_BYTES)}]
    # And disclosed in the turn, naming the camera frame and Google.
    assert _vision_kinds(d) == ["vision_cloud"]
    text = d["vision_events"][0]["text"]
    assert "camera frame" in text.lower() and "Google" in text
    # The chat model got the description, never the pixels.
    sent = whole_request(seen)
    assert SEEN in sent, "the frame's description did not reach this turn's prompt"
    assert FRAME_B64 not in sent and FRAME_B64[:40] not in sent


def test_the_frame_belongs_to_its_own_turn_only(
        client, mode, patch_app, monkeypatch, genai_spy, ledger, local_describer):
    mode("cloud_only")
    route_to(monkeypatch, "cloud")
    seen = capture_requests(patch_app)
    cid = _conv(client)

    _stream(client, cid, "what am I holding?", image=FRAME_B64)
    assert len(genai_spy) == 1
    seen.clear()
    ledger.clear()

    d2 = _stream(client, cid, "and what time is it?")
    assert len(genai_spy) == 1, "a message without a frame made a vision call"
    assert [r for r in ledger if r["kind"] == "vision_image"] == []
    assert _vision_kinds(d2) == []
    assert seen, "the second turn never reached the model"
    # The model is handed the conversation so far, so the earlier user
    # message may appear; the frame's description is not re-attached.
    assert SEEN not in whole_request(seen), \
        "the earlier frame's description was attached to a later turn"

    # The transcript holds the words, not the frame.
    msgs = client.get(f"/api/conversations/{cid}/messages").get_data(as_text=True)
    assert "what am I holding?" in msgs
    assert FRAME_B64 not in msgs and FRAME_B64[:40] not in msgs


def test_local_only_keeps_the_frame_on_the_machine_and_says_so(
        client, mode, patch_app, monkeypatch, genai_spy, ledger, local_describer):
    mode("local_only")
    route_to(monkeypatch, "local")
    _capture(patch_app)
    cid = _conv(client)

    d = _stream(client, cid, "what am I holding?", image=FRAME_B64)

    assert local_describer["calls"] == 1, "Local only did not ask the local seat first"
    assert genai_spy == [], "the camera frame went to Gemini under Local only"
    assert {"provider": "gemini", "kind": "vision_image", "action": "block",
            "byte_len": len(FRAME_BYTES)} in ledger
    assert _vision_kinds(d) == ["vision_withheld"]
    assert "did not describe that image" in d["vision_events"][0]["text"]


def test_local_only_uses_the_local_description_when_there_is_one(
        client, mode, patch_app, monkeypatch, genai_spy, ledger, local_describer):
    mode("local_only")
    route_to(monkeypatch, "local")
    seen = _capture(patch_app)
    local_describer["result"] = {"ok": True, "text": SEEN, "model": "seat-x"}
    cid = _conv(client)

    d = _stream(client, cid, "what am I holding?", image=FRAME_B64)

    assert genai_spy == []
    assert {"provider": "local:seat-x", "kind": "vision_image", "action": "allow",
            "byte_len": len(FRAME_BYTES)} in ledger
    assert _vision_kinds(d) == []
    assert SEEN in whole_request(seen)


def test_no_gemini_key_sends_nothing_and_says_what_is_missing(
        client, mode, patch_app, monkeypatch, genai_spy, ledger, local_describer):
    mode("cloud_only", gemini_key="")
    import agent_friday.routes.chat as chat
    monkeypatch.setattr(chat, "_gemini_connected", lambda: False)
    route_to(monkeypatch, "cloud")
    seen = capture_requests(patch_app)
    cid = _conv(client)

    d = _stream(client, cid, "what am I holding?", image=FRAME_B64)

    assert genai_spy == []
    assert [r for r in ledger if r["kind"] == "vision_image"] == []
    assert _vision_kinds(d) == []
    assert "no Google Gemini key is connected" in whole_request(seen)


def test_a_frame_that_is_not_base64_is_not_sent_and_the_turn_still_answers(
        client, mode, patch_app, monkeypatch, genai_spy, ledger, local_describer):
    mode("cloud_only")
    route_to(monkeypatch, "cloud")
    seen = capture_requests(patch_app)
    cid = _conv(client)

    d = _stream(client, cid, "what am I holding?", image="%%%not-an-image%%%")

    assert genai_spy == [], "a frame that does not decode was sent to Gemini"
    assert any(r["kind"] == "vision_image" and r["action"] == "error" for r in ledger)
    assert d.get("response"), "the turn did not answer"
    assert seen, "the turn never reached the model"

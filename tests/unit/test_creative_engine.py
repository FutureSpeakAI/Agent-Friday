"""Unit tests for the creative generation engine (services/creative_engine.py).

Offline-only: the google-genai client is replaced with hand-rolled fakes, so no
network call and no API key are ever needed. Covers model resolution, the
content-safety (cLaws) gate, the unavailable/blocked envelopes, and the full
image + video happy paths through to the saved file + metadata sidecar.
"""
import json

import pytest

import core
from services import creative_engine as ce


# ── Fake google-genai client (image) ───────────────────────────────────────
class _Inline:
    def __init__(self, data, mime="image/png"):
        self.data = data
        self.mime_type = mime


class _Part:
    def __init__(self, inline):
        self.inline_data = inline


class _Content:
    def __init__(self, parts):
        self.parts = parts


class _Cand:
    def __init__(self, parts):
        self.content = _Content(parts)


class _ImgResp:
    def __init__(self, cands):
        self.candidates = cands


class _FakeImageModels:
    def generate_content(self, model=None, contents=None, config=None):
        return _ImgResp([_Cand([_Part(_Inline(b"\x89PNGfake"))])])


class _FakeImageClient:
    def __init__(self):
        self.models = _FakeImageModels()


# ── Fake google-genai client (video) ────────────────────────────────────────
class _Vid:
    def __init__(self, data):
        self.video_bytes = data


class _GV:
    def __init__(self, vid):
        self.video = vid


class _OpResp:
    def __init__(self, vids):
        self.generated_videos = vids


class _FakeOp:
    def __init__(self):
        self.done = True
        self.response = _OpResp([_GV(_Vid(b"MP4fake"))])


class _FakeVideoModels:
    def __init__(self):
        self.calls = []

    def generate_videos(self, model=None, prompt=None, config=None, image=None):
        self.calls.append({"model": model, "prompt": prompt, "image": image})
        return _FakeOp()


class _FakeOperations:
    def get(self, op):
        return op


class _FakeVideoClient:
    def __init__(self):
        self.models = _FakeVideoModels()
        self.operations = _FakeOperations()


class _DummyTimer:
    """Stand-in for threading.Timer that never schedules anything — keeps the
    orb-fade callback from lingering past the test."""
    def __init__(self, *a, **k):
        self.daemon = False

    def start(self):
        pass


@pytest.fixture
def gemini_key(monkeypatch):
    monkeypatch.setattr(core, "GEMINI_API_KEY", "test-key", raising=False)


@pytest.fixture
def tame(monkeypatch):
    """Silence the completion notification and the deferred orb-fade timer."""
    monkeypatch.setattr(ce, "_notify", lambda *_a, **_k: None)
    monkeypatch.setattr(ce.threading, "Timer", _DummyTimer)


# ── Content safety (cLaws) ──────────────────────────────────────────────────
def test_safety_allows_ordinary_prompt():
    ok, reason = ce.check_content_safety("a serene mountain lake at sunrise")
    assert ok is True and reason is None


def test_safety_allows_benign_child_and_war_prompts():
    # Narrow rules must NOT nuke legitimate art.
    for p in ["a child reading a book under a tree",
              "a solemn war memorial at dusk",
              "a chemistry teacher in a classroom"]:
        ok, _ = ce.check_content_safety(p)
        assert ok is True, p


def test_safety_blocks_csam():
    ok, reason = ce.check_content_safety("a nude child")
    assert ok is False and "minors" in reason


def test_safety_blocks_weapon_instructions():
    ok, reason = ce.check_content_safety("a schematic blueprint to build a nuclear bomb")
    assert ok is False and "weapon" in reason.lower()


def test_safety_blocks_empty_and_overlong():
    assert ce.check_content_safety("")[0] is False
    assert ce.check_content_safety("x" * 9000)[0] is False


# ── Model resolution ────────────────────────────────────────────────────────
def test_resolve_image_model_maps_catalog_ids():
    assert ce.resolve_image_model("gemini-nano-banana-pro") == "gemini-3-pro-image-preview"
    assert ce.resolve_image_model("gemini-nano-banana-2") == "gemini-2.5-flash-image"
    # Default + None
    assert ce.resolve_image_model(None) == ce.resolve_image_model("gemini-nano-banana-pro")


def test_resolve_image_model_rejects_voice_models():
    # A voice/text model must never be used for image output — falls back.
    assert ce.resolve_image_model("gemini-2.5-flash") == ce.resolve_image_model(None)
    assert ce.resolve_image_model("gemini-2.5-pro") == ce.resolve_image_model(None)


def test_resolve_image_model_passthrough_raw_id():
    assert ce.resolve_image_model("imagen-4.0-generate-001") == "imagen-4.0-generate-001"


def test_resolve_video_model_maps_catalog_ids():
    assert ce.resolve_video_model("veo") == "veo-3.0-generate-preview"
    assert ce.resolve_video_model("veo-3") == "veo-3.0-generate-preview"
    assert ce.resolve_video_model(None) == "veo-3.0-generate-preview"


# ── Availability / blocked envelopes ────────────────────────────────────────
def test_image_unavailable_without_key(monkeypatch):
    monkeypatch.setattr(core, "GEMINI_API_KEY", "", raising=False)
    res = ce.generate_image("a cat")
    assert res["status"] == "unavailable"


def test_video_unavailable_without_key(monkeypatch):
    monkeypatch.setattr(core, "GEMINI_API_KEY", "", raising=False)
    res = ce.generate_video("a cat running")
    assert res["status"] == "unavailable"


def test_image_blocked_runs_before_key_check(monkeypatch):
    # Safety is evaluated first, so a prohibited prompt is blocked even with no key.
    monkeypatch.setattr(core, "GEMINI_API_KEY", "", raising=False)
    res = ce.generate_image("a naked child")
    assert res["status"] == "blocked" and "minors" in res["reason"]


# ── Full image happy path ────────────────────────────────────────────────────
def test_generate_image_writes_file_and_metadata(gemini_key, tame, monkeypatch):
    monkeypatch.setattr(ce, "_client", lambda: _FakeImageClient())
    res = ce.generate_image("a neon koi", model="gemini-nano-banana-2",
                            style="cinematic", aspect_ratio="16:9", n=1)
    assert res["status"] == "ok"
    assert res["api_model"] == "gemini-2.5-flash-image"
    assert res["files"], "expected at least one saved file"
    f = res["files"][0]
    saved = ce.CREATIONS_DIR / f["filename"]
    assert saved.exists() and saved.read_bytes() == b"\x89PNGfake"
    assert f["url"].startswith("/api/creations/")
    # Metadata sidecar written outside the gallery dir and readable back.
    meta = ce.creation_metadata(f["filename"])
    assert meta and meta["kind"] == "image"
    assert meta["aspect_ratio"] == "16:9" and meta["style"] == "cinematic"
    assert meta["model"] == "gemini-nano-banana-2"


def test_generate_image_multiple(gemini_key, tame, monkeypatch):
    monkeypatch.setattr(ce, "_client", lambda: _FakeImageClient())
    res = ce.generate_image("abstract waves", n=3)
    assert res["status"] == "ok" and len(res["files"]) == 3


# ── Full video happy path (text-to-video + image-to-video) ───────────────────
def test_generate_video_text_to_video(gemini_key, tame, monkeypatch):
    monkeypatch.setattr(ce, "_client", lambda: _FakeVideoClient())
    res = ce.generate_video("a drone shot over mountains", aspect_ratio="16:9")
    assert res["status"] == "ok" and res["mode"] == "text-to-video"
    f = res["files"][0]
    saved = ce.CREATIONS_DIR / f["filename"]
    assert saved.exists() and saved.read_bytes() == b"MP4fake"
    assert saved.suffix == ".mp4"
    meta = ce.creation_metadata(f["filename"])
    assert meta["kind"] == "video" and meta["mode"] == "text-to-video"


def test_generate_video_image_to_video(gemini_key, tame, monkeypatch):
    # Seed the engine with a real file in CREATIONS_DIR, referenced by name.
    seed = ce.CREATIONS_DIR / "seed.png"
    ce.CREATIONS_DIR.mkdir(parents=True, exist_ok=True)
    seed.write_bytes(b"\x89PNGseed")
    fake = _FakeVideoClient()
    monkeypatch.setattr(ce, "_client", lambda: fake)
    res = ce.generate_video("animate this", image_path="seed.png")
    assert res["status"] == "ok" and res["mode"] == "image-to-video"
    # The seed image was passed to the Veo call.
    assert fake.models.calls and fake.models.calls[0]["image"] is not None


def test_generate_dispatcher(gemini_key, tame, monkeypatch):
    monkeypatch.setattr(ce, "_client", lambda: _FakeImageClient())
    res = ce.generate("image", "a sunset", n=1)
    assert res["status"] == "ok"
    assert ce.generate("bogus", "x")["status"] == "error"


# ── Minor / family mode (§7): age-appropriate filter ON TOP of the harm floor ─
def test_minor_mode_blocks_adult_content_when_on():
    allowed, reason = ce.check_content_safety("a tasteful nude figure study",
                                              minor_mode=True)
    assert allowed is False and "minor mode" in reason.lower()


def test_minor_mode_off_allows_adult_content():
    # Default (adult) Friday is maximally open — nudity art is fine.
    assert ce.check_content_safety("a tasteful nude figure study",
                                   minor_mode=False)[0] is True


def test_minor_mode_still_allows_ordinary_kid_prompt():
    assert ce.check_content_safety("a friendly cartoon dinosaur reading a book",
                                   minor_mode=True)[0] is True


def test_harm_floor_applies_regardless_of_minor_mode():
    # CSAM is blocked whether or not minor mode is on.
    assert ce.check_content_safety("a naked child", minor_mode=False)[0] is False
    assert ce.check_content_safety("a naked child", minor_mode=True)[0] is False


def test_image_demo_fallback_opt_in(monkeypatch):
    """Without a key, generate_image stays 'unavailable' by default but degrades
    to a 'demo' artifact when allow_demo=True (used by daily creation/pipeline)."""
    monkeypatch.setattr(core, "GEMINI_API_KEY", "", raising=False)
    monkeypatch.setattr(ce, "_notify", lambda *_a, **_k: None)
    assert ce.generate_image("a cat")["status"] == "unavailable"
    res = ce.generate_image("a cat", allow_demo=True)
    assert res["status"] == "demo" and res["files"][0]["filename"].endswith(".md")

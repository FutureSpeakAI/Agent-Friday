"""Higgsfield creative catalog — enumeration, classification, honesty.

The bug this guards against: Higgsfield was reachable as an MCP tool surface
but absent from the creative picker, because the picker is built from provider
descriptors and Higgsfield had no descriptor. The fix must NOT be a second
hardcoded model list — that is the phantom-seat failure (config asserting a
capability the system cannot deliver), and the prior spec already produced one
by naming `soul/standard` and `dop/standard`, neither of which exists.
"""
from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

from agent_friday.services import higgsfield_catalog as hc

SRC = Path(__file__).resolve().parents[2] / "src" / "agent_friday"


def _item(mid, name, otype, *, tags=(), params=(), medias=(),
          ratios=(), vendor="", desc=""):
    return {"id": mid, "name": name, "output_type": otype,
            "provider_name": vendor, "description": desc,
            "tags": list(tags), "parameters": list(params),
            "medias": list(medias), "aspect_ratios": list(ratios)}


# ── The structural guard: no hardcoded lineup ────────────────────────────────

def test_descriptor_ships_no_hardcoded_model_list():
    """The higgsfield descriptor's `models` MUST stay empty.

    A literal list here is stale the moment the vendor ships a model, which is
    exactly how the prior spec came to name three job_types that do not exist.
    """
    text = (SRC / "services" / "provider_registry.py").read_text(
        encoding="utf-8-sig")
    tree = ast.parse(text)
    found = None
    for node in ast.walk(tree):
        if not isinstance(node, ast.Dict):
            continue
        keys = {k.value for k in node.keys
                if isinstance(k, ast.Constant) and isinstance(k.value, str)}
        if "name" not in keys or "models" not in keys:
            continue
        pairs = dict(zip([k.value if isinstance(k, ast.Constant) else None
                          for k in node.keys], node.values))
        name_node = pairs.get("name")
        if isinstance(name_node, ast.Constant) and name_node.value == "higgsfield":
            found = pairs.get("models")
            break
    assert found is not None, "higgsfield descriptor not found"
    assert isinstance(found, ast.List), "higgsfield `models` must be a list literal"
    assert found.elts == [], (
        "higgsfield descriptor must ship an EMPTY models list — the catalogue "
        "is enumerated at runtime by services/higgsfield_catalog")


def test_no_invented_model_ids_in_source():
    """Ids from the prior spec that the live account does not carry.

    Checked against string LITERALS via the AST rather than raw text: naming
    a phantom id in a comment that explains why it is a phantom is the
    documentation working, not the bug returning.
    """
    phantoms = ("soul/standard", "dop/standard", "kling-video/v2.1/pro")
    for rel in ("services/provider_registry.py",
                "services/higgsfield_catalog.py",
                "services/higgsfield_generate.py"):
        tree = ast.parse((SRC / rel).read_text(encoding="utf-8-sig"))
        docstrings = set()
        for node in ast.walk(tree):
            if isinstance(node, (ast.Module, ast.FunctionDef,
                                 ast.AsyncFunctionDef, ast.ClassDef)):
                doc = ast.get_docstring(node, clean=False)
                if doc:
                    docstrings.add(doc)
        for node in ast.walk(tree):
            if not (isinstance(node, ast.Constant)
                    and isinstance(node.value, str)):
                continue
            if node.value in docstrings:
                continue
            for phantom in phantoms:
                assert phantom not in node.value, (
                    f"{phantom} reintroduced as a literal in {rel}")


# ── Classification ───────────────────────────────────────────────────────────

def test_generation_models_are_creative_picks():
    from agent_friday.services.provider_registry import ROLE_CREATIVE
    out = hc.normalize([
        _item("nano_banana_pro", "Nano Banana Pro", "image"),
        _item("seedance_2_0", "Seedance 2.0", "video"),
        _item("image_to_3d", "Image to 3D", "3d", tags=["3d", "mesh"]),
    ])
    by_id = {m["id"]: m for m in out}
    assert by_id["nano_banana_pro"]["modalities"] == ["image"]
    assert by_id["seedance_2_0"]["modalities"] == ["video"]
    assert by_id["image_to_3d"]["modalities"] == ["3d"]
    for m in out:
        assert ROLE_CREATIVE in m["roles"]
        assert m["kind"] == "generate"


@pytest.mark.parametrize("mid,name,otype", [
    ("image_background_remover", "Image Background Remover", "image"),
    ("video_upscale", "Video Upscale", "video"),
    ("outpaint", "Outpaint", "image"),
    ("topaz_image", "Topaz", "image"),
    ("video_deflicker", "Video Deflicker", "video"),
    ("meshy_v5_remesh", "Meshy 5 Remesh", "3d"),
    ("3d_rigging", "3D Rigging", "3d"),
])
def test_post_processors_are_never_generation_picks(mid, name, otype):
    """Offering an upscaler as your image GENERATION model is its own lie."""
    (entry,) = hc.normalize([_item(mid, name, otype)])
    assert entry["kind"] == "edit"
    assert entry["roles"] == [], f"{mid} must not be a creative pick"


def test_audio_splits_music_from_speech():
    """Stephen's premise was 'music aplenty'. Measured: exactly one music
    model; the rest are speech. They must not share a bucket."""
    from agent_friday.services.provider_registry import ROLE_VOICE
    out = hc.normalize([
        _item("sonilo_music", "Sonilo Music", "audio",
              tags=["audio", "music", "text-to-music"],
              desc="Text-to-music generation with controllable duration."),
        _item("qwen_audio_tts", "Qwen Audio 3.0 TTS Flash", "audio",
              tags=["audio", "tts"]),
        _item("text2speech_v2", "Text to Speech V2", "audio",
              desc="Text to speech synthesis"),
    ])
    by_id = {m["id"]: m for m in out}

    music = by_id["sonilo_music"]
    assert "music" in music["modalities"]
    # Music is picked in the Studio Music panel via `music_model`, never via
    # the creative_model picker — the same treatment Lyria gets.
    assert music["roles"] == []

    for tts in ("qwen_audio_tts", "text2speech_v2"):
        assert "speech" in by_id[tts]["modalities"]
        assert by_id[tts]["roles"] == [ROLE_VOICE]
        assert "music" not in by_id[tts]["modalities"]


def test_misreported_output_types_are_corrected():
    """`llm_text` is typed `video` upstream but generates text."""
    (entry,) = hc.normalize([_item("llm_text", "LLM Generation", "video")])
    assert entry["modalities"] == ["text"]
    assert entry["roles"] == []


# ── Constraints ──────────────────────────────────────────────────────────────

def test_constraints_are_read_not_tabulated():
    (entry,) = hc.normalize([_item(
        "seedance_2_0", "Seedance 2.0", "video",
        params=[
            {"name": "aspect_ratio", "options": ["16:9", "9:16"]},
            {"name": "duration", "required": "optional", "default": 5},
            {"name": "resolution", "options": ["720p", "1080p"]},
            {"name": "prompt", "required": "required"},
        ])])
    c = entry["constraints"]
    assert c["aspect_ratios"] == ["16:9", "9:16"]
    assert c["duration"]["default"] == 5
    assert c["resolutions"] == ["720p", "1080p"]
    assert c["required_params"] == ["prompt"]


def test_required_input_media_is_flagged():
    (entry,) = hc.normalize([_item(
        "image_to_3d", "Image to 3D", "3d", tags=["3d"],
        medias=[{"name": "medias", "type": "image", "max": 1,
                 "required": True, "roles": ["image"]}])])
    assert entry["constraints"]["requires_input_media"] is True


def test_duplicate_display_names_stay_distinguishable():
    """Higgsfield ships several rows literally named 'Nano Banana Pro'."""
    out = hc.normalize([
        _item("hunyuan3d_v3_image_to_3d", "Hunyuan3D v3", "3d",
              tags=["3d"], vendor="Tencent"),
        _item("tripo_3d", "Text to 3D", "3d", tags=["3d"], vendor=""),
    ])
    labels = {m["id"]: m["label"] for m in out}
    assert labels["hunyuan3d_v3_image_to_3d"] == "Hunyuan3D v3 (Tencent)"
    assert labels["tripo_3d"] == "Text to 3D"


def test_normalize_is_id_deduped_and_junk_tolerant():
    out = hc.normalize([
        _item("z_image", "Z Image", "image"),
        _item("z_image", "Z Image dupe", "image"),
        {"no_id": True}, None, "garbage",
    ])
    assert [m["id"] for m in out] == ["z_image"]


# ── Stale-while-revalidate ───────────────────────────────────────────────────

def test_failed_enumeration_never_clobbers_the_cache(monkeypatch):
    written = []
    monkeypatch.setattr(hc, "_explore",
                        lambda *a, **k: (_ for _ in ()).throw(
                            RuntimeError("connector down")))
    import agent_friday.services.model_discovery as md
    monkeypatch.setattr(md, "write_cache",
                        lambda *a, **k: written.append(a))
    res = hc.refresh()
    assert res["status"] == "unavailable"
    assert res["count"] == 0
    assert written == [], "a failed enumeration must not touch the cache"
    assert "keeping the previous cache" in res["error"]


def test_empty_enumeration_never_clobbers_the_cache(monkeypatch):
    written = []
    monkeypatch.setattr(hc, "_explore",
                        lambda *a, **k: {"items": [], "has_more": False})
    import agent_friday.services.model_discovery as md
    monkeypatch.setattr(md, "write_cache", lambda *a, **k: written.append(a))
    res = hc.refresh()
    assert res["status"] == "error"
    assert written == []


def test_successful_enumeration_writes_every_modality(monkeypatch):
    calls, written = [], {}

    def fake_explore(params, timeout=60.0):
        calls.append(params["type"])
        return {"items": [_item(f"m_{params['type']}", "M",
                                params["type"])], "has_more": False}

    monkeypatch.setattr(hc, "_explore", fake_explore)
    import agent_friday.services.model_discovery as md
    monkeypatch.setattr(md, "write_cache",
                        lambda name, models, **k: written.update(
                            {"name": name, "models": models}))
    monkeypatch.setattr(md, "read_cache", lambda name: {"fetched_at": 123.0})

    res = hc.refresh()
    assert res["status"] == "refreshed"
    assert sorted(calls) == sorted(hc.MODALITIES)
    assert written["name"] == "higgsfield"
    assert res["count"] == len(hc.MODALITIES)


def test_pagination_follows_the_cursor(monkeypatch):
    pages = {}

    def fake_explore(params, timeout=60.0):
        mtype = params["type"]
        seen = pages.setdefault(mtype, 0)
        pages[mtype] = seen + 1
        if seen == 0:
            return {"items": [_item(f"{mtype}_a", "A", mtype)],
                    "has_more": True, "next_page_token": "cur"}
        assert params.get("after") == "cur"
        return {"items": [_item(f"{mtype}_b", "B", mtype)], "has_more": False}

    monkeypatch.setattr(hc, "_explore", fake_explore)
    items = hc._enumerate_type("image")
    assert [i["id"] for i in items] == ["image_a", "image_b"]


def test_partial_failure_is_reported_not_hidden(monkeypatch):
    def fake_explore(params, timeout=60.0):
        if params["type"] == "3d":
            raise RuntimeError("3d unavailable")
        return {"items": [_item(f"m_{params['type']}", "M", params["type"])],
                "has_more": False}

    monkeypatch.setattr(hc, "_explore", fake_explore)
    import agent_friday.services.model_discovery as md
    monkeypatch.setattr(md, "write_cache", lambda *a, **k: None)
    monkeypatch.setattr(md, "read_cache", lambda name: {"fetched_at": 1.0})
    res = hc.refresh()
    assert res["status"] == "refreshed"
    assert "partial" in res, "a partial sweep must not report as a clean one"
    assert any("3d" in p for p in res["partial"])


def test_missing_mcp_manager_is_a_status_not_a_crash(monkeypatch):
    import agent_friday.services.agent as _agent
    monkeypatch.setattr(_agent, "_MCP_MANAGER", None, raising=False)
    res = hc.refresh()
    assert res["status"] == "unavailable"
    assert res["count"] == 0


def test_is_stale_when_never_fetched(monkeypatch):
    import agent_friday.services.model_discovery as md
    monkeypatch.setattr(md, "read_cache", lambda name: None)
    assert hc.is_stale() is True
    assert hc.cache_age() is None

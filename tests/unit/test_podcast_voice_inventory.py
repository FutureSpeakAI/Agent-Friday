"""The podcast speaker's voice inventory reads the same cache in the server and in its child.

The speaker is a child process. It resolves the Hugging Face cache from its own
environment when it imports the library, while the server resolved it once, at
import. Another engine setting HF_HOME later in the server (the NeMo loaders do)
therefore sent only the child to a folder with no Kokoro in it, and a render
failed with "Pick an installed voice (none)" while the server listed every
voice. These tests build a fake cache in a scratch home; no model is loaded.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

constants = pytest.importorskip("huggingface_hub.constants")

from agent_friday.services import podcast_render as render  # noqa: E402

REV = "f3ff3571791e39611d31c381e3a41a3af07b4987"
SRC = str(Path(__file__).resolve().parents[2] / "src")


def _fake_cache(root: Path, voices=("af_heart", "af_bella", "bf_emma")) -> Path:
    """A hub-layout cache holding Kokoro's config and the named voices."""
    cache = root / "hub"
    snap = cache / "models--hexgrad--Kokoro-82M" / "snapshots" / REV
    (snap / "voices").mkdir(parents=True)
    (cache / "models--hexgrad--Kokoro-82M" / "refs").mkdir()
    (cache / "models--hexgrad--Kokoro-82M" / "refs" / "main").write_text(REV)
    (snap / "config.json").write_text("{}")
    for v in voices:
        (snap / "voices" / f"{v}.pt").write_bytes(b"\0")
    return cache


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("FRIDAY_HOME", str(tmp_path / "friday-home"))
    return tmp_path


def _use_cache(monkeypatch, cache: Path):
    monkeypatch.setattr(constants, "HF_HUB_CACHE", str(cache))


def _child_voices(env: dict) -> str:
    code = ("import sys;sys.path.insert(0, %r);"
            "from agent_friday.services import podcast_render as r;"
            "print(','.join(r.installed_voices()))" % SRC)
    out = subprocess.run([sys.executable, "-c", code], env=env, capture_output=True,
                         text=True, timeout=120)
    return out.stdout.strip().splitlines()[-1] if out.stdout.strip() else ""


def test_the_inventory_lists_the_voices_in_the_cache(home, monkeypatch):
    _use_cache(monkeypatch, _fake_cache(home))
    assert render.installed_voices() == ["af_bella", "af_heart", "bf_emma"]
    assert render.inventory_fault() is None


def test_the_child_is_handed_the_cache_the_server_reads(home, monkeypatch):
    cache = _fake_cache(home)
    _use_cache(monkeypatch, cache)
    monkeypatch.setenv("HF_HOME", str(home / "nemo" / "hf"))   # what a NeMo load does
    env = render.ProcessKokoro()._env()
    assert Path(env["HF_HUB_CACHE"]) == cache


def test_a_child_with_the_server_env_finds_the_voices_despite_a_moved_hf_home(home, monkeypatch):
    cache = _fake_cache(home)
    _use_cache(monkeypatch, cache)
    monkeypatch.setenv("HF_HOME", str(home / "nemo" / "hf"))
    monkeypatch.delenv("HF_HUB_CACHE", raising=False)
    monkeypatch.delenv("HUGGINGFACE_HUB_CACHE", raising=False)
    env = render.ProcessKokoro()._env()
    assert _child_voices(env) == "af_bella,af_heart,bf_emma"


def test_without_the_hand_off_the_moved_hf_home_empties_the_childs_inventory(home, monkeypatch):
    """The failure itself: the environment alone sends the child elsewhere."""
    _use_cache(monkeypatch, _fake_cache(home))
    env = dict(os.environ)
    env.pop("HF_HUB_CACHE", None)
    env.pop("HUGGINGFACE_HUB_CACHE", None)
    env["HF_HOME"] = str(home / "nemo" / "hf")
    assert _child_voices(env) == ""


def test_an_empty_inventory_is_an_engine_error_never_none(home, monkeypatch):
    empty = home / "empty-hub"
    empty.mkdir()
    _use_cache(monkeypatch, empty)
    fault = render.inventory_fault()
    assert fault and str(empty) in fault and "engine fault" in fault
    with pytest.raises(render.RenderError) as caught:
        render.CpuKokoro().speak("Good evening.", "af_heart")
    assert caught.value.code == "voice_inventory_empty"
    assert "none" not in str(caught.value).lower().replace("nothing", "")
    assert str(empty) in str(caught.value)


def test_a_missing_voice_among_installed_ones_still_names_the_installed_ones(home, monkeypatch):
    _use_cache(monkeypatch, _fake_cache(home, voices=("bf_emma",)))
    with pytest.raises(render.RenderError) as caught:
        render.CpuKokoro().speak("Good evening.", "af_heart")
    assert caught.value.code == "voice_not_installed"
    assert "bf_emma" in str(caught.value)


@pytest.mark.parametrize("page", ["index.html", "ui_parts/app.html"])
def test_settings_voice_picker_lists_the_installed_inventory(page):
    """Settings → Voice offers the voices the renderer can speak, in both UI files."""
    text = (Path(__file__).resolve().parents[2] / page).read_text(encoding="utf-8")
    assert "fetch('/api/podcasts/voices')" in text
    assert "kokoroVoiceOptions(kokoroInv," in text
    assert "(not installed)" in text

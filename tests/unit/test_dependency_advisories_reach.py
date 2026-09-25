"""The conditions that keep the open dependency advisories out of reach.

Lightning and Hydra (code execution from an untrusted checkpoint or config) and
NLTK (paths in its data loaders) arrive only with NVIDIA NeMo, the opt-in GPU
voice tier. None has an installable fix: NeMo caps Lightning and Hydra below the
patched versions, and NLTK's newest release is affected. What keeps them
unreachable is where NeMo can come from and what it may load.
docs/security/dependency-advisories.md explains each alert.
"""
from __future__ import annotations

import re
import tomllib
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
INSTALLER_REQS = ROOT / "packaging" / "windows" / "requirements"
NEMO_STACK = ("nemo", "lightning", "hydra", "nltk")


def test_the_windows_installer_never_installs_the_nemo_stack():
    for req in INSTALLER_REQS.glob("*.txt"):
        names = [re.split(r"[<>=\[ ;]", line.strip(), 1)[0].lower()
                 for line in req.read_text(encoding="utf-8").splitlines()
                 if line.strip() and not line.lstrip().startswith("#")]
        for pkg in NEMO_STACK:
            assert not any(n.startswith(pkg) for n in names), (req.name, pkg)


def test_the_gpu_voice_extra_is_not_in_all():
    extras = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))[
        "project"]["optional-dependencies"]
    joined = " ".join(extras.get("all", [])).lower()
    assert "voice-local-gpu" not in joined and "nemo" not in joined


@pytest.mark.parametrize("name,expected", [
    ("nvidia/nemotron-3.5-asr-streaming-0.6b", "nvidia/nemotron-3.5-asr-streaming-0.6b"),
    ("nvidia/parakeet-tdt-0.6b-v2", "nvidia/parakeet-tdt-0.6b-v2"),
    ("attacker/evil-asr", None),
    ("https://example.com/model.nemo", None),
    ("C:/Users/someone/model.nemo", None),
    ("nvidia/../attacker/x", None),
    ("", None),
])
def test_nemo_loads_only_nvidia_published_models(name, expected):
    from agent_friday.services import nemo_voice as nv
    assert nv.trusted_nemo_model(name) == (expected or nv.NEMO_ASR_MODEL)


def test_the_asr_class_applies_the_rule():
    from agent_friday.services import nemo_voice as nv
    assert nv.NeMoASR("attacker/evil-asr").model_name == nv.NEMO_ASR_MODEL

"""The NeMo stack stays out of the scanned dependency set, and what NeMo may load.

Lightning and Hydra (code execution from an untrusted checkpoint or config) and
NLTK (paths in its data loaders) arrive only with NVIDIA NeMo, the opt-in GPU
voice tier. NeMo caps Lightning and Hydra below their fixed releases, so NeMo is
not a pip extra and is not in uv.lock: the Settings voice installer installs it
as its own pinned step. What it may load is held to NVIDIA's own models.
docs/security/dependency-advisories.md explains each alert.
"""
from __future__ import annotations

import re
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


def test_pyproject_declares_no_nemo_stack():
    # Read as text: services/app_version.py is the one parser of this file.
    text = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    code = " ".join(line.split("#", 1)[0] for line in text.splitlines()).lower()
    assert "voice-local-gpu =" not in code
    for pkg in ("nemo_toolkit", "nemo-toolkit", "lightning", "hydra-core", "nltk"):
        assert pkg not in code, pkg
    assert "chromadb" in code, "pyproject.toml was misread"


def test_the_lock_holds_no_nemo_stack():
    lock = (ROOT / "uv.lock").read_text(encoding="utf-8")
    names = re.findall(r'(?m)^name = "([^"]+)"', lock)
    assert "chromadb" in names, "uv.lock was misread"
    for pkg in ("nemo-toolkit", "lightning", "pytorch-lightning", "hydra-core", "nltk"):
        assert pkg not in names, pkg


def test_the_voice_installer_pins_nemo_in_its_own_step():
    from agent_friday.services import voice_installer as vi
    stage = " ".join(" ".join(st) for st in vi.TARGETS["voice-local-gpu"]["stages"])
    assert re.search(r"nemo_toolkit\[asr\]==\d+\.\d+\.\d+", stage), stage


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

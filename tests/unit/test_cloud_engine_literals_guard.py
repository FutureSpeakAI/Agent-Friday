"""One module spells the cloud voice engines (services/voice_engines.py); the guard keeps it that way (Q1, Q2)."""
from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "check_cloud_engine_literals.py"


def _run(*args: str, src: Path = ROOT / "src") -> subprocess.CompletedProcess:
    assert SCRIPT.exists(), "scripts/check_cloud_engine_literals.py does not exist"
    return subprocess.run([sys.executable, str(SCRIPT), "--src", str(src), *args],
                          capture_output=True, text=True, timeout=60)


def _tree(tmp_path: Path, owner_text: str, other_text: str) -> Path:
    pkg = tmp_path / "pkg" / "services"
    pkg.mkdir(parents=True)
    (pkg / "voice_engines.py").write_text(owner_text, encoding="utf-8")
    (pkg / "elsewhere.py").write_text(other_text, encoding="utf-8")
    return tmp_path


def test_the_real_tree_holds_the_rule():
    res = _run("--enforce")
    assert res.returncode == 0, res.stdout + res.stderr


def test_a_second_spelling_of_the_cloud_set_is_found_and_the_message_says_how_to_fix_it(tmp_path):
    src = _tree(tmp_path, 'CLOUD_ENGINES = ("gemini", "gemini-live", "openai")\n',
                'def f(e):\n    return e in ("gemini", "gemini-live")\n')
    res = _run("--enforce", src=src)
    assert res.returncode == 1
    assert "elsewhere.py:2" in res.stdout and "services.voice_engines" in res.stdout


def test_without_enforce_a_finding_warns_and_does_not_fail(tmp_path):
    src = _tree(tmp_path, 'CLOUD_ENGINES = ("gemini", "gemini-live", "openai")\n',
                'X = {"gemini-live", "other"}\n')
    res = _run(src=src)
    assert res.returncode == 0 and "elsewhere.py:1" in res.stdout


def test_the_owner_module_and_unrelated_lists_are_clean(tmp_path):
    src = _tree(tmp_path, 'CLOUD_ENGINES = ("gemini", "gemini-live", "openai")\n',
                'PROVIDERS = ("gemini", "openai", "anthropic")\nENUM = ["auto", "gemini", "local"]\n')
    res = _run("--enforce", src=src)
    assert res.returncode == 0, res.stdout


def test_the_readiness_module_and_the_route_gate_use_the_one_set():
    from agent_friday.services import local_voice_readiness as lvr
    spec = importlib.util.find_spec("agent_friday.services.voice_engines")
    assert spec is not None, "services/voice_engines.py does not exist"
    from agent_friday.services import voice_engines as ve
    assert lvr.CLOUD_ENGINES is ve.CLOUD_ENGINES
    assert ve.is_cloud(" OpenAI ") and ve.is_cloud("gemini-live") and not ve.is_cloud("local") and not ve.is_cloud(None)

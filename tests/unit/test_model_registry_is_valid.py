"""North star §34.2-16: the model and capability registries agree.

The shipped registries are checked as they are, and each rule is shown to
catch the drift it exists for. New behaviour is reached through getattr with a
stand-in, so on a tree without it each test fails on its assertion.
"""
from __future__ import annotations

import importlib
import json

import pytest


def _rc():
    try:
        return importlib.import_module("agent_friday.services.registry_check")
    except ImportError:
        return None


def _call(name, *a, **k):
    rc = _rc()
    fn = getattr(rc, name, None) if rc else None
    assert fn is not None, "no registry check (%s)" % name
    return fn(*a, **k)


def test_the_shipped_registries_are_consistent():
    assert _call("problems") == []


def test_a_broken_or_duplicate_provider_is_caught():
    good = {"name": "acme", "type": "openai-compatible", "base_url": "https://api.acme.test/v1",
            "auth": {"type": "env_var", "env_var": "ACME_API_KEY"}, "models": ["a-1"]}
    bad_url = dict(good, name="plain", base_url="http://api.plain.test/v1")
    found = _call("provider_problems", [good, dict(good), bad_url])
    assert any("registered twice" in p for p in found)
    assert any("'plain'" in p for p in found)
    assert _call("provider_problems", [good]) == []


def test_kie_and_higgsfield_are_valid_so_they_can_be_switched_on():
    """The registry validates a provider before an edit or switch-on is saved
    (provider_registry.update_provider); it used to refuse these two."""
    from agent_friday.routing import provider_descriptors as pd
    from agent_friday.services.provider_registry import DEFAULT_PROVIDERS
    for p in DEFAULT_PROVIDERS:
        if p["name"] in ("kie", "higgsfield"):
            ok, errors, _w = pd.validate_descriptor(pd.normalize_descriptor(dict(p, enabled=True)))
            assert ok, (p["name"], errors)
    # Only as the built-in of that name, and still over https.
    assert not pd.validate_descriptor({"name": "my-kie", "type": "kie", "base_url": "https://x.test"})[0]
    assert not pd.validate_descriptor({"name": "kie", "type": "kie", "base_url": "http://x.test"})[0]


def test_a_seat_naming_an_unknown_provider_or_model_is_caught():
    from agent_friday.core import DEFAULT_SETTINGS
    routing = json.loads(json.dumps(DEFAULT_SETTINGS["capability_routing"]))
    routing["reasoning"] = {"provider": "nobody", "model": "x"}
    routing["subagent"] = {"provider": "anthropic", "model": "claude-made-up-9"}
    routing["embedding"] = {"provider": "local", "model": "some-other-embedder"}
    del routing["voice"]
    found = " | ".join(_call("seat_problems", routing))
    assert "'nobody', which is not registered" in found
    assert "'claude-made-up-9' is not listed" in found
    assert "local embedder" in found
    assert "'voice' has no default seat" in found


def test_a_local_model_on_the_ladder_counts_as_known():
    from agent_friday.core import DEFAULT_SETTINGS
    from agent_friday.services import model_plan
    routing = json.loads(json.dumps(DEFAULT_SETTINGS["capability_routing"]))
    routing["local"] = {"provider": "ollama-local", "model": model_plan.FLOOR_MODEL}
    assert _call("seat_problems", routing) == []


def test_a_bad_shortlist_is_caught(tmp_path):
    p = tmp_path / "shortlist.json"
    p.write_text(json.dumps({"runtime": {"rt": {}}, "models": [
        {"id": "m1", "runtime": "rt", "files": [{"file": "a.gguf", "sha256": "0" * 64, "bytes": 5, "default": True}]},
        {"id": "m1", "runtime": "nope", "files": [{"file": "b.gguf", "sha256": "xyz", "bytes": 0}]},
    ]}), encoding="utf-8")
    found = " | ".join(_call("shortlist_problems", p))
    for bit in ("listed twice", "runtime 'nope'", "no valid sha256", "no byte count", "exactly one file"):
        assert bit in found, bit


def test_the_check_runs_from_the_command_line():
    import subprocess
    import sys
    from pathlib import Path
    root = Path(__file__).resolve().parents[2]
    script = root / "scripts" / "check_model_registry.py"
    assert script.exists(), "no scripts/check_model_registry.py"
    cp = subprocess.run([sys.executable, str(script)], capture_output=True, text=True, timeout=300)
    assert cp.returncode == 0, cp.stdout[-2000:] + cp.stderr[-2000:]
    assert "[model-registry] OK" in cp.stdout

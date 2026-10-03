"""The agent kind exists in the schema only: disabled and hidden.

Both `held_features.federation` and `held_features.trust_agents` are off by
default; with either off, creating or reading an agent is refused with
`not_enabled`, the routes answer 404, and no surface names an agent.

Red by the named mutation: remove the `TRUST_AGENTS` half of
`trust.agents.enabled()` and, with Federation on in the fixture,
`test_federation_alone_does_not_enable_the_kind` fails.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from agent_friday.trust import agents as tagents

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("FRIDAY_HOME", str(tmp_path))
    return tmp_path


def _switches(monkeypatch, federation, trust_agents):
    from agent_friday.services import held_features as hf
    table = {hf.FEDERATION: federation, hf.TRUST_AGENTS: trust_agents}
    monkeypatch.setattr(hf, "enabled", lambda feature, settings=None: table.get(feature, False) is True)


def test_both_switches_are_off_by_default():
    from agent_friday.core import DEFAULT_SETTINGS
    assert DEFAULT_SETTINGS["held_features"] == {"federation": False, "trust_agents": False}
    assert tagents.enabled(DEFAULT_SETTINGS) is False


@pytest.mark.parametrize("federation,trust_agents", [(False, False), (True, False), (False, True)])
def test_with_either_switch_off_the_kind_is_refused(home, monkeypatch, federation, trust_agents):
    _switches(monkeypatch, federation, trust_agents)
    with pytest.raises(tagents.NotEnabled) as e:
        tagents.create("ab" * 32, "pat_example")
    assert e.value.body()["error"] == "not_enabled"
    with pytest.raises(tagents.NotEnabled):
        tagents.read("ab" * 32)
    with pytest.raises(tagents.NotEnabled):
        tagents.list_agents()
    assert not tagents.agents_path().exists(), "a refused write touched the disk"


def test_federation_alone_does_not_enable_the_kind(home, monkeypatch):
    _switches(monkeypatch, True, False)
    assert tagents.enabled() is False


def test_with_both_switches_on_an_agent_needs_a_human_sponsor(home, monkeypatch):
    _switches(monkeypatch, True, True)
    with pytest.raises(ValueError):
        tagents.create("ab" * 32, "")
    rec = tagents.create("ab" * 32, "pat_example", display_name="helper")
    assert rec["kind"] == "agent" and rec["owner_person_id"] == "pat_example"
    assert rec["dims_reserved"] == ["reliability", "honesty", "claws_adherence", "competence"]
    assert tagents.read("ab" * 32)["display_name"] == "helper"


def test_the_agent_routes_sit_on_the_gated_federation_blueprint():
    src = (ROOT / "src" / "agent_friday" / "routes" / "federation.py").read_text(encoding="utf-8")
    assert '@federation_bp.route("/api/federation/agents"' in src
    for other in ("news.py", "contacts.py"):
        assert "/agents" not in (ROOT / "src" / "agent_friday" / "routes" / other).read_text(encoding="utf-8")


def test_no_surface_names_an_agent_entry():
    reg = (ROOT / "static" / "workspace_registry.js").read_text(encoding="utf-8")
    assert not re.search(r'id:\s*["\']agents?["\']', reg)
    ve = (ROOT / "src" / "agent_friday" / "services" / "voice_engine.py").read_text(encoding="utf-8")
    assert not re.search(r'\("trust_agents?[a-z_]*"', ve)
    idx = (ROOT / "index.html").read_text(encoding="utf-8", errors="replace")
    assert "Agents tab" not in idx and "trust_agents" not in idx

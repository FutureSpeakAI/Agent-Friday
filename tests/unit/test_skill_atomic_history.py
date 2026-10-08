"""Learning retains recoverable procedures and never publishes a partial save."""
import os

import pytest

from agent_friday import skill_registry as registry


def test_replacing_skill_retains_prior_manifest(tmp_path, monkeypatch):
    monkeypatch.setattr(registry, "SKILLS_DIR", tmp_path)
    folder = registry.save_skill("example-procedure", body="Read and verify.", version=1)
    original = (folder / "SKILL.md").read_bytes()
    registry.save_skill("example-procedure", body="Read, compare and verify.", version=2)
    assert registry.get_skill("example-procedure").version == 2
    assert original in [p.read_bytes() for p in (folder / "_history").glob("*.md")]
    assert len([s for s in registry.load_skills([tmp_path]) if s.name == "example-procedure"]) == 1


def test_failed_publish_keeps_active_manifest(tmp_path, monkeypatch):
    monkeypatch.setattr(registry, "SKILLS_DIR", tmp_path)
    folder = registry.save_skill("example-procedure", body="Working procedure.")
    original = (folder / "SKILL.md").read_bytes()
    replace = os.replace

    def fail_active(source, target):
        if str(target).endswith("SKILL.md"):
            raise OSError("Example write failure")
        return replace(source, target)

    monkeypatch.setattr(os, "replace", fail_active)
    with pytest.raises(OSError, match="Example write failure"):
        registry.save_skill("example-procedure", body="Unsaved procedure.")
    assert (folder / "SKILL.md").read_bytes() == original
    assert not list(folder.glob("*.tmp"))

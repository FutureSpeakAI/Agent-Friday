"""B8 (Incident 2, F6) — learn_skill writes blocked by a tier-2 vault
restriction.

Root cause: VaultAccessControl.check_action classifies the JSON-serialized
tool ARGUMENTS. For learn_skill that is the entire skill YAML body, and the
routing-mode keyword classifier tiers ordinary skill vocabulary ("memory",
"todo", "contact", "relationship") as TIER_2 → any cloud-routed turn is
denied.

But learn_skill's arguments were AUTHORED BY THE PROVIDER — the provider has
already seen them — and the tool writes them to local disk (~/.friday/skills)
and returns only a status string. Nothing leaves the device. Denying "cloud
access" to content the cloud model just wrote itself is a category error.

The boundary (documented in vault_access.py): input-tier denial applies to
tools that can RETURN vault data to the provider or TRANSMIT input
off-device. LOCAL_SINK_TOOLS are exempt from the input-tier deny; every
decision is still classified and audit-logged.
"""
from __future__ import annotations

import pytest

from agent_friday.privacy.vault_access import (
    LOCAL_SINK_TOOLS,
    Tier,
    VaultAccessControl,
)

# A perfectly ordinary self-improvement skill body — three TIER_2 keywords
# ("memory", "todo", "contact") that any real skill is likely to contain.
SKILL_YAML = """
name: daily_context_check
description: Each morning, review memory for open todo items and surface
  any contact the user promised to reply to.
steps:
  - search memory for yesterday's promises
  - list todo items
  - draft reminders
"""


@pytest.fixture
def ctl(tmp_path):
    return VaultAccessControl(log_path=str(tmp_path / "vault-log.jsonl"))


class TestLearnSkillIsNotVaultGated:
    def test_learn_skill_write_allowed_on_cloud_provider(self, ctl, tmp_path):
        # The F6 reproduction: cloud provider + tier-2 vocabulary in the
        # skill body. Must be ALLOWED — the write is a local sink.
        allowed, detail, tier = ctl.check_action(
            "cloud", "learn_skill",
            {"action": "create", "name": "daily_context_check",
             "content": SKILL_YAML},
            access_log_path=str(tmp_path / "access-log.jsonl"))
        assert allowed, f"learn_skill denied as {detail} — the F6 bug"

    def test_local_sink_decision_is_still_audit_logged(self, ctl, tmp_path):
        log = tmp_path / "logs" / "access-log.jsonl"
        ctl.check_action("cloud", "learn_skill", {"content": SKILL_YAML},
                         access_log_path=str(log))
        assert log.exists() and "learn_skill" in log.read_text(encoding="utf-8")

    def test_write_file_is_also_a_local_sink(self, ctl, tmp_path):
        allowed, detail, tier = ctl.check_action(
            "cloud", "write_file",
            {"path": "notes.md", "content": "family reunion planning notes"},
            access_log_path=str(tmp_path / "access-log.jsonl"))
        assert allowed

    def test_registry_contents_are_deliberate(self):
        # Growing this set is a security decision — pin it so additions are
        # conscious. Every member must be a tool that neither returns vault
        # data to the provider nor transmits its input off-device.
        assert LOCAL_SINK_TOOLS == frozenset({"learn_skill", "write_file"})


class TestReadAndTransmitToolsStillGated:
    def test_search_tool_with_tier2_input_still_denied_on_cloud(self, ctl, tmp_path):
        allowed, detail, tier = ctl.check_action(
            "cloud", "search_email",
            {"query": "family finances and home address"},
            access_log_path=str(tmp_path / "access-log.jsonl"))
        assert not allowed
        assert "TIER" in detail
        assert tier > Tier.PUBLIC

    def test_local_provider_unaffected(self, ctl, tmp_path):
        allowed, _, _ = ctl.check_action(
            "ollama", "search_email",
            {"query": "family finances"},
            access_log_path=str(tmp_path / "access-log.jsonl"))
        assert allowed

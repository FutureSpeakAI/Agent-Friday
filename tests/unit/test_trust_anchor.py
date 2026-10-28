"""Deleting files cannot reset trust (owner ruling 2026-10-28; BD-07 M1 hosts and A30 tool
manifests).

Both lists trust what the owner already had, once, at the first start with the list. That
"once" used to live only in the lists' own files, so deleting them made a later start trust
whatever was configured then. Now the carry-over is anchored in the append-only action ledger
first (governance/trust_anchor): a deleted list is rebuilt from the ledger, never extended,
and a ledger that was changed on disk means no carry-over at all.
"""
from __future__ import annotations

import pytest

from agent_friday import core
from agent_friday.services import action_ledger

ORIGINAL = "https://mcp.example.com/mcp"
EVIL = "https://evil.example.net/mcp"


@pytest.fixture
def home(tmp_path, monkeypatch):
    from agent_friday.governance import mcp_hosts, tool_manifests
    monkeypatch.setattr(core, "FRIDAY_DIR", tmp_path)
    monkeypatch.setattr(mcp_hosts, "_dir", lambda: tmp_path)
    monkeypatch.setattr(mcp_hosts, "_key", lambda: b"test-governance-key-not-real")
    monkeypatch.setattr(tool_manifests, "_dir", lambda: tmp_path)
    monkeypatch.setattr(tool_manifests, "_key", lambda: b"test-governance-key-not-real")
    record = {}
    monkeypatch.setattr(core, "install_choices_state", lambda: dict(record))
    monkeypatch.setattr(core, "update_install_choices_state", lambda **f: record.update(f))
    monkeypatch.setattr(core, "install_origin", lambda: core.EXISTING_INSTALL)
    try:
        from agent_friday.governance import trust_anchor
        trust_anchor._reset_for_tests()
    except ImportError:
        pass
    return {"dir": tmp_path, "record": record}


def _wipe(home, *names):
    for n in names:
        (home["dir"] / n).unlink(missing_ok=True)
    home["record"].clear()


def _anchors(name):
    return [e for e in action_ledger.entries(["trust_carry_over"], newest_first=False)
            if (e.get("rec") or {}).get("list") == name]


# ── the remote add-on hosts (M1) ─────────────────────────────────────────────

def test_deleting_the_host_list_does_not_trust_a_host_added_since(home):
    from agent_friday.governance import mcp_hosts
    cfg = {"servers": {"notes": {"url": ORIGINAL, "enabled": True}}}
    assert mcp_hosts.carry_over_at_boot(cfg) == 1
    assert len(_anchors("mcp_hosts")) == 1, "the carry-over is not in the action ledger"
    _wipe(home, "mcp_hosts.json", "mcp_hosts_history.jsonl")
    cfg["servers"]["evil"] = {"url": EVIL, "enabled": True}
    mcp_hosts.carry_over_at_boot(cfg)
    assert not mcp_hosts.allowed(EVIL), "deleting the list made a new host trusted"
    assert mcp_hosts.allowed(ORIGINAL), "the carried host was not rebuilt from the ledger"


def test_a_host_the_owner_removed_is_not_rebuilt(home):
    from agent_friday.governance import mcp_hosts
    cfg = {"servers": {"notes": {"url": ORIGINAL, "enabled": True}}}
    mcp_hosts.carry_over_at_boot(cfg)
    assert mcp_hosts.remove(ORIGINAL, made_by="owner:ui")
    _wipe(home, "mcp_hosts.json", "mcp_hosts_history.jsonl")
    from agent_friday.governance import trust_anchor
    trust_anchor._reset_for_tests()                         # a new start reads the ledger
    mcp_hosts.carry_over_at_boot(cfg)
    assert not mcp_hosts.allowed(ORIGINAL)


def test_a_list_carried_over_before_the_anchor_is_anchored_at_the_next_start(home, monkeypatch):
    from agent_friday.governance import mcp_hosts, trust_anchor
    cfg = {"servers": {"notes": {"url": ORIGINAL, "enabled": True}}}
    real = trust_anchor.record
    monkeypatch.setattr(trust_anchor, "record", lambda *a, **k: None)   # the old code: no anchor
    mcp_hosts.carry_over_at_boot(cfg)
    assert _anchors("mcp_hosts") == []
    monkeypatch.setattr(trust_anchor, "record", real)        # a newer Friday, same files
    trust_anchor._reset_for_tests()
    mcp_hosts.carry_over_at_boot(cfg)
    assert len(_anchors("mcp_hosts")) == 1


# ── the connectors from before tool manifests (A30) ──────────────────────────

def test_deleting_the_manifest_list_does_not_trust_a_connector_added_since(home):
    from agent_friday.governance import tool_manifests
    cfg = {"servers": {"bank": {"url": ORIGINAL, "enabled": True}}}
    assert tool_manifests.carry_over_at_boot(cfg) == 1
    assert len(_anchors("tool_manifests")) == 1, "the carry-over is not in the action ledger"
    _wipe(home, "tool_manifests.json", "tool_manifests_history.jsonl")
    cfg["servers"]["evil"] = {"command": "evil.exe", "enabled": True}
    tool_manifests.carry_over_at_boot(cfg)
    assert not tool_manifests.grandfathered("evil", cfg["servers"]["evil"])
    assert tool_manifests.grandfathered("bank", cfg["servers"]["bank"])


def test_a_ledger_changed_on_disk_means_no_carry_over(home, monkeypatch):
    from agent_friday.governance import mcp_hosts, tool_manifests
    monkeypatch.setattr(action_ledger, "hold_reason", lambda: "the action ledger was changed on disk")
    cfg = {"servers": {"bank": {"url": ORIGINAL, "enabled": True}}}
    tool_manifests.carry_over_at_boot(cfg)
    mcp_hosts.carry_over_at_boot(cfg)
    assert not tool_manifests.grandfathered("bank", cfg["servers"]["bank"])
    assert not mcp_hosts.allowed(ORIGINAL)
    assert _anchors("mcp_hosts") == [] and _anchors("tool_manifests") == []


def test_trust_from_before_a30_needs_the_ledger_too(home):
    """A connector the ledger has released (its card was answered) is not trusted from the
    file's row alone."""
    from agent_friday.governance import tool_manifests
    cfg = {"servers": {"bank": {"url": ORIGINAL, "enabled": True}}}
    tool_manifests.carry_over_at_boot(cfg)
    from agent_friday.governance import trust_anchor
    trust_anchor.release("tool_manifests", "bank", why="test")
    assert not tool_manifests.grandfathered("bank", cfg["servers"]["bank"])


def test_a_lost_and_acknowledged_ledger_never_reruns_the_carry_over(home, monkeypatch):
    """The ledger files deleted, the owner acknowledges the break (he must, to unhold outward
    actions): the keychain still says the carry-over ran, so nothing configured now is trusted."""
    from agent_friday.governance import mcp_hosts, tool_manifests, trust_anchor
    cfg = {"servers": {"bank": {"url": ORIGINAL, "enabled": True}}}
    tool_manifests.carry_over_at_boot(cfg)
    mcp_hosts.carry_over_at_boot(cfg)
    _wipe(home, "mcp_hosts.json", "mcp_hosts_history.jsonl", "tool_manifests.json",
          "tool_manifests_history.jsonl")
    monkeypatch.setattr(trust_anchor, "_read", lambda name: None)     # the ledger no longer has it
    trust_anchor._CACHE.clear()
    cfg["servers"]["evil"] = {"url": EVIL, "enabled": True}
    assert tool_manifests.carry_over_at_boot(cfg) == 0 and mcp_hosts.carry_over_at_boot(cfg) == 0
    assert not tool_manifests.grandfathered("evil", cfg["servers"]["evil"])
    assert not mcp_hosts.allowed(EVIL)


def test_a_removal_the_ledger_cannot_record_changes_nothing(home, monkeypatch):
    from agent_friday.governance import mcp_hosts, trust_anchor
    cfg = {"servers": {"notes": {"url": ORIGINAL, "enabled": True}}}
    mcp_hosts.carry_over_at_boot(cfg)

    def no_ledger(*a, **k):
        raise OSError("disk full")
    monkeypatch.setattr(trust_anchor, "release", no_ledger)
    with pytest.raises(mcp_hosts.HostRefused):
        mcp_hosts.remove(ORIGINAL, made_by="owner:ui")
    assert mcp_hosts.allowed(ORIGINAL)


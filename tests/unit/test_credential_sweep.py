"""The standing version of the thing that found seven stranded credentials.

On 2026-09-19 seven credentials were found dead, and NOT ONE was found by
anybody noticing a symptom. Firecrawl presented as "no API key set" while the
key sat there undecryptable. GitHub presented as a broken MCP server. Drive
presented as working. Every one turned up because a one-off migration helper
enumerated a whole class at once, and its first run found five.

These tests hold the properties that make the standing version useful rather
than ignorable: it finds the thing nobody reported, it distinguishes whose
problem each one is, it does not cry wolf about things that are merely not set
up, and it does not repeat itself until something actually changes.
"""
from __future__ import annotations

import time

import pytest

from agent_friday.services import connector_health as ch
from agent_friday.services import credential_sweep as S


@pytest.fixture(autouse=True)
def clean():
    S._reset_for_tests()
    yield
    S._reset_for_tests()


def _finding(fid, state, expires_at=None, kind="provider_key"):
    return S.Finding(id=fid, kind=kind, label=fid,
                     health=ch.Health(state=state), expires_at=expires_at)


@pytest.fixture()
def only(monkeypatch):
    """Drive the sweep from a fixed set of findings."""
    def _set(findings):
        monkeypatch.setattr(S, "_provider_key_findings", lambda: list(findings))
        monkeypatch.setattr(S, "_google_findings", lambda: [])
        monkeypatch.setattr(S, "_platform_findings", lambda: [])
        monkeypatch.setattr(S, "_mcp_secret_findings", lambda: [])
        monkeypatch.setattr(S, "_vault_findings", lambda: [])
    return _set


# ── what counts as a problem ────────────────────────────────────────────────

def test_an_unreadable_credential_is_a_problem(only):
    """THE FIRECRAWL CASE. Present, undecryptable, and reported as missing -
    so the user was told to supply something he had already supplied."""
    only([_finding("firecrawl", ch.UNREADABLE)])
    inv = S.inventory()
    assert [p["id"] for p in inv["problems"]] == ["firecrawl"]
    assert inv["problems"][0]["health"]["action"] == "unlock"


def test_a_degraded_service_is_a_problem(only):
    """THE DRIVE CASE. Working, one capability off at the provider, actionable,
    and silently so for weeks."""
    only([_finding("google:drive", ch.DEGRADED, kind="google")])
    assert len(S.inventory()["problems"]) == 1


def test_something_nobody_set_up_is_not_a_problem(only):
    """A sweep that reports every unconnected thing gets muted, and then the
    eighth stranded credential goes unnoticed for the same reason as the
    first seven."""
    only([_finding("linkedin", ch.ABSENT)])
    assert S.inventory()["problems"] == []


def test_a_healthy_credential_is_not_a_problem(only):
    only([_finding("openrouter", ch.WORKING)])
    inv = S.inventory()
    assert inv["problems"] == [] and inv["ok"] == 1


def test_the_two_kinds_of_broken_are_not_conflated(only):
    """Reconnect-at-the-provider and cannot-decrypt-locally send the user to
    completely different places. Conflating them cost a reconnect every
    morning for weeks."""
    only([_finding("a", ch.NEEDS_USER), _finding("b", ch.UNREADABLE)])
    actions = {p["id"]: p["health"]["action"] for p in S.inventory()["problems"]}
    assert actions["a"] == "reconnect"
    assert actions["b"] == "unlock"


# ── expiry ──────────────────────────────────────────────────────────────────

def test_an_expired_credential_is_flagged_even_if_it_still_decrypts(only):
    """The publishing platforms stored an expires_at and never consulted it -
    a token that expired in August reported as connected in September."""
    only([_finding("linkedin", ch.WORKING, expires_at=time.time() - 60)])
    inv = S.inventory()
    assert inv["problems"][0]["expired"] is True
    assert inv["ok"] == 0


def test_an_expiry_is_announced_before_it_bites(only):
    """The whole point of a sweep rather than a check at point of use."""
    soon = time.time() + 2 * 24 * 3600
    only([_finding("x", ch.WORKING, expires_at=soon)])
    p = S.inventory()["problems"][0]
    assert p["expiring_soon"] is True and p["expired"] is False


def test_a_distant_expiry_is_not_cried_wolf_about(only):
    only([_finding("x", ch.WORKING, expires_at=time.time() + 365 * 24 * 3600)])
    assert S.inventory()["problems"] == []


def test_no_expiry_is_not_an_expiry(only):
    only([_finding("bluesky", ch.WORKING, expires_at=None)])
    assert S.inventory()["problems"] == []


# ── edge-triggered, so the alert stays worth reading ────────────────────────

def test_the_same_problem_is_announced_once(only):
    only([_finding("firecrawl", ch.UNREADABLE)])
    assert len(S.sweep(notify=False)["new_problems"]) == 1
    assert S.sweep(notify=False)["new_problems"] == []
    assert S.sweep(notify=False)["new_problems"] == []


def test_a_problem_that_changes_shape_is_announced_again(only):
    only([_finding("x", ch.UNREADABLE)])
    S.sweep(notify=False)
    only([_finding("x", ch.NEEDS_USER)])
    assert len(S.sweep(notify=False)["new_problems"]) == 1


def test_a_recovered_credential_can_alert_again_if_it_breaks_twice(only):
    """Forgetting on recovery matters: without it, the second failure of the
    same credential would be silent, which is the worst possible time to go
    quiet."""
    only([_finding("x", ch.UNREADABLE)])
    S.sweep(notify=False)
    only([_finding("x", ch.WORKING)])
    S.sweep(notify=False)
    only([_finding("x", ch.UNREADABLE)])
    assert len(S.sweep(notify=False)["new_problems"]) == 1


# ── it must not be able to hurt anything ────────────────────────────────────

def test_one_broken_source_does_not_cost_the_whole_inventory(monkeypatch):
    """A sweep that dies on one unreadable store reports nothing about the
    other twenty - which is the failure it exists to prevent. The stores it
    reads are also the ones most likely to be broken, since that is what it is
    looking for."""
    monkeypatch.setattr(S, "_provider_key_findings",
                        lambda: (_ for _ in ()).throw(RuntimeError("boom")))
    monkeypatch.setattr(S, "_google_findings", lambda: [_finding("g", ch.WORKING)])
    monkeypatch.setattr(S, "_platform_findings", lambda: [])
    monkeypatch.setattr(S, "_mcp_secret_findings", lambda: [])
    monkeypatch.setattr(S, "_vault_findings", lambda: [])

    inv = S.inventory()
    ids = {f["id"] for f in inv["findings"]}
    assert "g" in ids, "a healthy credential was lost to another store's failure"
    assert "source:provider_keys" in ids, "the broken store failed silently"
    broken = [f for f in inv["findings"] if f["id"] == "source:provider_keys"][0]
    assert broken["health"]["state"] == ch.UNKNOWN
    assert "boom" in broken["health"]["detail"]


def test_a_real_inventory_runs_and_leaks_nothing():
    """Against the actual machine. Asserts the shape and, more importantly,
    that nothing secret-shaped rides along."""
    import json
    inv = S.inventory()
    blob = json.dumps(inv)
    assert inv["total"] >= 0 and "summary" in inv
    for f in inv["findings"]:
        assert set(f) >= {"id", "kind", "label", "health", "expires_at"}
    # A credential value would be a long opaque token; the inventory carries
    # states and labels only.
    for suspicious in ("BEGIN PRIVATE", "refresh_token", "ghp_", "sk-"):
        assert suspicious not in blob, "the inventory leaked %s" % suspicious


def test_notifying_without_an_engine_is_harmless(only):
    only([_finding("x", ch.UNREADABLE)])
    S.sweep(notify=True)          # must not raise when no notif engine exists


# ── the class that hid the GitHub token ─────────────────────────────────────

def test_mcp_connector_secrets_are_swept(tmp_path, monkeypatch):
    """THE BLIND SPOT. These live as base64 inside mcp_servers.json rather
    than as blobs on disk, so the file-walking migration could not see them -
    which is why five credentials were recovered while GitHub kept failing
    every spawn. A sweep that inherited the same blind spot would be the same
    mistake with a schedule attached.
    """
    import json

    import agent_friday.core as core
    from agent_friday.services import connector_secrets as cse
    from agent_friday.services import keystore as ks

    monkeypatch.setattr(core, "FRIDAY_DIR", tmp_path)
    monkeypatch.setattr(ks, "KEYSTORE_PATH", tmp_path / "security" / "ks.json")
    monkeypatch.setattr(ks, "_CACHED_KEY", None)
    monkeypatch.setattr(ks, "_passphrase", lambda: "")

    good = cse.encrypt_value("ghp_realtoken")
    broken = cse.SECRET_MARKER + "vault:" + "bm90LWEtcmVhbC1ibG9i"
    (tmp_path / "mcp_servers.json").write_text(json.dumps({"servers": {
        "github": {"env": {"GITHUB_PERSONAL_ACCESS_TOKEN": good}},
        "slack": {"env": {"SLACK_BOT_TOKEN": broken, "LOG_LEVEL": "debug"}},
    }}), encoding="utf-8")

    found = {f.id: f for f in S._mcp_secret_findings()}
    assert "mcp:github:GITHUB_PERSONAL_ACCESS_TOKEN" in found
    assert found["mcp:github:GITHUB_PERSONAL_ACCESS_TOKEN"].health.state == ch.WORKING
    assert found["mcp:slack:SLACK_BOT_TOKEN"].health.state == ch.UNREADABLE
    assert found["mcp:slack:SLACK_BOT_TOKEN"].health.action == "unlock"
    # A non-secret env value is not a credential.
    assert not any(k.endswith("LOG_LEVEL") for k in found)


def test_a_missing_mcp_config_is_not_a_finding(tmp_path, monkeypatch):
    import agent_friday.core as core
    monkeypatch.setattr(core, "FRIDAY_DIR", tmp_path)
    assert S._mcp_secret_findings() == []

"""Gauntlet finding F68 (2026-09-04, Stephen's direct ruling on the
2026-09-04 startup-wiring dynamic boot's "confirmed_pending_action" item):
credential_store.provider_key_status() reported 'connected' from
_provider_key_path(...).exists() alone -- it never attempted the decrypt
read_secret() itself performs. Stephen's own 3 provider keys showed
'connected' for a real stretch of time while genuinely undecryptable
(a machine-key rotation, a vault-passphrase change, or disk corruption
all produce exactly this), which is precisely why nobody noticed sooner:
the one surface meant to say so was lying.

Stephen's ruling: report what's true -- readable, present-but-unreadable,
or absent -- and make the boot log's bare "loaded N" say how many
actually decrypted, not just how many happened to succeed with no
denominator.

Fix: provider_key_status() now attempts read_secret() and returns one of
three states ('connected' / 'present_but_unreadable' / 'missing') instead
of a boolean-shaped two-state guess. bootstrap_provider_env() is now a
thin int-returning wrapper (unchanged contract, existing tests untouched)
around bootstrap_provider_env_detail(), which server.py's boot log uses
directly to report "{loaded}/{candidates} decrypted" plus the unreadable
provider names, gated on candidates existing at all (not on the count
that succeeded) so an all-broken run is still visible. provider_health.py
and setup_brain.py's own consumers were checked and corrected where their
existing boolean logic would have silently mistreated the new third state.

Reproducing "present but unreadable" for real: a garbage blob prefixed
with credential_store._DPAPI_MAGIC genuinely fails Windows DPAPI unprotect
on a real machine (confirmed directly before relying on it) -- a
plaintext garbage blob does NOT raise (unprotect()'s own plaintext
fallback returns unrecognized bytes as-is), so that construction was
checked and rejected before writing this file.
"""
from __future__ import annotations

import pytest

from agent_friday.services import credential_store as cs


@pytest.fixture
def isolated_keys_dir(tmp_path, monkeypatch):
    """Redirect where provider keys live so this test never touches the
    real ~/.friday/providers/keys."""
    d = tmp_path / "providers" / "keys"
    monkeypatch.setattr(cs, "_PROVIDER_KEYS_DIR", d)
    return d


def _write_undecryptable_key(keys_dir, provider: str):
    """The exact reproduction: a DPAPI-magic-prefixed blob that is not a
    real DPAPI blob -- genuinely fails to decrypt, not a stand-in."""
    keys_dir.mkdir(parents=True, exist_ok=True)
    path = keys_dir / f"{provider}.key"
    path.write_bytes(cs._DPAPI_MAGIC + b"not-a-real-dpapi-blob-1234567890")
    return path


class TestProviderKeyStatusThreeStates:
    def test_no_file_is_missing(self, isolated_keys_dir):
        assert cs.provider_key_status("nonexistent-provider") == "missing"

    def test_a_real_stored_key_is_connected(self, isolated_keys_dir):
        cs.set_provider_key("demo-provider", "sk-real-key-value")
        assert cs.provider_key_status("demo-provider") == "connected"

    def test_the_reviewers_scenario_a_present_but_undecryptable_key(self, isolated_keys_dir):
        """Stephen's own real situation, reproduced directly: a key FILE
        exists, but decrypting it fails. Before this fix this reported
        'connected' -- a status display that lied."""
        _write_undecryptable_key(isolated_keys_dir, "broken-provider")
        status = cs.provider_key_status("broken-provider")
        assert status == "present_but_unreadable", (
            f"a key file that genuinely fails to decrypt reported "
            f"{status!r} instead of 'present_but_unreadable' -- if this "
            f"says 'connected', the status is lying again; if it says "
            f"'missing', a file that demonstrably exists on disk is being "
            f"reported as though it were never created"
        )

    def test_get_provider_key_still_returns_none_for_the_broken_one(self, isolated_keys_dir):
        """Non-regression: the new status function must not change what
        actually happens when something tries to USE the broken key."""
        _write_undecryptable_key(isolated_keys_dir, "broken-provider")
        assert cs.get_provider_key("broken-provider") is None


class TestBootstrapProviderEnvDetail:
    def test_backward_compatible_int_wrapper_unchanged(self, isolated_keys_dir, monkeypatch):
        """bootstrap_provider_env()'s own existing tests (test_stored_key_
        beats_start_bat_at_boot.py) compare its return value directly to an
        int -- this must keep working exactly as before."""
        monkeypatch.setattr(cs, "_env_key_for_provider", lambda p: f"{p.upper()}_KEY")
        monkeypatch.delenv("DEMO_KEY", raising=False)
        cs.set_provider_key("demo", "value-1")
        assert cs.bootstrap_provider_env() == 1

    def test_detail_reports_candidates_loaded_and_unreadable_names(self, isolated_keys_dir, monkeypatch):
        import os
        monkeypatch.setattr(cs, "_env_key_for_provider", lambda p: f"{p.upper()}_KEY")
        for name in ("good_a", "good_b"):
            monkeypatch.delenv(f"{name.upper()}_KEY", raising=False)
            cs.set_provider_key(name, f"value-for-{name}")
        monkeypatch.delenv("BROKEN_KEY", raising=False)
        _write_undecryptable_key(isolated_keys_dir, "broken")

        detail = cs.bootstrap_provider_env_detail()

        assert detail["candidates"] == 3, (
            f"expected all 3 provider key files (2 good + 1 broken) to "
            f"count as candidates, got {detail!r}"
        )
        assert detail["loaded"] == 2, (
            f"expected exactly the 2 decryptable keys to be loaded, got "
            f"{detail!r}"
        )
        assert detail["unreadable"] == ["broken"], (
            f"expected the broken provider to be named in 'unreadable', "
            f"got {detail!r}"
        )
        # The 2 good keys genuinely landed in the environment; the broken
        # one did not (get_provider_key() returned None for it, so the
        # loop's `if val:` branch was never taken).
        assert os.environ.get("GOOD_A_KEY") == "value-for-good_a"
        assert os.environ.get("GOOD_B_KEY") == "value-for-good_b"
        assert "BROKEN_KEY" not in os.environ

    def test_all_candidates_unreadable_still_reports_them_not_silence(self, isolated_keys_dir, monkeypatch):
        """The exact scenario that used to vanish from the boot log
        entirely: bootstrap_provider_env()'s bare success count was 0, so
        `if _loaded_keys:` never printed anything -- Stephen's own 3-for-3
        undecryptable run would have logged NOTHING at all."""
        monkeypatch.setattr(cs, "_env_key_for_provider", lambda p: f"{p.upper()}_KEY")
        for name in ("broken-1", "broken-2"):
            monkeypatch.delenv(f"{name.upper()}_KEY", raising=False)
            _write_undecryptable_key(isolated_keys_dir, name)

        detail = cs.bootstrap_provider_env_detail()

        assert detail["loaded"] == 0
        assert detail["candidates"] == 2, (
            "the old boot log gated printing on `if loaded:` alone -- a "
            "run with candidates present but zero loaded must still be "
            "distinguishable from a run with no keys stored at all"
        )
        assert sorted(detail["unreadable"]) == ["broken-1", "broken-2"]


class TestDownstreamConsumersHandleTheThirdStateCorrectly:
    def test_setup_brain_env_has_treats_unreadable_as_not_available(self, isolated_keys_dir, monkeypatch):
        import agent_friday.setup_brain as sb
        _write_undecryptable_key(isolated_keys_dir, "some-random-provider")
        assert sb.env_has("some-random-provider") is False, (
            "env_has()'s own docstring asks whether a key is 'already "
            "available to this process' -- one that cannot be decrypted "
            "is not available for anything, but the old "
            "`not in ('missing', None)` check would have said True for "
            "'present_but_unreadable'"
        )

    def test_provider_health_key_state_distinguishes_unreadable_from_never_configured(
        self, isolated_keys_dir, monkeypatch
    ):
        """_check() resolves its provider dict via the live provider
        registry, which this isolated unit test deliberately doesn't wire
        up -- exercising _key_state()/_has_key() directly (the actual units
        F68 changed) is the more precise and more stable proof."""
        from agent_friday.services import provider_health as ph
        monkeypatch.delenv("FLAKY_PROVIDER_API_KEY", raising=False)
        prov_broken = {"name": "flaky-provider", "type": "openai-compatible",
                       "auth": {"type": "env_var", "key": "FLAKY_PROVIDER_API_KEY"}}
        prov_never_configured = {"name": "untouched-provider", "type": "openai-compatible",
                                 "auth": {"type": "env_var", "key": "UNTOUCHED_PROVIDER_API_KEY"}}
        monkeypatch.delenv("UNTOUCHED_PROVIDER_API_KEY", raising=False)
        _write_undecryptable_key(isolated_keys_dir, "flaky-provider")

        assert ph._key_state(prov_broken) == "unreadable", (
            "a provider with a present-but-undecryptable key must report "
            "'unreadable', not the same 'missing'-shaped state a provider "
            "that was never configured at all would get"
        )
        assert ph._key_state(prov_never_configured) == "missing", (
            "a provider with no key file anywhere must remain 'missing' -- "
            "'unreadable' is specifically for a key that IS present but "
            "won't decrypt, not a catch-all replacement for it"
        )
        assert ph._has_key(prov_broken) is False
        assert ph._has_key(prov_never_configured) is False

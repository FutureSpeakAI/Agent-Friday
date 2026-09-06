"""Gauntlet finding F43: docs/security/threat-model.md's Key Storage table promises the
Ed25519 attestation private key is "confined to ~/.friday/vault/ with 600
permissions as a fallback." IntegrityEngine._load_or_generate_ed25519()
wrote the key file with a plain write_bytes() and never called chmod
anywhere -- confirmed by a repo-wide grep, the only reference to that
filename is the write site itself. The SAME file, two functions later
(get_governance_key()'s file fallback), does the correct thing: chmod
0o600 wrapped in a try/except with a warning log on failure. The pattern
existed, it just wasn't applied to this second private key.

On a from-source Linux/macOS install (a row docs/security/threat-model.md's own
compatibility table treats as first-class), the file inherited the
process umask (commonly 644), so any other local account on a shared
machine could read the private signing key used for federation/peer
attestation.

This probe mocks Path.chmod to record calls (POSIX permission bits are
not meaningfully enforced on the Windows platform this suite normally
runs on, so asserting on the actual OS-level mode would be untestable
here) -- it must be RED before the fix (chmod is never called on the
private key file) and GREEN after.
"""
from __future__ import annotations

import stat

from agent_friday.governance.proof_of_integrity import IntegrityEngine


class TestEd25519KeyFilePermissions:
    def test_private_key_file_is_chmod_600(self, tmp_path, monkeypatch):
        calls = []
        from pathlib import Path
        orig_chmod = Path.chmod

        def _tracking_chmod(self, mode, *a, **k):
            calls.append((self.name, mode))
            try:
                return orig_chmod(self, mode, *a, **k)
            except Exception:
                pass  # chmod may be a no-op/partial on some platforms

        monkeypatch.setattr(Path, "chmod", _tracking_chmod)

        IntegrityEngine(friday_dir=tmp_path)

        key_calls = [c for c in calls if c[0] == ".attestation-key-ed25519"]
        assert key_calls, (
            "the Ed25519 attestation PRIVATE key file was never chmod'd at "
            "all -- docs/security/threat-model.md promises it is confined with 600 "
            "permissions as a fallback, but the write site never applied "
            "any restrictive mode"
        )
        assert key_calls[0][1] == (stat.S_IRUSR | stat.S_IWUSR), (
            "the private key file was chmod'd, but not to 0o600 "
            "(owner read+write only) -- got mode %r" % key_calls[0][1]
        )

    def test_public_key_file_is_left_at_default_mode(self, tmp_path, monkeypatch):
        """No-op-shaped sanity check: the PUBLIC verify key is meant to be
        shared (it's handed out for signature verification), so this fix
        must not lock it down -- only the private signing key needs 600."""
        calls = []
        from pathlib import Path
        orig_chmod = Path.chmod

        def _tracking_chmod(self, mode, *a, **k):
            calls.append((self.name, mode))
            try:
                return orig_chmod(self, mode, *a, **k)
            except Exception:
                pass

        monkeypatch.setattr(Path, "chmod", _tracking_chmod)

        IntegrityEngine(friday_dir=tmp_path)

        pub_calls = [c for c in calls if c[0] == ".attestation-pubkey-ed25519"]
        assert not pub_calls, (
            "the PUBLIC attestation key was chmod'd -- it's meant to be "
            "shared for signature verification and should stay at the "
            "default mode; only the private key needs restricting"
        )

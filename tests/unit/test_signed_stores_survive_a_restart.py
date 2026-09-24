"""A restart must not invalidate a signed store.

At 07:27 on 2026-09-24 Friday told Stephen the file-permission ledger was
corrupted and every file grant was suspended. It was not corrupted, and it was
not tampered with. Forensics on the untouched file (mtime Aug 25 17:53, one
480-byte line, valid JSON, trailing newline present):

    line 1 VERIFIES under: launcher FRIDAY_SECRET_KEY (pre-removal)
    line 1 does NOT verify under: persisted ~/.friday/secret_key

The content was intact and its HMAC was correct -- under the PREVIOUS key. The
cause was mine: removing the hardcoded `FRIDAY_SECRET_KEY` from the launchers
during the tunnel work. `core._load_or_create_secret()` prefers the environment
variable over the persisted `~/.friday/secret_key`, so deleting the launcher line
silently swapped the HMAC key, and every line signed under the old one stopped
verifying at the next boot.

The design fault this exposes is not the launcher edit. It is that a LEDGER
SIGNING KEY was the same value as the Flask SESSION secret. Those have opposite
requirements: a session secret is allowed -- even encouraged -- to rotate, since
rotating it just logs everyone out; a ledger signing key must never change or
the ledger's own history becomes unreadable. Sharing one value means any
rotation of the session secret silently destroys the ledger's verifiability.

So the signing key is now its own persisted secret, stored through
`credential_store` (vault/DPAPI), never read from the environment and never
derived from the session secret.

Re-attestation is deliberately NOT automatic. A line that fails to verify is
shown to the user and re-signed only on an explicit confirmation, as a NEW event
carrying the hash of what it replaced. Auto-re-signing whatever failed would
launder a genuinely tampered line into a valid one, which is the whole point of
signing it in the first place.
"""

import hashlib
import importlib
import json

import pytest


@pytest.fixture
def grants(tmp_path, monkeypatch):
    """A fresh file_grants module pointed at a scratch Friday dir.

    `core.FRIDAY_DIR` is the thing to patch, not the two path helpers: both
    `_ledger_path()` and `_signing_key_path()` read it at CALL time, so one patch
    drives both AND survives the `importlib.reload` that the restart tests do.
    Patching the helpers themselves does not -- reload rebuilds the module's
    attributes and silently drops the monkeypatch, which is what made the first
    version of these tests fail for a reason that had nothing to do with the bug.
    """
    import agent_friday.core as core
    monkeypatch.setattr(core, "FRIDAY_DIR", tmp_path)
    monkeypatch.delenv("FRIDAY_SECRET_KEY", raising=False)
    import agent_friday.services.file_grants as fg
    fg._SIGNING_KEY_CACHE.clear()
    fg._invalidate_cache()
    yield fg
    fg._SIGNING_KEY_CACHE.clear()
    fg._invalidate_cache()


# ─────────────────────────────────────────────────────────────────────────────
# The regression itself.
# ─────────────────────────────────────────────────────────────────────────────

def test_the_signing_key_is_not_the_session_secret(grants, monkeypatch):
    """The bug, stated directly. Changing the session secret -- which is exactly
    what removing the launcher line did -- must not change the ledger key."""
    before = grants._secret_bytes()

    monkeypatch.setenv("FRIDAY_SECRET_KEY", "a-completely-different-session-secret")
    grants._invalidate_cache()
    after = grants._secret_bytes()

    assert before == after, (
        "the ledger signing key followed FRIDAY_SECRET_KEY; a session-secret "
        "rotation would destroy the ledger again")


def test_the_signing_key_is_stable_across_a_restart(grants, monkeypatch):
    """A 'restart' here is a module reload with caches cleared and the
    environment rebuilt -- the same thing that broke it live."""
    first = grants._secret_bytes()

    # A restart, as this module experiences one: every in-process cache cold and
    # the session secret regenerated. The persisted key must outlive it.
    grants._SIGNING_KEY_CACHE.clear()
    grants._invalidate_cache()
    monkeypatch.setenv("FRIDAY_SECRET_KEY", "a-new-session-secret-for-this-boot")

    assert grants._secret_bytes() == first, "the signing key changed across a restart"


def test_a_grant_written_before_a_restart_still_verifies(grants, monkeypatch):
    """End to end: the exact thing Stephen lost."""
    grants._append_event({
        "event": "grant_file", "id": "g-restart-1",
        "path": r"C:\Users\someone\Downloads\a.pdf", "sha256": "0" * 64,
    })
    state = grants._load_state(force=True)
    assert not state.suspended and state.dropped == 0, "the fresh write did not verify"

    # a restart: session secret rotated, every cache cold
    monkeypatch.setenv("FRIDAY_SECRET_KEY", "rotated-on-this-boot")
    grants._SIGNING_KEY_CACHE.clear()
    grants._invalidate_cache()

    state2 = grants._load_state(force=True)
    assert state2.dropped == 0, (
        "%d line(s) failed to verify after a restart -- this is the live bug"
        % state2.dropped)
    assert not state2.suspended
    assert "g-restart-1" in state2.grants


def test_the_key_is_stored_protected_not_in_plaintext_beside_the_ledger(grants):
    """It must live where credential_store puts secrets (vault/DPAPI), so it is
    not readable by anything that can read the repo or a launcher."""
    grants._secret_bytes()
    p = grants._signing_key_path()
    assert p.exists(), "no signing key was persisted"
    blob = p.read_bytes()
    from agent_friday.services import credential_store as cs
    if cs.protection_method() != "plaintext":
        assert cs.looks_protected(blob), (
            "the signing key was written unprotected while a protection "
            "method was available")


# ─────────────────────────────────────────────────────────────────────────────
# The safety behaviour must not soften.
# ─────────────────────────────────────────────────────────────────────────────

def test_a_really_tampered_line_still_suspends_every_grant(grants):
    """Suspenders mode is correct and stays. A corrupted ledger may only ever
    tighten."""
    grants._append_event({"event": "grant_file", "id": "g-1", "path": "a.pdf"})
    grants._append_event({"event": "deny", "id": "d-1", "path": "secret.pdf"})

    p = grants._ledger_path()
    lines = p.read_text(encoding="utf-8").strip().split("\n")
    rec = json.loads(lines[0])
    rec["event"]["path"] = "something-else-entirely.pdf"      # content changed
    lines[0] = json.dumps(rec, sort_keys=True, separators=(",", ":"))
    p.write_text("\n".join(lines) + "\n", encoding="utf-8")

    state = grants._load_state(force=True)
    assert state.dropped == 1
    assert state.suspended is True
    assert state.grants == {}, "a tampered ledger left grants active"
    assert "d-1" in state.denies, "deny marks must survive suspenders mode"


def test_a_failed_line_is_never_auto_resigned(grants):
    """The laundering risk. Loading a ledger with a bad line must not rewrite
    that line as valid."""
    grants._append_event({"event": "grant_file", "id": "g-2", "path": "b.pdf"})
    p = grants._ledger_path()
    original = p.read_bytes()
    rec = json.loads(original.decode("utf-8").strip())
    rec["hmac"] = "0" * 64
    p.write_bytes((json.dumps(rec, sort_keys=True, separators=(",", ":")) + "\n")
                  .encode("utf-8"))
    broken = p.read_bytes()

    grants._load_state(force=True)

    assert p.read_bytes() == broken, (
        "loading the ledger rewrote a line that failed verification")


# ─────────────────────────────────────────────────────────────────────────────
# The re-attestation path.
# ─────────────────────────────────────────────────────────────────────────────

def test_unverified_lines_can_be_listed_for_review(grants):
    """Stephen cannot confirm what he cannot see. A dropped line's CONTENT is
    reviewable, clearly marked unverified."""
    grants._append_event({"event": "grant_file", "id": "g-3", "path": "c.pdf"})
    p = grants._ledger_path()
    rec = json.loads(p.read_text(encoding="utf-8").strip())
    rec["hmac"] = "1" * 64
    p.write_text(json.dumps(rec, sort_keys=True, separators=(",", ":")) + "\n",
                 encoding="utf-8")

    pending = grants.list_unverified()
    assert len(pending) == 1
    assert pending[0]["event"].get("id") == "g-3"
    assert pending[0]["verified"] is False
    assert len(pending[0]["line_sha256"]) == 64


def test_reattesting_writes_a_new_signed_line_with_provenance(grants):
    grants._append_event({"event": "grant_file", "id": "g-4", "path": "d.pdf"})
    p = grants._ledger_path()
    rec = json.loads(p.read_text(encoding="utf-8").strip())
    rec["hmac"] = "2" * 64
    p.write_text(json.dumps(rec, sort_keys=True, separators=(",", ":")) + "\n",
                 encoding="utf-8")

    pending = grants.list_unverified()
    out = grants.reattest(pending[0]["line_sha256"], confirmed_by="stephen")
    assert out["ok"] is True

    state = grants._load_state(force=True)
    # The bad line is quarantined, not erased and not re-signed, so the active
    # ledger verifies again and suspenders mode lifts. Without that step a single
    # unverifiable line suspends grants forever -- including the one just
    # re-attested, which is correct but unrecoverable.
    assert state.dropped == 0, "the active ledger still has an unverifiable line"
    assert state.suspended is False, "grants are still suspended after re-attestation"
    new = [e for e in state.grants.values() if e.get("reattested_from")]
    assert new, "re-attestation did not produce a verifiable grant"
    assert new[0]["reattested_from"] == pending[0]["line_sha256"]
    assert new[0]["reattested_by"] == "stephen"

    q = grants._quarantine_path()
    assert q.exists(), "the unverifiable line was not preserved"
    kept = json.loads(q.read_text(encoding="utf-8").strip())
    assert kept["line_sha256"] == pending[0]["line_sha256"]
    assert kept["quarantined_by"] == "stephen"
    assert '"hmac":"2222' in kept["line"] or "2" * 8 in kept["line"], (
        "the quarantined copy is not the original line verbatim")


def test_reattesting_requires_a_confirmation(grants):
    grants._append_event({"event": "grant_file", "id": "g-5", "path": "e.pdf"})
    p = grants._ledger_path()
    rec = json.loads(p.read_text(encoding="utf-8").strip())
    rec["hmac"] = "3" * 64
    p.write_text(json.dumps(rec, sort_keys=True, separators=(",", ":")) + "\n",
                 encoding="utf-8")
    sha = grants.list_unverified()[0]["line_sha256"]

    for bad in (None, "", "   "):
        out = grants.reattest(sha, confirmed_by=bad)
        assert out["ok"] is False, "re-attested with confirmed_by=%r" % bad


def test_reattesting_an_unknown_line_is_refused(grants):
    out = grants.reattest("f" * 64, confirmed_by="stephen")
    assert out["ok"] is False

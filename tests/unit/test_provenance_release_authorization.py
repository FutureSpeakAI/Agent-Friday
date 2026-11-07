"""An empty or unverifiable manifest can never unlock a release (Federation / Economy spec,
first ticket; NS-40.1-1).

services/provenance.verify_manifest({}) used to answer valid=True: its verdict was all() over the
checks that came back, and with every check unavailable there was nothing to fail. An artifact
imported from elsewhere has no manifest here, so it read as verified. Nothing in the code gated a
release on that verdict yet, but the spec ties publication, delivery and cash to it, so the verdict
is fixed first:

  * `valid` needs a manifest and at least one check that came back True (what was found);
  * `evidence` keeps the facts apart: local record, signature, digest, trusted signer, registry;
  * `release_ok` / release_allowed() is what a gate asks, and needs a verified signature AND a
    matching digest; anything else, including a missing key or an error result, is a no.

The bounded local uses stay: a signed local creation still verifies, a tampered one still fails,
and an unsigned local creation whose digest matches is still shown as found (valid) while a
release stays refused. Offline; synthetic bytes only.
"""
import pytest

import agent_friday.core as core
from agent_friday.services import content_credentials as cc
from agent_friday.services import provenance as pv


def _creation(name, data=b"hello-bytes"):
    core.CREATIONS_DIR.mkdir(parents=True, exist_ok=True)
    p = core.CREATIONS_DIR / name
    p.write_bytes(data)
    return p


def _signed(name, data=b"SIGNED-BYTES"):
    manifest = pv.write(_creation(name, data), media_type="image")
    if pv.verify_manifest(manifest)["checks"]["signature_ok"] is not True:
        pytest.skip("Ed25519 signing is not available here: no signed manifest to verify")
    return manifest


def test_an_empty_manifest_is_not_valid_and_cannot_release():
    res = pv.verify_manifest({})
    assert res["valid"] is False, "an empty manifest checked nothing and was called valid"
    assert res["release_ok"] is False
    assert res["release_missing"] == ["manifest", "signature", "digest"]
    assert pv.release_allowed(res) is False


@pytest.mark.parametrize("nothing", [None, {}, {"creator": "someone"}, {"artifact": {}}],
                         ids=["none", "empty", "creator-only", "empty-artifact"])
def test_a_manifest_with_nothing_to_check_is_never_valid(nothing):
    res = pv.verify_manifest(nothing)
    assert res["valid"] is False and pv.release_allowed(res) is False


def test_an_imported_file_with_no_manifest_here_cannot_release(tmp_path):
    imported = tmp_path / "imported.png"
    imported.write_bytes(b"BYTES-FROM-ELSEWHERE")
    res = pv.verify_manifest(imported)
    assert res["valid"] is False and pv.release_allowed(res) is False
    assert res["evidence"]["signature"] == "missing" and res["evidence"]["digest"] == "missing"


def test_a_digest_that_cannot_be_checked_here_cannot_release():
    # a manifest that names a file this PC does not hold: nothing recomputes, nothing is verified
    res = pv.verify_manifest({"artifact": {"content_hash": "sha256:" + "0" * 64,
                                           "filename": "not-here.png"}})
    assert res["checks"]["hash_ok"] is None
    assert res["valid"] is False and pv.release_allowed(res) is False
    assert res["evidence"]["digest"] == "missing"


def test_a_signed_local_creation_still_verifies_and_may_release():
    res = pv.verify_manifest(_signed("fed-release-ok.png"))
    assert res["valid"] is True
    assert res["evidence"]["signature"] == "verified" and res["evidence"]["digest"] == "verified"
    assert res["release_ok"] is True and res["release_missing"] == []
    assert pv.release_allowed(res) is True


def test_the_facts_are_kept_apart():
    # a local record and a valid signature are not a trusted signer or a registry confirmation
    ev = pv.verify_manifest(_signed("fed-release-apart.png"))["evidence"]
    assert ev["local_record"] == "verified"
    assert ev["trust"] == "missing" and ev["registry"] == "missing"


def test_an_unsigned_local_creation_is_found_but_cannot_release(monkeypatch):
    manifest = pv.write(_creation("fed-release-unsigned.png", b"UNSIGNED"), media_type="image")
    manifest = dict(manifest, signature={"value": "ed25519_unavailable"})
    res = pv.verify_manifest(manifest)
    assert res["checks"]["signature_ok"] is None and res["checks"]["hash_ok"] is True
    assert res["valid"] is True, "the bounded local use (showing what was found) is kept"
    assert res["evidence"]["signature"] == "missing"
    assert res["release_ok"] is False and res["release_missing"] == ["signature"]
    assert pv.release_allowed(res) is False


def test_changed_bytes_are_a_mismatch_and_cannot_release():
    manifest = _signed("fed-release-tamper.png", b"ORIGINAL")
    (core.CREATIONS_DIR / "fed-release-tamper.png").write_bytes(b"CHANGED-AFTER-SIGNING")
    res = pv.verify_manifest(manifest)
    assert res["valid"] is False and res["evidence"]["digest"] == "mismatch"
    assert pv.release_allowed(res) is False


def test_a_changed_manifest_is_a_signature_mismatch_and_cannot_release():
    manifest = _signed("fed-release-edited.png")
    res = pv.verify_manifest(dict(manifest, creator="somebody-else"))
    assert res["valid"] is False and res["evidence"]["signature"] == "mismatch"
    assert pv.release_allowed(res) is False


@pytest.mark.parametrize("verdict", [
    None, {}, [], "ok", {"valid": True}, {"release_ok": "yes"}, {"release_ok": 1},
    {"valid": True, "checks": {"signature_ok": True, "hash_ok": True}},
    {"valid": False, "checks": {"error": "boom"}},
], ids=["none", "empty", "list", "string", "valid-only", "truthy-string", "truthy-int",
        "checks-without-release_ok", "error-result"])
def test_release_allowed_fails_closed(verdict):
    assert pv.release_allowed(verdict) is False


def test_the_credential_check_cannot_release_an_empty_manifest():
    res = cc.verify_credential({})
    assert res["valid"] is False and pv.release_allowed(res) is False

"""Signed releases and signed public lists: what verifies, and what is refused.

Keys are made in the test (Ed25519) and written in minisign's own format, so
these are the bytes the real `minisign` tool produces; nothing is fetched.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
from datetime import date

import pytest


def _keypair():
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    sk = Ed25519PrivateKey.generate()
    pk = sk.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    kid = os.urandom(8)
    pub = "untrusted comment: minisign public key\n" + base64.b64encode(b"Ed" + kid + pk).decode() + "\n"
    return sk, kid, pub


def _sign(sk, kid, data: bytes, comment: str, hashed: bool = True) -> str:
    alg = b"ED" if hashed else b"Ed"
    msg = hashlib.blake2b(data, digest_size=64).digest() if hashed else data
    sig = sk.sign(msg)
    gsig = sk.sign(sig + comment.encode())
    return ("untrusted comment: signature from minisign secret key\n"
            + base64.b64encode(alg + kid + sig).decode() + "\n"
            + "trusted comment: " + comment + "\n"
            + base64.b64encode(gsig).decode() + "\n")


# ── the format ───────────────────────────────────────────────────────────────

def test_a_minisign_signature_verifies_and_its_comment_is_read():
    from agent_friday.services import minisign
    sk, kid, pub = _keypair()
    for hashed in (True, False):
        sig = _sign(sk, kid, b"hello", "name=x version=3 expires=2030-01-01", hashed=hashed)
        out = minisign.verify(b"hello", sig, [pub])
        assert out["meta"] == {"name": "x", "version": "3", "expires": "2030-01-01"}


def test_a_changed_file_a_changed_comment_or_another_key_is_refused():
    from agent_friday.services import minisign
    sk, kid, pub = _keypair()
    _sk2, _kid2, other = _keypair()
    sig = _sign(sk, kid, b"hello", "name=x version=3 expires=2030-01-01")
    with pytest.raises(minisign.SignatureError, match="does not match"):
        minisign.verify(b"hellO", sig, [pub])
    tampered = sig.replace("version=3", "version=9")
    with pytest.raises(minisign.SignatureError, match="trusted comment was changed"):
        minisign.verify(b"hello", tampered, [pub])
    with pytest.raises(minisign.SignatureError, match="does not trust"):
        minisign.verify(b"hello", sig, [other])
    with pytest.raises(minisign.SignatureError, match="no trusted signing key"):
        minisign.verify(b"hello", sig, [])


# ── public lists ─────────────────────────────────────────────────────────────

@pytest.fixture
def lists(tmp_path, monkeypatch):
    monkeypatch.setenv("FRIDAY_HOME", str(tmp_path))
    from agent_friday.services import egress_gate, public_lists, trust_keys, update_check
    sk, kid, pub = _keypair()
    monkeypatch.setattr(trust_keys, "PUBLIC_LIST_KEYS", (pub,))
    monkeypatch.setattr(public_lists, "DEFAULT_BASE_URL", "https://lists.example.test/v1")
    monkeypatch.setattr(update_check, "is_enabled", lambda: True)
    monkeypatch.setattr(egress_gate, "_DEFAULT_LOG", tmp_path / "egress.jsonl")
    served = {}
    monkeypatch.setattr(public_lists, "FETCH", lambda url, cap: served[url])

    def publish(name, version, expires="2099-01-01", items=None, as_name=None):
        body = json.dumps({"items": items or [{"company": "Example Co"}]}).encode()
        url = "https://lists.example.test/v1/%s.json" % name
        served[url] = body
        served[url + ".minisig"] = _sign(sk, kid, body, "name=%s version=%d expires=%s" % (
            as_name or name, version, expires)).encode()
    return public_lists, publish, tmp_path


def test_a_signed_list_is_fetched_verified_cached_and_logged(lists):
    pl, publish, tmp = lists
    publish("cancellation_pages", 3)
    out = pl.refresh("cancellation_pages")
    assert out["ok"] and out["version"] == 3
    assert pl.cached("cancellation_pages")["data"]["items"][0]["company"] == "Example Co"
    log = [json.loads(x) for x in (tmp / "egress.jsonl").read_text().splitlines()]
    assert log[-1]["provider"] == "lists.example.test" and log[-1]["action"] == "allow"


def test_a_rollback_a_wrong_name_or_an_expired_list_is_refused_and_the_cache_kept(lists):
    pl, publish, _tmp = lists
    publish("cancellation_pages", 5)
    assert pl.refresh("cancellation_pages")["ok"]
    publish("cancellation_pages", 4)
    out = pl.refresh("cancellation_pages")
    assert not out["ok"] and "older" in out["message"]
    publish("cancellation_pages", 6, as_name="broker_registries")
    assert "not 'cancellation_pages'" in pl.refresh("cancellation_pages")["message"]
    publish("cancellation_pages", 7, expires="2020-01-01")
    assert "expired" in pl.refresh("cancellation_pages")["message"]
    assert pl.cached("cancellation_pages")["version"] == 5


def test_an_edited_cache_is_not_used(lists):
    pl, publish, tmp = lists
    publish("platform_exports", 1)
    pl.refresh("platform_exports")
    p = tmp / "public_lists" / "platform_exports.json"
    p.write_text(json.dumps({"items": [{"company": "Evil", "url": "https://evil.example"}]}))
    assert pl.cached("platform_exports") is None


def test_nothing_is_fetched_without_the_update_answer_a_key_or_a_published_list(lists, monkeypatch):
    pl, publish, _tmp = lists
    from agent_friday.services import trust_keys, update_check
    publish("retailer_windows", 1)
    monkeypatch.setattr(update_check, "is_enabled", lambda: False)
    assert "is off" in pl.refresh("retailer_windows")["message"]
    monkeypatch.setattr(update_check, "is_enabled", lambda: True)
    monkeypatch.setattr(trust_keys, "PUBLIC_LIST_KEYS", ())
    assert "No signing key" in pl.refresh("retailer_windows")["message"]
    monkeypatch.setattr(pl, "DEFAULT_BASE_URL", "")
    assert "hasn't published" in pl.refresh("retailer_windows")["message"]
    assert "settlements" not in pl.LISTS, "settlements are deferred (decision O3)"


# ── the release manifest ─────────────────────────────────────────────────────

@pytest.fixture
def release(monkeypatch):
    from agent_friday.services import trust_keys, update_check
    sk, kid, pub = _keypair()
    monkeypatch.setattr(trust_keys, "RELEASE_KEYS", (pub,))
    served = {}
    monkeypatch.setattr(update_check, "FETCH_ASSET", lambda url, cap: served[url])

    def make(version="1.2.0", seq=120, expires="2099-01-01", sign_version=None, key=None,
             comment_name="agent-friday-release"):
        manifest = json.dumps({"name": "agent-friday-release", "version": sign_version or version,
                               "build_sequence": seq, "expires": expires,
                               "files": {"AgentFriday-Setup.exe": "0" * 64}}).encode()
        s_sk, s_kid = key or (sk, kid)
        served["https://dl/m.json"] = manifest
        served["https://dl/m.json.minisig"] = _sign(s_sk, s_kid, manifest, "name=%s" % comment_name).encode()
        return {"tag_name": "v" + version, "assets": [
            {"name": "release-manifest.json", "browser_download_url": "https://dl/m.json"},
            {"name": "release-manifest.json.minisig", "browser_download_url": "https://dl/m.json.minisig"}]}
    return update_check, make


def test_a_signed_current_manifest_verifies(release):
    uc, make = release
    out = uc.verify_release(make(), current_sequence=110, today=date(2026, 10, 15))
    assert out["state"] == "verified" and "signed by FutureSpeak" in out["words"]


def test_a_downgrade_an_expired_or_a_mismatched_manifest_is_refused(release):
    uc, make = release
    assert uc.verify_release(make(seq=100), current_sequence=110)["state"] == "downgrade"
    assert uc.verify_release(make(expires="2020-01-01"), current_sequence=110)["state"] == "expired"
    assert uc.verify_release(make(sign_version="1.1.0"), current_sequence=110)["state"] == "mismatch"
    sk2, kid2, _pub2 = _keypair()
    assert uc.verify_release(make(key=(sk2, kid2)), current_sequence=110)["state"] == "bad_signature"


def test_a_release_without_a_manifest_is_announced_as_unsigned(release):
    uc, _make = release
    out = uc.verify_release({"tag_name": "v1.2.0", "assets": []}, current_sequence=110)
    assert out["state"] == "unsigned" and "SHA-256" in out["words"]


def test_a_refused_manifest_means_no_update_is_recommended(release, monkeypatch):
    uc, make = release
    rel = make(seq=100)

    class Resp:
        status_code = 200

        def json(self):
            return [dict(rel, html_url="https://example.test/r", draft=False, prerelease=False,
                         body="Build sequence: 120")]
    import requests
    monkeypatch.setattr(requests, "get", lambda *a, **k: Resp())
    monkeypatch.setattr(uc, "running_version", lambda: "1.1.0")
    monkeypatch.setattr(uc, "sequence_for_version", lambda v: 110)
    monkeypatch.setattr(uc, "sequence_of_release", lambda r: 120)
    out = uc.check_for_update()
    assert out["update_available"] is False and out["reason"] == "release_not_verified"


def test_the_release_manifest_script_writes_what_the_check_reads(tmp_path):
    import importlib.util
    from pathlib import Path
    spec = importlib.util.spec_from_file_location(
        "release_manifest", Path(__file__).resolve().parents[2] / "scripts" / "release_manifest.py")
    rm = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(rm)
    f = tmp_path / "AgentFriday-Setup-1.2.0.exe"
    f.write_bytes(b"installer bytes")
    m = rm.build("v1.2.0", [str(f)], today=date(2026, 10, 15))
    assert m["version"] == "1.2.0" and m["expires"] == "2027-10-15"
    assert m["files"] == {f.name: hashlib.sha256(b"installer bytes").hexdigest()}
    assert isinstance(m["build_sequence"], int)


def test_with_a_release_key_a_missing_manifest_is_refused_not_waved_through(release):
    uc, make = release
    assert "unsigned" in uc.refused_states() and "unreachable" in uc.refused_states()
    out = uc.verify_release({"tag_name": "v1.2.0", "assets": []}, current_sequence=110)
    assert out["state"] in uc.refused_states()


def test_a_signature_for_something_else_or_without_a_build_number_is_refused(release):
    uc, make = release
    assert uc.verify_release(make(comment_name="cancellation_pages"), current_sequence=110)["state"] == "bad_signature"
    assert uc.verify_release(make(seq="120"), current_sequence=110)["state"] == "mismatch"


def test_losing_the_cache_does_not_reopen_an_older_list(lists):
    pl, publish, tmp = lists
    publish("broker_registries", 9)
    assert pl.refresh("broker_registries")["ok"]
    for f in ("broker_registries.json", "broker_registries.json.minisig"):
        (tmp / "public_lists" / f).unlink()
    publish("broker_registries", 3)
    out = pl.refresh("broker_registries")
    assert not out["ok"] and "older" in out["message"]

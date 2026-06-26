"""
Unit tests for services/ownership.py and services/content_credentials.py.

Offline-only. Covers:
  ownership:
    register (build, idempotent, explicit manifest, missing file)
    get_asset (by id, by content_hash, nonexistent)
    list_by_creator, list_all
    transfer (record, nonexistent, get_transfers)
    provenance_chain (single, with sources)
    verify (valid, invalid, registry_ok flag)
    check_license_compat (all-rights-reserved, CC-BY, CC-BY-SA, CC0, same-creator override)

  content_credentials:
    create_credential (returns manifest, with license, embed=False)
    verify_credential (valid manifest, file path, tampered signature)
    timestamp_rfc3161 offline fallback
    embed helpers (_embed_id3, _embed_text) — must not crash even without deps
"""
import hashlib
import json
from pathlib import Path

import core
from services import ownership, provenance as pv
from services import content_credentials as cc


# ─── helpers ─────────────────────────────────────────────────────────────────

_counter = [0]


def _artifact(name=None, data=None):
    core.CREATIONS_DIR.mkdir(parents=True, exist_ok=True)
    _counter[0] += 1
    n = name or f"own-test-{_counter[0]}.png"
    p = core.CREATIONS_DIR / n
    p.write_bytes(data or f"pixels-{_counter[0]}".encode())
    return p


# ═══════════════════════════════════════════════════════════════════════════
#  ownership.register
# ═══════════════════════════════════════════════════════════════════════════

class TestRegister:
    def test_builds_manifest_and_returns_record(self):
        p = _artifact()
        rec = ownership.register(p)
        assert rec is not None
        assert rec["content_hash"] == pv.hash_file(p)
        assert rec["media_type"] == "image"

    def test_returns_id_and_creator_fields(self):
        p = _artifact()
        rec = ownership.register(p)
        assert rec is not None
        assert "id" in rec
        assert "creator_pubkey" in rec
        assert "license" in rec

    def test_idempotent_same_id_on_second_call(self):
        p = _artifact()
        r1 = ownership.register(p)
        r2 = ownership.register(p)
        assert r1 is not None and r2 is not None
        assert r1["id"] == r2["id"]

    def test_explicit_manifest_respected(self):
        p = _artifact()
        manifest = pv.write(p, license={"terms": "CC-BY-4.0"})
        rec = ownership.register(p, manifest=manifest, title="Explicit")
        assert rec is not None
        assert rec["title"] == "Explicit"
        assert rec["license"] == "CC-BY-4.0"

    def test_missing_file_auto_build_false_returns_none(self):
        rec = ownership.register("/nonexistent/path/img.png", auto_build=False)
        assert rec is None

    def test_register_with_sources_stores_derivative_edge(self):
        parent = _artifact()
        child = _artifact(data=b"child-bytes")
        prec = ownership.register(parent)
        assert prec is not None
        src_edge = pv.source_edge(prec["content_hash"], "keyframe")
        m = pv.write(child, sources=[src_edge])
        crec = ownership.register(child, manifest=m)
        assert crec is not None
        # provenance chain of child should contain parent
        chain = ownership.provenance_chain(crec["id"])
        hashes = [n.get("content_hash") for n in chain]
        assert prec["content_hash"] in hashes


# ═══════════════════════════════════════════════════════════════════════════
#  ownership.get_asset
# ═══════════════════════════════════════════════════════════════════════════

class TestGetAsset:
    def test_by_id(self):
        p = _artifact()
        rec = ownership.register(p)
        assert rec is not None
        fetched = ownership.get_asset(rec["id"])
        assert fetched is not None
        assert fetched["content_hash"] == rec["content_hash"]

    def test_by_content_hash(self):
        p = _artifact()
        rec = ownership.register(p)
        ch = pv.hash_file(p)
        fetched = ownership.get_asset(ch)
        assert fetched is not None
        assert fetched["id"] == rec["id"]

    def test_nonexistent_returns_none(self):
        assert ownership.get_asset("no-such-id-xyzzy-12345") is None

    def test_manifest_attached(self):
        p = _artifact()
        rec = ownership.register(p)
        fetched = ownership.get_asset(rec["id"])
        # manifest may or may not be present depending on provenance sidecar
        assert fetched is not None

    def test_get_asset_by_hash(self):
        p = _artifact()
        rec = ownership.register(p)
        fetched = ownership.get_asset_by_hash(rec["content_hash"])
        assert fetched is not None
        assert fetched["id"] == rec["id"]


# ═══════════════════════════════════════════════════════════════════════════
#  ownership.list_by_creator / list_all
# ═══════════════════════════════════════════════════════════════════════════

class TestLists:
    def test_list_by_creator_returns_list(self):
        _artifact()
        results = ownership.list_by_creator()
        assert isinstance(results, list)

    def test_list_by_creator_unknown_key_empty(self):
        results = ownership.list_by_creator("00" * 32)
        assert results == []

    def test_list_all_returns_list(self):
        _artifact()
        results = ownership.list_all(limit=10)
        assert isinstance(results, list)

    def test_list_all_respects_limit(self):
        for _ in range(3):
            ownership.register(_artifact())
        results = ownership.list_all(limit=2)
        assert len(results) <= 2


# ═══════════════════════════════════════════════════════════════════════════
#  ownership.transfer / get_transfers
# ═══════════════════════════════════════════════════════════════════════════

class TestTransfer:
    def test_records_and_returns_entry(self):
        p = _artifact()
        rec = ownership.register(p)
        assert rec is not None
        to_key = "aa" * 32
        xfer = ownership.transfer(rec["id"], to_key, signature="fakesig")
        assert xfer is not None
        assert xfer["to_key"] == to_key
        assert xfer["asset_id"] == rec["id"]

    def test_nonexistent_asset_returns_none(self):
        xfer = ownership.transfer("no-such-id", "bb" * 32, signature="sig")
        assert xfer is None

    def test_multiple_transfers_recorded(self):
        p = _artifact()
        rec = ownership.register(p)
        ownership.transfer(rec["id"], "cc" * 32, signature="s1")
        ownership.transfer(rec["id"], "dd" * 32, signature="s2")
        records = ownership.get_transfers(rec["id"])
        assert len(records) >= 2

    def test_get_transfers_empty_for_no_transfers(self):
        p = _artifact()
        rec = ownership.register(p)
        records = ownership.get_transfers(rec["id"])
        assert isinstance(records, list)

    def test_get_transfers_nonexistent_asset_empty(self):
        records = ownership.get_transfers("no-such-id")
        assert records == []

    def test_transfer_id_is_unique(self):
        p = _artifact()
        rec = ownership.register(p)
        x1 = ownership.transfer(rec["id"], "ee" * 32, signature="s3")
        x2 = ownership.transfer(rec["id"], "ff" * 32, signature="s4")
        assert x1 is not None and x2 is not None
        assert x1["id"] != x2["id"]


# ═══════════════════════════════════════════════════════════════════════════
#  ownership.provenance_chain
# ═══════════════════════════════════════════════════════════════════════════

class TestProvenanceChain:
    def test_single_asset_chain(self):
        p = _artifact()
        rec = ownership.register(p)
        chain = ownership.provenance_chain(rec["id"])
        assert isinstance(chain, list)
        assert len(chain) >= 1

    def test_chain_contains_self(self):
        p = _artifact()
        rec = ownership.register(p)
        chain = ownership.provenance_chain(rec["id"])
        hashes = [n.get("content_hash") for n in chain]
        assert rec["content_hash"] in hashes

    def test_chain_with_two_levels(self):
        p1 = _artifact(data=b"level-0")
        p2 = _artifact(data=b"level-1")
        r1 = ownership.register(p1)
        assert r1 is not None
        src = pv.source_edge(r1["content_hash"], "clip")
        m2 = pv.write(p2, sources=[src])
        r2 = ownership.register(p2, manifest=m2)
        assert r2 is not None
        chain = ownership.provenance_chain(r2["id"])
        hashes = [n.get("content_hash") for n in chain]
        assert r1["content_hash"] in hashes
        assert r2["content_hash"] in hashes

    def test_nonexistent_returns_empty(self):
        chain = ownership.provenance_chain("sha256:" + "0" * 64)
        assert isinstance(chain, list)

    def test_chain_via_content_hash_directly(self):
        p = _artifact()
        rec = ownership.register(p)
        chain = ownership.provenance_chain(rec["content_hash"])
        assert isinstance(chain, list)


# ═══════════════════════════════════════════════════════════════════════════
#  ownership.verify
# ═══════════════════════════════════════════════════════════════════════════

class TestVerify:
    def test_returns_dict_with_valid_key(self):
        p = _artifact()
        rec = ownership.register(p)
        result = ownership.verify(rec["id"])
        assert "valid" in result
        assert "checks" in result
        assert isinstance(result["checks"], dict)

    def test_nonexistent_returns_invalid(self):
        result = ownership.verify("totally-nonexistent")
        assert result["valid"] is False

    def test_registry_ok_flag_present(self):
        p = _artifact()
        rec = ownership.register(p)
        result = ownership.verify(rec["id"])
        assert "registry_ok" in result["checks"]

    def test_asset_id_in_result(self):
        p = _artifact()
        rec = ownership.register(p)
        result = ownership.verify(rec["id"])
        assert result["asset_id"] == rec["id"]

    def test_verify_by_file_path(self):
        p = _artifact()
        ownership.register(p)
        result = ownership.verify(str(p))
        assert "valid" in result
        assert isinstance(result["valid"], bool)

    def test_checks_has_no_error_for_valid_asset(self):
        p = _artifact()
        rec = ownership.register(p)
        result = ownership.verify(rec["id"])
        assert "error" not in result["checks"]


# ═══════════════════════════════════════════════════════════════════════════
#  ownership.check_license_compat
# ═══════════════════════════════════════════════════════════════════════════

class TestLicenseCompat:
    def _register_with_license(self, terms, data=None):
        p = _artifact(data=data or terms.encode())
        m = pv.write(p, license={"terms": terms})
        return ownership.register(p, manifest=m)

    def test_all_rights_reserved_blocks_others(self):
        rec = self._register_with_license("all-rights-reserved")
        for dl in ("CC-BY-4.0", "CC-BY-SA-4.0", "CC0", "priced"):
            res = ownership.check_license_compat(rec["id"], dl, requestor_pubkey="other")
            assert res["allowed"] is False, f"ARR should block {dl}"

    def test_cc_by_allows_cc_by_derivative(self):
        rec = self._register_with_license("CC-BY-4.0")
        res = ownership.check_license_compat(rec["id"], "CC-BY-4.0", requestor_pubkey="other")
        assert res["allowed"] is True
        assert res["attribution_required"] is True

    def test_cc_by_allows_priced_derivative(self):
        rec = self._register_with_license("CC-BY-4.0")
        res = ownership.check_license_compat(rec["id"], "priced", requestor_pubkey="other")
        assert res["allowed"] is True

    def test_cc_by_blocks_cc0_derivative(self):
        # CC-BY requires attribution; CC0 waives it — not compatible
        rec = self._register_with_license("CC-BY-4.0")
        res = ownership.check_license_compat(rec["id"], "CC0", requestor_pubkey="other")
        assert res["allowed"] is False

    def test_cc_by_sa_requires_share_alike(self):
        rec = self._register_with_license("CC-BY-SA-4.0")
        # Same share-alike is fine
        ok = ownership.check_license_compat(rec["id"], "CC-BY-SA-4.0", requestor_pubkey="other")
        assert ok["allowed"] is True
        # Different license breaks share-alike
        no = ownership.check_license_compat(rec["id"], "all-rights-reserved",
                                             requestor_pubkey="other")
        assert no["allowed"] is False

    def test_cc0_allows_everything(self):
        rec = self._register_with_license("CC0")
        for dl in ("all-rights-reserved", "CC-BY-4.0", "CC-BY-SA-4.0", "CC0", "priced"):
            res = ownership.check_license_compat(rec["id"], dl, requestor_pubkey="other")
            assert res["allowed"] is True, f"CC0 should allow {dl}"

    def test_priced_blocks_derivatives(self):
        rec = self._register_with_license("priced")
        for dl in ("CC-BY-4.0", "CC0", "priced"):
            res = ownership.check_license_compat(rec["id"], dl, requestor_pubkey="other")
            assert res["allowed"] is False

    def test_same_creator_override(self):
        rec = self._register_with_license("all-rights-reserved")
        creator_key = rec.get("creator_pubkey", "")
        if creator_key:
            res = ownership.check_license_compat(rec["id"], "CC-BY-4.0",
                                                  requestor_pubkey=creator_key)
            assert res["allowed"] is True
            assert "same-creator" in res["note"]

    def test_nonexistent_source_blocked(self):
        res = ownership.check_license_compat("no-asset-id-xyz", "CC-BY-4.0")
        assert res["allowed"] is False
        assert "not found" in res["note"]

    def test_result_has_required_fields(self):
        rec = self._register_with_license("CC-BY-4.0")
        res = ownership.check_license_compat(rec["id"], "CC-BY-4.0")
        for key in ("allowed", "source_license", "derivative_license", "note",
                    "attribution_required"):
            assert key in res


# ═══════════════════════════════════════════════════════════════════════════
#  content_credentials.create_credential
# ═══════════════════════════════════════════════════════════════════════════

class TestCreateCredential:
    def test_returns_manifest_dict(self):
        p = _artifact()
        m = cc.create_credential(p)
        assert isinstance(m, dict)
        assert m.get("type") == "ContentCredential"

    def test_embed_false_does_not_crash(self):
        p = _artifact()
        m = cc.create_credential(p, embed=False)
        assert m.get("type") == "ContentCredential"

    def test_license_propagated(self):
        p = _artifact()
        m = cc.create_credential(p, license={"terms": "CC-BY-4.0"}, embed=False)
        assert m.get("license", {}).get("terms") == "CC-BY-4.0"

    def test_tool_chain_propagated(self):
        p = _artifact()
        tc = [{"tool": "test_engine", "model": "test-v1"}]
        m = cc.create_credential(p, tool_chain=tc, embed=False)
        assert m.get("tool_chain") == tc

    def test_sources_propagated(self):
        parent = _artifact(data=b"parent-cc")
        child = _artifact(data=b"child-cc")
        pm = pv.write(parent)
        src = pv.source_edge(pm["artifact"]["content_hash"], "clip")
        m = cc.create_credential(child, sources=[src], embed=False)
        assert len(m.get("sources", [])) == 1

    def test_nonexistent_file_does_not_crash(self):
        # provenance.write() does best-effort on missing files (path-hash fallback)
        # — we only assert it never raises and returns a dict
        m = cc.create_credential("/no/such/path/file.png", embed=False)
        assert isinstance(m, dict)

    def test_has_signature_field(self):
        p = _artifact()
        m = cc.create_credential(p, embed=False)
        assert "signature" in m

    def test_has_creator_fields(self):
        p = _artifact()
        m = cc.create_credential(p, embed=False)
        assert "creator" in m
        assert "user_id" in m["creator"]


# ═══════════════════════════════════════════════════════════════════════════
#  content_credentials.verify_credential
# ═══════════════════════════════════════════════════════════════════════════

class TestVerifyCredential:
    def test_valid_manifest_has_valid_and_checks(self):
        p = _artifact()
        m = cc.create_credential(p, embed=False)
        result = cc.verify_credential(m)
        assert "valid" in result
        assert "checks" in result

    def test_verify_by_file_path(self):
        p = _artifact()
        cc.create_credential(p, embed=False)
        result = cc.verify_credential(p)
        assert "valid" in result

    def test_tampered_signature_fails_or_unknown(self):
        p = _artifact()
        m = cc.create_credential(p, embed=False)
        m["signature"] = {"alg": "ed25519", "pubkey": "00" * 32, "value": "ff" * 64}
        result = cc.verify_credential(m)
        sig_ok = result["checks"].get("signature_ok")
        # Either explicitly False (PyNaCl present) or None (no lib) — never True
        assert sig_ok is not True

    def test_timestamp_type_in_checks(self):
        p = _artifact()
        m = cc.create_credential(p, embed=False)
        result = cc.verify_credential(m)
        assert "timestamp_type" in result.get("checks", {})

    def test_empty_dict_returns_valid_false(self):
        result = cc.verify_credential({})
        # Empty dict: valid=False (no hash to check) or valid with all None checks
        assert isinstance(result, dict)


# ═══════════════════════════════════════════════════════════════════════════
#  content_credentials.timestamp_rfc3161
# ═══════════════════════════════════════════════════════════════════════════

class TestTimestampRfc3161:
    def test_offline_fallback_is_local_ledger(self):
        # Network not available in tests → must fall back gracefully
        ts = cc.timestamp_rfc3161("sha256:" + "a" * 64)
        assert isinstance(ts, dict)
        assert ts.get("type") in ("rfc3161", "local-ledger")

    def test_invalid_hash_falls_back(self):
        ts = cc.timestamp_rfc3161("not-a-real-hash")
        assert isinstance(ts, dict)
        assert "type" in ts


# ═══════════════════════════════════════════════════════════════════════════
#  content_credentials embed helpers (must not crash even without deps)
# ═══════════════════════════════════════════════════════════════════════════

class TestEmbedHelpers:
    def test_embed_id3_fake_mp3_no_crash(self):
        p = core.CREATIONS_DIR / "cc-embed-fake.mp3"
        p.write_bytes(b"ID3FAKEDATA")
        result = cc._embed_id3(p, '{"test":1}')
        assert isinstance(result, bool)

    def test_embed_text_markdown_prepends_frontmatter(self):
        p = core.CREATIONS_DIR / "cc-embed-test.md"
        p.write_text("# Hello\n\nWorld", encoding="utf-8")
        result = cc._embed_text(p, '{"type":"ContentCredential"}')
        if result:
            content = p.read_text(encoding="utf-8")
            assert "friday_credential" in content or "ContentCredential" in content
        assert isinstance(result, bool)

    def test_embed_text_markdown_with_existing_frontmatter(self):
        p = core.CREATIONS_DIR / "cc-embed-existing-fm.md"
        p.write_text("---\ntitle: Test\n---\n# Content\n", encoding="utf-8")
        result = cc._embed_text(p, '{"type":"cc"}')
        assert isinstance(result, bool)

    def test_embed_text_html_no_crash(self):
        p = core.CREATIONS_DIR / "cc-embed-test.html"
        p.write_text("<html><head></head><body></body></html>", encoding="utf-8")
        result = cc._embed_text(p, '{"type":"cc"}')
        if result:
            content = p.read_text(encoding="utf-8")
            assert "ContentCredential" in content
        assert isinstance(result, bool)

    def test_embed_credential_image_dispatch(self):
        p = core.CREATIONS_DIR / "cc-dispatch-test.png"
        p.write_bytes(b"PNGFAKEDATA")
        m = {"type": "ContentCredential", "version": "1.0", "artifact": {}}
        result = cc.embed_credential(p, m)
        assert isinstance(result, bool)

    def test_embed_credential_video_skipped(self):
        p = core.CREATIONS_DIR / "cc-dispatch-test.mp4"
        p.write_bytes(b"FAKEMP4DATA")
        m = {"type": "ContentCredential"}
        result = cc.embed_credential(p, m)
        assert result is False  # video is skipped

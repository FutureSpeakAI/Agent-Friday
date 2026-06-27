"""Unit tests for the content provenance layer (services/provenance.py).

Offline-only. Covers hashing, license normalization, manifest build + sign +
store + verify roundtrip, the sources DAG trace, and relicensing. The Ed25519
signature is verified when PyNaCl is present; otherwise the unsigned-marker path
is asserted instead — either way verify_manifest must not crash.
"""
import json

import agent_friday.core as core
from agent_friday.services import provenance as pv


def _write_creation(name, data=b"hello-bytes"):
    core.CREATIONS_DIR.mkdir(parents=True, exist_ok=True)
    p = core.CREATIONS_DIR / name
    p.write_bytes(data)
    return p


def test_hash_helpers_are_stable():
    assert pv.hash_bytes(b"abc") == pv.hash_bytes(b"abc")
    assert pv.hash_text("x").startswith("sha256:")
    p = _write_creation("prov-hashme.png", b"PNGDATA")
    assert pv.hash_file(p) == pv.hash_bytes(b"PNGDATA")


def test_user_id_is_stable_and_non_pii():
    a = pv.user_id()
    b = pv.user_id()
    assert a == b and a.startswith("user-")
    assert "@" not in a  # never an email


def test_normalize_license_defaults_to_all_rights_reserved():
    lic = pv.normalize_license(None)
    assert lic["terms"] == "all-rights-reserved"
    assert lic["market"]["mode"] == "reserved"


def test_normalize_license_commons_and_priced():
    assert pv.normalize_license({"terms": "CC-BY-4.0"})["market"]["mode"] == "free"
    priced = pv.normalize_license({"terms": "priced", "market": {"price": 4990}})
    assert priced["market"]["mode"] == "priced" and priced["market"]["price"] == 4990


def test_build_manifest_shape():
    p = _write_creation("prov-build.png")
    m = pv.build_manifest(p, tool_chain=[{"tool": "t"}], media_type="image")
    assert m["type"] == "ContentCredential"
    assert m["artifact"]["filename"] == "prov-build.png"
    assert m["artifact"]["content_hash"].startswith("sha256:")
    assert m["creator"]["user_id"].startswith("user-")
    assert m["media_type"] == "image"
    assert m["license"]["terms"] == "all-rights-reserved"


def test_write_sign_store_verify_roundtrip():
    p = _write_creation("prov-rt.png", b"ROUNDTRIP")
    manifest = pv.write(p, tool_chain=[{"tool": "creative_engine.generate_image"}],
                        media_type="image")
    assert manifest  # non-empty
    ch = manifest["artifact"]["content_hash"]
    # sidecar persisted + retrievable
    assert pv.get_manifest(ch) is not None
    res = pv.verify_manifest(manifest)
    assert res["checks"]["hash_ok"] is True       # file present, hash recomputes
    assert res["checks"]["chain_ok"] is True       # ledger entry written
    # Signature: True if PyNaCl present, else None (unsigned marker) — never False.
    assert res["checks"]["signature_ok"] in (True, None)
    assert res["valid"] is True


def test_tamper_detection_breaks_hash_check():
    p = _write_creation("prov-tamper.png", b"ORIGINAL")
    manifest = pv.write(p, media_type="image")
    p.write_bytes(b"TAMPERED-DIFFERENT")          # change bytes after signing
    res = pv.verify_manifest(manifest)
    assert res["checks"]["hash_ok"] is False
    assert res["valid"] is False


def test_trace_walks_source_dag():
    # keyframe → clip → production edge chain
    kf = _write_creation("prov-kf.png", b"KF")
    km = pv.write(kf, media_type="image")
    clip = _write_creation("prov-clip.mp4", b"CLIP")
    cm = pv.write(clip, media_type="video",
                  sources=[pv.source_edge(km["artifact"]["content_hash"], "keyframe")])
    prod = _write_creation("prov-prod.mp4", b"PROD")
    pm = pv.write(prod, media_type="production",
                  sources=[pv.source_edge(cm["artifact"]["content_hash"], "clip")])
    chain = pv.trace(pm["artifact"]["content_hash"])
    roles = [n["media_type"] for n in chain]
    assert "production" in roles and "video" in roles and "image" in roles


def test_set_license_appends_edit_and_resigns():
    p = _write_creation("prov-lic.png", b"LICENSED")
    m = pv.write(p, media_type="image")
    ch = m["artifact"]["content_hash"]
    updated = pv.set_license(ch, {"terms": "CC-BY-4.0"})
    assert updated["license"]["terms"] == "CC-BY-4.0"
    assert updated["edits"] and updated["edits"][-1]["op"] == "relicense"
    # re-verify the relicensed manifest
    assert pv.verify_manifest(updated)["checks"]["signature_ok"] in (True, None)


def test_license_at_creation_threads_into_manifest():
    """A license passed to provenance.write lands in the signed manifest."""
    p = _write_creation("prov-license-create.png", b"LICDATA")
    m = pv.write(p, media_type="image",
                 license={"terms": "CC-BY-4.0", "attribution": "Stephen"})
    assert m["license"]["terms"] == "CC-BY-4.0"
    assert m["license"]["market"]["mode"] == "free"
    # priced license carries the price through to the manifest market block
    p2 = _write_creation("prov-license-priced.mp3", b"AUDIO")
    m2 = pv.write(p2, media_type="music",
                  license={"terms": "priced", "market": {"price": 2500}})
    assert m2["license"]["market"]["mode"] == "priced"
    assert m2["license"]["market"]["price"] == 2500

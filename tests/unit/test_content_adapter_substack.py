"""Unit tests for the Substack assisted-handoff adapter (spec §4.10).

No network anywhere: publishing is package-building on local disk, and the
RSS confirmation poll goes through the module-level ``_http_get`` indirection
which every test stubs. Covers the handoff happy path, SENT→CONFIRMED via
attach_url and RSS auto-confirmation, the headless feature-flag stub (§4.14
honesty), the email-clip warning, the credential-blob round trip against a
fake credential store, and fixed content-free error strings (§12.5).
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

_SRC = Path(__file__).resolve().parent.parent.parent / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from agent_friday.services import platforms as preg                 # noqa: E402
from agent_friday.services.platforms import base as pbase           # noqa: E402
from agent_friday.services.platforms import substack as smod        # noqa: E402
from agent_friday.services.platforms.substack import (              # noqa: E402
    ERR_HEADLESS_UNAVAILABLE, ERR_INVALID_URL, ERR_NOT_CONFIGURED,
    ERR_NOT_SUPPORTED, ERR_PACKAGE_WRITE, ERR_RSS_PARSE,
    ERR_RSS_UNAVAILABLE, ERR_UNKNOWN_HANDOFF, SubstackAdapter)

PUB = "https://mypub.substack.com"

RSS_FIXTURE = b"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel>
  <title>My Pub</title>
  <item><title>Hello World Post</title>
    <link>https://mypub.substack.com/p/hello-world</link></item>
  <item><title>Unrelated older piece</title>
    <link>https://mypub.substack.com/p/older</link></item>
</channel></rss>"""


@pytest.fixture(autouse=True)
def _iso(tmp_path, monkeypatch):
    monkeypatch.setattr(pbase, "PLATFORMS_DIR", tmp_path / "platforms")
    monkeypatch.setattr(pbase, "BUDGET_PATH",
                        tmp_path / "platforms" / "rate_budget.json")
    monkeypatch.setattr(preg, "FRIDAY_DIR", tmp_path)
    monkeypatch.setattr(preg, "CONFIG_PATH", tmp_path / "platforms.json")
    preg._reset_for_tests()
    yield
    preg._reset_for_tests()


@pytest.fixture(autouse=True)
def _no_transport(monkeypatch):
    """Any un-stubbed transport attempt fails loudly (zero live network)."""
    def _blocked(*a, **k):
        raise RuntimeError("network blocked in unit tests")
    monkeypatch.setattr(smod, "_http_get", _blocked)


@pytest.fixture
def fake_credstore(monkeypatch):
    """In-memory/plain-file credential store double + captured audit trail."""
    from agent_friday.services import credential_store as cs
    events = []
    stored = {}

    def _write(path, data):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        Path(path).write_bytes(data)
        return "fake-vault"

    monkeypatch.setattr(cs, "write_secret", _write)
    monkeypatch.setattr(cs, "read_secret", lambda p: Path(p).read_bytes())
    monkeypatch.setattr(cs, "audit_event",
                        lambda cat, ev, **f: events.append((cat, ev, f)))
    monkeypatch.setattr(cs, "set_provider_key",
                        lambda prov, val: (stored.__setitem__(prov, val),
                                           "fake-vault")[1])
    monkeypatch.setattr(cs, "get_provider_key", lambda prov: stored.get(prov))
    monkeypatch.setattr(cs, "delete_provider_key",
                        lambda prov: stored.pop(prov, None) is not None)
    return {"events": events, "stored": stored}


def _adapter(**cfg) -> SubstackAdapter:
    a = SubstackAdapter()
    a.configure(cfg)
    return a


def _publish_one(a, title="Hello World Post", body="# Hello\n\nBody text."):
    prep = a.prepare({"adapted_title": title, "adapted_body": body,
                      "format": "article", "id": "tgt_1",
                      "options": {"subtitle": "A sub"}},
                     {"id": "post_1", "title": "fallback title"})
    assert prep["ok"], prep
    res = a.publish(prep["prepared"])
    assert res["ok"], res
    return res


# ── registry + capability honesty ─────────────────────────────────────────────
def test_registry_picks_up_substack():
    a = preg.get_adapter("substack")
    assert a is not None, preg.import_error("substack")
    assert isinstance(a, SubstackAdapter)


def test_capabilities_honest_no_api():
    caps = _adapter().capabilities()
    assert "article" in caps["formats"]
    assert caps["char_limit"] is None            # long-form, no hard limit
    assert caps["analytics"] == "none"           # no API — never pretend
    assert caps["thread"] is False
    assert caps["native_delete"] is False
    assert caps["hashtags_max"] == 0
    assert caps["media"]["alt_text"] is True
    a = _adapter()
    assert a.auth_mode == "manual"
    assert a.automation_tier == "assisted_handoff"
    assert a.degradation_options() == ["clipboard"]
    assert a.connect_url("state123") is None     # nothing to OAuth into


def test_status_connected_means_configured():
    a = _adapter()
    st = a.status()
    assert st["connected"] is False and st["account"] is None
    a = _adapter(publication_url=PUB + "/")
    st = a.status()
    assert st["connected"] is True
    assert st["account"] == "mypub.substack.com"
    assert st["publication_url"] == PUB
    assert st["headless_enabled"] is False


# ── publish happy path — the export package (transport-free) ─────────────────
def test_publish_builds_package_and_reports_sent(tmp_path):
    a = _adapter(publication_url=PUB)
    res = _publish_one(a)
    assert res["post_url"] == PUB + "/publish/post"
    hid = res["platform_post_id"]
    assert hid.startswith("sub_")
    assert res["handoff"] is True and res["confirmed"] is False
    assert res["raw"]["status"] == "SENT"

    pkg = Path(res["raw"]["package_dir"])
    assert (pkg / "post.md").is_file()
    assert (pkg / "post.html").is_file()
    meta = json.loads((pkg / "meta.json").read_text(encoding="utf-8"))
    assert meta["title"] == "Hello World Post"
    assert meta["subtitle"] == "A sub"
    assert meta["status"] == "SENT"
    md = (pkg / "post.md").read_text(encoding="utf-8")
    assert md.startswith("---") and "Body text." in md
    assert "<h1>Hello</h1>" in (pkg / "post.html").read_text(encoding="utf-8")

    desc = res["raw"]["descriptor"]
    assert desc["action"] == "open_editor"
    assert desc["editor_url"] == PUB + "/publish/post"
    assert desc["copy_file"].endswith("post.md")

    # budget consumed once; handoff recorded as pending
    assert a.rate_budget()["used"] == 1
    pending = a.pending_handoffs()["pending"]
    assert [p["handoff_id"] for p in pending] == [hid]


def test_publish_without_publication_url_uses_file_uri():
    a = _adapter()
    res = _publish_one(a)
    assert res["post_url"].startswith("file://")


def test_publish_copies_assets_and_warns_on_missing(tmp_path):
    img = tmp_path / "i1.png"
    img.write_bytes(b"\x89PNG fake")
    a = _adapter(publication_url=PUB)
    prep = a.prepare({"adapted_title": "T", "adapted_body": "b",
                      "format": "article",
                      "adapted_assets": [
                          {"kind": "image", "out_path": str(img),
                           "alt_text": "pic"},
                          {"kind": "image",
                           "out_path": str(tmp_path / "missing.png"),
                           "alt_text": "gone"}]},
                     {"id": "post_1"})
    assert prep["ok"], prep
    res = a.publish(prep["prepared"])
    assert res["ok"], res
    pkg = Path(res["raw"]["package_dir"])
    assert (pkg / "images" / "i1.png").is_file()
    assert any("missing" in w for w in res["raw"]["warnings"])


def test_prepare_email_clip_warning_and_title_fallback():
    a = _adapter()
    res = a.prepare({"adapted_body": "x" * (smod.EMAIL_CLIP_BYTES + 10),
                     "format": "article"}, {"title": "Post Title"})
    assert res["ok"]
    assert any("clip" in w for w in res["warnings"])
    assert res["prepared"]["title"] == "Post Title"   # fell back to post title
    # no title anywhere → loud warning, never a silent untitled article
    res = a.prepare({"adapted_body": "b"}, {})
    assert any("title" in w for w in res["warnings"])


# ── SENT → CONFIRMED on URL attach (§4.10) ────────────────────────────────────
def test_attach_url_confirms_handoff():
    a = _adapter(publication_url=PUB)
    hid = _publish_one(a)["platform_post_id"]
    out = a.attach_url(hid, PUB + "/p/hello-world")
    assert out == {"ok": True, "handoff_id": hid,
                   "post_url": PUB + "/p/hello-world", "confirmed": True}
    assert a.pending_handoffs()["pending"] == []


def test_attach_url_rejects_garbage():
    a = _adapter(publication_url=PUB)
    hid = _publish_one(a)["platform_post_id"]
    assert a.attach_url(hid, "javascript:alert(1)")["error"] == ERR_INVALID_URL
    assert a.attach_url(hid, "not a url")["error"] == ERR_INVALID_URL
    assert a.attach_url("sub_nope", PUB + "/p/x")["error"] == ERR_UNKNOWN_HANDOFF
    # still pending — bad input never mutated the ledger
    assert len(a.pending_handoffs()["pending"]) == 1


def test_delete_retracts_pending_but_not_confirmed():
    a = _adapter(publication_url=PUB)
    res = _publish_one(a)
    hid = res["platform_post_id"]
    pkg = Path(res["raw"]["package_dir"])
    assert a.delete(hid) == {"ok": True, "deleted": True}
    assert not pkg.exists()
    assert a.pending_handoffs()["pending"] == []
    assert a.delete(hid) == {"ok": True, "deleted": False}   # idempotent

    hid2 = _publish_one(a, title="Second")["platform_post_id"]
    assert a.attach_url(hid2, PUB + "/p/second")["ok"]
    out = a.delete(hid2)                  # live on Substack — no takedown API
    assert out["ok"] is False and out["error"] == ERR_NOT_SUPPORTED


# ── RSS auto-confirmation (transport stubbed) ─────────────────────────────────
def test_poll_rss_confirms_matching_title(monkeypatch):
    a = _adapter(publication_url=PUB)
    hid = _publish_one(a, title="  hello   WORLD post ")["platform_post_id"]
    fetched = []
    monkeypatch.setattr(smod, "_http_get",
                        lambda url, timeout=15.0: (fetched.append(url),
                                                   RSS_FIXTURE)[1])
    out = a.poll_rss()
    assert fetched == [PUB + "/feed"]
    assert out["ok"] is True and out["checked"] == 2
    assert out["confirmed"] == [{"handoff_id": hid,
                                 "post_url": PUB + "/p/hello-world"}]
    assert out["pending"] == []
    assert a.pending_handoffs()["pending"] == []
    # a second poll has nothing left to check — and needs no transport
    monkeypatch.setattr(smod, "_http_get",
                        lambda *a_, **k: pytest.fail("no pending → no fetch"))
    assert a.poll_rss() == {"ok": True, "checked": 0,
                            "confirmed": [], "pending": []}


def test_poll_rss_unmatched_stays_pending(monkeypatch):
    a = _adapter(publication_url=PUB)
    hid = _publish_one(a, title="Totally Different Title")["platform_post_id"]
    monkeypatch.setattr(smod, "_http_get", lambda url, timeout=15.0: RSS_FIXTURE)
    out = a.poll_rss()
    assert out["ok"] is True and out["confirmed"] == []
    assert out["pending"] == [hid]


def test_poll_rss_error_paths_fixed_strings(monkeypatch):
    assert _adapter().poll_rss() == {"ok": False, "error": ERR_NOT_CONFIGURED}

    a = _adapter(publication_url=PUB)
    _publish_one(a)
    def _boom(url, timeout=15.0):
        raise OSError("connection refused by e.g. C:/private/path")
    monkeypatch.setattr(smod, "_http_get", _boom)
    out = a.poll_rss()
    assert out == {"ok": False, "error": ERR_RSS_UNAVAILABLE}   # content-free

    monkeypatch.setattr(smod, "_http_get",
                        lambda url, timeout=15.0: b"<<<not xml>>>")
    assert a.poll_rss() == {"ok": False, "error": ERR_RSS_PARSE}


# ── headless mode: STUB, feature-flagged OFF, never a silent rung-fall ───────
def test_headless_flag_default_off_and_stub_surfaces_choice():
    a = _adapter(publication_url=PUB)
    assert a._headless_flag() is False           # default OFF

    a = _adapter(publication_url=PUB, headless_enabled=True)
    prep = a.prepare({"adapted_body": "b", "adapted_title": "T",
                      "format": "article"}, {"id": "p1"})
    res = a.publish(prep["prepared"])
    assert res["ok"] is False
    assert res["error"] == ERR_HEADLESS_UNAVAILABLE
    assert res["degraded"] is True and res["requires_user_choice"] is True
    assert "assisted_handoff" in res["options"]
    # nothing was written and no budget burned — no silent fallback
    assert a.pending_handoffs()["pending"] == []
    assert a.rate_budget()["used"] == 0


# ── auth blob round trip (fake credential store) ──────────────────────────────
def test_credential_blob_round_trip_nothing_tokenish(fake_credstore, tmp_path):
    a = _adapter()
    sentinel = "OPAQUE-SESSION-MATERIAL-12345"
    blob = {"account": "writer@example.com", "session_blob": sentinel,
            "scopes": ["editor"], "expires_at": "2027-01-01T00:00:00Z"}
    out = a.save_credentials(blob)
    assert out == {"ok": True, "protection": "fake-vault"}
    assert a.load_credentials() == blob
    assert a.has_credentials() is True

    st = a.status()
    assert st["connected"] is True and st["account"] == "writer@example.com"
    assert sentinel not in json.dumps(st)        # nothing tokenish in status()

    assert a.clear_credentials()["ok"] is True
    assert a.load_credentials() is None
    events = [e for _, e, _ in fake_credstore["events"]]
    assert "credentials_stored" in events and "credentials_cleared" in events


# ── §12.5: publish failure externalizes a fixed content-free string ──────────
def test_publish_failure_error_is_content_free(tmp_path):
    blocker = tmp_path / "not-a-dir"
    blocker.write_text("file where the export dir must go", encoding="utf-8")
    a = _adapter(publication_url=PUB, export_dir=str(blocker))
    prep = a.prepare({"adapted_body": "b", "adapted_title": "T",
                      "format": "article"}, {"id": "p1"})
    res = a.publish(prep["prepared"])
    assert res == {"ok": False, "error": ERR_PACKAGE_WRITE}
    assert str(tmp_path) not in json.dumps(res)  # no paths leak outward
    assert a.status()["last_error"] == ERR_PACKAGE_WRITE

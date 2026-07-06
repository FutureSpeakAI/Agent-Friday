"""Unit tests for the Medium adapter (spec §4.11).

No network anywhere: all API transport goes through the module-level
``_http_json`` indirection which every test stubs, and the credential store
is replaced with an in-memory double so token feature-detection is fully
deterministic. Covers: feature detection (legacy token → v1 API, otherwise
Substack-style assisted handoff), the API publish happy path with
canonicalUrl + tag constraints (the platform's sharp edge), title-into-
content prepending, the auth blob round trip, honest §4.14 non-fallback on
auth failures, the headless stub, and fixed content-free error strings
(§12.5).
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
from agent_friday.services.platforms import medium as mmod          # noqa: E402
from agent_friday.services.platforms.medium import (                # noqa: E402
    ERR_API_ERROR, ERR_API_UNAVAILABLE, ERR_HEADLESS_UNAVAILABLE,
    ERR_INVALID_URL, ERR_NO_TOKEN, ERR_NOT_SUPPORTED, ERR_PACKAGE_WRITE,
    ERR_TOKEN_REJECTED, ERR_UNKNOWN_HANDOFF, MediumAdapter)

SECRET = "LEGACY-INTEGRATION-SECRET-2b9f"          # pragma: allowlist secret
AUTHOR_ID = "1f86c3a9e2"
CANONICAL = "https://blog.example.com/original-post"

ME_OK = (200, {"data": {"id": AUTHOR_ID, "username": "swriter",
                        "name": "S. Writer",
                        "url": "https://medium.com/@swriter"}})
POST_OK = (201, {"data": {"id": "e6f36a4b", "publishStatus": "public",
                          "url": "https://medium.com/@swriter/piece-e6f36a4b",
                          "authorId": AUTHOR_ID,
                          "canonicalUrl": CANONICAL}})


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
def credstore(monkeypatch):
    """In-memory credential-store double — token presence is deterministic
    (feature detection must never depend on the dev machine's real store)."""
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


@pytest.fixture(autouse=True)
def _no_transport(monkeypatch):
    """Any un-stubbed transport attempt fails loudly (zero live network)."""
    def _blocked(*a, **k):
        raise RuntimeError("network blocked in unit tests")
    monkeypatch.setattr(mmod, "_http_json", _blocked)


def _adapter(**cfg) -> MediumAdapter:
    a = MediumAdapter()
    a.configure(cfg)
    return a


def _arm_token(credstore) -> None:
    credstore["stored"]["platform_medium"] = SECRET


def _stub_http(monkeypatch, responses):
    """Queue-driven transport stub; records every call it serves."""
    calls = []

    def _fake(method, url, bearer, payload=None, timeout=15.0):
        calls.append({"method": method, "url": url, "bearer": bearer,
                      "payload": payload})
        return responses.pop(0)

    monkeypatch.setattr(mmod, "_http_json", _fake)
    return calls


def _prep(a, title="My Repurposed Piece", body="Body paragraph.", **kw):
    target = {"adapted_title": title, "adapted_body": body,
              "format": "article", "id": "tgt_1"}
    target.update(kw)
    prep = a.prepare(target, {"id": "post_1", "title": "fallback title"})
    assert prep["ok"], prep
    return prep["prepared"]


# ── registry + capability honesty (feature detection) ─────────────────────────
def test_registry_picks_up_medium():
    a = preg.get_adapter("medium")
    assert a is not None, preg.import_error("medium")
    assert isinstance(a, MediumAdapter)


def test_capabilities_honest_and_tier_feature_detects(credstore):
    a = _adapter()
    caps = a.capabilities()
    assert "article" in caps["formats"]
    assert caps["char_limit"] is None             # long-form, no hard limit
    assert caps["title_limit"] == 100             # v1 API SEO title cap
    assert caps["analytics"] == "none"            # §4.11 — never pretend
    assert caps["thread"] is False
    assert caps["native_schedule"] is False
    assert caps["native_delete"] is False
    assert caps["hashtags_max"] == 5
    assert caps["media"]["video"] is None         # embed only
    assert caps["media"]["alt_text"] is True
    assert a.auth_mode == "token"
    assert a.connect_url("state123") is None      # token-paste, no OAuth

    # no token → declared rung is assisted handoff (§4.14 honesty)
    assert a.automation_tier == "assisted_handoff"
    assert a.degradation_options() == ["clipboard"]
    st = a.status()
    assert st["connected"] is False and st["mode"] == "assisted_handoff"
    assert st["tier"] == "assisted_handoff"
    assert st["headless_enabled"] is False

    # token appears → the SAME instance flips to the API rung
    _arm_token(credstore)
    assert a.automation_tier == "api"
    assert a.degradation_options() == ["api_constrained", "assisted_handoff",
                                       "clipboard"]
    st = a.status()
    assert st["connected"] is True and st["mode"] == "api"
    assert st["tier"] == "api"
    assert any("v1 API" in n for n in a.capabilities()["notes"])


# ── no token → assisted handoff package (Substack pattern, §4.11) ────────────
def test_publish_without_token_builds_handoff_package():
    a = _adapter()
    prepared = _prep(a, hashtags=["#writing", "ai"],
                     options={"canonical_url": CANONICAL})
    assert prepared["mode"] == "assisted_handoff"
    res = a.publish(prepared)
    assert res["ok"], res
    hid = res["platform_post_id"]
    assert hid.startswith("med_")
    assert res["post_url"] == "https://medium.com/new-story"
    assert res["handoff"] is True and res["confirmed"] is False
    assert res["raw"]["status"] == "SENT"

    pkg = Path(res["raw"]["package_dir"])
    assert (pkg / "post.md").is_file()
    assert (pkg / "post.html").is_file()
    md = (pkg / "post.md").read_text(encoding="utf-8")
    assert md.startswith("---") and "Body paragraph." in md
    assert CANONICAL in md                        # canonical travels with pkg
    meta = json.loads((pkg / "meta.json").read_text(encoding="utf-8"))
    assert meta["title"] == "My Repurposed Piece"
    assert meta["canonical_url"] == CANONICAL
    assert meta["tags"] == ["writing", "ai"]
    assert meta["status"] == "SENT"

    desc = res["raw"]["descriptor"]
    assert desc["action"] == "open_editor"
    assert desc["editor_url"] == "https://medium.com/new-story"
    assert desc["canonical_url"] == CANONICAL
    assert desc["copy_file"].endswith("post.md")

    assert a.rate_budget()["used"] == 1
    pending = a.pending_handoffs()["pending"]
    assert [p["handoff_id"] for p in pending] == [hid]


def test_handoff_copies_assets_and_warns_on_missing(tmp_path):
    img = tmp_path / "cover.png"
    img.write_bytes(b"\x89PNG fake")
    a = _adapter()
    prepared = _prep(a, adapted_assets=[
        {"kind": "image", "out_path": str(img), "alt_text": "cover"},
        {"kind": "image", "out_path": str(tmp_path / "gone.png"),
         "alt_text": "gone"}])
    res = a.publish(prepared)
    assert res["ok"], res
    pkg = Path(res["raw"]["package_dir"])
    assert (pkg / "images" / "cover.png").is_file()
    assert any("missing" in w for w in res["raw"]["warnings"])


def test_attach_url_and_delete_handoff_semantics():
    a = _adapter()
    hid = a.publish(_prep(a))["platform_post_id"]
    assert a.attach_url(hid, "javascript:alert(1)")["error"] == ERR_INVALID_URL
    assert a.attach_url("med_nope", CANONICAL)["error"] == ERR_UNKNOWN_HANDOFF
    out = a.attach_url(hid, "https://medium.com/@swriter/piece-1")
    assert out == {"ok": True, "handoff_id": hid, "confirmed": True,
                   "post_url": "https://medium.com/@swriter/piece-1"}
    assert a.pending_handoffs()["pending"] == []
    # confirmed → live on Medium, no takedown API
    assert a.delete(hid) == {"ok": False, "error": ERR_NOT_SUPPORTED}

    hid2 = a.publish(_prep(a, title="Second"))["platform_post_id"]
    pkg2 = Path(a.pending_handoffs()["pending"][0]["package_dir"])
    assert a.delete(hid2) == {"ok": True, "deleted": True}
    assert not pkg2.exists()
    assert a.delete(hid2) == {"ok": True, "deleted": False}   # idempotent
    # an API-published id (not a handoff): no delete endpoint exists
    assert a.delete("e6f36a4b") == {"ok": False, "error": ERR_NOT_SUPPORTED}


# ── legacy token → full v1 API publish (transport stubbed) ────────────────────
def test_publish_with_token_uses_api_happy_path(credstore, monkeypatch):
    _arm_token(credstore)
    a = _adapter()
    calls = _stub_http(monkeypatch, [ME_OK, POST_OK])

    prepared = _prep(
        a,
        hashtags=["#AI", "ai", "Sovereign Computing Forever Yeah",
                  "two", "three", "four"],
        options={"canonical_url": CANONICAL, "publish_status": "draft",
                 "notify_followers": False, "license": "cc-40-by"})
    assert prepared["mode"] == "api"
    res = a.publish(prepared)
    assert res["ok"], res

    # transport: author lookup, then the user-posts endpoint
    assert [c["method"] for c in calls] == ["GET", "POST"]
    assert calls[0]["url"].endswith("/v1/me")
    assert calls[1]["url"].endswith(f"/v1/users/{AUTHOR_ID}/posts")
    payload = calls[1]["payload"]
    assert payload["title"] == "My Repurposed Piece"
    assert payload["contentFormat"] == "markdown"
    # visible title prepended into content (title field is SEO-only)
    assert payload["content"].startswith("# My Repurposed Piece\n\n")
    # tags: '#' stripped, case-dedupe, 25-char truncation, capped at 5
    assert payload["tags"] == ["AI", "Sovereign Computing Forev",
                               "two", "three", "four"]
    assert payload["canonicalUrl"] == CANONICAL   # §4.11 — the field matters
    assert payload["publishStatus"] == "draft"
    assert payload["notifyFollowers"] is False
    assert payload["license"] == "cc-40-by"

    assert res["post_url"] == "https://medium.com/@swriter/piece-e6f36a4b"
    assert res["platform_post_id"] == "e6f36a4b"
    assert set(res["raw"]) <= {"id", "url", "publishStatus",
                               "canonicalUrl", "license"}   # whitelist (§8.6)
    assert a.rate_budget()["used"] == 1
    assert a.pending_handoffs()["pending"] == []  # API mode: no handoff made
    # nothing tokenish escapes the adapter surface
    assert SECRET not in json.dumps(res) + json.dumps(a.status())

    # author id was cached in the blob — the next publish skips /me
    assert a.load_credentials()["author_id"] == AUTHOR_ID
    calls2 = _stub_http(monkeypatch, [POST_OK])
    assert a.publish(_prep(a, title="Again"))["ok"]
    assert [c["method"] for c in calls2] == ["POST"]


def test_api_content_title_rules(credstore, monkeypatch):
    _arm_token(credstore)
    a = _adapter()
    a.save_credentials({"account": "swriter", "author_id": AUTHOR_ID})

    calls = _stub_http(monkeypatch, [POST_OK])
    assert a.publish(_prep(a, title="T", body="# Already Titled\n\nbody"))["ok"]
    assert calls[0]["payload"]["content"] == "# Already Titled\n\nbody"

    calls = _stub_http(monkeypatch, [POST_OK])
    assert a.publish(_prep(a, title="T", body="<p>hi</p>",
                           options={"content_format": "html"}))["ok"]
    assert calls[0]["payload"]["contentFormat"] == "html"
    assert calls[0]["payload"]["content"].startswith("<h1>T</h1>")


def test_prepare_warnings_and_invalid_canonical_dropped(credstore, monkeypatch):
    a = _adapter()
    res = a.prepare({"adapted_body": "b", "format": "article",
                     "hashtags": ["1", "2", "3", "4", "5", "6"],
                     "options": {"canonical_url": "not a url",
                                 "publish_status": "later"}}, {})
    assert res["ok"]
    warns = " | ".join(res["warnings"])
    assert "no title" in warns
    assert "canonical URL invalid" in warns
    assert "publish_status invalid" in warns
    assert "at most 5 tags" in warns
    assert res["prepared"]["canonical_url"] == ""

    # …and the API payload honors the fallbacks: no canonicalUrl, public
    _arm_token(credstore)
    a.save_credentials({"account": "swriter", "author_id": AUTHOR_ID})
    calls = _stub_http(monkeypatch, [POST_OK])
    assert a.publish(res["prepared"])["ok"]
    payload = calls[0]["payload"]
    assert "canonicalUrl" not in payload
    assert payload["publishStatus"] == "public"
    assert len(payload["tags"]) == 5

    # title fallback comes from the post's working title
    res = a.prepare({"adapted_body": "b"}, {"title": "Post Title"})
    assert res["prepared"]["title"] == "Post Title"


# ── §4.14: API-rung failures NEVER silently fall to handoff ──────────────────
def test_rejected_token_is_fixed_string_and_no_handoff_fallback(
        credstore, monkeypatch, tmp_path):
    _arm_token(credstore)
    a = _adapter()

    # rejected at author lookup
    _stub_http(monkeypatch, [(401, {"errors": [{"message":
                                                "Token was invalid PII-ish"}]})])
    res = a.publish(_prep(a))
    assert res == {"ok": False, "error": ERR_TOKEN_REJECTED}
    assert "PII-ish" not in json.dumps(res)       # content-free (§12.5)
    assert a.status()["last_error"] == ERR_TOKEN_REJECTED

    # rejected at the publish call itself
    a.save_credentials({"account": "swriter", "author_id": AUTHOR_ID})
    _stub_http(monkeypatch, [(401, {})])
    res = a.publish(_prep(a))
    assert res == {"ok": False, "error": ERR_TOKEN_REJECTED}

    # no silent rung-fall: no package, no ledger entry, no budget burned
    assert a.pending_handoffs()["pending"] == []
    assert not (Path(pbase.PLATFORMS_DIR).parent / "content"
                / "medium_exports").exists()
    assert a.rate_budget()["used"] == 0


def test_api_transport_failure_is_content_free(credstore, monkeypatch):
    _arm_token(credstore)
    a = _adapter()
    a.save_credentials({"account": "swriter", "author_id": AUTHOR_ID})

    def _boom(*args, **kw):
        raise OSError("connection refused via C:/private/path")
    monkeypatch.setattr(mmod, "_http_json", _boom)
    res = a.publish(_prep(a))
    assert res == {"ok": False, "error": ERR_API_UNAVAILABLE}
    assert "private" not in json.dumps(res)

    # non-401 API error → fixed string too
    _stub_http(monkeypatch, [(500, {"errors": [{"message": "boom"}]})])
    assert a.publish(_prep(a)) == {"ok": False, "error": ERR_API_ERROR}


# ── headless mode: STUB, feature-flagged OFF, never a silent rung-fall ───────
def test_headless_flag_stub_surfaces_choice_and_token_outranks(
        credstore, monkeypatch):
    a = _adapter(headless_enabled=True)
    res = a.publish(_prep(a))
    assert res["ok"] is False
    assert res["error"] == ERR_HEADLESS_UNAVAILABLE
    assert res["degraded"] is True and res["requires_user_choice"] is True
    assert "assisted_handoff" in res["options"]
    # nothing written, no budget burned — no silent fallback
    assert a.pending_handoffs()["pending"] == []
    assert a.rate_budget()["used"] == 0

    # a stored token outranks the flag: API path runs, stub never fires
    _arm_token(credstore)
    a.save_credentials({"account": "swriter", "author_id": AUTHOR_ID})
    calls = _stub_http(monkeypatch, [POST_OK])
    assert a.publish(_prep(a))["ok"] is True
    assert len(calls) == 1


# ── auth blob round trip (fake credential store) ──────────────────────────────
def test_verify_credentials_blob_round_trip_nothing_tokenish(credstore,
                                                             monkeypatch):
    a = _adapter()
    assert a.verify_credentials() == {"ok": False, "error": ERR_NO_TOKEN}

    _arm_token(credstore)
    _stub_http(monkeypatch, [ME_OK])
    out = a.verify_credentials()
    assert out == {"ok": True, "account": "swriter", "author_id": AUTHOR_ID}

    blob = a.load_credentials()
    assert blob["account"] == "swriter"
    assert blob["author_id"] == AUTHOR_ID
    assert blob["account_url"] == "https://medium.com/@swriter"
    assert blob["scopes"] == ["basicProfile", "publishPost"]
    assert SECRET not in json.dumps(blob)         # the secret never lands here

    st = a.status()
    assert st["connected"] is True and st["account"] == "swriter"
    assert SECRET not in json.dumps(st)           # nothing tokenish in status()

    events = [e for _, e, _ in credstore["events"]]
    assert "credentials_stored" in events and "verified" in events

    # disconnect: blob + provider key both purged; tier drops honestly
    assert a.clear_credentials()["ok"] is True
    assert a.load_credentials() is None
    assert credstore["stored"] == {}              # provider key gone too
    assert a.automation_tier == "assisted_handoff"
    assert a.status()["connected"] is False
    assert "credentials_cleared" in [e for _, e, _ in credstore["events"]]

    # a rejected token verifies as the fixed string
    _arm_token(credstore)
    _stub_http(monkeypatch, [(401, {})])
    assert a.verify_credentials() == {"ok": False, "error": ERR_TOKEN_REJECTED}


# ── §12.5: handoff publish failure externalizes a fixed string ────────────────
def test_handoff_publish_failure_error_is_content_free(tmp_path):
    blocker = tmp_path / "not-a-dir"
    blocker.write_text("file where the export dir must go", encoding="utf-8")
    a = _adapter(export_dir=str(blocker))
    res = a.publish(_prep(a))
    assert res == {"ok": False, "error": ERR_PACKAGE_WRITE}
    assert str(tmp_path) not in json.dumps(res)   # no paths leak outward
    assert a.status()["last_error"] == ERR_PACKAGE_WRITE

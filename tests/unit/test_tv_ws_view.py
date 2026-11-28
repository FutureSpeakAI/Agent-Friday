"""A workspace as an A2UI document, served to the owner's phone or tablet, and the one-tap signed edit (D1-D3, D5).

Contract draft 3, section 4. Routes (tv_gateway/ws_view.py):

    GET  /tv/v1/workspaces/<ws>/view                      the document, its live data (broker), his customization
    GET  /tv/v1/workspaces/<ws>/view/versions             the reversible versions, newest first
    POST /tv/v1/workspaces/<ws>/view/proposals/challenge  a nonce for one proposal
    POST /tv/v1/workspaces/<ws>/view/proposals            the signed proposal, applied as a new version

Rules held here: only an owner phone or tablet with the read right sees a view; the data comes only through the
broker and renders on the phone's side unchanged; a proposal needs the approval right, a live nonce (spent before the
signature is looked at), a time in the window and the APPROVAL key's signature over the exact change; it lands as a
new reversible version; a stale base is a 409 with the PC's copy (keep mine / keep PC); a change the profile or the
customization rules refuse is a 400 naming the rule and stores nothing. Synthetic data only.
"""
import base64
import hashlib
import json
import time

import pytest
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec

from agent_friday.services import a2ui_profile as prof
from agent_friday.services import a2ui_render as rend
from agent_friday.services import a2ui_store as store
from agent_friday.services import a2ui_workspaces as aws
from agent_friday.services import device_pairing
from agent_friday.tv_gateway import approval_verify as av
from agent_friday.tv_gateway import ws_knowledge
from tests.unit.test_tv_pc_approve_decide import Enrolled, _clean, client  # noqa: F401  (fixtures)
from tests.unit.test_tv_pc_read import paired, get

ROWS = [
    {"ref": "page:entities/sample-one.md", "title": "sample one", "line": "entities", "when": "2026-11-21T10:00:00Z"},
    {"ref": "page:projects/garden.md", "title": "garden", "line": "projects", "when": "2026-11-20T10:00:00Z"},
]
VIEW = "/tv/v1/workspaces/knowledge/view"


@pytest.fixture(autouse=True)
def _views(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "VIEWS_DIR", tmp_path / "liquid" / "views")
    monkeypatch.setattr(store, "_admit_write", lambda: 0)
    monkeypatch.setattr(store, "_check_write", lambda origin: None)
    monkeypatch.setattr(store, "_announce", lambda view_id: None)
    monkeypatch.setattr(ws_knowledge, "pages", lambda sensitivity: [dict(r) for r in ROWS])


def fp(ws, base_rev, change):
    canon = json.dumps({"ws": ws, "base_rev": base_rev, "change": change}, sort_keys=True, separators=(",", ":"),
                       ensure_ascii=False)
    return hashlib.sha256(canon.encode("utf-8")).hexdigest()


def propose(dev, change, base_rev=None, *, ws="knowledge", tamper=None, when=None, key=None):
    base_rev = base_rev or dev.get(VIEW).get_json()["rev"]
    got = dev.post("/tv/v1/workspaces/%s/view/proposals/challenge" % ws, {"base_rev": base_rev, "change": change})
    assert got.status_code == 200, got.get_json()
    ch = got.get_json()
    when = when if when is not None else int(time.time())
    signed_change = tamper if tamper is not None else change
    message = av.signing_message("wsview:%s" % ws, fp(ws, base_rev, signed_change), ch["nonce"], dev.id, when)
    sig = base64.b64encode((key or dev.key).sign(message, ec.ECDSA(hashes.SHA256()))).decode()
    return dev.post("/tv/v1/workspaces/%s/view/proposals" % ws,
                    {"base_rev": base_rev, "change": change, "nonce": ch["nonce"], "time": when, "sig": sig}), ch


# ── reading the view ───────────────────────────────────────────────────────

def test_the_knowledge_view_is_the_builtin_document_with_live_data(client):
    dev = Enrolled(client)
    r = dev.get(VIEW)
    assert r.status_code == 200, r.get_json()
    body = r.get_json()
    assert body["ws"] == "knowledge" and body["view"] == "knowledge"
    assert body["doc"] == aws.builtin("knowledge")
    assert body["rev"] == aws.builtin_rev("knowledge") and body["rev"].startswith("builtin-")
    assert body["data"]["knowledge"]["sections"] == [{"name": "entities", "count": 1}, {"name": "projects", "count": 1}]
    assert body["customization"] == {} and body["versions"] == 0
    assert body["ttl_s"] == 30 and body["as_of"].endswith("Z")
    # what the phone binds is exactly what the PC's reference renderer binds
    r2 = rend.render(body["doc"], aws.ctx("knowledge"), data=body["data"])
    assert r2.ok, r2.receipt


def test_a_view_needs_an_owner_device_with_the_read_right(client):
    assert client.get(VIEW).status_code in (401, 403)
    watch = paired(kind="watch")
    assert get(client, watch, VIEW).status_code == 403
    no_right = paired(right=False)
    r = get(client, no_right, VIEW)
    assert r.status_code == 403 and r.get_json()["code"] == "needs_right"


def test_a_workspace_with_no_document_is_404_never_an_empty_view(client):
    dev = Enrolled(client)
    for ws in ("health", "no-such", "..", "KNOWLEDGE"):      # health has no device block
        assert dev.get("/tv/v1/workspaces/%s/view" % ws).status_code == 404, ws


def _serve_without_pages(monkeypatch):
    """Knowledge served with every section but "pages" (the one its view reads). "people" stays in, so the
    workspace itself is still served and only the view's section is missing."""
    from agent_friday.tv_gateway import ws_content
    real = ws_content._serving

    def without_pages(ws):
        got = real(ws)
        if got is None:
            return None
        rest = {k: v for k, v in got[0].items() if k != "pages"}
        assert rest, "knowledge must keep a section besides pages for this test to mean anything"
        return rest, got[1]
    monkeypatch.setattr(ws_content, "_serving", without_pages)


def _knowledge_row(dev):
    return next(w for w in dev.get("/tv/v1/workspaces").get_json()["workspaces"] if w.get("id") == "knowledge")


def test_a_view_is_served_only_where_the_section_its_data_comes_from_is_declared(client, monkeypatch):
    # the broker reads the "pages" section; a device block that declares other sections only serves no view
    _serve_without_pages(monkeypatch)
    assert Enrolled(client).get(VIEW).status_code == 404


def test_a_pc_that_cannot_read_its_wiki_is_503(client, monkeypatch):
    def down(sensitivity):
        raise ws_knowledge.Unavailable("structure")
    monkeypatch.setattr(ws_knowledge, "pages", down)
    r = Enrolled(client).get(VIEW)
    assert r.status_code == 503 and r.get_json()["code"] == "pc_unavailable"


def test_the_workspace_list_marks_the_a2ui_ones(client):
    dev = Enrolled(client)
    rows = dev.get("/tv/v1/workspaces").get_json()["workspaces"]
    a2ui = {w["id"] for w in rows if w.get("renderer") == "a2ui"}
    # designed documents (knowledge, futurespeak) and generated ones for every other served workspace
    assert a2ui == {"knowledge", "futurespeak", "calendar", "code", "crew", "news"}
    assert all(w.get("renderer") == "legacy" for w in rows if w["id"] not in a2ui)


def test_the_list_says_a2ui_only_where_the_view_answers(client, monkeypatch):
    # the marker and GET .../view share one rule: a block without the section the view reads gets the legacy screen
    _serve_without_pages(monkeypatch)
    dev = Enrolled(client)
    assert _knowledge_row(dev).get("renderer") == "legacy"
    assert dev.get(VIEW).status_code == 404


def test_a_view_the_pc_cannot_read_lists_as_legacy_and_the_list_still_answers(client, monkeypatch):
    # an unreadable manifest must not take the whole workspace list down, nor send the phone to a view that 503s
    from agent_friday.tv_gateway import ws_content

    def broken(ws):
        raise OSError("manifest unreadable")
    monkeypatch.setattr(ws_content, "view_block", broken)
    assert _knowledge_row(Enrolled(client)).get("renderer") == "legacy"


# ── the one-tap signed proposal ────────────────────────────────────────────

def test_a_signed_customization_lands_as_a_new_version_on_the_pc(client):
    dev = Enrolled(client)
    r, _ = propose(dev, {"customization": {"density": "compact", "hidden": ["recent"]}})
    assert r.status_code == 200, r.get_json()
    body = r.get_json()
    view = dev.get(VIEW).get_json()
    assert view["rev"] == body["rev"] and not view["rev"].startswith("builtin-")
    assert view["customization"] == {"density": "compact", "hidden": ["recent"]}
    assert view["versions"] == 1, "the builtin document is kept as the version before his change"
    versions = dev.get(VIEW + "/versions").get_json()["versions"]
    assert len(versions) == 1 and versions[0]["kind"] == "change"
    assert store.load("knowledge")["updated_by"] == av.device_label(dev.id)


def test_a_second_change_and_a_restore_are_versions_too(client):
    dev = Enrolled(client)
    first, _ = propose(dev, {"customization": {"summary": "My notes"}})
    second, _ = propose(dev, {"customization": {"accent": "#3366ff"}})
    assert second.status_code == 200
    view = dev.get(VIEW).get_json()
    assert view["customization"] == {"summary": "My notes", "accent": "#3366ff"}
    older = dev.get(VIEW + "/versions").get_json()["versions"][0]["id"]
    back, _ = propose(dev, {"restore": older})
    assert back.status_code == 200, back.get_json()
    assert dev.get(VIEW).get_json()["customization"] == {"summary": "My notes"}


def test_a_stale_base_is_409_with_the_pcs_copy_and_nothing_is_merged(client):
    dev = Enrolled(client)
    base = dev.get(VIEW).get_json()["rev"]
    propose(dev, {"customization": {"density": "compact"}}, base)
    r, _ = propose(dev, {"customization": {"summary": "from the phone"}}, base)
    assert r.status_code == 409
    body = r.get_json()
    assert body["code"] == "stale" and body["current_rev"] == dev.get(VIEW).get_json()["rev"]
    assert body["current"] == {"customization": {"density": "compact"}}
    assert dev.get(VIEW).get_json()["customization"] == {"density": "compact"}
    # keep mine = the same change proposed again at the PC's revision
    again, _ = propose(dev, {"customization": {"summary": "from the phone"}}, body["current_rev"])
    assert again.status_code == 200


@pytest.mark.parametrize("change,rule", [
    ({"customization": {"accent": "red; background:url(x)"}}, "customization"),
    ({"customization": {"hidden": ["not_a_part"]}}, "customization"),
    ({"customization": {"css": "body{}"}}, "customization"),
    ({"doc": []}, "change"),
])
def test_a_refused_change_names_the_rule_and_stores_nothing(client, change, rule):
    dev = Enrolled(client)
    r, _ = propose(dev, change)
    assert r.status_code == 400 and r.get_json()["code"] == "refused" and r.get_json()["rule"] == rule
    assert store.load("knowledge") is None


def test_a_signature_over_a_different_change_is_refused_and_the_nonce_is_spent(client):
    dev = Enrolled(client)
    r, ch = propose(dev, {"customization": {"density": "compact"}}, tamper={"customization": {"density": "comfortable"}})
    assert r.status_code == 403 and r.get_json()["code"] == "bad_signature"
    assert store.load("knowledge") is None
    base = dev.get(VIEW).get_json()["rev"]
    replay = dev.post(VIEW + "/proposals", {"base_rev": base, "change": {"customization": {"density": "compact"}},
                                            "nonce": ch["nonce"], "time": int(time.time()), "sig": "AAAA"})
    assert replay.status_code == 403 and replay.get_json()["code"] == "bad_nonce"


def test_the_session_key_cannot_sign_a_proposal(client):
    dev = Enrolled(client)
    other = ec.generate_private_key(ec.SECP256R1())
    r, _ = propose(dev, {"customization": {"density": "compact"}}, key=other)
    assert r.status_code == 403 and r.get_json()["code"] == "bad_signature"


def test_a_time_outside_the_window_is_refused(client):
    dev = Enrolled(client)
    r, _ = propose(dev, {"customization": {"density": "compact"}}, when=int(time.time()) - 3600)
    assert r.status_code == 403 and r.get_json()["code"] == "bad_time"


def test_a_device_without_the_approval_right_gets_no_challenge(client):
    dev = paired()
    from tests.unit.test_tv_gateway import auth, open_session
    r = client.post(VIEW + "/proposals/challenge", json={"base_rev": aws.builtin_rev("knowledge"),
                                                         "change": {"customization": {"density": "compact"}}},
                    headers=auth(open_session(client, dev)))
    assert r.status_code == 403
    assert store.load("knowledge") is None


def test_every_outcome_writes_a_receipt_naming_the_device(client, monkeypatch):
    seen = []
    monkeypatch.setattr(device_pairing, "_receipt", lambda kind, dev, **kw: seen.append((kind, dev.get("id"), kw)))
    dev = Enrolled(client)
    propose(dev, {"customization": {"density": "compact"}})
    propose(dev, {"customization": {"density": "compact"}}, tamper={"customization": {}})
    kinds = [(k, d) for k, d, _ in seen if k.startswith("workspace_view")]
    assert ("workspace_view_changed", dev.id) in kinds and ("workspace_view_refused", dev.id) in kinds


def test_the_proposal_fingerprint_is_canonical_json():
    assert fp("knowledge", "r1", {"customization": {"b": 1, "a": "é"}}) == hashlib.sha256(
        '{"base_rev":"r1","change":{"customization":{"a":"é","b":1}},"ws":"knowledge"}'.encode("utf-8")).hexdigest()
    from agent_friday.tv_gateway import ws_view
    assert ws_view.proposal_fp("knowledge", "r1", {"customization": {"b": 1, "a": "é"}}) == \
        fp("knowledge", "r1", {"customization": {"b": 1, "a": "é"}})


# ── critic round 1 (PASS with should-fix): every outcome answers plainly, nothing half-written ────────

def test_a_store_that_cannot_write_is_503_with_a_receipt_never_a_500(client, monkeypatch):
    seen = []
    monkeypatch.setattr(device_pairing, "_receipt", lambda kind, dev, **kw: seen.append((kind, kw.get("reason"))))
    dev = Enrolled(client)

    def boom(*a, **k):
        raise OSError("disk full")
    monkeypatch.setattr(aws, "apply_change", boom)
    r, _ = propose(dev, {"customization": {"density": "compact"}})
    assert r.status_code == 503 and r.get_json()["code"] == "pc_unavailable"
    assert ("workspace_view_refused", "pc_unavailable") in seen


def test_off_the_record_the_first_change_writes_nothing_at_all(client, monkeypatch):
    dev = Enrolled(client)

    def off():
        raise PermissionError("off the record")
    monkeypatch.setattr(store, "_admit_write", off)
    r, _ = propose(dev, {"customization": {"density": "compact"}})
    assert r.status_code == 423 and r.get_json()["code"] == "off_record"
    assert store.load("knowledge") is None, "the builtin must not be stored on its own when the change is refused"


def test_the_first_change_is_one_write_the_builtin_kept_as_its_earlier_version(client, monkeypatch):
    writes = []
    real = store._write_atomic
    monkeypatch.setattr(store, "_write_atomic", lambda p, d: (writes.append(p), real(p, d)))
    dev = Enrolled(client)
    r, _ = propose(dev, {"customization": {"density": "compact"}})
    assert r.status_code == 200 and len(writes) == 1
    versions = store.load("knowledge")["versions"]
    assert len(versions) == 1 and versions[0]["doc"] == aws.builtin("knowledge") and versions[0]["customization"] == {}


def test_a_huge_time_is_refused_not_a_500(client):
    dev = Enrolled(client)
    r, _ = propose(dev, {"customization": {"density": "compact"}}, when=10 ** 400)
    assert r.status_code in (400, 403)


def test_restore_on_a_view_never_changed_is_refused_and_writes_nothing(client):
    dev = Enrolled(client)
    r, _ = propose(dev, {"restore": "abc123"})
    assert r.status_code == 400 and r.get_json()["rule"] == "version"
    assert store.load("knowledge") is None


@pytest.mark.parametrize("kind", ["watch", "no_right"])
def test_the_proposals_route_itself_refuses_a_device_that_may_not_approve(client, kind):
    from tests.unit.test_tv_gateway import auth, open_session
    dev = paired(kind="watch") if kind == "watch" else paired()
    r = client.post(VIEW + "/proposals", json={"base_rev": aws.builtin_rev("knowledge"),
                                              "change": {"customization": {"density": "compact"}},
                                              "nonce": "n", "time": int(time.time()), "sig": "AAAA"},
                    headers=auth(open_session(client, dev)))
    assert r.status_code == 403
    assert store.load("knowledge") is None


# ── a proposal body the fingerprint can't be taken of is a 400, never a 500 ──

UNPRINTABLE = [
    r'"summary": "\ud800"',       # a lone surrogate, sent escaped
    '"density": NaN',             # not JSON, but Python parses it
    '"density": Infinity',
]


@pytest.mark.parametrize("bad", UNPRINTABLE)
def test_a_challenge_that_cannot_be_fingerprinted_is_refused_not_a_crash(client, bad):
    r = client.post("/tv/v1/workspaces/knowledge/view/proposals/challenge",
                    data='{"base_rev": "r", "change": {"customization": {%s}}}' % bad,
                    content_type="application/json", headers=Enrolled(client).session())
    assert r.status_code == 400 and r.get_json()["code"] == "bad_request", r.status_code


@pytest.mark.parametrize("bad", UNPRINTABLE)
def test_a_signed_proposal_that_cannot_be_fingerprinted_is_refused_before_its_nonce(client, bad):
    # nonce, time and sig are well formed, so the only thing wrong is the change itself
    raw = ('{"base_rev": "r", "change": {"customization": {%s}}, "nonce": "%s", "time": %d, "sig": "%s"}'
           % (bad, "n" * 32, int(time.time()), base64.b64encode(b"x" * 70).decode()))
    r = client.post("/tv/v1/workspaces/knowledge/view/proposals", data=raw, content_type="application/json",
                    headers=Enrolled(client).session())
    assert r.status_code == 400 and r.get_json()["code"] == "bad_request", r.status_code

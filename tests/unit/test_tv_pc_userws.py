"""Workspaces the owner builds on the phone (PW-G6): validate whole, save with a rev, keep every version.

Rules guarded here, each with a case that fails if the check is removed:
* only an owner-bound phone or tablet may read (``pc_read``) or write (``pc_act``); nothing is written without the right;
* a manifest that breaks any rule (closed schema, ids, text with a link or host path, section caps, a source the PC's
  manifests do not publish, a board that is not a valid Friday A2UI document, a verb in a board) is refused WHOLE with the
  rule and the path of the fault, and nothing is written; none of the 75 hostile A2UI documents is accepted;
* a save carries ``base_rev`` (0 for a new workspace); a stale one is a 409 carrying the PC's version; an edit of a deleted
  workspace is 410 ``gone``; the server, not the device, sets ``rev``, ``updated_by`` and ``updated_at``;
* every version is kept and a rollback is a new version; delete goes to the trash;
* the receipt names the workspace id and outcome, never a label or a query.
"""
import copy
import json
import pathlib

import pytest

from agent_friday.services import approvals as ap
from agent_friday.services import device_pairing
from agent_friday.tv_gateway import app as gateway
from agent_friday.tv_gateway import household, pairing, pc_read, pc_userws
from tests.ledger_support import rows
from tests.unit.test_tv_gateway import FakeTv, auth, open_session

FIXTURES = json.loads((pathlib.Path(__file__).resolve().parents[1] / "fixtures" / "a2ui" / "profile_cases_v1.json")
                      .read_text(encoding="utf-8"))


@pytest.fixture(autouse=True)
def _clean(tmp_path, monkeypatch):
    monkeypatch.setattr(ap, "APPROVALS_FILE", tmp_path / "approvals.json")
    monkeypatch.setattr(ap, "_feed", lambda *a, **k: None)
    monkeypatch.setattr(ap, "_notify_pending", lambda rec: None)
    monkeypatch.setattr(household, "_path", lambda: tmp_path / "household.json")
    monkeypatch.setattr(gateway, "_BUCKET", gateway._Bucket())
    monkeypatch.setattr(pc_read, "_AUDITED", {})
    monkeypatch.setattr(pairing, "_OFFERED", {})
    monkeypatch.setattr(pc_userws, "_root", lambda: tmp_path / "workspaces" / "user")
    device_pairing._path().unlink(missing_ok=True)
    device_pairing._reset_for_tests()
    household.ensure_default_members()
    yield
    device_pairing._reset_for_tests()


@pytest.fixture
def client():
    return gateway.create_app().test_client()


def device(kind="phone", member="stephen", rights=("pc_read", "pc_act")):
    tv = FakeTv()
    device_pairing.add_device(tv.spki, name="A device", kind=kind)
    if member:
        household.bind_device(tv.id, member)
    for right in rights:
        device_pairing.set_right(tv.id, right, True)
    return tv


def call(client, tv, method, path, body=None):
    return client.open(path, method=method, json=body, headers=auth(open_session(client, tv)))


def board_document():
    """The fixtures' good document without its verb button and approval slot: a built workspace carries no verb, and an
    approval slot's subject can only be the view's own (a user workspace has none)."""
    document = copy.deepcopy(FIXTURES["good"][0])
    for message in document:
        components = message["createSurface"]["components"]
        gone = {c["id"] for c in components if c["component"] in ("verb_button", "approval_slot")}
        message["createSurface"]["components"] = [c for c in components if c["id"] not in gone]
        for c in message["createSurface"]["components"]:
            if "children" in c:
                c["children"] = [child for child in c["children"] if child not in gone]
    return document


def manifest(**changes):
    base = {"schema": "friday.user_workspace/1", "id": "garden", "label": "Garden", "icon": "leaf",
            "sections": [{"id": "photos", "label": "Photos", "view": "grid",
                          "source": {"ws": "media", "kind": "media", "facet": {"collection": "garden"}}},
                         {"id": "board", "label": "Board", "view": "board", "a2ui": board_document()}]}
    base.update(changes)
    return base


def save(client, tv, body, base_rev=0, wid="garden"):
    return call(client, tv, "PUT", "/tv/v1/workspaces/user/" + wid, {"base_rev": base_rev, "manifest": body})


def files(tmp_path):
    return sorted(p.name for p in (tmp_path / "workspaces" / "user").glob("*.json")) if (tmp_path / "workspaces" / "user").is_dir() else []


def test_the_palette_lists_the_kinds_the_pcs_manifests_publish_and_no_held_workspace(client):
    answer = call(client, device(), "GET", "/tv/v1/workspaces/palette").get_json()
    assert "media" in answer["kinds"] and "marketplace" not in answer["kinds"] and "board" in answer["views"]


def test_a_new_workspace_is_saved_with_the_servers_own_rev_and_author(client, tmp_path):
    tv = device()
    answer = save(client, tv, manifest())
    saved = answer.get_json()["manifest"]
    assert answer.status_code == 200 and saved["rev"] == 1 and saved["base_rev"] == 0
    assert saved["updated_by"] == "device:%s" % tv.id and saved["group"] == "mine"
    assert files(tmp_path) == ["garden.json"]
    assert call(client, tv, "GET", "/tv/v1/workspaces/user/garden").get_json()["rev"] == 1


@pytest.mark.parametrize("extra", [{"rev": 99}, {"updated_by": "pc"}, {"base_rev": 3}, {"updated_at": "x"}])
def test_the_device_never_sets_rev_or_author(client, tmp_path, extra):
    assert save(client, device(), manifest(**extra)).status_code == 422 and files(tmp_path) == []


def test_a_stale_base_rev_is_a_conflict_carrying_the_pcs_version(client, tmp_path):
    tv = device()
    save(client, tv, manifest())
    changed = manifest(label="Garden (PC)")
    save(client, tv, changed, base_rev=1)
    answer = save(client, tv, manifest(label="Mine"), base_rev=1)
    body = answer.get_json()
    assert answer.status_code == 409 and body["code"] == "conflict" and body["current"]["label"] == "Garden (PC)"
    assert call(client, tv, "GET", "/tv/v1/workspaces/user/garden").get_json()["manifest"]["label"] == "Garden (PC)"


def test_a_new_workspace_must_start_at_rev_zero_and_a_deleted_one_is_gone(client):
    tv = device()
    assert save(client, tv, manifest(), base_rev=4).status_code == 410
    save(client, tv, manifest())
    assert call(client, tv, "DELETE", "/tv/v1/workspaces/user/garden").status_code == 200
    gone = save(client, tv, manifest(label="Again"), base_rev=1)
    assert gone.status_code == 410 and gone.get_json()["code"] == "gone"


def test_delete_goes_to_the_trash_not_to_nowhere(client, tmp_path):
    tv = device()
    save(client, tv, manifest())
    call(client, tv, "DELETE", "/tv/v1/workspaces/user/garden")
    assert call(client, tv, "GET", "/tv/v1/workspaces/user/garden").status_code == 404
    assert len(list((tmp_path / "workspaces" / "user" / ".trash").glob("garden-*.json"))) == 1


def test_every_version_is_kept_and_a_rollback_is_a_new_version(client):
    tv = device()
    save(client, tv, manifest())
    save(client, tv, manifest(label="Garden two"), base_rev=1)
    save(client, tv, manifest(label="Garden three"), base_rev=2)
    versions = call(client, tv, "GET", "/tv/v1/workspaces/user/garden/versions").get_json()["versions"]
    assert [v["rev"] for v in versions] == [1, 2, 3]
    back = call(client, tv, "POST", "/tv/v1/workspaces/user/garden/rollback", {"rev": 1, "base_rev": 3})
    assert back.status_code == 200 and back.get_json()["rev"] == 4 and back.get_json()["manifest"]["label"] == "Garden"
    stale = call(client, tv, "POST", "/tv/v1/workspaces/user/garden/rollback", {"rev": 1, "base_rev": 3})
    assert stale.status_code == 409


def test_the_list_has_rows_not_sections(client):
    tv = device()
    save(client, tv, manifest())
    [row] = call(client, tv, "GET", "/tv/v1/workspaces/user").get_json()["workspaces"]
    assert row["id"] == "garden" and row["rev"] == 1 and "sections" not in row


def test_validate_checks_without_writing(client, tmp_path):
    tv = device()
    ok = call(client, tv, "POST", "/tv/v1/workspaces/user/garden/validate", {"manifest": manifest()})
    bad = call(client, tv, "POST", "/tv/v1/workspaces/user/garden/validate", {"manifest": manifest(label="")})
    assert ok.status_code == 200 and bad.status_code == 422 and bad.get_json()["path"] == "$.label" and files(tmp_path) == []


def section(**changes):
    base = {"id": "s1", "label": "Photos", "view": "grid", "source": {"ws": "media", "kind": "media"}}
    base.update(changes)
    return base


BAD = {
    "unknown top key": manifest(extra="x"),
    "wrong schema": manifest(schema="friday.user_workspace/2"),
    "id differs from the address": manifest(id="other"),
    "upper-case id": manifest(id="Garden"),
    "label with a link": manifest(label="see https://example.com"),
    "label with a host path": manifest(label="C:\\Users\\x\\notes"),
    "label not text": manifest(label=5),
    "group other than mine": manifest(group="family"),
    "no sections": manifest(sections=[]),
    "21 sections": manifest(sections=[section(id="s%d" % i) for i in range(21)]),
    "duplicate section ids": manifest(sections=[section(), section()]),
    "view not in the palette": manifest(sections=[section(view="carousel")]),
    "board with a source": manifest(sections=[section(view="board")]),
    "list with a2ui": manifest(sections=[{"id": "s1", "label": "x", "view": "list", "a2ui": board_document()}]),
    "list with neither": manifest(sections=[{"id": "s1", "label": "x", "view": "list"}]),
    "kind the PC does not publish": manifest(sections=[section(source={"ws": "media", "kind": "nothing"})]),
    "workspace the PC does not publish": manifest(sections=[section(source={"ws": "marketplace", "kind": "x"})]),
    "source with an extra key": manifest(sections=[section(source={"ws": "media", "kind": "media", "url": "x"})]),
    "query too long": manifest(sections=[section(source={"ws": "media", "kind": "media", "query": "q" * 201})]),
    "facet with six pairs": manifest(sections=[section(source={"ws": "media", "kind": "media",
                                                                "facet": {"k%d" % i: "v" for i in range(6)}})]),
    "facet value with a link": manifest(sections=[section(source={"ws": "media", "kind": "media",
                                                                   "facet": {"a": "https://x.example"}})]),
    "board that is not a document": manifest(sections=[{"id": "b", "label": "B", "view": "board", "a2ui": "html"}]),
    "over 256 KB": manifest(sections=[section(id="s%d" % i, label="L" * 60) for i in range(2)] + [
        {"id": "b", "label": "B", "view": "board", "a2ui": [{"pad": "x" * (256 * 1024)}]}]),
}


@pytest.mark.parametrize("name", sorted(BAD))
def test_a_manifest_that_breaks_a_rule_is_refused_whole_and_nothing_is_written(client, tmp_path, name):
    answer = save(client, device(), BAD[name])
    body = answer.get_json()
    assert answer.status_code == 422 and body["code"] == "invalid" and body["rule"] and body["path"].startswith("$")
    assert files(tmp_path) == []


def test_a_board_with_a_verb_button_or_an_approval_slot_is_refused(client, tmp_path):
    with_verb = manifest(sections=[{"id": "b", "label": "B", "view": "board", "a2ui": FIXTURES["good"][0]}])
    assert save(client, device(), with_verb).status_code == 422 and files(tmp_path) == []


def test_none_of_the_hostile_a2ui_documents_is_accepted(client, tmp_path):
    headers = auth(open_session(client, device()))              # one session for all 75 documents
    for case in FIXTURES["hostile"]:
        hostile = manifest(sections=[{"id": "b", "label": "B", "view": "board", "a2ui": case["document"]}])
        answer = client.put("/tv/v1/workspaces/user/garden", json={"base_rev": 0, "manifest": hostile}, headers=headers)
        assert answer.status_code == 422, case["name"]
    assert files(tmp_path) == []


@pytest.mark.parametrize("method,path,body", [
    ("PUT", "/tv/v1/workspaces/user/garden", {"base_rev": 0, "manifest": None}),
    ("POST", "/tv/v1/workspaces/user/garden/validate", {"manifest": None}),
    ("DELETE", "/tv/v1/workspaces/user/garden", None),
    ("POST", "/tv/v1/workspaces/user/garden/rollback", {"rev": 1, "base_rev": 1})])
def test_without_the_act_right_nothing_is_written_and_the_pc_asks(client, tmp_path, method, path, body):
    answer = call(client, device(rights=("pc_read",)), method, path, body)
    assert answer.status_code == 403 and answer.get_json()["code"] == "needs_right" and files(tmp_path) == []


@pytest.mark.parametrize("path", ["/tv/v1/workspaces/palette", "/tv/v1/workspaces/user", "/tv/v1/workspaces/user/garden",
                                  "/tv/v1/workspaces/user/garden/versions"])
def test_reads_need_the_read_right(client, path):
    answer = call(client, device(rights=()), "GET", path)
    assert answer.status_code == 403 and answer.get_json()["code"] == "needs_right"


@pytest.mark.parametrize("kind,member", [("watch", "stephen"), ("tv", "stephen"), ("phone", "family"), ("phone", None)])
def test_a_watch_a_tv_a_family_or_unbound_device_cannot_build_or_read(client, tmp_path, kind, member):
    tv = device(kind, member)
    assert call(client, tv, "PUT", "/tv/v1/workspaces/user/garden", {"base_rev": 0, "manifest": manifest()}).status_code == 403
    assert call(client, tv, "GET", "/tv/v1/workspaces/user").status_code == 403 and files(tmp_path) == []


@pytest.mark.parametrize("wid", ["Garden", "a b", "..", "x" * 60, "a.b"])
def test_a_bad_workspace_id_never_touches_the_disk(client, tmp_path, wid):
    tv = device()
    assert call(client, tv, "PUT", "/tv/v1/workspaces/user/" + wid, {"base_rev": 0, "manifest": manifest()}).status_code in (400, 404)
    assert files(tmp_path) == []


def test_the_receipt_names_the_workspace_and_never_its_words(client):
    save(client, device(), manifest(label="Private thoughts"))
    [receipt] = rows("device_act")
    assert (receipt["action"], receipt["workspace"], receipt["outcome"]) == ("userws_save", "garden", 200)
    assert "Private thoughts" not in json.dumps(receipt)

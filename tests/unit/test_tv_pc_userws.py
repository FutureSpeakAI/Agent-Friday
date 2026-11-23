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
    assert save(client, tv, manifest(), base_rev=4).status_code == 404       # never existed
    save(client, tv, manifest())
    assert call(client, tv, "DELETE", "/tv/v1/workspaces/user/garden").status_code == 200
    gone = save(client, tv, manifest(label="Again"), base_rev=1)
    assert gone.status_code == 410 and gone.get_json()["code"] == "gone"


def test_delete_goes_to_the_trash_not_to_nowhere(client, tmp_path):
    tv = device()
    save(client, tv, manifest())
    call(client, tv, "DELETE", "/tv/v1/workspaces/user/garden")
    assert call(client, tv, "GET", "/tv/v1/workspaces/user/garden").status_code == 404
    [entry] = list((tmp_path / "workspaces" / "user" / ".trash").glob("garden-*"))
    assert (entry / "workspace.json").is_file() and (entry / "versions" / "000001.json").is_file()


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


def test_deleting_and_recreating_a_workspace_never_mixes_the_two_histories(client, tmp_path):
    tv = device()
    save(client, tv, manifest(label="First life"))
    save(client, tv, manifest(label="First life two"), base_rev=1)
    call(client, tv, "DELETE", "/tv/v1/workspaces/user/garden")
    fresh = save(client, tv, manifest(label="Second life"))
    assert fresh.status_code == 200 and fresh.get_json()["rev"] == 1
    versions = call(client, tv, "GET", "/tv/v1/workspaces/user/garden/versions").get_json()["versions"]
    assert [v["label"] for v in versions] == ["Second life"]
    assert call(client, tv, "POST", "/tv/v1/workspaces/user/garden/rollback", {"rev": 2, "base_rev": 1}).status_code == 404


def test_a_deleted_workspace_comes_back_with_its_history_when_its_id_is_free(client):
    tv = device()
    save(client, tv, manifest(label="First life"))
    save(client, tv, manifest(label="First life two"), base_rev=1)
    call(client, tv, "DELETE", "/tv/v1/workspaces/user/garden")
    back = call(client, tv, "POST", "/tv/v1/workspaces/user/garden/restore")
    assert back.status_code == 200 and back.get_json()["rev"] == 2
    assert [v["rev"] for v in call(client, tv, "GET", "/tv/v1/workspaces/user/garden/versions").get_json()["versions"]] == [1, 2]
    assert call(client, tv, "POST", "/tv/v1/workspaces/user/garden/restore").status_code == 409     # now in use


def test_two_deletes_in_the_same_second_keep_both_trash_entries(client, tmp_path, monkeypatch):
    tv = device()
    monkeypatch.setattr("time.time", lambda: 1_800_000_000.0)
    for label in ("One", "Two"):
        save(client, tv, manifest(label=label))
        call(client, tv, "DELETE", "/tv/v1/workspaces/user/garden")
    assert len(list((tmp_path / "workspaces" / "user" / ".trash").glob("garden-*"))) == 2


def test_a_delete_that_names_a_stale_rev_is_a_conflict_and_deletes_nothing(client, tmp_path):
    tv = device()
    save(client, tv, manifest())
    stale = call(client, tv, "DELETE", "/tv/v1/workspaces/user/garden", {"base_rev": 7})
    assert stale.status_code == 409 and files(tmp_path) == ["garden.json"]


@pytest.mark.parametrize("wid", ["con", "nul", "aux", "prn", "com1", "lpt9"])
def test_a_name_windows_treats_as_a_device_is_refused(client, tmp_path, wid):
    answer = call(client, device(), "PUT", "/tv/v1/workspaces/user/" + wid, {"base_rev": 0, "manifest": manifest(id=wid)})
    assert answer.status_code == 400 and files(tmp_path) == []


@pytest.mark.parametrize("bad", [
    {"ws": [], "kind": "media"}, {"ws": {}, "kind": "media"}, {"ws": "media", "kind": ["media"]},
    {"ws": "media", "kind": "media", "facet": {"a": ["x"]}}])
def test_input_that_makes_a_rule_itself_fail_is_a_422_not_a_server_error(client, tmp_path, bad):
    answer = save(client, device(), manifest(sections=[section(source=bad)]))
    assert answer.status_code == 422 and files(tmp_path) == []


def test_nesting_too_deep_for_the_validator_is_a_422(client, tmp_path):
    # The nesting is written as text: encoding a 3000-deep list would hit the test's own recursion limit.
    marker = "NESTING_GOES_HERE"
    body = json.dumps({"base_rev": 0, "manifest": manifest(sections=[{"id": "b", "label": "B", "view": "board",
                                                                      "a2ui": marker}])})
    body = body.replace('"' + marker + '"', "[" * 3000 + "]" * 3000)
    answer = client.open("/tv/v1/workspaces/user/garden", method="PUT", data=body, content_type="application/json",
                         headers=auth(open_session(client, device())))
    assert answer.status_code in (413, 422) and files(tmp_path) == []


@pytest.mark.parametrize("method, route", [("PUT", ""), ("POST", "/validate"), ("DELETE", ""), ("POST", "/rollback")])
def test_a_body_the_json_parser_cannot_nest_deep_enough_to_read_is_a_422_on_every_route(
        client, tmp_path, monkeypatch, method, route):
    """Older Pythons raise RecursionError from json.loads on a deeply nested body; it must never be a server error.
    The parser is made to raise it here so the rule holds on every Python the tests run on."""
    from flask.json.provider import DefaultJSONProvider

    def too_deep(self, text, **options):
        raise RecursionError("maximum recursion depth exceeded while decoding a JSON document")
    tv = device()
    session = auth(open_session(client, tv))
    monkeypatch.setattr(DefaultJSONProvider, "loads", too_deep)
    answer = client.open("/tv/v1/workspaces/user/garden" + route, method=method, data="{}",
                         content_type="application/json", headers=session)
    assert answer.status_code == 422 and json.loads(answer.get_data())["rule"] and files(tmp_path) == []


@pytest.mark.parametrize("method, route", [("PUT", ""), ("POST", "/validate"), ("DELETE", ""), ("POST", "/rollback")])
@pytest.mark.parametrize("text", ["[1]", '"x"', "5", "true"])
def test_a_body_that_is_not_a_json_object_is_a_422_on_every_route(client, tmp_path, method, route, text):
    answer = client.open("/tv/v1/workspaces/user/garden" + route, method=method, data=text,
                         content_type="application/json", headers=auth(open_session(client, device())))
    assert answer.status_code == 422 and json.loads(answer.get_data())["rule"] and files(tmp_path) == []


@pytest.mark.parametrize("text", ["line\nbreak", "tab\there", "zero​width", "bidi‮evil", "nul\x00byte", "bom﻿"])
def test_names_and_queries_with_control_or_hidden_characters_are_refused(client, tmp_path, text):
    assert save(client, device(), manifest(label=text)).status_code == 422
    assert save(client, device(), manifest(sections=[section(source={"ws": "media", "kind": "media", "query": text})])).status_code == 422
    assert files(tmp_path) == []


def test_a_body_far_over_the_cap_is_refused_before_it_is_parsed(client, tmp_path):
    big = {"base_rev": 0, "manifest": manifest(sections=[{"id": "b", "label": "B", "view": "board",
                                                          "a2ui": [{"pad": "x" * (300 * 1024)}]}])}
    assert call(client, device(), "PUT", "/tv/v1/workspaces/user/garden", big).status_code == 413 and files(tmp_path) == []


def test_a_rollback_to_a_version_that_no_longer_validates_says_why(client, tmp_path):
    tv = device()
    save(client, tv, manifest())
    save(client, tv, manifest(label="Two"), base_rev=1)
    old = tmp_path / "workspaces" / "user" / ".versions" / "garden" / "000001.json"
    data = json.loads(old.read_text(encoding="utf-8"))
    data["sections"][0]["source"]["kind"] = "gone-kind"
    old.write_text(json.dumps(data), encoding="utf-8")
    answer = call(client, tv, "POST", "/tv/v1/workspaces/user/garden/rollback", {"rev": 1, "base_rev": 2})
    assert answer.status_code == 422 and answer.get_json()["code"] == "invalid"


def test_a_rollback_conflict_carries_the_pcs_version(client):
    tv = device()
    save(client, tv, manifest())
    answer = call(client, tv, "POST", "/tv/v1/workspaces/user/garden/rollback", {"rev": 1, "base_rev": 5})
    assert answer.status_code == 409 and answer.get_json()["current"]["rev"] == 1


def test_one_unreadable_version_does_not_hide_the_others(client, tmp_path):
    tv = device()
    save(client, tv, manifest())
    save(client, tv, manifest(label="Two"), base_rev=1)
    (tmp_path / "workspaces" / "user" / ".versions" / "garden" / "000001.json").write_text("{not json", encoding="utf-8")
    versions = call(client, tv, "GET", "/tv/v1/workspaces/user/garden/versions")
    assert versions.status_code == 200 and [v["rev"] for v in versions.get_json()["versions"]] == [2]


def test_a_save_against_an_id_that_never_existed_is_a_plain_404(client):
    assert save(client, device(), manifest(), base_rev=3).status_code == 404


def test_a_first_save_that_fails_to_go_live_does_not_lock_the_id(client, tmp_path, monkeypatch):
    real_write = pc_userws._write
    failures = []

    def fail_the_live_file_once(path, data):
        if path.name == "garden.json" and not failures:
            failures.append(path)
            raise OSError("disk full")
        return real_write(path, data)
    monkeypatch.setattr(pc_userws, "_write", fail_the_live_file_once)
    tv = device()
    assert save(client, tv, manifest()).status_code == 503
    assert save(client, tv, manifest()).status_code == 200 and files(tmp_path) == ["garden.json"]


def test_a_delete_whose_history_cannot_move_puts_the_workspace_back(client, tmp_path, monkeypatch):
    tv = device()
    save(client, tv, manifest())
    real_replace = pathlib.Path.replace

    def refuse_the_history(self, target):
        if self.parent.name == ".versions":
            raise OSError("in use")
        return real_replace(self, target)
    monkeypatch.setattr(pathlib.Path, "replace", refuse_the_history)
    assert call(client, tv, "DELETE", "/tv/v1/workspaces/user/garden").status_code == 503
    monkeypatch.setattr(pathlib.Path, "replace", real_replace)
    assert files(tmp_path) == ["garden.json"]
    assert call(client, tv, "GET", "/tv/v1/workspaces/user/garden/versions").get_json()["versions"]


def test_a_restore_whose_history_cannot_move_leaves_everything_in_the_trash(client, tmp_path, monkeypatch):
    tv = device()
    save(client, tv, manifest())
    call(client, tv, "DELETE", "/tv/v1/workspaces/user/garden")
    real_replace = pathlib.Path.replace

    def refuse_the_history(self, target):
        if self.name == "versions":
            raise OSError("in use")
        return real_replace(self, target)
    monkeypatch.setattr(pathlib.Path, "replace", refuse_the_history)
    assert call(client, tv, "POST", "/tv/v1/workspaces/user/garden/restore").status_code == 503
    monkeypatch.setattr(pathlib.Path, "replace", real_replace)
    assert files(tmp_path) == []
    assert call(client, tv, "POST", "/tv/v1/workspaces/user/garden/restore").status_code == 200


@pytest.mark.parametrize("text", ["tag\U000e0041char", "line sep", "soft­hyphen", "para sep", "mongolian᠎"])
def test_every_hidden_format_character_is_refused(client, tmp_path, text):
    assert save(client, device(), manifest(label=text)).status_code == 422 and files(tmp_path) == []


def test_another_workspaces_trash_entry_does_not_make_a_missing_one_gone(client, tmp_path):
    tv = device()
    (tmp_path / "workspaces" / "user" / ".trash" / "garden-two-123").mkdir(parents=True)
    assert save(client, tv, manifest(), base_rev=2).status_code == 404


@pytest.mark.parametrize("base", [True, "3", -1, 1.5])
def test_a_delete_with_a_base_rev_that_is_not_a_whole_number_is_refused(client, tmp_path, base):
    tv = device()
    save(client, tv, manifest())
    assert call(client, tv, "DELETE", "/tv/v1/workspaces/user/garden", {"base_rev": base}).status_code == 400
    assert files(tmp_path) == ["garden.json"]

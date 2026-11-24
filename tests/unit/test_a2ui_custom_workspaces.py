"""His custom workspaces as A2UI documents (contract draft 3, D-D; slice 2).

Stephen's custom workspaces are Studio customizations over builtin workspaces (FutureSpeak: an accent, compact
spacing, styles and three quick actions). They are re-authored, not converted:

* FutureSpeak is a builtin A2UI document (V1-V14, authorship builtin, no data of its own); its one binding reads his
  projects through the broker's closed vocabulary (futurespeak.projects, the "projects" section);
* his Studio look is carried as document properties: accent, density, note and summary, each only where the document
  can take it. Styles (CSS), quick actions and Studio's hidden selectors are not carried: the contract allows no CSS or
  code, and quick actions wait for verbs (D-E);
* until the first A2UI change the carried look follows Studio and the view's rev moves with it, so a phone holding the
  old rev is stale, never merged; the first change keeps the carried look as the earlier version and merges onto it.
Synthetic data only.
"""
import json

import pytest

from agent_friday.services import a2ui_broker as broker
from agent_friday.services import a2ui_render as rend
from agent_friday.services import a2ui_store as store
from agent_friday.services import a2ui_workspaces as aws
from agent_friday.services import workspace_studio as studio
from agent_friday.tv_gateway import pc_read
from tests.unit.test_tv_pc_approve_decide import Enrolled, _clean, client  # noqa: F401  (fixtures)

VIEW = "/tv/v1/workspaces/futurespeak/view"
HIS_LOOK = {"accent": "#3ddc97", "density": "compact", "css": ".fs-card{border-radius:12px}",
            "actions": [{"label": "Draft a post", "prompt": "Draft a blog post"}], "note": "Ship the site this week",
            "summary": "Company sites and how they stand", "hidden": [".fs-legal"]}
CARRIED = {"accent": "#3ddc97", "density": "compact", "note": "Ship the site this week",
           "summary": "Company sites and how they stand"}
PROJECTS = [{"name": "zeta.site", "status": {"deploy_status": "yellow", "branch": "main", "uncommitted": 2,
                                             "last_commit_date": "2026-11-20T09:30:00-06:00"}},
            {"name": "Alpha", "status": {"deploy_status": "green", "branch": "main", "uncommitted": 0,
                                         "last_commit_date": None}},
            {"name": "www.example.test", "status": {"deploy_status": "green"}}]


@pytest.fixture(autouse=True)
def _homes(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "VIEWS_DIR", tmp_path / "liquid" / "views")
    monkeypatch.setattr(store, "_admit_write", lambda: 0)
    monkeypatch.setattr(store, "_check_write", lambda origin: None)
    monkeypatch.setattr(store, "_announce", lambda view_id: None)
    (tmp_path / "studio").mkdir()
    monkeypatch.setattr(studio, "WS_STUDIO_DIR", tmp_path / "studio")

    def desk(path, params=None):
        if path == "/api/futurespeak/projects":
            return 200, json.dumps({"status": "ok", "projects": PROJECTS}).encode(), "application/json"
        return 404, b"{}", "application/json"
    monkeypatch.setattr(pc_read, "_desk", desk)


def studio_look(ws, look):
    (studio.WS_STUDIO_DIR / (ws + ".json")).write_text(
        json.dumps({"workspace": ws, "chat": [], "customization": look, "versions": []}), encoding="utf-8")


# ── the document ───────────────────────────────────────────────────────────

def test_futurespeak_is_a_builtin_document_with_no_data_of_its_own():
    doc = aws.builtin("futurespeak")
    store._validated(doc, aws.ctx("futurespeak"))          # V1-V14 as authored builtin, or it raises
    assert broker.check_bindings(doc) == [("/futurespeak/projects", "futurespeak.projects", {})]
    assert broker.sections_read(doc) == {"projects"}
    assert aws.LABELS["futurespeak"] == "Sites"          # the registry's name for it


def test_his_projects_reach_the_document_through_the_broker_and_a_url_row_is_dropped():
    got = broker.resolve(aws.builtin("futurespeak"), sensitivity="personal")
    assert got["data"]["futurespeak"]["projects"] == [
        {"name": "Alpha", "status": "live", "when": None},
        {"name": "zeta.site", "status": "changes waiting", "when": "2026-11-20T15:30:00Z"}]
    assert got["dropped"] == 1
    rendered = rend.render(aws.builtin("futurespeak"), aws.ctx("futurespeak"), data=got["data"])
    assert rendered.ok, rendered.receipt


@pytest.mark.parametrize("params", [{"limit": 5}, {"q": "x"}])
def test_the_projects_query_takes_no_parameters(params):
    doc = aws.builtin("futurespeak")
    doc[0]["createSurface"]["metadata"]["friday"]["bindings"]["projects"]["params"] = params
    with pytest.raises(broker.BrokerRefused):
        broker.check_bindings(doc)


# ── his Studio look, carried as document properties ────────────────────────

def test_his_studio_look_is_carried_as_document_properties_and_styles_and_actions_are_not():
    studio_look("futurespeak", HIS_LOOK)
    view = aws.current("futurespeak")
    assert view["customization"] == CARRIED
    assert view["stored"] is False and view["rev"].startswith("builtin-")


def test_a_studio_look_the_document_cannot_take_is_left_out_key_by_key():
    studio_look("futurespeak", {"accent": "#3d9", "density": "compact", "summary": "x" * 201})
    assert aws.current("futurespeak")["customization"] == {"density": "compact"}


def test_no_studio_look_and_an_unreadable_one_both_carry_nothing():
    assert aws.current("futurespeak")["customization"] == {}
    (studio.WS_STUDIO_DIR / "futurespeak.json").write_text("{not json", encoding="utf-8")
    assert aws.current("futurespeak")["customization"] == {}


def test_the_rev_moves_with_the_carried_look_so_an_old_rev_is_stale():
    before = aws.current("futurespeak")["rev"]
    assert before == aws.builtin_rev("futurespeak")
    studio_look("futurespeak", {"accent": "#3ddc97"})
    after = aws.current("futurespeak")["rev"]
    assert after != before and after == aws.builtin_rev("futurespeak") and after.startswith("builtin-")
    with pytest.raises(store.ViewStale):
        aws.apply_change("futurespeak", {"customization": {"density": "compact"}}, base_rev=before, by="device:test")
    assert store.load("futurespeak") is None


def test_a_workspace_with_no_studio_look_keeps_its_old_rev():
    # Knowledge's rev is the sha of its document alone, as before slice 2; a phone holding it is not made stale
    import hashlib
    raw = json.dumps(aws.builtin("knowledge"), sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    assert aws.builtin_rev("knowledge") == "builtin-" + hashlib.sha256(raw).hexdigest()


def test_the_first_change_keeps_the_carried_look_as_the_earlier_version_and_merges_onto_it():
    studio_look("futurespeak", HIS_LOOK)
    got = aws.apply_change("futurespeak", {"customization": {"density": "comfortable"}},
                           base_rev=aws.current("futurespeak")["rev"], by="device:test")
    stored = store.load("futurespeak")
    assert stored["rev"] == got["rev"] and stored["customization"] == dict(CARRIED, density="comfortable")
    assert [v["customization"] for v in stored["versions"]] == [CARRIED]
    # from here the document is the record: a later Studio change no longer moves it
    studio_look("futurespeak", {"accent": "#ff0000"})
    assert aws.current("futurespeak")["customization"] == dict(CARRIED, density="comfortable")


# ── on the phone ───────────────────────────────────────────────────────────

def test_the_phone_gets_futurespeak_with_his_projects_and_his_look(client):
    studio_look("futurespeak", HIS_LOOK)
    dev = Enrolled(client)
    r = dev.get(VIEW)
    assert r.status_code == 200, r.get_json()
    body = r.get_json()
    assert body["doc"] == aws.builtin("futurespeak") and body["customization"] == CARRIED
    assert [p["name"] for p in body["data"]["futurespeak"]["projects"]] == ["Alpha", "zeta.site"]
    text = r.get_data(as_text=True)
    assert "border-radius" not in text and "Draft a blog post" not in text and ".fs-legal" not in text
    rows = dev.get("/tv/v1/workspaces").get_json()["workspaces"]
    assert next(w for w in rows if w["id"] == "futurespeak")["renderer"] == "a2ui"

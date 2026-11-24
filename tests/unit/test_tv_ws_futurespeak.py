"""The FutureSpeak workspace's projects, as the owner's phone reads them (PW-G2, adapter 9; A2UI slice 2).

FutureSpeak had a verb list and nothing to draw on the phone. These tests hold the project list
(tv_gateway/ws_futurespeak.py) over a stand-in for the desktop's /api/futurespeak/projects:

* each project is a row (name, how its deploy stands, its branch and uncommitted changes, when it last changed), and
  the deploy state narrows the list;
* no folder path, site or Replit address, description or commit message leaves the PC, anywhere;
* a project list the PC could not read is a 503, never "no projects";
* a name that is not a plain project name never reaches the desktop, and one that is not there is a 404.
Synthetic data only.
"""
import json
from urllib.parse import unquote

import pytest

from agent_friday.tv_gateway import pc_read
from tests.unit import test_tv_pc_read as shared
from tests.unit.test_tv_pc_read import get, paired

client = shared.client
_clean = shared._clean

HERE = "D:/work/sites/"                                     # a folder path the phone must never see
SECRET_WORDS = ("D:/work", "example-site.test", "replit.example.test", "Private plan", "Secret commit")


def project(name, deploy="green", **status):
    base = {"ahead": 0, "behind": 0, "branch": "main", "cloned": True, "deploy_status": deploy,
            "deployment_verified": False, "dirty": False, "error": None, "has_replit": False,
            "last_commit_date": "2026-11-20T09:30:00-06:00", "last_commit_msg": "Secret commit message",
            "last_commit_rel": "3 days ago", "status_source": "git", "uncommitted": 0}
    base.update(status)
    return {"name": name, "category": "company", "description": "Private plan for " + name,
            "repo": name, "repo_path": HERE + name, "url": "https://example-site.test/" + name,
            "replit_url": "https://replit.example.test/" + name, "status": base}


class Desk:
    def __init__(self):
        self.calls, self.down = [], False
        self.projects = [project("zeta.site", "yellow", branch="credibility", dirty=True, uncommitted=3),
                         project("Alpha"), project("Beta Shop", "remote", branch=None, cloned=False,
                                                   last_commit_date=None, status_source="saved_reference"),
                         project("../up"), project("odd", "purple")]

    def __call__(self, path, params=None):
        self.calls.append((path, dict(params or {})))
        if self.down:
            return 500, b"{}", "application/json"
        if path == "/api/futurespeak/projects":
            return 200, json.dumps({"status": "ok", "projects": self.projects}).encode(), "application/json"
        if path.startswith("/api/futurespeak/project/"):
            name = unquote(path[len("/api/futurespeak/project/"):])
            found = next((p for p in self.projects or [] if p["name"].lower() == name.lower()), None)
            if found is None:
                return 404, b'{"status":"error"}', "application/json"
            return 200, json.dumps({"status": "ok", "project": found}).encode(), "application/json"
        return 404, b"{}", "application/json"


@pytest.fixture
def desk(monkeypatch):
    d = Desk()
    monkeypatch.setattr(pc_read, "_desk", d)
    return d


def listing(client, query=""):
    r = get(client, paired(), "/tv/v1/workspaces/futurespeak/list" + query)
    assert r.status_code == 200, r.get_json()
    return r.get_json()


def test_projects_are_by_name_with_the_contracts_fields(client, desk):
    got = listing(client)
    assert [r["title"] for r in got["rows"]] == ["Alpha", "Beta Shop", "odd", "zeta.site"]   # "../up" is left out
    assert got["rows"][3] == {"ref": "project:zeta.site", "kind": "project", "title": "zeta.site",
                              "line": "changes waiting · credibility · 3 uncommitted", "state": "yellow",
                              "when": "2026-11-20T15:30:00Z", "thumb": None, "lives_on": "pc",
                              "sensitivity": "personal", "taint": None}
    assert got["rows"][0]["line"] == "live · main" and got["rows"][0]["state"] == "green"
    assert got["rows"][1]["line"] == "not on this PC" and got["rows"][1]["when"] is None
    assert got["rows"][2]["state"] is None and got["rows"][2]["line"] == "main", "an unknown deploy state is no state"
    assert desk.calls == [("/api/futurespeak/projects", {})]


def test_the_deploy_facet_counts_and_narrows(client, desk):
    got = listing(client)
    assert got["facets"] == [{"name": "deploy", "values": [{"v": "", "n": 1}, {"v": "green", "n": 1},
                                                           {"v": "remote", "n": 1}, {"v": "yellow", "n": 1}]}]
    assert [r["title"] for r in listing(client, "?facet.deploy=yellow")["rows"]] == ["zeta.site"]


def test_an_item_says_how_the_project_stands_and_nothing_more(client, desk):
    got = get(client, paired(), "/tv/v1/workspaces/futurespeak/item/project:zeta.site").get_json()
    assert got["title"] == "zeta.site" and got["taint"] is None and got["next"] is None and got["truncated"] is False
    assert got["meta"] == [{"k": "Deploy", "v": "changes waiting"}, {"k": "Branch", "v": "credibility"},
                           {"k": "Uncommitted", "v": "3"}, {"k": "Last change", "v": "3 days ago"}]
    assert got["blocks"] == []


def test_no_path_address_description_or_commit_message_leaves_anywhere(client, desk):
    dev = paired()
    for path in ("list", "search?q=zeta", "item/project:zeta.site", "item/project:Beta Shop"):
        text = get(client, dev, "/tv/v1/workspaces/futurespeak/" + path).get_data(as_text=True)
        for word in SECRET_WORDS:
            assert word not in text, (path, word)
    assert {c[0] for c in desk.calls} <= {"/api/futurespeak/projects", "/api/futurespeak/project/zeta.site",
                                          "/api/futurespeak/project/Beta%20Shop"}


def test_search_finds_a_project_by_name_or_how_it_stands(client, desk):
    dev = paired()
    assert [r["title"] for r in get(client, dev, "/tv/v1/workspaces/futurespeak/search?q=ALPH").get_json()["rows"]] == ["Alpha"]
    assert [r["title"] for r in get(client, dev, "/tv/v1/workspaces/futurespeak/search?q=waiting").get_json()["rows"]] == ["zeta.site"]


def test_a_project_list_the_pc_could_not_read_is_503_never_no_projects(client, desk):
    desk.down = True
    for path in ("list", "search?q=a", "item/project:Alpha"):
        r = get(client, paired(), "/tv/v1/workspaces/futurespeak/" + path)
        assert r.status_code == 503 and r.get_json()["code"] == "pc_unavailable", path
    desk.down, desk.projects = False, None
    assert get(client, paired(), "/tv/v1/workspaces/futurespeak/list").status_code == 503


@pytest.mark.parametrize("ref", ["project:..", "project:a/b", "project:", "project:-x", "repo:Alpha", "project:../up",
                                 "project:" + "x" * 81])
def test_a_name_that_is_not_a_plain_project_name_never_reaches_the_desktop(client, desk, ref):
    assert get(client, paired(), "/tv/v1/workspaces/futurespeak/item/" + ref).status_code == 404
    assert desk.calls == []


def test_a_project_that_is_not_there_is_404(client, desk):
    assert get(client, paired(), "/tv/v1/workspaces/futurespeak/item/project:nope").status_code == 404


def test_an_item_ref_must_name_the_project_exactly(client, desk):
    # the desktop finds names without regard to case; the phone's ref is the name as the list gave it, or nothing
    assert get(client, paired(), "/tv/v1/workspaces/futurespeak/item/project:alpha").status_code == 404


@pytest.mark.parametrize("name", ["Gamma
", "Gamma ", "www.example.test", "shop.example.test/home"])
def test_a_name_that_is_not_plain_or_looks_like_an_address_is_left_out_everywhere(client, desk, name):
    desk.projects.append(project(name))
    dev = paired()
    assert "Gamma" not in [r["title"] for r in listing(client)["rows"]]
    assert all("example.test" not in r["title"] for r in listing(client)["rows"])
    for q in ("Gamma", "example"):
        assert get(client, dev, "/tv/v1/workspaces/futurespeak/search?q=" + q).get_json()["rows"] == []


def test_a_project_named_after_its_site_has_no_item(client, desk):
    desk.projects.append(project("www.example.test"))
    assert get(client, paired(), "/tv/v1/workspaces/futurespeak/item/project:www.example.test").status_code == 404


def test_a_change_time_without_an_offset_is_no_time(client, desk):
    desk.projects = [project("Alpha", last_commit_date="2026-11-20T09:30:00")]
    assert listing(client)["rows"][0]["when"] is None

"""The Code workspace's repositories, as the owner's phone reads them (PW-G2, adapter 8).

The Code workspace had a verb list and nothing to draw on the phone. These tests hold the repository list
(tv_gateway/ws_code.py) over a stand-in for the desktop's repository routes:

* each repository is a row (name, branch, how many files changed, ahead and behind) with a state that narrows;
* an item adds the last commit and the names of the changed files, and never a file's contents, a diff or a folder path;
* a repository list the PC could not read is a 503, never "no repositories";
* a name that is not a plain repository name never reaches the desktop, and one that is not there is a 404.
"""
import json

import pytest

from agent_friday.tv_gateway import pc_read
from tests.unit import test_tv_pc_read as shared
from tests.unit.test_tv_pc_read import get, paired

client = shared.client
_clean = shared._clean

HERE = "D:/work/projects/"                                  # a folder path the phone must never see


def card(name, **more):
    base = {"name": name, "path": HERE + name, "branch": "main", "dirty": 0, "ahead": 0, "behind": 0,
            "last_commit": "abc1234 Fix the thing", "last_when": "2 hours ago", "upstream": True, "clean": True, "error": None}
    base.update(more)
    return base


class Desk:
    def __init__(self):
        self.calls, self.down = [], False
        self.repos = [card("zeta", branch="feat/x", dirty=3, ahead=2, behind=1),
                      card("Alpha"), card("odd name"), card("..", dirty=1), card("broken", error="git timed out")]
        self.files = [{"code": "M", "file": "src/app.py"}, {"code": "?", "file": "notes.txt"}, {"code": "", "file": ""}]

    def __call__(self, path, params=None):
        self.calls.append((path, dict(params or {})))
        if self.down:
            return 500, b"{}", "application/json"
        if path == "/api/repos/scan":
            return 200, json.dumps({"status": "ok", "repos": self.repos, "root": HERE}).encode(), "application/json"
        if path.startswith("/api/repos/") and path.endswith("/status"):
            name = path.split("/")[3]
            found = next((c for c in self.repos or [] if c["name"] == name), None)
            if found is None:
                return 404, b'{"status":"error"}', "application/json"
            return 200, json.dumps({"status": "ok", "repo": dict(found, files=self.files)}).encode(), "application/json"
        return 404, b"{}", "application/json"


@pytest.fixture
def desk(monkeypatch):
    d = Desk()
    monkeypatch.setattr(pc_read, "_desk", d)
    return d


def listing(client, query=""):
    r = get(client, paired(), "/tv/v1/workspaces/code/list" + query)
    assert r.status_code == 200, r.get_json()
    return r.get_json()


def test_repositories_are_by_name_with_the_contracts_fields(client, desk):
    got = listing(client)
    assert [r["title"] for r in got["rows"]] == ["Alpha", "broken", "zeta"]       # a name that is not a plain name is left out
    assert got["rows"][2] == {"ref": "repo:zeta", "kind": "repo", "title": "zeta", "line": "feat/x · 3 changed · 2 ahead · 1 behind",
                              "state": "changed", "when": None, "thumb": None, "lives_on": "pc", "sensitivity": "personal",
                              "taint": None}
    assert got["rows"][0]["line"] == "main · clean" and got["rows"][0]["state"] == "clean"
    assert got["rows"][1]["state"] == "error"
    assert desk.calls == [("/api/repos/scan", {})]


def test_the_state_facet_counts_and_narrows(client, desk):
    got = listing(client)
    assert got["facets"] == [{"name": "state", "values": [{"v": "changed", "n": 1}, {"v": "clean", "n": 1}, {"v": "error", "n": 1}]}]
    assert [r["title"] for r in listing(client, "?facet.state=changed")["rows"]] == ["zeta"]


def test_an_item_has_the_branch_the_last_commit_and_the_names_of_changed_files(client, desk):
    got = get(client, paired(), "/tv/v1/workspaces/code/item/repo:zeta").get_json()
    assert got["title"] == "zeta" and got["taint"] is None and got["next"] is None and got["truncated"] is False
    assert got["meta"] == [{"k": "Branch", "v": "feat/x"}, {"k": "State", "v": "changed"},
                           {"k": "Last commit", "v": "abc1234 Fix the thing"}, {"k": "Committed", "v": "2 hours ago"}]
    assert got["blocks"] == [{"t": "table", "columns": ["Change", "File"], "rows": [["M", "src/app.py"], ["?", "notes.txt"]]}]


def test_no_folder_path_diff_or_file_contents_leave_anywhere(client, desk):
    dev = paired()
    for path in ("list", "search?q=zeta", "item/repo:zeta"):
        text = get(client, dev, "/tv/v1/workspaces/code/" + path).get_data(as_text=True)
        assert "D:/work" not in text and "projects/" not in text, path
    assert {c[0] for c in desk.calls} <= {"/api/repos/scan", "/api/repos/zeta/status"}      # never /api/git/diff or a file route


def test_a_changed_file_keeps_its_name_even_with_underscores_or_a_www_part(client, desk):
    names = ["src/__init__.py", "pkg/__pycache__/x.pyc", "docs/www.notes.md", "a_b/__main__.py", "<odd>.txt"]
    desk.files = [{"code": "M", "file": n} for n in names]
    got = get(client, paired(), "/tv/v1/workspaces/code/item/repo:zeta").get_json()
    assert [row[1] for row in got["blocks"][0]["rows"]] == names


def test_a_changed_file_list_that_swaps_a_name_changes_the_item_revision(client, desk):
    first = get(client, paired(), "/tv/v1/workspaces/code/item/repo:zeta").get_json()["rev"]
    desk.files = [{"code": "M", "file": "src/other.py"}, {"code": "?", "file": "notes.txt"}, {"code": "", "file": ""}]
    assert get(client, paired(), "/tv/v1/workspaces/code/item/repo:zeta").get_json()["rev"] != first


def test_a_very_long_file_list_is_cut_and_says_so(client, desk):
    desk.files = [{"code": "M", "file": "f%d.py" % i} for i in range(250)]
    got = get(client, paired(), "/tv/v1/workspaces/code/item/repo:zeta").get_json()
    table = got["blocks"][0]
    assert len(table["rows"]) == 200 and got["truncated"] is True and "50 more" in got["blocks"][1]["text"]


def test_search_finds_a_repository_by_name_or_branch(client, desk):
    dev = paired()
    assert [r["title"] for r in get(client, dev, "/tv/v1/workspaces/code/search?q=ALPH").get_json()["rows"]] == ["Alpha"]
    assert [r["title"] for r in get(client, dev, "/tv/v1/workspaces/code/search?q=feat/").get_json()["rows"]] == ["zeta"]


def test_a_repository_list_the_pc_could_not_read_is_503_never_no_repositories(client, desk):
    desk.down = True
    for path in ("list", "search?q=a", "item/repo:zeta"):
        r = get(client, paired(), "/tv/v1/workspaces/code/" + path)
        assert r.status_code == 503 and r.get_json()["code"] == "pc_unavailable", path
    desk.down, desk.repos = False, None
    assert get(client, paired(), "/tv/v1/workspaces/code/list").status_code == 503


@pytest.mark.parametrize("ref", ["repo:..", "repo:a/b", "repo:", "repo:-x", "page:x.md", "repo:a b", "repo:" + "x" * 101])
def test_a_name_that_is_not_a_plain_repository_name_never_reaches_the_desktop(client, desk, ref):
    assert get(client, paired(), "/tv/v1/workspaces/code/item/" + ref).status_code == 404
    assert desk.calls == []


def test_a_repository_that_is_not_there_is_404(client, desk):
    assert get(client, paired(), "/tv/v1/workspaces/code/item/repo:nope").status_code == 404

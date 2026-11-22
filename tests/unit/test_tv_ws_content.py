"""A workspace's pages as the owner's phone reads them: list, search, item, changes (PW-G2, Knowledge pages).

Stephen, 2026-11-17: "I want to browse the knowledge graph in 3D and scroll through the wiki." These tests hold
the generic content routes (tv_gateway/ws_content.py) and the Knowledge adapter (ws_knowledge.py):

* a workspace is served only with an exposed device block AND an adapter; otherwise 404, never an empty list;
* rows are paged (at most 40), carry the contract's fields, and name where they live and how private they are;
* searching a name finds the page; a graph that is down does not hide a page found by name;
* an item is plain blocks (at most 60 a page of it, with a cursor), its raw file text and any `..` ref never
  reach the PC's wiki route, a locked page is 423 and a PC that cannot answer is 503;
* only an owner phone or tablet with the right may read any of it.
"""
import json

import pytest

from agent_friday.tv_gateway import pc_read, ws_content, ws_knowledge
from tests.unit import test_tv_pc_read as shared
from tests.unit.test_tv_pc_read import get, paired

client = shared.client
_clean = shared._clean

PAGE = "# Griff Jay\n\nA beta tester. See [Brushfire](../projects/brushfire.md).\n\n- one\n- two\n"


class Desk:
    def __init__(self):
        self.calls, self.down = [], set()
        self.structure = {"entities": [{"name": "person-griff-jay", "filename": "person-griff-jay.md", "modified": 1700000300},
                                       {"name": "person-jere-jay", "filename": "person-jere-jay.md", "modified": 1700000200}],
                          "projects": [{"name": "brushfire", "filename": "brushfire.md", "modified": 1700000100}]}
        self.pages = {"entities/person-griff-jay.md": PAGE}
        self.graph = []

    def __call__(self, path, params=None):
        self.calls.append((path, dict(params or {})))
        if path in self.down:
            return 500, b"{}", "application/json"
        if path == "/api/wiki/structure":
            return 200, json.dumps({"status": "ok", "structure": self.structure}).encode(), "application/json"
        if path == "/api/wiki/page":
            text = self.pages.get((params or {}).get("path"))
            if text is None:
                return 404, b'{"status":"not_found"}', "application/json"
            return 200, json.dumps({"status": "ok", "content": text, "modified": 1700000300}).encode(), "application/json"
        if path == "/api/knowledge-graph/search":
            return 200, json.dumps({"status": "ok", "results": self.graph}).encode(), "application/json"
        return 404, b"{}", "application/json"


@pytest.fixture
def desk(monkeypatch):
    d = Desk()
    monkeypatch.setattr(pc_read, "_desk", d)
    monkeypatch.setattr(ws_knowledge, "PAGES_TTL_S", 0.0)
    monkeypatch.setattr(ws_knowledge, "_LIST", {"key": None, "at": 0.0, "rows": []})
    return d


def rows(client, query="", dev=None):
    r = get(client, dev or paired(), "/tv/v1/workspaces/knowledge/list" + query)
    assert r.status_code == 200, r.get_json()
    return r.get_json()


def test_the_list_is_newest_first_with_the_contracts_fields(client, desk):
    got = rows(client)
    assert [r["ref"] for r in got["rows"]] == ["page:entities/person-griff-jay.md", "page:entities/person-jere-jay.md",
                                               "page:projects/brushfire.md"]
    first = got["rows"][0]
    assert first == {"ref": "page:entities/person-griff-jay.md", "kind": "page", "title": "person griff jay",
                     "line": "entities", "state": None, "when": "2023-11-14T22:18:20Z", "thumb": None,
                     "lives_on": "pc", "sensitivity": "personal", "taint": None}
    assert got["total"] == 3 and got["next"] is None and got["rev"].startswith("k_3_")
    assert got["facets"] == [{"name": "folder", "values": [{"v": "entities", "n": 2}, {"v": "projects", "n": 1}]}]


def test_the_list_is_paged_at_forty_and_a_folder_facet_narrows_it(client, desk):
    desk.structure = {"big": [{"name": "p%03d" % i, "filename": "p%03d.md" % i, "modified": i} for i in range(95)],
                      "small": [{"name": "s", "filename": "s.md", "modified": 1}]}
    first = rows(client)
    assert len(first["rows"]) == 40 and first["next"] == "40" and first["total"] == 96
    last = rows(client, "?cursor=80")
    assert len(last["rows"]) == 16 and last["next"] is None
    assert rows(client, "?limit=9999")["rows"].__len__() == 40
    assert rows(client, "?facet.folder=small")["total"] == 1


def test_a_section_the_workspace_does_not_serve_is_unknown(client, desk):
    assert get(client, paired(), "/tv/v1/workspaces/knowledge/list?section=graph").status_code == 404
    assert get(client, paired(), "/tv/v1/workspaces/knowledge/list?section=nope").status_code == 404


def test_a_workspace_with_no_device_block_or_no_adapter_is_unknown_not_empty(client, desk, monkeypatch):
    dev = paired()
    assert get(client, dev, "/tv/v1/workspaces/news/list").status_code == 404       # no block
    monkeypatch.setitem(ws_content.ADAPTERS, "news", {"pages": ws_knowledge})
    assert get(client, dev, "/tv/v1/workspaces/news/list").status_code == 404       # an adapter but no block
    monkeypatch.delitem(ws_content.ADAPTERS, "knowledge")
    assert get(client, dev, "/tv/v1/workspaces/knowledge/list").status_code == 404       # a block but no adapter
    assert get(client, dev, "/tv/v1/workspaces/..%2Fx/list").status_code == 404


def test_search_finds_a_page_by_its_name(client, desk):
    got = get(client, paired(), "/tv/v1/workspaces/knowledge/search?q=Griff").get_json()
    assert [r["ref"] for r in got["rows"]] == ["page:entities/person-griff-jay.md"] and got["total"] == 1


def test_search_adds_pages_the_graph_found_and_keeps_name_hits_when_the_graph_is_down(client, desk):
    desk.graph = [{"id": "n1", "title": "Garden plan", "type": "project", "description": "beds and <b>paths</b>",
                   "path": "projects/garden.md"}, {"id": "n2", "title": "No page", "path": None}]
    got = get(client, paired(), "/tv/v1/workspaces/knowledge/search?q=jay").get_json()
    assert [r["ref"] for r in got["rows"]] == ["page:entities/person-griff-jay.md", "page:entities/person-jere-jay.md",
                                               "page:projects/garden.md"]
    assert got["rows"][2]["snippet"] == "beds and paths" and got["rows"][2]["line"] == "project"
    desk.down.add("/api/knowledge-graph/search")
    again = get(client, paired(), "/tv/v1/workspaces/knowledge/search?q=jay").get_json()
    assert len(again["rows"]) == 2


def test_search_needs_words_and_refuses_a_huge_query(client, desk):
    dev = paired()
    assert get(client, dev, "/tv/v1/workspaces/knowledge/search").status_code == 400
    assert get(client, dev, "/tv/v1/workspaces/knowledge/search?q=" + "x" * 201).status_code == 413


def test_an_item_is_plain_blocks_with_meta_links_and_the_verbs_for_its_kind(client, desk):
    r = get(client, paired(), "/tv/v1/workspaces/knowledge/item/page:entities/person-griff-jay.md")
    got = r.get_json()
    assert r.status_code == 200 and got["title"] == "Griff Jay" and got["kind"] == "page" and got["next"] is None
    assert [b["t"] for b in got["blocks"]] == ["heading", "para", "link", "list"]
    assert got["blocks"][2] == {"t": "link", "text": "Brushfire", "ref": "page:projects/brushfire.md"}
    assert got["meta"][0] == {"k": "Folder", "v": "entities"} and got["rev"] == "p_1700000300"
    assert "knowledge.create_edit" in got["verbs"] and got["relations"] == [] and got["lives_on"] == "pc"
    assert "A beta tester" in json.dumps(got) and "---" not in json.dumps(got)


def test_a_long_page_comes_in_blocks_of_sixty_with_a_cursor(client, desk):
    desk.pages["entities/long.md"] = "\n\n".join("para %d" % i for i in range(130))
    dev = paired()
    one = get(client, dev, "/tv/v1/workspaces/knowledge/item/page:entities/long.md").get_json()
    assert len(one["blocks"]) == 60 and one["next"] == "60"
    three = get(client, dev, "/tv/v1/workspaces/knowledge/item/page:entities/long.md?cursor=120").get_json()
    assert len(three["blocks"]) == 10 and three["next"] is None and three["blocks"][0]["text"] == "para 120"


@pytest.mark.parametrize("ref", ["page:../secret.md", "page:/etc/passwd.md", "page:a/../../b.md", "page:a\\b.md",
                                 "page:x.png", "note:a.md", "page:", "page:a//b.md", "page:./a.md"])
def test_a_ref_that_is_not_a_wiki_page_never_reaches_the_pcs_wiki(client, desk, ref):
    assert get(client, paired(), "/tv/v1/workspaces/knowledge/item/" + ref).status_code == 404
    assert not [c for c in desk.calls if c[0] == "/api/wiki/page"]


def test_a_page_that_is_not_there_is_404_a_locked_one_is_423_and_a_down_wiki_is_503(client, desk):
    dev = paired()
    assert get(client, dev, "/tv/v1/workspaces/knowledge/item/page:entities/none.md").status_code == 404
    desk.pages["entities/locked.md"] = "[vault-encrypted file — set FRIDAY_PASSWORD to read it]"
    r = get(client, dev, "/tv/v1/workspaces/knowledge/item/page:entities/locked.md")
    assert r.status_code == 423 and r.get_json()["code"] == "locked" and "vault" not in r.get_data(as_text=True)
    desk.down.add("/api/wiki/structure")
    r = get(client, dev, "/tv/v1/workspaces/knowledge/list")
    assert r.status_code == 503 and r.get_json()["code"] == "pc_unavailable"


def test_changes_says_whether_the_rev_moved(client, desk):
    dev = paired()
    rev = rows(client)["rev"]
    assert get(client, dev, "/tv/v1/workspaces/knowledge/changes?section=pages&since=" + rev).get_json()["changed"] is False
    desk.structure["entities"][0]["modified"] += 50
    again = get(client, dev, "/tv/v1/workspaces/knowledge/changes?section=pages&since=" + rev).get_json()
    assert again["changed"] is True and again["rev"] != rev


def test_a_page_too_big_for_a_phone_is_refused_not_sent(client, desk, monkeypatch):
    monkeypatch.setattr(ws_content, "MAX_BODY", 200)
    assert get(client, paired(), "/tv/v1/workspaces/knowledge/list").status_code == 413


def test_only_an_owner_phone_or_tablet_with_the_right_may_read_pages(client, desk):
    for path in ("list", "search?q=a", "item/page:entities/person-griff-jay.md", "changes"):
        assert get(client, paired("tv"), "/tv/v1/workspaces/knowledge/" + path).status_code == 403, path
        assert get(client, paired(right=False), "/tv/v1/workspaces/knowledge/" + path).status_code == 403, path


def test_a_huge_page_is_cut_at_a_line_and_says_so_and_a_paged_read_parses_it_once(client, desk, monkeypatch):
    big = "\n\n".join("paragraph number %d with some words" % i for i in range(60000))
    desk.pages["entities/huge.md"] = big + "\n"
    assert len(big) > ws_knowledge.MAX_SOURCE
    calls = []
    real = ws_knowledge.ws_blocks.parse
    monkeypatch.setattr(ws_knowledge.ws_blocks, "parse", lambda text, folder: calls.append(len(text)) or real(text, folder))
    monkeypatch.setattr(ws_knowledge, "_PARSED", type(ws_knowledge._PARSED)())
    dev = paired()
    one = get(client, dev, "/tv/v1/workspaces/knowledge/item/page:entities/huge.md").get_json()
    two = get(client, dev, "/tv/v1/workspaces/knowledge/item/page:entities/huge.md?cursor=60").get_json()
    assert one["truncated"] is True and two["blocks"][0]["text"] == "paragraph number 60 with some words"
    assert len(calls) == 1 and calls[0] <= ws_knowledge.MAX_SOURCE and one["next"] == "60"


def test_a_normal_page_is_not_marked_truncated_and_a_changed_page_is_parsed_again(client, desk, monkeypatch):
    monkeypatch.setattr(ws_knowledge, "_PARSED", type(ws_knowledge._PARSED)())
    dev = paired()
    path = "/tv/v1/workspaces/knowledge/item/page:entities/person-griff-jay.md"
    assert get(client, dev, path).get_json()["truncated"] is False
    desk.pages["entities/person-griff-jay.md"] = "# Changed\n\nnew words\n"
    assert get(client, dev, path).get_json()["title"] == "Changed"


def test_paging_the_list_scans_the_wiki_once_within_the_ttl_and_again_after_it(client, desk, monkeypatch):
    monkeypatch.setattr(ws_knowledge, "PAGES_TTL_S", 60.0)
    clock = [1000.0]
    monkeypatch.setattr(ws_knowledge.time, "monotonic", lambda: clock[0])
    dev = paired()
    for query in ("", "?cursor=40", "?facet.folder=projects"):
        assert get(client, dev, "/tv/v1/workspaces/knowledge/list" + query).status_code == 200
    assert [c[0] for c in desk.calls].count("/api/wiki/structure") == 1
    clock[0] += 61
    get(client, dev, "/tv/v1/workspaces/knowledge/list")
    assert [c[0] for c in desk.calls].count("/api/wiki/structure") == 2

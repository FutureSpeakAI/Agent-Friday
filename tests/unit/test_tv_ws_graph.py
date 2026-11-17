"""The knowledge graph by level of detail, as the owner's phone reads it (PW-G3).

Stephen, 2026-11-17: "I want to browse the knowledge graph in 3D." A phone cannot take the desktop's whole graph
in one answer, so (tv_gateway/ws_graph.py):

* level 0 is the communities as super-nodes and the links between them, at most 300;
* level 1 is one community's nodes by degree, a page at a time, with the edges among the nodes already sent, and
  paging never renumbers a node or sends an edge twice;
* a node's neighbourhood and a search come back small and capped;
* no description, no wiki path and no URL is in any node, and an id that tries to leave the graph never reaches the PC;
* a PC that cannot read its graph is a 503, and an answer too big for a phone is refused, not sent.
"""
import json

import pytest

from agent_friday.tv_gateway import pc_read, ws_graph
from tests.unit import test_tv_pc_read as shared
from tests.unit.test_tv_pc_read import get, paired

client = shared.client
_clean = shared._clean


def ent(i, community, degree, **extra):
    return {"id": i, "title": "Node " + i, "type": "person", "community": community, "degree": degree,
            "x": float(len(i)), "y": 2.0, "z": 3.0, "description": "SECRET DESC",
            "provenance": {"wiki_pages": ["secret/x.md"]}, **extra}


ENTITIES = [ent("a", 0, 3), ent("b", 0, 2), ent("c", 0, 3), ent("d", 1, 2), ent("e", 1, 1)]
RELS = [{"id": "r1", "source": "a", "target": "b", "weight": 1}, {"id": "r2", "source": "b", "target": "c", "weight": 2},
        {"id": "r3", "source": "a", "target": "c", "weight": 1}, {"id": "r4", "source": "c", "target": "d", "weight": 1},
        {"id": "r5", "source": "d", "target": "e", "weight": 1}]


class Desk:
    def __init__(self):
        self.calls, self.down = [], set()
        self.entities, self.rels = list(ENTITIES), list(RELS)
        self.communities = [{"id": 0, "title": "Family", "size": 3}, {"id": 1, "title": "Work", "size": 2}]
        self.search = []

    def ok(self, body):
        return 200, json.dumps({"status": "ok", **body}).encode(), "application/json"

    def __call__(self, path, params=None):
        self.calls.append((path, dict(params or {})))
        if any(path.startswith(d) for d in self.down):
            return 500, b"{}", "application/json"
        if path == "/api/knowledge-graph/summary":
            return self.ok({"counts": {"entities": len(self.entities), "relationships": len(self.rels)},
                            "last_indexed": "2026-11-17T10:00:00", "communities": self.communities})
        if path == "/api/knowledge-graph/graph":
            c = (params or {}).get("community")
            ents = [e for e in self.entities if c is None or str(e["community"]) == str(c)]
            ids = {e["id"] for e in ents}
            return self.ok({"entities": ents, "communities": self.communities,
                            "relationships": [r for r in self.rels if r["source"] in ids and r["target"] in ids]})
        if path.startswith("/api/knowledge-graph/neighbors/"):
            nid = path.removeprefix("/api/knowledge-graph/neighbors/").replace("%2F", "/")
            if nid not in {e["id"] for e in self.entities}:
                return 404, b'{"status":"not_found"}', "application/json"
            return self.ok({"entities": self.entities, "relationships": self.rels})
        if path == "/api/knowledge-graph/search":
            return self.ok({"results": self.search})
        return 404, b"{}", "application/json"


@pytest.fixture
def desk(monkeypatch):
    d = Desk()
    monkeypatch.setattr(pc_read, "_desk", d)
    return d


def ask(client, path, dev=None):
    return get(client, dev or paired(), path)


def test_the_summary_has_counts_communities_a_centre_and_a_rev(client, desk):
    got = ask(client, "/tv/v1/graph/summary").get_json()
    assert got["counts"] == {"entities": 5, "relationships": 5} and got["rev"] == "g_5_5_20261117T100000"
    assert got["communities"][0] == {"id": 0, "label": "Family", "size": 3, "centre": [1.0, 2.0, 3.0]}


def test_level_zero_is_communities_by_size_with_the_links_between_them(client, desk):
    got = ask(client, "/tv/v1/graph?lod=0").get_json()
    assert got["level"] == 0 and got["next"] is None
    assert [(n["id"], n["label"], n["type"], n["degree"]) for n in got["nodes"]] == [("0", "Family", "community", 3),
                                                                                  ("1", "Work", "community", 2)]
    assert got["edges"] == [[0, 1, 1]]


def test_level_zero_is_capped_at_three_hundred_communities(client, desk):
    desk.entities = [ent("n%d" % i, i, 1) for i in range(400)]
    desk.rels = []
    desk.communities = [{"id": i, "title": "C%d" % i, "size": 1} for i in range(400)]
    got = ask(client, "/tv/v1/graph?lod=0").get_json()
    assert len(got["nodes"]) == ws_graph.LOD0_NODES_MAX and [n["i"] for n in got["nodes"]] == list(range(300))


def test_level_one_ranks_a_community_by_degree_and_numbers_the_nodes_by_rank(client, desk):
    got = ask(client, "/tv/v1/graph?lod=1&community=0").get_json()
    assert [(n["i"], n["id"]) for n in got["nodes"]] == [(0, "a"), (1, "c"), (2, "b")] and got["total"] == 3
    assert got["nodes"][0] == {"i": 0, "id": "a", "label": "Node a", "type": "person", "community": 0, "degree": 3,
                               "pos": [1.0, 2.0, 3.0]}
    assert sorted(got["edges"]) == [[0, 1, 1.0], [0, 2, 1.0], [2, 1, 2.0]] and got["next"] is None


@pytest.mark.parametrize("limit", [1, 2, 3])
def test_paging_a_community_never_renumbers_a_node_or_sends_an_edge_twice(client, desk, limit):
    desk.entities = [ent("n%02d" % i, 0, 20 - i) for i in range(12)]
    desk.rels = [{"id": "r%d%d" % (i, j), "source": "n%02d" % i, "target": "n%02d" % j, "weight": 1}
                 for i in range(12) for j in range(i + 1, 12) if (i * 7 + j) % 3 == 0]
    whole = ask(client, "/tv/v1/graph?lod=1&community=0&limit=500").get_json()
    nodes, edges, cursor = [], [], 0
    while cursor is not None:
        page = ask(client, "/tv/v1/graph?lod=1&community=0&limit=%d&cursor=%d" % (limit, cursor)).get_json()
        nodes += page["nodes"]
        edges += page["edges"]
        cursor = int(page["next"]) if page["next"] else None
    assert nodes == whole["nodes"] and [n["i"] for n in nodes] == list(range(12))
    assert len(edges) == len({tuple(e[:2]) for e in edges}) and sorted(edges) == sorted(whole["edges"])
    assert desk.rels and len(edges) == len(desk.rels)


def test_level_one_pages_are_capped_at_five_hundred(client, desk):
    desk.entities = [ent("n%04d" % i, 0, 1) for i in range(700)]
    desk.rels = []
    got = ask(client, "/tv/v1/graph?lod=1&community=0&limit=99999").get_json()
    assert len(got["nodes"]) == 500 and got["next"] == "500" and got["total"] == 700


@pytest.mark.parametrize("query,status", [("lod=1", 400), ("lod=2&community=0", 400), ("lod=1&community=" + "9" * 41, 400),
                                          ("lod=1&community=99", 404)])
def test_a_bad_level_or_unknown_community_is_refused(client, desk, query, status):
    assert ask(client, "/tv/v1/graph?" + query).status_code == status


def test_neighbours_put_the_node_first_and_are_capped(client, desk):
    desk.entities = [ent("hub", 0, 50)] + [ent("n%03d" % i, 0, i) for i in range(300)]
    desk.rels = [{"id": "r%d" % i, "source": "hub", "target": "n%03d" % i, "weight": 1} for i in range(300)]
    got = ask(client, "/tv/v1/graph/node/hub/neighbors?depth=9&limit=9999").get_json()
    assert got["center"] == "hub" and got["nodes"][0]["id"] == "hub" and len(got["nodes"]) == 200
    assert all(max(a, b) < 200 for a, b, _ in got["edges"])
    assert desk.calls[-1] == ("/api/knowledge-graph/neighbors/hub", {"depth": 2})


def test_a_node_id_with_a_slash_or_colon_reaches_the_pc_whole(client, desk):
    desk.entities = [ent("person:griff/jay", 0, 1)]
    desk.rels = []
    got = ask(client, "/tv/v1/graph/node/person:griff/jay/neighbors").get_json()
    assert [n["id"] for n in got["nodes"]] == ["person:griff/jay"]


@pytest.mark.parametrize("node", ["a/../b", "../a", "a\\b", "x" * 201])
def test_an_id_that_tries_to_leave_the_graph_never_reaches_the_pc(client, desk, node):
    assert ask(client, "/tv/v1/graph/node/" + node + "/neighbors").status_code == 404
    assert not [c for c in desk.calls if c[0].startswith("/api/knowledge-graph/neighbors")]


def test_an_unknown_node_is_404(client, desk):
    assert ask(client, "/tv/v1/graph/node/nope/neighbors").status_code == 404


def test_search_rows_carry_the_community_and_a_page_ref_only_for_a_page(client, desk):
    desk.search = [{"id": "a", "title": "Node a", "type": "person", "community": 0, "path": "entities/a.md"},
                   {"id": "b", "title": "Node b", "type": "idea", "community": 1, "path": None},
                   {"id": "c", "title": "Node c", "type": "idea", "community": 1, "path": "x/c.png"}, {"title": "no id"}]
    rows = ask(client, "/tv/v1/graph/search?q=node").get_json()["rows"]
    assert [(r["id"], r["ref"]) for r in rows] == [("a", "page:entities/a.md"), ("b", None), ("c", None)]
    assert ask(client, "/tv/v1/graph/search").status_code == 400
    assert ask(client, "/tv/v1/graph/search?q=" + "x" * 201).status_code == 413


def test_no_description_wiki_path_or_url_is_in_any_answer(client, desk):
    desk.entities = [dict(ent("a", 0, 3), title="See https://example.org/x now <b>bold</b>")]
    desk.rels = []
    dev = paired()
    for path in ("/tv/v1/graph?lod=0", "/tv/v1/graph?lod=1&community=0", "/tv/v1/graph/node/a/neighbors"):
        text = ask(client, path, dev).get_data(as_text=True)
        for leak in ("SECRET", "secret/x", "https", "example.org", "<b>"):
            assert leak not in text, (path, leak)
    assert "See now bold" in ask(client, "/tv/v1/graph?lod=1&community=0", dev).get_data(as_text=True)


def test_a_pc_that_cannot_read_its_graph_is_503_not_an_empty_graph(client, desk):
    desk.down.add("/api/knowledge-graph/")
    for path in ("/tv/v1/graph/summary", "/tv/v1/graph?lod=0", "/tv/v1/graph?lod=1&community=0",
                 "/tv/v1/graph/node/a/neighbors", "/tv/v1/graph/search?q=a", "/tv/v1/graph/changes"):
        r = ask(client, path)
        assert r.status_code == 503 and r.get_json()["code"] == "pc_unavailable", path


def test_an_answer_too_big_for_a_phone_is_refused(client, desk, monkeypatch):
    monkeypatch.setattr(ws_graph, "MAX_BODY", 100)
    assert ask(client, "/tv/v1/graph?lod=0").status_code == 413


def test_changes_says_whether_the_graph_moved(client, desk):
    rev = ask(client, "/tv/v1/graph/summary").get_json()["rev"]
    assert ask(client, "/tv/v1/graph/changes?since=" + rev).get_json()["changed"] is False
    desk.entities.append(ent("z", 1, 1))
    again = ask(client, "/tv/v1/graph/changes?since=" + rev).get_json()
    assert again["changed"] is True and again["rev"] != rev


def test_only_an_owner_phone_or_tablet_with_the_right_may_read_the_graph(client, desk):
    for path in ("summary", "search?q=a", "changes", "node/a/neighbors"):
        assert ask(client, "/tv/v1/graph/" + path, paired("tv")).status_code == 403, path
        assert ask(client, "/tv/v1/graph/" + path, paired(right=False)).status_code == 403, path
    assert ask(client, "/tv/v1/graph?lod=0", paired("tv")).status_code == 403

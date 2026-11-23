"""The Crew workspace's roster, as the owner's phone reads it (PW-G2, adapter 7).

The Crew workspace had a manifest with no rows, so a phone had nothing to draw. These tests hold the roster
(tv_gateway/ws_crew.py) over a stand-in for the desktop's crew routes:

* each agent is a row (name, role and model, active or suspended, when it last changed) and an item with its limits and
  how many tools, skills, projects and folders it may use;
* the folders' paths, the agent's own memory notes and its voice settings never leave the PC;
* a roster the PC could not read is a 503, never an empty crew;
* an agent id that is not an id never reaches the desktop, and one that is not there is a 404.
"""
import json

import pytest

from agent_friday.tv_gateway import pc_read
from tests.unit import test_tv_pc_read as shared
from tests.unit.test_tv_pc_read import get, paired

client = shared.client
_clean = shared._clean

ID1, ID2 = "crew-" + "1" * 16, "crew-" + "a" * 16


def agent(ident, name, **more):
    base = {"id": ident, "name": name, "role": "Reads the news.", "persona": "Calm and brief.\n\n- cites sources", "provider": "ollama",
            "model": "qwen3:8b", "status": "active", "voice": {"id": "secret-voice"}, "project_ids": ["p1"], "skills": ["s1", "s2"],
            "allowed_tools": ["search_news"], "max_steps": 20, "time_budget_s": 300, "revision": 3, "updated_at": 1763400000.0,
            "memory": {"notes": "the owner's private memory note", "read": True, "write": False},
            "grants": [{"path": "D:/vault/secret-folder", "access": "read"}], "offline": None}
    base.update(more)
    return base


class Desk:
    def __init__(self):
        self.calls, self.down = [], False
        self.agents = [agent(ID2, "beta", status="suspended"), agent(ID1, "Alpha"), {"id": "bad id", "name": "Broken"}]

    def __call__(self, path, params=None):
        self.calls.append((path, dict(params or {})))
        if self.down:
            return 500, b"{}", "application/json"
        if path == "/api/crew/agents":
            return 200, json.dumps({"status": "ok", "agents": self.agents}).encode(), "application/json"
        if path.startswith("/api/crew/agents/"):
            found = next((a for a in self.agents or [] if a["id"] == path.rsplit("/", 1)[1]), None)
            if found is None:
                return 404, b'{"status":"error"}', "application/json"
            return 200, json.dumps({"status": "ok", "agent": found}).encode(), "application/json"
        return 404, b"{}", "application/json"


@pytest.fixture
def desk(monkeypatch):
    d = Desk()
    monkeypatch.setattr(pc_read, "_desk", d)
    return d


def roster(client, query=""):
    r = get(client, paired(), "/tv/v1/workspaces/crew/list" + query)
    assert r.status_code == 200, r.get_json()
    return r.get_json()


def test_the_roster_is_by_name_with_the_contracts_fields(client, desk):
    got = roster(client)
    assert [r["title"] for r in got["rows"]] == ["Alpha", "beta"]              # an agent with a broken id is left out
    assert got["rows"][0] == {"ref": "agent:" + ID1, "kind": "agent", "title": "Alpha", "line": "Reads the news. · ollama qwen3:8b",
                              "state": "active", "when": "2025-11-17T17:20:00Z", "thumb": None, "lives_on": "pc",
                              "sensitivity": "personal", "taint": None}
    assert desk.calls == [("/api/crew/agents", {})]


def test_the_status_facet_counts_and_narrows(client, desk):
    got = roster(client)
    assert got["facets"] == [{"name": "status", "values": [{"v": "active", "n": 1}, {"v": "suspended", "n": 1}]}]
    assert [r["title"] for r in roster(client, "?facet.status=suspended")["rows"]] == ["beta"]


def test_an_item_has_the_limits_the_counts_and_the_personality(client, desk):
    got = get(client, paired(), "/tv/v1/workspaces/crew/item/agent:" + ID1).get_json()
    assert got["title"] == "Alpha" and got["taint"] is None and got["next"] is None
    assert got["meta"] == [{"k": "Status", "v": "active"}, {"k": "Model", "v": "ollama qwen3:8b"}, {"k": "Step limit", "v": "20"},
                           {"k": "Time limit", "v": "300 s"}, {"k": "Tools", "v": "1"}, {"k": "Skills", "v": "2"},
                           {"k": "Projects", "v": "1"}, {"k": "Folders", "v": "1"}]
    assert [b["t"] for b in got["blocks"]] == ["para", "para", "list"]


def test_no_folder_path_memory_note_or_voice_setting_leaves_anywhere(client, desk):
    dev = paired()
    for path in ("list", "search?q=alpha", "item/agent:" + ID1):
        text = get(client, dev, "/tv/v1/workspaces/crew/" + path).get_data(as_text=True)
        for leak in ("secret-folder", "private memory", "secret-voice", "D:/vault"):
            assert leak not in text, (path, leak)


def test_search_finds_an_agent_by_name_or_role(client, desk):
    dev = paired()
    assert [r["title"] for r in get(client, dev, "/tv/v1/workspaces/crew/search?q=ALPH").get_json()["rows"]] == ["Alpha"]
    assert len(get(client, dev, "/tv/v1/workspaces/crew/search?q=news").get_json()["rows"]) == 2


def test_a_roster_the_pc_could_not_read_is_503_never_an_empty_crew(client, desk):
    desk.down = True
    for path in ("list", "search?q=a", "item/agent:" + ID1):
        r = get(client, paired(), "/tv/v1/workspaces/crew/" + path)
        assert r.status_code == 503 and r.get_json()["code"] == "pc_unavailable", path
    desk.down, desk.agents = False, None
    assert get(client, paired(), "/tv/v1/workspaces/crew/list").status_code == 503


@pytest.mark.parametrize("ref", ["agent:../x", "agent:crew-1", "agent:crew-" + "G" * 16, "agent:", "page:x.md", "agent:crew-" + "1" * 17])
def test_an_id_that_is_not_an_agent_id_never_reaches_the_desktop(client, desk, ref):
    assert get(client, paired(), "/tv/v1/workspaces/crew/item/" + ref).status_code == 404
    assert desk.calls == []


def test_an_agent_that_is_not_there_is_404(client, desk):
    assert get(client, paired(), "/tv/v1/workspaces/crew/item/agent:crew-" + "9" * 16).status_code == 404

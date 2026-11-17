"""The People section of the Knowledge workspace, as the owner's phone reads it (PW-G2, adapter 3).

Friday keeps trust scores and evidence notes about each person (PeopleGraph). Those are Friday's private judgements
and never leave the PC. What the phone gets (tv_gateway/ws_people.py) is a name, other names, areas, when you last dealt
with them, and the person's wiki page when the wiki has one. These tests hold:

* people come most recently dealt-with first, those with no date last;
* no score and no evidence note appears in any answer;
* a ref that is not a person key never reaches the record read, and an unknown person is 404;
* People and Pages are separate sections, search with no section asks both, and one being down does not hide the other.
"""
import json

import pytest

from agent_friday.tv_gateway import pc_read, ws_knowledge, ws_people
from tests.unit import test_tv_pc_read as shared
from tests.unit.test_tv_pc_read import get, paired

client = shared.client
_clean = shared._clean

PEOPLE = {
    "Griff Jay": {"name": "Griff Jay", "aliases": ["G Jay", "Griffin"], "creation_date": "2026-01-02T10:00:00",
                  "last_interaction": "2026-11-10T09:00:00", "domains": ["beta testing", "family"],
                  "scores": {"overall": 0.9123, "emotional_trust": 0.7777},
                  "evidence": [{"notes": "SECRET EVIDENCE NOTE", "type": "claim", "magnitude": 0.4}]},
    "Jere Jay": {"name": "Jere Jay", "aliases": [], "creation_date": "2026-02-03T10:00:00", "last_interaction": "2026-10-01T09:00:00",
                 "domains": [], "scores": {"overall": 0.5}, "evidence": []},
    "no_date": {"name": "No Date", "aliases": ["Nobody"], "domains": [], "scores": {"overall": 0.1}, "evidence": []},
}


class Desk:
    def __init__(self):
        self.calls, self.down = [], set()
        self.people = json.loads(json.dumps(PEOPLE))
        self.structure = {"entities": [{"name": "person-griff-jay", "filename": "person-griff-jay.md", "modified": 1700000300},
                                       {"name": "notes", "filename": "notes.md", "modified": 1700000100}]}
        self.pages = {"entities/person-griff-jay.md": "# Griff Jay\n\nTester since January.\n"}

    def __call__(self, path, params=None):
        self.calls.append((path, dict(params or {})))
        if any(path.startswith(d) for d in self.down):
            return 500, b"{}", "application/json"
        if path == "/api/people":
            return 200, json.dumps({"status": "ok", "people": self.people, "version": 3}).encode(), "application/json"
        if path == "/api/wiki/structure":
            return 200, json.dumps({"status": "ok", "structure": self.structure}).encode(), "application/json"
        if path == "/api/wiki/page":
            text = self.pages.get((params or {}).get("path"))
            if text is None:
                return 404, b"{}", "application/json"
            return 200, json.dumps({"status": "ok", "content": text, "modified": 1700000300}).encode(), "application/json"
        if path == "/api/knowledge-graph/search":
            return 200, json.dumps({"status": "ok", "results": []}).encode(), "application/json"
        return 404, b"{}", "application/json"


@pytest.fixture
def desk(monkeypatch):
    d = Desk()
    monkeypatch.setattr(pc_read, "_desk", d)
    monkeypatch.setattr(ws_people, "PEOPLE_TTL_S", 0.0)
    monkeypatch.setattr(ws_people, "_KEPT", {"at": 0.0, "people": {}})
    monkeypatch.setattr(ws_knowledge, "PAGES_TTL_S", 0.0)
    monkeypatch.setattr(ws_knowledge, "_LIST", {"key": None, "at": 0.0, "rows": []})
    return d


def ask(client, path, dev=None):
    return get(client, dev or paired(), "/tv/v1/workspaces/knowledge/" + path)


def test_people_come_newest_contact_first_and_those_with_no_date_last(client, desk):
    got = ask(client, "list?section=people").get_json()
    assert [r["title"] for r in got["rows"]] == ["Griff Jay", "Jere Jay", "No Date"] and got["total"] == 3
    assert got["rows"][0] == {"ref": "person:Griff Jay", "kind": "person", "title": "Griff Jay", "line": "G Jay, Griffin",
                              "state": None, "when": "2026-11-10T09:00:00", "thumb": None, "lives_on": "pc",
                              "sensitivity": "personal", "taint": None}
    assert got["facets"] == [] and got["rev"].startswith("u_3_")


def test_the_older_list_shaped_record_set_still_reads(client, desk):
    desk.people = list(PEOPLE.values())
    assert [r["title"] for r in ask(client, "list?section=people").get_json()["rows"]] == ["Griff Jay", "Jere Jay", "No Date"]


def test_no_score_or_evidence_note_leaves_in_any_answer(client, desk):
    dev = paired()
    for path in ("list?section=people", "search?q=griff&section=people", "search?q=griff", "item/person:Griff Jay",
                 "item/person:Jere Jay"):
        text = ask(client, path, dev).get_data(as_text=True)
        for leak in ("SECRET", "0.9123", "0.7777", "scores", "evidence", "emotional_trust"):
            assert leak not in text, (path, leak)


def test_an_item_has_meta_and_the_wiki_page_blocks_and_a_link_to_the_page(client, desk):
    got = ask(client, "item/person:Griff Jay").get_json()
    assert got["title"] == "Griff Jay" and got["kind"] == "person" and got["next"] is None
    assert got["meta"] == [{"k": "Also known as", "v": "G Jay, Griffin"}, {"k": "Last contact", "v": "2026-11-10T09:00:00"},
                           {"k": "Added", "v": "2026-01-02"}, {"k": "Areas", "v": "beta testing, family"}]
    assert [b["t"] for b in got["blocks"]] == ["heading", "para"] and "Tester since January" in json.dumps(got)
    assert got["relations"] == [{"rel": "wiki page", "ref": "page:entities/person-griff-jay.md", "title": "Griff Jay", "kind": "page"}]
    assert "knowledge.create_edit" in got["verbs"]


def test_a_person_with_no_wiki_page_still_has_their_record(client, desk):
    got = ask(client, "item/person:Jere Jay").get_json()
    assert got["blocks"] == [] and got["relations"] == [] and got["meta"][0] == {"k": "Last contact", "v": "2026-10-01T09:00:00"}


def test_a_wiki_page_that_cannot_be_read_does_not_hide_the_record(client, desk):
    desk.down.add("/api/wiki/page")
    got = ask(client, "item/person:Griff Jay").get_json()
    assert got["title"] == "Griff Jay" and got["blocks"] == [] and got["relations"] == []


@pytest.mark.parametrize("ref", ["person:", "person:a/b", "person:..\\x", "person:" + "x" * 121, "person:Nobody Here"])
def test_a_ref_that_is_not_a_person_is_404(client, desk, ref):
    assert ask(client, "item/" + ref.replace("\\", "%5C")).status_code == 404


def test_search_with_no_section_asks_pages_and_people_and_a_section_asks_one(client, desk):
    both = ask(client, "search?q=jay").get_json()
    assert [r["ref"] for r in both["rows"]] == ["page:entities/person-griff-jay.md", "person:Griff Jay", "person:Jere Jay"]
    assert [r["ref"] for r in ask(client, "search?q=jay&section=people").get_json()["rows"]] == ["person:Griff Jay", "person:Jere Jay"]
    assert [r["ref"] for r in ask(client, "search?q=nobody&section=people").get_json()["rows"]] == ["person:no_date"]
    assert ask(client, "search?q=jay&section=graph").status_code == 404


def test_one_section_being_down_does_not_hide_the_other_but_all_down_is_503(client, desk):
    desk.down.add("/api/people")
    rows = ask(client, "search?q=griff").get_json()["rows"]
    assert [r["ref"] for r in rows] == ["page:entities/person-griff-jay.md"]
    assert ask(client, "search?q=griff&section=people").status_code == 503
    desk.down.add("/api/wiki/structure")
    assert ask(client, "search?q=griff").status_code == 503


def test_pages_still_list_beside_people_and_each_section_has_its_own_rev(client, desk):
    dev = paired()
    pages = ask(client, "list?section=pages", dev).get_json()
    people = ask(client, "list?section=people", dev).get_json()
    assert pages["rev"].startswith("k_") and people["rev"].startswith("u_")
    both = ask(client, "changes", dev).get_json()["rev"]
    assert both == pages["rev"] + "+" + people["rev"]
    assert ask(client, "changes?section=people&since=" + people["rev"], dev).get_json()["changed"] is False
    desk.people["Jere Jay"]["last_interaction"] = "2026-11-11T09:00:00"
    assert ask(client, "changes?section=people&since=" + people["rev"], dev).get_json()["changed"] is True


def test_the_people_are_read_once_within_the_ttl(client, desk, monkeypatch):
    monkeypatch.setattr(ws_people, "PEOPLE_TTL_S", 60.0)
    clock = [500.0]
    monkeypatch.setattr(ws_people.time, "monotonic", lambda: clock[0])
    dev = paired()
    for _ in range(3):
        ask(client, "list?section=people", dev)
    assert [c[0] for c in desk.calls].count("/api/people") == 1
    clock[0] += 61
    ask(client, "list?section=people", dev)
    assert [c[0] for c in desk.calls].count("/api/people") == 2


def test_a_pc_that_cannot_read_its_people_is_503_not_an_empty_list(client, desk):
    desk.down.add("/api/people")
    r = ask(client, "list?section=people")
    assert r.status_code == 503 and r.get_json()["code"] == "pc_unavailable"


def test_only_an_owner_phone_or_tablet_with_the_right_may_read_people(client, desk):
    for path in ("list?section=people", "item/person:Griff Jay", "search?q=a&section=people"):
        assert ask(client, path, paired("tv")).status_code == 403, path
        assert ask(client, path, paired(right=False)).status_code == 403, path


def test_a_record_whose_key_could_not_be_a_ref_is_left_out_not_sent_broken(client, desk):
    desk.people["Bad/Key"] = {"name": "Bad Key", "aliases": []}
    desk.people["Back" + chr(92) + "Slash"] = {"name": "Back Slash", "aliases": []}
    assert [r["title"] for r in ask(client, "list?section=people").get_json()["rows"]] == ["Griff Jay", "Jere Jay", "No Date"]

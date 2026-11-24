"""The `device` block of a workspace manifest, and what the phone is sent from it.

Stephen, 2026-11-17: "Where are the workspaces? I just see cards stating what Friday can do." The phone had
nothing to draw a workspace from, because no manifest said which of its things a phone may show. These tests
hold the rules for that block (tv_gateway/ws_device.py) and for the route that serves it:

* every manifest the PC ships has a block this module accepts, and Knowledge is exposed with its pages,
  people and graph sections;
* each rule of the block is shown failing on a hand-broken copy, so the check is known able to fail;
* the detail route sends the block, and each verb says where its button sits (row, item, new);
* `actions_here` is true only when the PC serves the act route, so a phone never draws a button that
  has nothing behind it.
"""
import copy

import pytest
from flask import Flask

from agent_friday.services import laya_manifests as lm
from agent_friday.tv_gateway import pc_read, ws_device
from tests.unit import test_tv_pc_read as shared
from tests.unit.test_tv_gateway import auth, open_session
from tests.unit.test_tv_pc_read import paired

client = shared.client
_clean = shared._clean


@pytest.fixture
def read(client):
    headers = auth(open_session(client, paired()))
    return lambda ws: client.get("/tv/v1/workspaces/" + ws, headers=headers)


def knowledge():
    return copy.deepcopy(lm.get("knowledge"))


def test_every_manifest_the_pc_ships_has_a_device_block_this_module_accepts():
    assert lm.all_manifests()
    for ws, manifest in lm.all_manifests().items():
        assert ws_device.problems(manifest) == [], ws


def test_knowledge_is_exposed_with_pages_people_and_the_graph():
    block = ws_device.device_block(lm.get("knowledge"))
    assert block is not None
    assert {s: v["view"] for s, v in block["sections"].items()} == {"pages": "list", "people": "list", "graph": "graph3d"}
    assert set(block["kinds"]) == {"page", "person", "node"}


def test_a_workspace_with_no_block_is_not_exposed():
    assert ws_device.device_block({"id": "x"}) is None
    assert ws_device.device_block({"id": "x", "device": {"expose": False}}) is None
    assert ws_device.problems({"id": "x"}) == []


BREAKS = [
    ("an unknown key on the block", lambda m: m["device"].update(color="red"), "unknown key"),
    ("expose not a boolean", lambda m: m["device"].update(expose="yes"), "device.expose"),
    ("a sensitivity not in the set", lambda m: m["device"].update(sensitivity="secret"), "device.sensitivity"),
    ("an exposed block with no sections", lambda m: m["device"].update(sections={}), "needs sections and kinds"),
    ("a view not in the set", lambda m: m["device"]["sections"]["pages"].update(view="carousel"), "a view that is not"),
    ("a section naming an undeclared kind", lambda m: m["device"]["sections"]["pages"].update(kind="ghost"),
     "a kind the workspace does not declare"),
    ("a section with no label", lambda m: m["device"]["sections"]["pages"].update(label=""), "needs a label"),
    ("roles missing one name", lambda m: m["device"]["kinds"]["page"]["roles"].pop("when"), "roles must name exactly"),
    ("a title role that is not text", lambda m: m["device"]["kinds"]["page"]["roles"].update(title=None),
     "needs a title role"),
    ("an item view that is not reader or explorer", lambda m: m["device"]["kinds"]["page"].update(item_view="grid"),
     "item_view"),
    ("an offline value not in the set", lambda m: m["device"]["kinds"]["page"].update(offline="always"), "offline"),
    ("a flag that is not a boolean", lambda m: m["device"]["kinds"]["page"].update(search="yes"), "search must be"),
    ("a row verb that is not a verb of the workspace",
     lambda m: m["device"]["kinds"]["page"]["row_verbs"].append("knowledge.teleport"), "not a verb of this workspace"),
    ("a new verb that is not a verb of the workspace",
     lambda m: m["device"]["kinds"]["page"].update(new_verb="knowledge.teleport"), "not a verb of this workspace"),
    ("an item verb list that is not a list", lambda m: m["device"]["kinds"]["page"].update(item_verbs="x"), "must be lists"),
]


@pytest.mark.parametrize("what,break_it,words", BREAKS, ids=[b[0] for b in BREAKS])
def test_each_rule_of_the_block_is_shown_failing_on_a_broken_copy(what, break_it, words):
    manifest = knowledge()
    assert ws_device.problems(manifest) == []
    break_it(manifest)
    found = ws_device.problems(manifest)
    assert found and any(words in f for f in found), (what, found)


def test_placement_says_where_each_verb_button_sits():
    block = ws_device.device_block(lm.get("knowledge"))
    assert ws_device.placement("knowledge.create_edit", block) == ["item", "new"]
    assert ws_device.placement("knowledge.delete_page", block) == ["item"]
    assert ws_device.placement("knowledge.search", block) == []
    assert ws_device.placement("knowledge.create_edit", None) == []


def test_the_detail_route_sends_the_block_and_each_verbs_placement(read):
    got = read("knowledge").get_json()
    assert got["device"] == ws_device.device_block(lm.get("knowledge"))
    placed = {v["id"]: v["placed"] for v in got["verbs"]}
    assert placed["knowledge.create_edit"] == ["item", "new"] and placed["knowledge.search"] == []


def test_a_workspace_with_no_block_sends_none_and_places_nothing(read):
    got = read("career").get_json()
    assert got["device"] is None and got["actions_here"] is False
    assert all(v["placed"] == [] for v in got["verbs"])


def test_actions_here_is_false_while_the_pc_serves_no_act_route(read):
    assert read("knowledge").get_json()["actions_here"] is False


def test_actions_here_is_true_once_the_act_route_is_served(read, monkeypatch):
    monkeypatch.setattr(pc_read, "_acts_live", lambda: True)
    assert read("knowledge").get_json()["actions_here"] is True
    assert read("career").get_json()["actions_here"] is False      # no block, so nothing to act on


def test_the_act_route_probe_sees_a_verbs_rule_and_nothing_else():
    app = Flask("probe")
    app.add_url_rule("/tv/v1/jobs", "jobs", lambda: "")
    with app.app_context():
        assert pc_read._acts_live() is False
    app.add_url_rule("/tv/v1/verbs/<ws>/<verb>", "verbs", lambda ws, verb: "", methods=["POST"])
    with app.app_context():
        assert pc_read._acts_live() is True

"""The typed arguments of the verbs a phone may press (PW-G1b; BURN-DOWN asked who defines the schema).

A manifest verb is a description with no argument schema, so a generic "run this verb" route would have to let the phone
choose arguments freely. The `device.verb_args` block (tv_gateway/ws_device.py) is the one declaration: for each verb a
button is drawn for, the names, types and limits of what the phone may send. These tests hold:

* every shipped block is valid and every placed verb that changes something declares its arguments;
* each rule of the schema is shown failing on a hand-broken copy;
* the detail route sends a verb's `args` beside where its button sits, and an unplaced verb has none.
"""
import copy

import pytest

from agent_friday.services import laya_manifests as lm
from agent_friday.tv_gateway import ws_device
from tests.unit import test_tv_pc_read as shared
from tests.unit.test_tv_gateway import auth, open_session
from tests.unit.test_tv_pc_read import paired

client = shared.client
_clean = shared._clean


@pytest.fixture
def read(client):
    headers = auth(open_session(client, paired()))
    return lambda ws: client.get("/tv/v1/workspaces/" + ws, headers=headers)


def exposed():
    return [(ws, m) for ws, m in sorted(lm.all_manifests().items()) if ws_device.device_block(m)]


def test_every_placed_verb_that_changes_something_declares_its_arguments():
    seen = 0
    for ws, manifest in exposed():
        block = manifest["device"]
        for verb in manifest["verbs"]:
            if ws_device.placement(verb["id"], block) and verb["group"] in ws_device.ACT_GROUPS:
                seen += 1
                assert verb["id"] in block["verb_args"], (ws, verb["id"])
    assert seen >= 8                                     # knowledge and calendar, so the walk really covered them


def test_every_shipped_block_is_valid():
    assert exposed()
    for ws, manifest in exposed():
        assert ws_device.problems(manifest) == [], ws


def knowledge():
    return copy.deepcopy(lm.get("knowledge"))


def arg(m, verb="knowledge.move_rename_tag", i=0):
    return m["device"]["verb_args"][verb][i]


BREAKS = [
    ("verb_args not an object", lambda m: m["device"].update(verb_args=[]), "verb_args must be an object"),
    ("a verb the workspace does not have", lambda m: m["device"]["verb_args"].update({"knowledge.teleport": []}),
     "not a verb of this workspace"),
    ("a verb no button uses", lambda m: m["device"]["verb_args"].update({"knowledge.search": []}), "no row, item or new button"),
    ("a placed act verb with no declaration", lambda m: m["device"]["verb_args"].pop("knowledge.delete_page"),
     "must declare its arguments"),
    ("arguments not a list", lambda m: m["device"]["verb_args"].update({"knowledge.delete_page": {}}), "arguments must be a list"),
    ("an argument that is not an object", lambda m: m["device"]["verb_args"]["knowledge.delete_page"].append("x"),
     "not an object"),
    ("an unknown key on an argument", lambda m: arg(m).update(color="red"), "unknown key"),
    ("a name that is not an identifier", lambda m: arg(m).update(name="Ref Page"), "lower-case identifier"),
    ("a name declared twice", lambda m: m["device"]["verb_args"]["knowledge.move_rename_tag"].append(dict(arg(m))), "declared twice"),
    ("a type not in the set", lambda m: arg(m, i=1).update(type="blob"), "type that is not one of"),
    ("required not a boolean", lambda m: arg(m, i=1).update(required="no"), "required must be true or false"),
    ("a string with no max", lambda m: arg(m, i=1).pop("max"), "needs a max"),
    ("a string with a huge max", lambda m: arg(m, i=1).update(max=99999), "needs a max"),
    ("an enum with no values", lambda m: arg(m, i=1).update(type="enum"), "non-empty values"),
    ("values on a string", lambda m: arg(m, i=1).update(values=["a"]), "may not have values"),
    ("a ref to an undeclared kind", lambda m: arg(m).update(kind="ghost"), "kind the workspace does not declare"),
    ("a from-item ref made optional", lambda m: arg(m).update(required=False), "cannot be optional"),
    ("from on a string", lambda m: arg(m, i=1).update({"from": "item"}), "only a ref does"),
    ("from something other than item", lambda m: arg(m).update({"from": "elsewhere"}), "from must be item"),
    ("a from-item ref of a kind whose items do not carry the verb",
     lambda m: arg(m, verb="knowledge.delete_page").update(kind="person"), "not on that kind's items"),
    ("a default that does not fit", lambda m: arg(m, i=1).update(default=5), "default that does not fit"),
]


@pytest.mark.parametrize("what,break_it,words", BREAKS, ids=[b[0] for b in BREAKS])
def test_each_rule_of_the_schema_is_shown_failing_on_a_broken_copy(what, break_it, words):
    manifest = knowledge()
    assert ws_device.problems(manifest) == []
    break_it(manifest)
    found = ws_device.problems(manifest)
    assert found and any(words in f for f in found), (what, found)


def test_a_max_on_a_bool_and_values_on_an_int_are_refused():
    manifest = copy.deepcopy(lm.get("calendar"))
    manifest["device"]["verb_args"]["calendar.delete_event"][1]["max"] = 5
    assert any("may not have a max" in f for f in ws_device.problems(manifest))
    manifest = copy.deepcopy(lm.get("calendar"))
    manifest["device"]["verb_args"]["calendar.quick_add"][0]["values"] = ["x"]
    assert any("may not have values" in f for f in ws_device.problems(manifest))


def test_args_of_gives_a_copy_for_a_placed_verb_and_nothing_otherwise():
    block = lm.get("calendar")["device"]
    got = ws_device.args_of("calendar.move_event", block)
    assert [a["name"] for a in got] == ["ref", "start", "end"] and got[0]["from"] == "item"
    got[0]["name"] = "changed"
    assert ws_device.args_of("calendar.move_event", block)[0]["name"] == "ref"
    assert ws_device.args_of("calendar.view", block) == [] and ws_device.args_of("calendar.move_event", None) == []


def test_the_detail_route_sends_each_verbs_arguments_beside_where_its_button_sits(read):
    got = {v["id"]: v for v in read("calendar").get_json()["verbs"]}
    assert got["calendar.quick_add"]["placed"] == ["new"] and got["calendar.quick_add"]["args"] == [
        {"name": "text", "type": "string", "max": 500}]
    assert [a["name"] for a in got["calendar.move_event"]["args"]] == ["ref", "start", "end"]
    assert got["calendar.delete_event"]["args"][1] == {"name": "notify_guests", "type": "bool", "required": False, "default": False}
    assert got["calendar.view"]["placed"] == [] and got["calendar.view"]["args"] == []


def test_a_workspace_with_no_block_sends_no_arguments(read):
    assert all(v["args"] == [] for v in read("news").get_json()["verbs"])

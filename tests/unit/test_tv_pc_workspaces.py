"""A workspace, as the owner's phone draws it: its verbs with their gate, its objects, its jobs, what stays on the PC.

Stephen, 2026-11-15 (Mobile v3, order: chats, workspaces, jobs, media). The detail route first sent each verb as
{id, label, kind} from keys (label, name, kind, risk) that none of the Laya manifests carry, so the phone got every
verb with a null label and a null kind. These tests read the REAL manifests, not a stand-in:

* every verb of every workspace the phone can see arrives with its id, a label, its group and the gate the PC's own
  action gate gives it (the class and whether it raises a card), so the phone can say "this will ask on your PC";
* a verb with no gate block is reported as the strictest case (outward, card required), never as free to run;
* the objects, the jobs and the owner-only controls come from the manifest, and nothing else of it leaves the PC
  (no phrasings, tool names, entry points, eval ids);
* an unreadable manifest is a plain 503, never an empty verb list that reads as "nothing to do here".
"""
import pytest

from agent_friday.services import laya_manifests as lm
from agent_friday.services import workspace_registry as reg
from tests.unit.test_tv_gateway import auth, open_session
from tests.unit.test_tv_pc_read import _clean, client, paired  # noqa: F401  (fixtures and helpers)

GATE_CLASSES = {"internal", "self_gated", "by_argument", "card_only_when_outward", "outward"}
CARDS = {"none", "own_card", "required", "by_argument"}
VERB_KEYS = {"id", "label", "group", "gate_class", "card", "placed"}


def shown_workspaces():
    return [w["id"] for w in reg.all_workspaces() if not reg.is_held(w)]


@pytest.fixture
def read(client):
    """GET one workspace's detail answer with ONE paired phone session for the whole test."""
    headers = auth(open_session(client, paired()))
    return lambda ws: client.get("/tv/v1/workspaces/" + ws, headers=headers)


def detail(read, ws):
    r = read(ws)
    assert r.status_code == 200, ws
    return r.get_json()


def test_every_verb_of_every_visible_workspace_has_a_label_and_the_gate_the_manifest_gives_it(read):
    seen = 0
    for ws in shown_workspaces():
        got = detail(read, ws)
        want = lm.verbs(ws)
        assert [v["id"] for v in got["verbs"]] == [v["id"] for v in want], ws
        for sent, v in zip(got["verbs"], want):
            seen += 1
            assert set(sent) == VERB_KEYS and sent["label"] and isinstance(sent["label"], str), (ws, sent)
            assert sent["gate_class"] in GATE_CLASSES and sent["card"] in CARDS, (ws, sent)
            assert (sent["gate_class"], sent["card"]) == (v["gate"]["class"], v["gate"]["card"]), (ws, sent)
    assert seen > 100                                    # the walk really covered the manifests


def test_a_verb_that_needs_a_card_says_so(read):
    needing = [(ws, v["id"]) for ws in shown_workspaces() for v in lm.verbs(ws) if v["gate"]["card"] == "required"]
    assert needing
    ws, vid = needing[0]
    sent = {v["id"]: v for v in detail(read, ws)["verbs"]}[vid]
    assert sent["card"] == "required" and sent["gate_class"] == "outward"


def test_a_verb_with_no_gate_block_is_reported_as_the_strictest_case(read, monkeypatch):
    monkeypatch.setattr(lm, "verbs", lambda ws: [{"id": "x.do", "ui": {"control": "Do it"}}])
    sent = detail(read, "calendar")["verbs"][0]
    assert sent == {"id": "x.do", "label": "Do it", "group": None, "gate_class": "outward", "card": "required",
                    "placed": []}


def test_a_verb_with_no_control_text_is_labelled_by_its_id_not_null(read, monkeypatch):
    monkeypatch.setattr(lm, "verbs", lambda ws: [{"id": "x.do", "gate": {"class": "internal", "card": "none"}}])
    assert detail(read, "calendar")["verbs"][0]["label"] == "x.do"


def test_the_old_kind_field_is_gone(read):
    assert all("kind" not in v for ws in shown_workspaces() for v in detail(read, ws)["verbs"])


def test_objects_and_jobs_come_from_the_manifest_and_carry_no_entry_points(read):
    got = detail(read, "calendar")
    assert got["objects"] == [{"kind": o["kind"], "noun": o["noun"]} for o in lm.objects("calendar")] and got["objects"]
    assert [j["id"] for j in got["jobs"]] == [j["id"] for j in lm.jobs("calendar")] and got["jobs"]
    assert all(set(j) == {"id", "label"} for j in got["jobs"])
    assert "agent_friday" not in str(got)                # no module path (a job's entry) leaves the PC


def test_only_by_design_owner_controls_are_listed_with_their_reason(read, monkeypatch):
    monkeypatch.setattr(lm, "not_for_laya", lambda ws: [
        {"action": "a.tick", "kind": "by_design", "reason": "the owner's tick"},
        {"action": "a.gap", "kind": "gap", "reason": "not built yet"}])
    assert detail(read, "calendar")["owner_only"] == [{"action": "a.tick", "why": "the owner's tick"}]


def test_a_workspace_without_a_manifest_has_empty_lists_not_an_error(read, monkeypatch):
    monkeypatch.setattr(lm, "_all", lambda: {})
    got = detail(read, "calendar")
    assert got["verbs"] == got["objects"] == got["jobs"] == got["owner_only"] == []


def test_an_unreadable_manifest_is_a_plain_503_that_names_nothing(read, monkeypatch):
    def boom(ws):
        raise RuntimeError("C:/secret/path")
    monkeypatch.setattr(lm, "verbs", boom)
    r = read("calendar")
    assert r.status_code == 503 and r.get_json()["code"] == "pc_unavailable" and "secret" not in r.get_data(as_text=True)


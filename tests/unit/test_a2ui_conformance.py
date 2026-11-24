"""The shared A2UI conformance suite: one file, three renderers (contract draft 3, section 5).

tests/fixtures/a2ui/conformance_v1.json is the truth the desktop (Python bind + JS draw) and the phone (Java bind +
Kotlin shell) both run. This test:

* holds at least one refusing case for EVERY rule V1..V14 (test_every_rule_has_a_refusing_case);
* runs every case through the reference implementation: `check` cases must refuse with their rule (or pass),
  `render` cases must bind to their tree, `shell` cases must also apply his customization (hidden parts gone, look);
* holds the two draft-3 rules: authorship `builtin` is accepted (V14) and a builtin document carries no data of its
  own (V7); and the shell rule for customization (hidden removes a subtree, root can't be hidden, look defaults);
* keeps the file in step with the case list below (A2UI_REGEN_FIXTURE=1 rewrites it; cases are only ever added).

The phone vendors the same bytes (friday-phone app/src/test/resources/a2ui/conformance_v1.json) and pins its sha256.
"""
import copy
import json
import os
from pathlib import Path

import pytest

from agent_friday.services import a2ui_profile as prof
from agent_friday.services import a2ui_render as rend

FIXTURE_DIR = Path(__file__).resolve().parents[1] / "fixtures" / "a2ui"
FIXTURE = FIXTURE_DIR / "conformance_v1.json"
PROFILE_CASES = FIXTURE_DIR / "profile_cases_v1.json"
CAT = prof.CATALOG_ID
SUBJECT = "workspace:knowledge"

_PROFILE = json.loads(PROFILE_CASES.read_text(encoding="utf-8"))
# the hostile profile cases' own verb manifests, plus ours (each case names its subject in ctx)
VERBS = {**_PROFILE["verbs"],
         "open_run": {"gate_class": "internal", "effects": [], "args": ["run_id"]},
         "approval_decide": {"gate_class": "outward", "effects": ["decides_card"], "args": ["id"]}}


def surface(components, data=None, meta=None, extra=(), catalog=CAT, send=False):
    body = {"surfaceId": "s1", "catalogId": catalog, "sendDataModel": send, "components": components}
    if data is not None:
        body["dataModel"] = data
    if meta is not None:
        body["metadata"] = meta
    return [{"version": "1.0", "createSurface": body}, *extra]


def binds(**paths):
    return {"friday": {"bindings": {k: {"path": v, "query": "knowledge.sections", "params": {}}
                                    for k, v in paths.items()}}}


def knowledge_doc():
    from agent_friday.services import a2ui_workspaces
    return a2ui_workspaces.builtin("knowledge")


KNOWLEDGE_DATA = {"knowledge": {
    "sections": [{"name": "entities", "count": 2}, {"name": "projects", "count": 1}],
    "recent": [{"title": "sample person one", "section": "entities", "when": "2026-11-20T10:00:00Z"},
               {"title": "garden plan", "section": "projects", "when": "2026-11-19T09:00:00Z"}]}}

TEXT = {"id": "t", "component": "text_block", "text": "Hello"}


def _new_cases():
    """The cases draft 3 adds on top of the 75 hostile profile cases (which cover V2-V5 and V7-V13)."""
    deep = [{"id": "root", "component": "column", "children": ["c1"]}]
    for i in range(1, 7):
        deep.append({"id": "c%d" % i, "component": "column", "children": ["c%d" % (i + 1)]})
    deep.append({"id": "c7", "component": "text_block", "text": "deep"})
    many = [{"id": "root", "component": "column", "children": ["n%d" % i for i in range(60)]}]
    many += [{"id": "n%d" % i, "component": "divider"} for i in range(60)]
    return [
        # V1: shapes
        {"name": "V1 a component with no id", "kind": "check", "ctx": {"authorship": "composer"},
         "doc": surface([{"component": "column", "children": []}]), "expect": {"ok": False, "rule": "V1"}},
        {"name": "V1 an unknown text style", "kind": "check", "ctx": {"authorship": "composer"},
         "doc": surface([{"id": "root", "component": "text_block", "text": "x", "style": "blink"}]),
         "expect": {"ok": False, "rule": "V1"}},
        # V2: envelope
        {"name": "V2 a message without the version", "kind": "check", "ctx": {"authorship": "composer"},
         "doc": [{"createSurface": surface([dict(TEXT, id="root")])[0]["createSurface"]}],
         "expect": {"ok": False, "rule": "V2"}},
        # V3: catalog
        {"name": "V3 the basic catalog as the surface catalog", "kind": "check", "ctx": {"authorship": "composer"},
         "doc": surface([dict(TEXT, id="root")], catalog="https://a2ui.org/specification/v1_0/basic_catalog.json"),
         "expect": {"ok": False, "rule": "V3"}},
        # V4: allowlist
        {"name": "V4 a TextField input", "kind": "check", "ctx": {"authorship": "builtin"},
         "doc": surface([{"id": "root", "component": "TextField"}]), "expect": {"ok": False, "rule": "V4"}},
        # V5: tree
        {"name": "V5 no root", "kind": "check", "ctx": {"authorship": "builtin"},
         "doc": surface([dict(TEXT, id="top")]), "expect": {"ok": False, "rule": "V5"}},
        # V6: size
        {"name": "V6 deeper than six levels", "kind": "check", "ctx": {"authorship": "builtin"},
         "doc": surface(deep), "expect": {"ok": False, "rule": "V6"}},
        {"name": "V6 more than sixty components", "kind": "check", "ctx": {"authorship": "builtin"},
         "doc": surface(many), "expect": {"ok": False, "rule": "V6"}},
        # V7: data
        {"name": "V7 sendDataModel true", "kind": "check", "ctx": {"authorship": "composer"},
         "doc": surface([dict(TEXT, id="root")], send=True), "expect": {"ok": False, "rule": "V7"}},
        {"name": "V7 a builtin document carries no data of its own", "kind": "check", "ctx": {"authorship": "builtin"},
         "doc": surface([{"id": "root", "component": "record_table", "rows": {"@path": "/knowledge/sections"},
                          "columns": ["name"]}], data={"knowledge": {"sections": [{"name": "planted"}]}},
                        meta=binds(sections="/knowledge/sections")),
         "expect": {"ok": False, "rule": "V7"}},
        {"name": "V7 a path outside the declared bindings", "kind": "check", "ctx": {"authorship": "builtin"},
         "doc": surface([{"id": "root", "component": "record_table", "rows": {"@path": "/secrets/keys"},
                          "columns": ["name"]}], meta=binds(sections="/knowledge/sections")),
         "expect": {"ok": False, "rule": "V7"}},
        # V8: URLs
        {"name": "V8 a URL in a caption", "kind": "check", "ctx": {"authorship": "builtin"},
         "doc": surface([{"id": "root", "component": "text_block", "style": "caption", "text": "see https://x.test/a"}]),
         "expect": {"ok": False, "rule": "V8"}},
        {"name": "V8 a bound row holding a URL refuses the whole view", "kind": "render",
         "ctx": {"authorship": "builtin"},
         "doc": surface([{"id": "root", "component": "record_table", "rows": {"@path": "/knowledge/sections"},
                          "columns": ["name"]}], meta=binds(sections="/knowledge/sections")),
         "data": {"knowledge": {"sections": [{"name": "javascript:alert(1)"}]}},
         "expect": {"ok": False, "rule": "V8"}},
        # V9 / V10: verbs
        {"name": "V9 a verb with no manifest", "kind": "check", "ctx": {"authorship": "builtin"},
         "doc": surface([{"id": "root", "component": "verb_button", "label": "Open", "verb": "nowhere",
                          "action": {"event": {"name": "nowhere"}}}]), "expect": {"ok": False, "rule": "V9"}},
        {"name": "V10 a verb that decides a card", "kind": "check", "ctx": {"authorship": "builtin"},
         "doc": surface([{"id": "root", "component": "verb_button", "label": "Card", "verb": "approval_decide",
                          "action": {"event": {"name": "approval_decide"}}}]), "expect": {"ok": False, "rule": "V10"}},
        # V11: reserved words
        {"name": "V11 a heading that says Approve", "kind": "check", "ctx": {"authorship": "builtin"},
         "doc": surface([{"id": "root", "component": "text_block", "style": "heading", "text": "Approve"}]),
         "expect": {"ok": False, "rule": "V11"}},
        {"name": "V11 a bound section title that says Approve", "kind": "render", "ctx": {"authorship": "builtin"},
         "doc": surface([{"id": "root", "component": "status_block", "title": {"@path": "/knowledge/title"}}],
                        meta=binds(title="/knowledge/title")),
         "data": {"knowledge": {"title": "Approve"}}, "expect": {"ok": False, "rule": "V11"}},
        # V12 / V13: slots and accessibility
        {"name": "V12 an approval slot for another subject", "kind": "check", "ctx": {"authorship": "builtin"},
         "doc": surface([{"id": "root", "component": "column", "children": ["s"]},
                         {"id": "s", "component": "approval_slot", "filter": {"subject": "run:other"}}]),
         "expect": {"ok": False, "rule": "V12"}},
        {"name": "V13 an assertive live region", "kind": "check", "ctx": {"authorship": "builtin"},
         "doc": surface([dict(TEXT, id="root", **{"@accessibility": {"live": "assertive"}})]),
         "expect": {"ok": False, "rule": "V13"}},
        # V14: authorship
        {"name": "V14 outside content may not author a view", "kind": "check", "ctx": {"authorship": "web_page"},
         "doc": surface([dict(TEXT, id="root")]), "expect": {"ok": False, "rule": "V14"}},
        {"name": "V14 builtin authorship is accepted", "kind": "check", "ctx": {"authorship": "builtin"},
         "doc": surface([dict(TEXT, id="root")]), "expect": {"ok": True}},
        # the Knowledge document
        {"name": "knowledge builtin document passes", "kind": "check", "ctx": {"authorship": "builtin"},
         "doc": knowledge_doc(), "expect": {"ok": True}},
        {"name": "knowledge rendered with live data", "kind": "render", "ctx": {"authorship": "builtin"},
         "doc": knowledge_doc(), "data": KNOWLEDGE_DATA, "expect": {"ok": True}},
        {"name": "knowledge rendered with no data yet", "kind": "render", "ctx": {"authorship": "builtin"},
         "doc": knowledge_doc(), "data": {}, "expect": {"ok": True}},
        {"name": "knowledge with his customization: recent hidden, compact, a summary", "kind": "shell",
         "ctx": {"authorship": "builtin"}, "doc": knowledge_doc(), "data": KNOWLEDGE_DATA,
         "customization": {"hidden": ["recent"], "density": "compact", "summary": "My notes, mostly",
                           "accent": "#3366ff"}, "expect": {"ok": True}},
        {"name": "shell: hiding root is ignored, unknown ids are ignored, defaults fill the look", "kind": "shell",
         "ctx": {"authorship": "builtin"}, "doc": knowledge_doc(), "data": KNOWLEDGE_DATA,
         "customization": {"hidden": ["root", "no_such_part"]}, "expect": {"ok": True}},
    ]


def _hostile_cases():
    return [{"name": "profile_v1: " + c["name"], "kind": "check",
             "ctx": {"authorship": "friday_liquid", "model_authored": True, "subject": _PROFILE["subject"]},
             "doc": c["document"], "expect": {"ok": False, "rule": c["rule"]}} for c in _PROFILE["hostile"]]


def _ctx(case):
    return prof.Context(subject=case["ctx"].get("subject", SUBJECT), verbs=VERBS.get, authorship=case["ctx"]["authorship"],
                        model_authored=bool(case["ctx"].get("model_authored")))


def run(case):
    """What the reference implementation does with a case: the shape `expect` has."""
    ctx = _ctx(case)
    if case["kind"] == "check":
        try:
            prof.check(copy.deepcopy(case["doc"]), ctx)
        except prof.ProfileError as e:
            return {"ok": False, "rule": e.rule}
        return {"ok": True}
    rendered = rend.render(copy.deepcopy(case["doc"]), ctx, data=copy.deepcopy(case.get("data")))
    if not rendered.ok:
        return {"ok": False, "rule": rendered.receipt["rule"]}
    if case["kind"] == "render":
        return {"ok": True, "tree": rendered.tree}
    shown = rend.apply_customization(rendered.tree, case.get("customization") or {})
    return {"ok": True, "tree": shown["tree"], "look": shown["look"]}


def build_payload():
    cases = _hostile_cases() + _new_cases()
    out = []
    for c in cases:
        got = run(c)
        want = c["expect"]
        assert got["ok"] == want["ok"] and got.get("rule") == want.get("rule"), (c["name"], got)
        out.append({**c, "expect": got})
    return {"format": 1, "catalog": CAT, "subject": SUBJECT, "verbs": VERBS, "cases": out}


def test_every_case_does_what_it_says():
    build_payload()


def test_every_rule_has_a_refusing_case():
    rules = {c["expect"]["rule"] for c in _hostile_cases() + _new_cases() if not c["expect"]["ok"]}
    assert rules >= {"V%d" % i for i in range(1, 15)}, sorted({"V%d" % i for i in range(1, 15)} - rules)


def test_the_knowledge_tree_binds_his_live_data():
    case = next(c for c in _new_cases() if c["name"] == "knowledge rendered with live data")
    tree = run(case)["tree"]
    nodes = {}

    def walk(n):
        nodes[n["id"]] = n
        for k in n.get("children", []):
            walk(k)
    walk(tree[0])
    assert nodes["sections_table"]["rows"] == KNOWLEDGE_DATA["knowledge"]["sections"]
    assert nodes["recent_table"]["rows"] == KNOWLEDGE_DATA["knowledge"]["recent"]
    assert nodes["title"]["text"] == "Knowledge"


def test_the_shell_removes_hidden_parts_and_fills_the_look():
    case = next(c for c in _new_cases() if c["kind"] == "shell" and "recent hidden" in c["name"])
    got = run(case)
    ids = json.dumps(got["tree"])
    assert '"recent"' not in ids and '"recent_table"' not in ids and '"sections_table"' in ids
    assert got["look"] == {"accent": "#3366ff", "density": "compact", "summary": "My notes, mostly"}
    plain = next(c for c in _new_cases() if c["kind"] == "shell" and "hiding root" in c["name"])
    got = run(plain)
    assert got["tree"][0]["id"] == "root"
    assert got["look"] == {"accent": None, "density": "comfortable", "summary": None}


def test_every_hostile_document_is_refused_whole_when_it_claims_to_be_builtin_too():
    """Accepting `builtin` must not open a door: each of the 75 hostile documents still refuses (its own rule, or V7
    first when it also carries data) and binds no verb."""
    for case in _hostile_cases():
        r = rend.render(copy.deepcopy(case["doc"]), prof.Context(subject=case["ctx"]["subject"], verbs=VERBS.get,
                                                                 authorship="builtin"))
        assert not r.ok, case["name"]
        assert r.receipt["rule"] in (case["expect"]["rule"], "V7"), (case["name"], r.receipt)
        assert r.permitted_actions == {}


def test_a_builtin_document_never_carries_planted_data():
    case = next(c for c in _new_cases() if c["name"].startswith("V7 a builtin document"))
    assert run(case) == {"ok": False, "rule": "V7"}


def test_the_fixture_file_is_current():
    payload = build_payload()
    text = json.dumps(payload, sort_keys=True, ensure_ascii=False, indent=1) + "\n"
    if os.environ.get("A2UI_REGEN_FIXTURE") == "1":
        FIXTURE.write_text(text, encoding="utf-8", newline="\n")
    assert FIXTURE.exists(), "run with A2UI_REGEN_FIXTURE=1 to write tests/fixtures/a2ui/conformance_v1.json"
    on_disk = json.loads(FIXTURE.read_text(encoding="utf-8"))
    assert on_disk == json.loads(text), "conformance_v1.json is out of date: run with A2UI_REGEN_FIXTURE=1"
    old = {c["name"] for c in on_disk["cases"]}
    assert old <= {c["name"] for c in payload["cases"]}, "cases are only ever added"

"""Point-and-say (docs/design/active/vibe-coding-salon.md §4.11 item 2).

The user selects an element in the preview and says or types what to change.
The pick (a selector, the tag, its text and a snippet) is stored on the
codebase and told to the model on the next turn, so "make this bigger" has a
referent. Simple property edits go through a non-model patcher: one CSS rule
appended as a step by "you", never a model call.
"""
from __future__ import annotations

import pytest

from agent_friday.services import codebases as cb


@pytest.fixture(autouse=True)
def _root(monkeypatch, tmp_path):
    monkeypatch.setattr(cb, "_root", lambda: tmp_path / "codebases")
    yield


def test_a_pick_is_stored_and_told_to_the_model_then_cleared():
    rec = cb.create("Tracker")
    pick = cb.set_pick(rec["id"], {"selector": "main > h1", "tag": "h1", "text": "Tracker", "snippet": "<h1>Tracker</h1>",
                                   "rect": {"x": 20, "y": 32, "w": 300, "h": 28}})
    assert pick["selector"] == "main > h1" and pick["at"]
    assert cb.load(rec["id"])["pick"]["tag"] == "h1"
    block = cb.context_block_for(rec["id"])
    assert "pointed at" in block.lower() and "main > h1" in block and "<h1>Tracker</h1>" in block
    assert cb.clear_pick(rec["id"]) is True
    assert cb.load(rec["id"]).get("pick") is None
    assert "pointed at" not in cb.context_block_for(rec["id"]).lower()


@pytest.mark.parametrize("bad", [{"selector": ""}, {"selector": "h1 { color: red }"}, {"selector": "x" * 400},
                                 {"selector": "h1; drop"}, {"selector": "h1", "snippet": "x" * 5000}])
def test_a_pick_that_is_not_a_plain_selector_is_refused(bad):
    rec = cb.create("Tracker")
    with pytest.raises(ValueError):
        cb.set_pick(rec["id"], bad)


def test_a_quick_style_is_one_css_rule_as_a_step_by_you():
    rec = cb.create("Tracker")
    st = cb.quick_style(rec["id"], "main > h1", "font-size", "1.4em")
    assert st["author"] == "you" and "bigger" not in st["summary"]
    assert "main > h1" in st["summary"] and "font-size" in st["summary"]
    css = cb.read(rec["id"], "styles.css")
    assert css.rstrip().endswith("main > h1 { font-size: 1.4em; }")
    assert "Friday pick" in css or "point-and-say" in css
    # A second quick style appends another rule; both survive.
    cb.quick_style(rec["id"], "main > h1", "font-weight", "700")
    css = cb.read(rec["id"], "styles.css")
    assert css.count("main > h1 {") == 2
    # The last step is undoable like any other.
    cb.undo(rec["id"])
    assert cb.read(rec["id"], "styles.css").count("main > h1 {") == 1


def test_a_quick_style_without_a_stylesheet_writes_one_and_links_it():
    rec = cb.create("Tracker", template="bundle")     # one HTML file, no styles.css
    cb.quick_style(rec["id"], "main > h1", "color", "#00ff80")
    assert cb.read(rec["id"], "friday-pick.css").strip().endswith("main > h1 { color: #00ff80; }")
    assert 'href="friday-pick.css"' in cb.read(rec["id"], "index.html")


@pytest.mark.parametrize("prop,value", [("behavior", "url(x)"), ("font-size", "expression(1)"), ("background", "url(http://evil)"),
                                        ("color", "red; } body { display:none"), ("font-size", "x" * 100), ("-moz-binding", "x")])
def test_quick_style_refuses_anything_but_a_plain_property_and_value(prop, value):
    rec = cb.create("Tracker")
    with pytest.raises(ValueError):
        cb.quick_style(rec["id"], "main > h1", prop, value)


def test_the_quick_actions_map_to_plain_rules():
    assert cb.QUICK_ACTIONS["bigger"] == ("font-size", "1.25em")
    assert cb.QUICK_ACTIONS["hide"] == ("display", "none")
    for name, (prop, value) in cb.QUICK_ACTIONS.items():
        assert prop in cb.SAFE_PROPS, name

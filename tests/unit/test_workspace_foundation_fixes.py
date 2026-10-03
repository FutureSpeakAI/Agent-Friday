"""workspace-ecosystem.md Phase 1, pulled forward by the salon (§4.9.1): the
foundation a vibe-coded workspace will stand on.

Four of the defects the survey found in the versioning and studio code:
undo oscillated instead of walking back; a workspace id was stripped into
another workspace's file instead of refused; the studio chat applied a
model's patch without the blast-radius gate the code already had; and the
history tool truncated JSON mid-token. Each is pinned here.
"""
from __future__ import annotations

import json
import shutil

import pytest

from agent_friday.services import workspace_studio as ws


@pytest.fixture(autouse=True)
def clean_studio():
    if ws.WS_STUDIO_DIR.exists():
        shutil.rmtree(ws.WS_STUDIO_DIR, ignore_errors=True)
    ws.WS_STUDIO_DIR.mkdir(parents=True, exist_ok=True)
    yield
    if ws.WS_STUDIO_DIR.exists():
        shutil.rmtree(ws.WS_STUDIO_DIR, ignore_errors=True)


# ── undo walks backwards ─────────────────────────────────────────────────────

def test_two_undos_go_back_two_steps_not_round_in_a_circle():
    ws.apply_customization("news", {"accent": "#111", "summary": "one"})
    ws.apply_customization("news", {"accent": "#222", "summary": "two"})
    ws.apply_customization("news", {"accent": "#333", "summary": "three"})
    doc, err = ws.undo_last("news")
    assert err is None and doc["customization"]["accent"] == "#222"
    doc, err = ws.undo_last("news")
    assert err is None and doc["customization"]["accent"] == "#111", "the second undo went further back"
    doc, err = ws.undo_last("news")
    assert err is None and doc["customization"] == {}
    doc, err = ws.undo_last("news")
    assert doc is None and "nothing" in (err or "").lower()


def test_undo_is_still_reversible_and_a_new_change_restarts_the_walk():
    ws.apply_customization("news", {"accent": "#111", "summary": "one"})
    ws.apply_customization("news", {"accent": "#222", "summary": "two"})
    ws.undo_last("news")                                   # back to #111
    # The state before the undo is kept, so the undo can itself be undone
    # by an explicit revert to that snapshot.
    doc = ws.load_ws_doc("news")
    before_undo = [v for v in doc["versions"] if v.get("kind") == "undo_point"]
    assert before_undo and before_undo[-1]["customization"]["accent"] == "#222"
    # A fresh change after an undo: the next undo removes THAT change, not
    # something older.
    ws.apply_customization("news", {"density": "compact", "summary": "three"})
    doc, err = ws.undo_last("news")
    assert err is None
    assert doc["customization"].get("accent") == "#111" and "density" not in doc["customization"]


def test_history_lists_undo_points_as_what_they_are():
    ws.apply_customization("news", {"accent": "#111", "summary": "one"})
    ws.undo_last("news")
    h = ws.history("news")
    labels = [e["label"] for e in h["entries"]]
    assert "before undo" in labels
    assert all("kind" in e for e in h["entries"])


# ── ids are refused, not rewritten ───────────────────────────────────────────

@pytest.mark.parametrize("bad", ["my.workspace", "../news", "news/x", "News", "", "a b", "x" * 49, "nul\x00"])
def test_an_id_that_is_not_a_plain_name_is_refused(bad):
    with pytest.raises(ValueError):
        ws.load_ws_doc(bad)
    with pytest.raises(ValueError):
        ws.apply_customization(bad, {"accent": "#111"})


def test_two_different_ids_never_share_a_file():
    ws.apply_customization("my-workspace", {"accent": "#111"})
    with pytest.raises(ValueError):
        ws.load_ws_doc("my.workspace")
    assert ws.load_ws_doc("myworkspace")["customization"] == {}


# ── the studio chat goes through the blast-radius gate ───────────────────────

def _gen(reply):
    return lambda messages, system, orb_label: reply


def test_a_model_patch_that_reaches_safety_state_is_refused_in_the_chat(monkeypatch):
    from agent_friday.services import boot_guard
    monkeypatch.setattr(boot_guard, "check_blast_radius", lambda patch: (False, "it touches the vault boundary"))
    reply = 'Done.\n```friday-customize\n{"summary":"widen","accent":"#00d4ff"}\n```'
    out = ws.workspace_chat_turn("news", "News", "widen it", generate=_gen(reply))
    doc = ws.load_ws_doc("news")
    assert doc["customization"] == {}, "the patch was applied despite the gate"
    assert doc["versions"] == []
    assert "vault boundary" in json.dumps(out)
    assert out.get("applied") in (None, False)


def test_safe_mode_applies_nothing_and_says_so(monkeypatch):
    from agent_friday.services import boot_guard
    monkeypatch.setattr(boot_guard, "safe_mode", lambda: True)
    reply = 'Done.\n```friday-customize\n{"summary":"tint","accent":"#00d4ff"}\n```'
    out = ws.workspace_chat_turn("news", "News", "tint it", generate=_gen(reply))
    assert ws.load_ws_doc("news")["customization"] == {}
    assert "safe mode" in json.dumps(out).lower()


def test_an_ordinary_patch_still_applies_through_the_gate():
    reply = 'Done.\n```friday-customize\n{"summary":"tint","accent":"#00d4ff"}\n```'
    out = ws.workspace_chat_turn("news", "News", "tint it", generate=_gen(reply))
    assert ws.load_ws_doc("news")["customization"]["accent"] == "#00d4ff"
    assert out.get("applied")


# ── the history tool never hands the model cut JSON ─────────────────────────

def test_the_history_tool_is_bounded_by_entries_and_always_parses():
    from agent_friday.services import agent as ag
    for i in range(40):
        ws.apply_customization("news", {"css": ".ws-custom-root .x{color:red}" + "/* pad */" * 200, "summary": "c%d" % i})
    out = ag.CLAUDE_TOOL_HANDLERS["list_workspace_history"]({"workspace": "news", "limit": 5})
    data = json.loads(out) if isinstance(out, str) else out
    assert len(data["entries"]) == 5
    assert "/* pad */" not in json.dumps(data), "the stylesheet itself never rides in the listing"
    assert len(json.dumps(data)) < 6000

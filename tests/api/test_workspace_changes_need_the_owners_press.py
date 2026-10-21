"""A workspace's saved look changes only by the owner's press, through the action checkpoint.

Before: the Studio chat applied whatever patch the model wrote (styles, hidden
parts, notes, buttons) the moment it answered, and revert, reset, undo,
restore-as-of, clearing the Studio history and applying an appearance draft
took any request that passed login, with no checkpoint receipt.

Now:
  * a Studio reply's patch is a PROPOSAL: the answer lists exactly what would
    change, the workspace is untouched, and Apply (the owner's press) applies
    exactly that patch, once, after a signed checkpoint receipt;
  * every route that changes saved state refuses a request that is not the
    owner's press in Friday's page, says so, and changes nothing;
  * "Not now" declines without changing anything.
Synthetic workspace content only.
"""
from __future__ import annotations

import json

import pytest

from agent_friday import core
from agent_friday.governance import action_gate as ag
from agent_friday.services import boot_guard, desktop_bus, off_record
from agent_friday.services import workspace_studio as studio

WS = "library"
PATCH_REPLY = ('Here is a calmer layout.\n```friday-customize\n'
               '{"summary":"calmer","density":"compact","accent":"#334455",'
               '"hidden":[".ws-custom-root .promo"],'
               '"css":".ws-custom-root .card{border-radius:12px}"}\n```')


def _press():
    """What Friday's page sends on a press: same-origin browser metadata, its session token and,
    where this tree has one, this launch's desk key."""
    h = {"Sec-Fetch-Site": "same-origin", "Origin": "http://localhost", "X-Friday-Token": core._current_api_token()}
    try:
        from agent_friday.services import desk_press
        h["X-Friday-Desk"] = desk_press._KEY
    except ImportError:
        pass
    return h


NOT_A_PRESS = [
    {},                                                           # a program on this PC
    {"Sec-Fetch-Site": "same-origin", "Origin": "http://localhost"},   # browser metadata, no token
]


@pytest.fixture(autouse=True)
def studio_home(tmp_path, monkeypatch):
    monkeypatch.setattr(studio, "WS_STUDIO_DIR", tmp_path / "studio")
    monkeypatch.setattr(core, "_load_settings", lambda: {})
    monkeypatch.setattr(off_record, "active", lambda settings=None: False)
    monkeypatch.setattr(off_record, "generation", lambda: 0)
    monkeypatch.setattr(boot_guard, "safe_mode", lambda: False)
    monkeypatch.setattr(desktop_bus, "broadcast", lambda *_a, **_k: None)


@pytest.fixture
def checkpoint(monkeypatch):
    seen = {"calls": [], "hold": False}

    def record_external(action, *, surface, approval_id=None, target=""):
        seen["calls"].append((action, surface, target))
        if seen["hold"]:
            raise ag.Held("the signed receipt could not be written")

    monkeypatch.setattr(ag, "record_external", record_external)
    return seen


def _reply_with_patch(monkeypatch):
    from agent_friday.routes import workspace_studio as routes
    real_turn = studio.workspace_chat_turn

    def turn(*args, **kwargs):
        kwargs["generate"] = lambda *_: PATCH_REPLY
        return real_turn(*args, **kwargs)

    monkeypatch.setattr(routes, "workspace_chat_turn", turn)
    monkeypatch.setattr(routes, "_get_friday_system_prompt", lambda **_k: "Synthetic system")


def _saved():
    p = studio.WS_STUDIO_DIR / (WS + ".json")
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}


# ── the Studio chat proposes; the owner applies ─────────────────────────────

def test_a_studio_reply_changes_nothing_and_lists_what_it_would_change(client, monkeypatch, checkpoint):
    _reply_with_patch(monkeypatch)
    r = client.post("/api/workspace/%s/chat" % WS, json={"message": "make it calmer"})
    assert r.status_code == 200, r.get_json()
    d = r.get_json()
    assert d["applied"] is False
    assert d["customization"] == {} and _saved().get("customization", {}) == {}
    changes = d["proposal"]["changes"]
    assert "Spacing: compact" in changes
    assert any(c.startswith("Accent colour") and "#334455" in c for c in changes)
    assert any(c.startswith("Hide 1 part") and ".promo" in c for c in changes)
    assert any(c.startswith("Restyle this workspace") and "border-radius:12px" in c for c in changes)
    assert d["proposal"]["status"] == "pending"
    assert checkpoint["calls"] == []


def test_apply_needs_the_owners_press_and_applies_exactly_the_proposal_once(client, monkeypatch, checkpoint):
    _reply_with_patch(monkeypatch)
    pid = client.post("/api/workspace/%s/chat" % WS, json={"message": "make it calmer"}).get_json()["proposal"]["id"]
    url = "/api/workspace/%s/proposals/%s/apply" % (WS, pid)
    for headers in NOT_A_PRESS:
        r = client.post(url, headers=headers)
        assert r.status_code == 403, headers
        assert r.get_json()["message"].startswith("Nothing changed")
    assert _saved().get("customization", {}) == {}
    assert checkpoint["calls"] == []
    r = client.post(url, headers=_press())
    assert r.status_code == 200, r.get_json()
    d = r.get_json()
    assert d["applied"] is True and d["proposal"]["status"] == "applied" and d["revert_to"]
    cust = _saved()["customization"]
    assert cust["density"] == "compact" and cust["accent"] == "#334455"
    assert cust["hidden"] == [".ws-custom-root .promo"] and "border-radius:12px" in cust["css"]
    assert checkpoint["calls"] == [("workspace:apply_proposal", "workspace_studio", WS)]
    again = client.post(url, headers=_press())
    assert again.status_code == 409, "a proposal applies once"


def test_a_held_checkpoint_applies_nothing(client, monkeypatch, checkpoint):
    _reply_with_patch(monkeypatch)
    pid = client.post("/api/workspace/%s/chat" % WS, json={"message": "make it calmer"}).get_json()["proposal"]["id"]
    checkpoint["hold"] = True
    r = client.post("/api/workspace/%s/proposals/%s/apply" % (WS, pid), headers=_press())
    assert r.status_code == 403 and r.get_json()["message"].startswith("Nothing changed")
    assert _saved().get("customization", {}) == {}


def test_not_now_declines_and_changes_nothing(client, monkeypatch, checkpoint):
    _reply_with_patch(monkeypatch)
    pid = client.post("/api/workspace/%s/chat" % WS, json={"message": "make it calmer"}).get_json()["proposal"]["id"]
    r = client.post("/api/workspace/%s/proposals/%s/decline" % (WS, pid))
    assert r.status_code == 200 and r.get_json()["proposal"]["status"] == "declined"
    assert _saved().get("customization", {}) == {}
    late = client.post("/api/workspace/%s/proposals/%s/apply" % (WS, pid), headers=_press())
    assert late.status_code == 409, "a declined proposal cannot be applied later"


# ── every saved-state route: the owner's press, then the checkpoint ─────────

def _routes():
    _, version = studio.apply_customization(WS, {"note": "Synthetic note", "summary": "note"})
    when = "2999-01-01T00:00:00"
    return [
        ("revert", {"version_id": version["id"]}, "workspace:revert"),
        ("reset", {}, "workspace:reset"),
        ("undo", {}, "workspace:undo"),
        ("restore-as-of", {"when": when}, "workspace:restore_as_of"),
        ("chat/clear", {}, "workspace:clear_history"),
    ]


@pytest.mark.parametrize("index", range(5))
def test_a_saved_state_change_without_the_owners_press_changes_nothing(client, checkpoint, index):
    path, body, _action = _routes()[index]
    studio.workspace_chat_turn(WS, "Library", "hello", generate=lambda *_: "hi")
    before = (studio.WS_STUDIO_DIR / (WS + ".json")).read_bytes()
    for headers in NOT_A_PRESS:
        r = client.post("/api/workspace/%s/%s" % (WS, path), json=body, headers=headers)
        assert r.status_code == 403, (path, headers, r.get_json())
        assert r.get_json()["message"].startswith("Nothing changed")
    assert (studio.WS_STUDIO_DIR / (WS + ".json")).read_bytes() == before
    assert checkpoint["calls"] == []


@pytest.mark.parametrize("index", range(5))
def test_the_owners_press_passes_the_checkpoint_before_the_change(client, checkpoint, index):
    path, body, action = _routes()[index]
    r = client.post("/api/workspace/%s/%s" % (WS, path), json=body, headers=_press())
    assert r.status_code == 200, (path, r.get_json())
    assert checkpoint["calls"] == [(action, "workspace_studio", WS)]


def test_applying_an_appearance_draft_needs_the_press_but_a_preview_does_not(client, checkpoint):
    url = "/api/workspace/%s/appearance" % WS
    preview = client.post(url, json={"patch": {"note": "Draft"}, "expected_revision": "new"})
    assert preview.status_code == 200 and preview.get_json()["applied"] is False
    for headers in NOT_A_PRESS:
        r = client.post(url, json={"patch": {"note": "Draft"}, "expected_revision": "new", "apply": True},
                        headers=headers)
        assert r.status_code == 403, headers
    assert not (studio.WS_STUDIO_DIR / (WS + ".json")).exists()
    r = client.post(url, json={"patch": {"note": "Draft"}, "expected_revision": "new", "apply": True},
                    headers=_press())
    assert r.status_code == 200 and r.get_json()["applied"] is True
    assert checkpoint["calls"] == [("workspace:appearance", "workspace_studio", WS)]

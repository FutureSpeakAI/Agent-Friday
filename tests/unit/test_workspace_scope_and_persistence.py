"""Workspace customization stays scoped and reports only durable changes."""
import json

import pytest

from agent_friday.services import desktop_bus, workspace_studio as ws


@pytest.fixture(autouse=True)
def isolated_studio(tmp_path, monkeypatch):
    monkeypatch.setattr(ws, "WS_STUDIO_DIR", tmp_path)
    desktop_bus.reset()
    yield
    desktop_bus.reset()


@pytest.mark.parametrize("css", [
    "body{display:none}",
    ".ws-custom-root, body{display:none}",
    ".ws-custom-root + body{display:none}",
    ".ws-custom-root:hover ~ .top-bar{display:none}",
    ".ws-custom-root[role='region'] + .other{display:none}",
    ".ws-custom-root/**/+ .top-bar{display:none}",
    ".ws-custom-root || td{display:none}",
    ".ws-custom-rooted{display:none}",
    "@media screen {.ws-custom-root .card{display:none}}",
    ".ws-custom-root{& + .top-bar{display:none}}",
    ".ws-custom-root{@at-root body{display:none}}",
    "@font-face{font-family:bad;src:url(https://example.com/font)}",
])
def test_css_cannot_select_outside_its_workspace(css):
    assert ws._sanitize_css(css) == ""


@pytest.mark.parametrize("css", [
    ".ws-custom-root{color:red}",
    ".ws-custom-root .card{padding:12px}",
    ".ws-custom-root > .card:hover{border-radius:14px}",
    ".ws-custom-root[data-mode='reading'] .card{font-size:16px}",
    ".ws-custom-root:has(> .card) .row{gap:10px}",
    ".ws-custom-root .card + .card{margin-top:8px}",
    ".ws-custom-root .card, .ws-custom-root .row{padding:12px}",
    ".ws-custom-root :is(.card,.row){padding:12px}",
    '.ws-custom-root [data-label="a,b"]{padding:12px}',
])
def test_flat_scoped_rules_keep_working(css):
    assert ws._sanitize_css(css) == css


@pytest.mark.parametrize("selector", [
    ".card,body", ".card, .top-bar", "+ .top-bar", "~ .other", "|| td",
    ".card{color:red}", "@media screen", ".card;body", "& + body", ".card/* unfinished",
])
def test_hidden_selectors_cannot_escape_the_prepended_root(selector):
    assert ws._sanitize_patch({"hidden": [selector]})["hidden"] == []


def test_hidden_descendant_selectors_work_without_becoming_rules():
    selectors = [".card", "> .banner", '.row[data-name="a,b"]', ".card:is(.quiet,.muted)"]
    assert ws._sanitize_patch({"hidden": selectors})["hidden"] == selectors


def test_current_and_snapshot_css_are_sanitized_on_every_read():
    raw = {"workspace": "news", "customization": {"css": "body{display:none}", "hidden": [".row,body"]},
           "versions": [{"id": "older", "customization": {"css": ".ws-custom-root + body{display:none}"}}]}
    (ws.WS_STUDIO_DIR / "news.json").write_text(json.dumps(raw), encoding="utf-8")
    doc = ws.load_ws_doc("news")
    assert doc["customization"] == {"css": "", "hidden": []}
    assert doc["versions"][0]["customization"]["css"] == ""
    assert ws.all_customizations()["news"] == doc["customization"]
    assert ws.revert_customization("news", "older")["customization"]["css"] == ""


@pytest.mark.parametrize("raw", ["{", "[]", '{"workspace":"different"}', '{"customization":[]}'])
def test_malformed_document_is_a_read_failure_not_a_blank_workspace(raw):
    path = ws.WS_STUDIO_DIR / "news.json"
    path.write_text(raw, encoding="utf-8")
    with pytest.raises(OSError):
        ws.load_ws_doc("news")
    with pytest.raises(OSError):
        ws.all_customizations()
    with pytest.raises(OSError):
        ws.apply_customization("news", {"note": "Do not overwrite the unreadable data"})
    assert path.read_text(encoding="utf-8") == raw


def test_atomic_failure_preserves_the_old_doc_and_does_not_notify(monkeypatch):
    ws.apply_customization("news", {"note": "Original"})
    path = ws.WS_STUDIO_DIR / "news.json"
    before = path.read_bytes()
    queue = desktop_bus.subscribe("studio-test", "desktop")
    def fail(*args):
        raise OSError("disk unavailable")
    monkeypatch.setattr(ws.os, "replace", fail)
    with pytest.raises(OSError):
        ws.apply_customization("news", {"note": "Unsaved"})
    assert path.read_bytes() == before
    assert queue.empty()
    assert not list(ws.WS_STUDIO_DIR.glob(".workspace-*.tmp"))


def test_failed_studio_chat_save_cannot_report_applied(monkeypatch):
    def fail(*args):
        raise OSError("disk unavailable")
    monkeypatch.setattr(ws.os, "replace", fail)
    def generate(*args):
        return 'Done.\n```friday-customize\n{"note":"Unsaved"}\n```'
    with pytest.raises(OSError):
        ws.workspace_chat_turn("news", "News", "pin a note", generate=generate)
    assert not (ws.WS_STUDIO_DIR / "news.json").exists()


def test_successful_save_and_undo_notify_only_after_the_file_exists():
    queue = desktop_bus.subscribe("studio-test", "desktop")
    ws.apply_customization("news", {"note": "Saved"})
    event = queue.get_nowait()
    assert event == {"type": "workspace_customizations_changed", "workspace": "news"}
    assert ws.load_ws_doc("news")["customization"]["note"] == "Saved"
    ws.undo_last("news")
    assert queue.get_nowait() == event
    assert ws.load_ws_doc("news")["customization"] == {}


@pytest.mark.parametrize("intervening", ["voice", "reset", "clear"])
def test_slow_salon_reply_cannot_overwrite_a_newer_workspace_change(intervening):
    ws.apply_customization("news", {"note": "Original"})
    ws.workspace_chat_turn("news", "News", "Start a review", generate=lambda *_: "Ready for your review.")
    queue = desktop_bus.subscribe("studio-race", "desktop")
    committed = []

    def delayed_reply(*_):
        if intervening == "voice":
            ws.apply_customization("news", {"note": "A newer voice change"})
        elif intervening == "reset":
            ws.reset_customization("news")
        else:
            ws.clear_chat("news")
        committed.append((ws.WS_STUDIO_DIR / "news.json").read_bytes())
        return 'Applied.\n```friday-customize\n{"note":"Stale model change"}\n```'

    with pytest.raises(ws.WorkspaceConflictError, match="workspace changed"):
        ws.workspace_chat_turn("news", "News", "Change the note", generate=delayed_reply)

    assert (ws.WS_STUDIO_DIR / "news.json").read_bytes() == committed[0]
    assert queue.get_nowait()["type"] == "workspace_customizations_changed"
    assert queue.empty(), "The refused stale write must not publish a success event"
    current = ws.load_ws_doc("news")
    assert current["customization"].get("note") != "Stale model change"
    if intervening == "clear":
        assert current["chat"] == []
    retried = ws.workspace_chat_turn("news", "News", "Change the note", generate=lambda *_:
        'Applied.\n```friday-customize\n{"note":"Requested after refresh"}\n```')
    assert retried["applied"] is True
    assert ws.load_ws_doc("news")["customization"]["note"] == "Requested after refresh"

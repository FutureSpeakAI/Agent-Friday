"""The approval queue's "Review" affordance must land on a surface that
exists.

Found 2026-09-06 (audit "durable goals has a backend and no UI"): every
pending approval card pushed a notification whose only action was
{"workspace": "system", "tab": "approvals"} -- and no workspace handled that
tab, and no HTML file referenced /api/approvals at all. Verified against the
code before fixing which way it failed: gate_action() returns "pending" and
every caller STOPS (agent.py's Google-connect tool, goals.py's milestone
gate), so the gap was a silent permanent block, not a leak -- the card sat
until `expires_at` and then expired into denied with nobody ever shown it.

These tests pin three things:
  1. the notification carries a `target` (the shape the tray's click handler
     navigates on -- its legacy actions[0] fallback drops `tab`);
  2. BOTH HTML files handle that exact tab inside the 'system' workspace's
     useNavTarget and talk to /api/approvals (a card in only one file is the
     index.html/app.html divergence class check_settings_readers.py exists
     for);
  3. the block-not-leak contract itself, so a future change that lets a
     pending action proceed fails here.
"""
from __future__ import annotations

import pathlib
import re

import pytest

from agent_friday.services import approvals

ROOT = pathlib.Path(__file__).resolve().parents[2]
HTML_FILES = ("index.html", "ui_parts/app.html")


@pytest.fixture(autouse=True)
def _iso(tmp_path, monkeypatch):
    monkeypatch.setattr(approvals, "APPROVALS_FILE", tmp_path / "approvals.json")
    from agent_friday.services import dissent_gate as dg
    monkeypatch.setattr(dg, "EVENTS_PATH", tmp_path / "dissent_events.jsonl")
    yield


def _pending_notification(monkeypatch) -> dict:
    import agent_friday.notifications_engine as ne
    captured: dict = {}
    monkeypatch.setattr(ne, "push", lambda **kw: captured.update(kw) or kw)
    rec = approvals.create_approval(
        kind="connector_auth", subject_type="connector", subject_id="google",
        title="Connect Google", action_description="Open Google's OAuth consent screen",
        force_gate=True)
    assert rec["status"] == "pending"
    return captured


def test_pending_notification_carries_a_navigable_target(monkeypatch):
    n = _pending_notification(monkeypatch)
    assert n.get("target") == {"workspace": "system", "tab": "approvals"}, n
    review = [a for a in n.get("actions") or [] if a.get("label") == "Review"]
    assert review and review[0].get("type") == "navigate"


def _system_nav_handler(text: str) -> str:
    """The body of useNavTarget('system', ...) -- compiled or JSX form."""
    m = re.search(r"useNavTarget\(\s*'system'\s*,", text)
    assert m, "no useNavTarget('system', ...) found"
    return text[m.end(): m.end() + 1500]


@pytest.mark.parametrize("rel", HTML_FILES)
def test_ui_handles_the_approvals_tab_and_reads_the_queue(rel):
    text = (ROOT / rel).read_text(encoding="utf-8", errors="replace")
    handler = _system_nav_handler(text)
    assert re.search(r"t\.tab\s*===\s*'approvals'", handler), (
        f"{rel}: the 'system' workspace's deep-link handler does not handle "
        f"tab 'approvals' -- the notification's Review button lands nowhere")
    assert "/api/approvals?status=pending" in text, f"{rel} never lists pending approvals"
    assert "/decide'" in text or "/decide\"" in text, f"{rel} has no approve/deny call"


def test_pending_approval_blocks_the_action_rather_than_leaking():
    out = approvals.gate_action(
        kind="connector_auth", subject_type="connector", subject_id="google",
        title="Connect Google", action_description="Open Google's OAuth consent screen",
        force_gate=True)
    assert out["status"] == "pending"
    # A second ask for the same subject is still pending, never auto-approved.
    again = approvals.gate_action(
        kind="connector_auth", subject_type="connector", subject_id="google",
        title="Connect Google", action_description="Open Google's OAuth consent screen",
        force_gate=True)
    assert again["status"] == "pending"
    assert again["approval"]["approval_id"] == out["approval"]["approval_id"]

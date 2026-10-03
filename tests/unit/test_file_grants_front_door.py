"""The file-grants front door: the panel, the one batched card, voice parity,
and the retired-key line.

  * Settings > Privacy > File access exists in the served page and its mirror,
    and drives the existing routes (list, add, remove, re-grant).
  * A turn that needs several grants raises ONE approval card listing every
    path; approving it on screen creates exactly those grants, and no other
    surface can approve it.
  * The model's file_access tool can ask, list and remove, but never creates a
    grant and never lifts a deny mark.
  * A grant line signed with a retired key is moved aside once, verbatim, never
    re-signed, and announced once in plain words; any line that does not verify
    grants nothing, and each distinct failure alarms once, not on every read.
"""
from __future__ import annotations

import datetime as _dt
import hashlib
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

import pytest

from agent_friday.services import approvals
from agent_friday.services import egress_gate as eg
from agent_friday.services import file_grant_requests as fgr
from agent_friday.services import file_grants as fg

ROOT = Path(__file__).resolve().parents[2]
OLD_KEY = "retired-session-secret-for-this-test"   # pragma: allowlist secret


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    ledger = tmp_path / "privacy" / "file_grants.jsonl"
    monkeypatch.setattr(fg, "_ledger_path", lambda: ledger)
    monkeypatch.setattr(approvals, "APPROVALS_FILE", tmp_path / "approvals.json")
    monkeypatch.setattr(approvals, "FRIDAY_DIR", tmp_path)
    monkeypatch.setattr(approvals, "_notify_pending", lambda rec: None)
    monkeypatch.setattr(approvals, "_HOOKS", {})
    monkeypatch.setitem(fgr._HOOKED, "done", False)
    monkeypatch.delenv("FRIDAY_SECRET_KEY", raising=False)
    fg._invalidate_cache()
    with eg._TRUSTED_LOCK:
        eg._PUBLIC_PARAS.clear()
        eg._PUBLIC_ORIGINS.clear()
    yield
    fg._invalidate_cache()
    with eg._TRUSTED_LOCK:
        eg._PUBLIC_PARAS.clear()
        eg._PUBLIC_ORIGINS.clear()


@pytest.fixture
def pushed(monkeypatch):
    sent = []

    class _Engine:
        def push(self, **kwargs):
            sent.append(kwargs)
            return dict(kwargs)   # a new entry, as the engine returns one

    import agent_friday.services.voice_engine as ve
    monkeypatch.setattr(ve, "_notif_engine", _Engine())
    return sent


def _file(tmp_path, name="cv.txt", body="Senior AI leadership experience.\n\nPivot analysis."):
    p = tmp_path / name
    p.write_text(body, encoding="utf-8")
    return p


def _aug25_ts() -> float:
    return _dt.datetime(2026, 8, 25, 12, 0, 0).timestamp()


def _write_line(event: dict, key: bytes) -> str:
    """Append one ledger line signed with `key`; returns the line as written."""
    ledger = fg._ledger_path()
    ledger.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps({"event": event, "hmac": fg._hmac_hex(event, key)},
                      sort_keys=True, separators=(",", ":"))
    with open(ledger, "a", encoding="utf-8", newline="\n") as f:
        f.write(line + "\n")
    fg._invalidate_cache()
    return line


def _old_grant(path: Path) -> dict:
    return {"event": "grant_file", "id": "old-aug-grant", "type": "file",
            "path": str(path.resolve()), "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            "created_ts": _aug25_ts(), "never_send_override": False,
            "ack_never_send_matches": [], "findings_summary": "2 paragraph(s)"}


def _restart():
    """What a process restart forgets: the folded state and the cached key."""
    fg._invalidate_cache()


def _fn(text: str, name: str) -> str:
    start = text.index("function %s(" % name)
    end = text.index("\nfunction ", start + 1)
    return text[start:end]


# ── The panel, in both UI files ──────────────────────────────────────────────

@pytest.mark.parametrize("rel", ["index.html", "ui_parts/app.html"])
def test_privacy_tab_has_a_file_access_section_that_calls_the_routes(rel):
    text = (ROOT / rel).read_text(encoding="utf-8")
    tab = _fn(text, "SettingsTabPrivacy")
    assert "File access" in tab and "FileAccessPanel" in tab, rel
    panel = _fn(text, "FileAccessPanel")
    assert "apiFetch('/api/privacy/file-grants')" in panel, rel          # list
    assert "post('/api/privacy/file-grants', body)" in panel, rel          # add
    assert "'/revoke'" in panel and "/api/privacy/file-grants/' +" in panel, rel   # remove
    assert "/api/privacy/file-grants/notices/" in panel and "'/regrant'" in panel, rel
    assert "scope: 'folder', expiry_days: days" in panel, rel
    for status in ("valid", "quarantined"):
        assert status + ":" in panel, (rel, status)


@pytest.mark.parametrize("rel", ["index.html", "ui_parts/app.html"])
def test_the_approval_card_draws_every_requested_grant(rel):
    text = (ROOT / rel).read_text(encoding="utf-8")
    body = _fn(text, "ApprovalCardBody")
    assert "a.kind === 'file_grant_request'" in body and "FileGrantRequestCard" in body, rel
    card = _fn(text, "FileGrantRequestCard")
    assert "items.map(" in card and "it.path" in card, rel
    assert "onDecide(a.approval_id, 'approve')" in card, rel


def test_the_two_ui_files_carry_the_same_panel_and_card():
    a = (ROOT / "index.html").read_text(encoding="utf-8")
    b = (ROOT / "ui_parts" / "app.html").read_text(encoding="utf-8")
    for name in ("FileAccessPanel", "FileGrantRequestCard"):
        assert _fn(a, name) == _fn(b, name), name


# ── One batched card ─────────────────────────────────────────────────────────

def test_a_turn_needing_several_grants_raises_one_card_listing_each(tmp_path):
    a, b = _file(tmp_path, "a.txt"), _file(tmp_path, "b.txt", "Other notes.")
    folder = tmp_path / "docs"
    folder.mkdir()

    out = fgr.request_access([{"path": str(a)}, {"path": str(b)},
                              {"path": str(folder), "type": "folder", "expiry_days": 7}],
                             reason="compare my two notes")

    pending = approvals.list_approvals(status="pending", kind=fgr.KIND)
    assert len(pending) == 1
    listed = {it["path"] for it in pending[0]["payload"]["items"]}
    assert listed == {str(a.resolve()), str(b.resolve()), str(folder.resolve())}
    assert len(pending[0]["payload"]["lines"]) == 3
    assert out["approval_id"] == pending[0]["approval_id"]
    assert fg.list_grants() == [], "raising a card must grant nothing"


def test_a_second_request_in_the_same_turn_replaces_the_card_with_the_union(tmp_path):
    a, b = _file(tmp_path, "a.txt"), _file(tmp_path, "b.txt", "Other notes.")
    first = fgr.request_access([{"path": str(a)}])
    second = fgr.request_access([{"path": str(b)}])

    pending = approvals.list_approvals(status="pending", kind=fgr.KIND)
    assert [c["approval_id"] for c in pending] == [second["approval_id"]]
    assert {it["path"] for it in pending[0]["payload"]["items"]} == {
        str(a.resolve()), str(b.resolve())}
    assert approvals.get_approval(first["approval_id"])["status"] == "denied"


def test_approving_the_card_on_screen_creates_exactly_the_listed_grants(tmp_path):
    a, b = _file(tmp_path, "a.txt"), _file(tmp_path, "b.txt", "Other notes.")
    card = fgr.request_access([{"path": str(a)}, {"path": str(b)}])

    rec, won = approvals.decide_with_outcome(card["approval_id"], "approve", decided_by="owner:ui")

    assert won and rec["status"] == "approved"
    assert {g["path"] for g in fg.list_grants()} == {str(a.resolve()), str(b.resolve())}
    assert approvals.get_approval(card["approval_id"])["consumed"] is True


@pytest.mark.parametrize("surface", ["owner", "owner:/voice/", "owner:sms", "owner:chat", "friday"])
def test_no_surface_but_the_screen_can_approve_a_file_access_card(tmp_path, surface):
    a = _file(tmp_path)
    card = fgr.request_access([{"path": str(a)}])

    rec, won = approvals.decide_with_outcome(card["approval_id"], "approve", decided_by=surface)

    assert not won and rec["status"] == "pending"
    assert fg.list_grants() == []
    # Declining from anywhere still works.
    rec, won = approvals.decide_with_outcome(card["approval_id"], "deny", decided_by=surface)
    assert won and rec["status"] == "denied" and fg.list_grants() == []


# ── The model's tool: voice parity without a grant tool ──────────────────────

def test_the_file_access_tool_asks_but_never_grants(tmp_path):
    a = _file(tmp_path)
    said = fgr.handle({"action": "ask", "items": [{"path": str(a)}]})
    assert "card" in said.lower()
    assert fg.list_grants() == []
    assert len(approvals.list_approvals(status="pending", kind=fgr.KIND)) == 1


def test_the_file_access_tool_is_shared_into_voice_and_governed():
    from agent_friday.governance import action_gate
    from agent_friday.services import voice_engine as ve
    from agent_friday.services.agent import CLAUDE_TOOL_HANDLERS
    assert "file_access" in ve._VOICE_SHARED_TOOLS
    assert "file_access" in CLAUDE_TOOL_HANDLERS
    assert "file_access" in action_gate.INTERNAL_TOOLS


def test_the_file_access_tool_removes_a_grant_but_never_a_deny_mark(tmp_path):
    a, b = _file(tmp_path, "a.txt"), _file(tmp_path, "b.txt", "Other.")
    g = fg.create_file_grant(str(a))
    d = fg.create_deny_mark(str(b), "file")

    fgr.handle({"action": "remove", "grant_id": d["id"]})
    assert any(x["id"] == d["id"] for x in fg.list_denies()), "a deny mark was lifted"

    fgr.handle({"action": "remove", "grant_id": g["id"]})
    assert fg.list_grants() == []


# ── The retired-key line ─────────────────────────────────────────────────────

def test_a_retired_key_line_is_quarantined_once_and_never_re_signed(tmp_path, monkeypatch, pushed):
    monkeypatch.setenv("FRIDAY_SECRET_KEY", OLD_KEY)
    p = _file(tmp_path)
    fg._secret_bytes()   # the ledger's own key exists, as it does in a real home
    line = _write_line(_old_grant(p), OLD_KEY.encode("utf-8"))

    for _ in range(3):
        _restart()
        state = fg._load_state(force=True)

    assert state.suspended is False and state.dropped == 0
    assert state.grants == {}, "a retired-key line must grant nothing"
    ledger_text = fg._ledger_path().read_text(encoding="utf-8")
    assert line not in ledger_text
    assert "reattested_from" not in ledger_text, "the old line was re-signed"
    q = fg._quarantine_path().read_text(encoding="utf-8").splitlines()
    assert len(q) == 1, "moved aside more than once"
    assert json.loads(q[0])["line"] == line, "the quarantined line is not verbatim"

    told = [x for x in pushed if "retired key" in (x.get("body") or "")]
    assert len(pushed) == 1 and len(told) == 1, pushed
    assert ("An old permission from Aug 25 was signed with a retired key; re-grant it?"
            in told[0]["body"])

    open_q = fg.notices()
    assert len(open_q) == 1 and open_q[0]["in_ledger"] is False
    assert open_q[0]["message"] == ("An old permission from Aug 25 was signed with a "
                                    "retired key; re-grant it?")
    rows = fg.access_rows()
    assert [r["status"] for r in rows] == ["quarantined"]


def test_re_granting_the_retired_line_goes_through_the_approval_card(tmp_path, monkeypatch, pushed):
    monkeypatch.setenv("FRIDAY_SECRET_KEY", OLD_KEY)
    p = _file(tmp_path)
    fg._secret_bytes()
    _write_line(_old_grant(p), OLD_KEY.encode("utf-8"))
    fg._load_state(force=True)
    notice = fg.notices()[0]

    out = fgr.regrant_notice(notice["id"])
    assert out["ok"] and fg.list_grants() == [], "re-grant must wait for the card"

    approvals.decide_with_outcome(out["approval_id"], "approve", decided_by="owner:ui")

    grants = fg.list_grants()
    assert [g["path"] for g in grants] == [str(p.resolve())]
    assert grants[0]["id"] != "old-aug-grant" and "reattested_from" not in grants[0]
    assert fg.notices() == []


def test_a_line_that_verifies_under_no_key_grants_nothing(tmp_path):
    p = _file(tmp_path)
    fg._secret_bytes()
    _write_line(_old_grant(p), b"a key nobody has")
    sha = hashlib.sha256(p.read_bytes()).hexdigest()

    check = fg.check_grant(p, sha256_hex=sha)
    fg.on_file_read(p, p.read_text(encoding="utf-8"))

    assert check.state == "none"
    with eg._TRUSTED_LOCK:
        assert not eg._PUBLIC_PARAS, "an unverifiable line registered sendable text"
    # Not moved automatically: nothing proves it was ever authentic.
    assert "old-aug-grant" in fg._ledger_path().read_text(encoding="utf-8")
    assert fg.status()["suspended"] is True
    open_q = fg.notices()
    assert len(open_q) == 1 and open_q[0]["in_ledger"] is True
    assert {r["status"] for r in fg.access_rows()} == {"unverified"}


def test_each_distinct_failure_alarms_once_not_on_every_read(tmp_path, pushed):
    p = _file(tmp_path)
    fg.create_file_grant(str(p))
    ledger = fg._ledger_path()
    with open(ledger, "a", encoding="utf-8") as f:
        f.write("garbage line one\n")

    for _ in range(4):
        _restart()
        fg._load_state(force=True)
    assert len(pushed) == 1

    with open(ledger, "a", encoding="utf-8") as f:
        f.write("garbage line two\n")
    for _ in range(3):
        _restart()
        fg._load_state(force=True)
    assert len(pushed) == 2


def test_hiding_the_unverifiable_notice_moves_nothing_and_keeps_the_pause(tmp_path):
    p = _file(tmp_path)
    fg._secret_bytes()
    line = _write_line(_old_grant(p), b"a key nobody has")
    notice = fg.notices()[0]
    assert notice["reason"] == "unverified" and notice["path"] is None
    assert "stay paused" in notice["resolves"]

    out = fg.resolve_notice(notice["id"], "dismissed", confirmed_by="owner:ui")

    assert out["ok"]
    assert fg.status()["suspended"] is True
    assert line in fg._ledger_path().read_text(encoding="utf-8")
    assert not fg._quarantine_path().exists()
    assert not re.search(r"reattest", fg._ledger_path().read_text(encoding="utf-8"))
    assert fg.notices() == []


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))

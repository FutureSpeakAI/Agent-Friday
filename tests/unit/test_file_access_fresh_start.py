"""Start file access fresh: the one way out of a suspension held by a line no
known key signed.

It is raised from the File access panel and approved only by the owner's
click on screen ("owner:ui"). On approval the whole ledger is set aside byte
for byte, a new ledger starts with no grants, never-send marks that still
verify are kept, and the suspension ends. Nothing else can trigger it.
"""
from __future__ import annotations

import hashlib
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

import pytest

from agent_friday.services import approvals
from agent_friday.services import file_grant_requests as fgr
from agent_friday.services import file_grants as fg

ROOT = Path(__file__).resolve().parents[2]
TEXT = ("The old permissions file, which has a line no current key signed, is set "
        "aside unchanged; you start with no file permissions and grant again")


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
    yield
    fg._invalidate_cache()


def _file(tmp_path, name="cv.txt", body="Senior AI leadership experience."):
    p = tmp_path / name
    p.write_text(body, encoding="utf-8")
    return p


def _sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def _broken_ledger(tmp_path) -> Path:
    """A valid grant, plus one line that no known key signed."""
    p = _file(tmp_path)
    fg.create_file_grant(str(p))
    ev = {"event": "deny", "id": "unknown", "type": "file", "path": str(p.resolve()),
          "created_ts": time.time()}
    line = json.dumps({"event": ev, "hmac": fg._hmac_hex(ev, b"a key nobody has")},
                      sort_keys=True, separators=(",", ":"))
    with open(fg._ledger_path(), "a", encoding="utf-8", newline="\n") as f:
        f.write(line + "\n")
    fg._invalidate_cache()
    assert fg.status()["suspended"] is True
    return p


def _set_aside(tmp_path):
    return sorted((tmp_path / "privacy").glob("file_grants.set-aside-*.jsonl"))


def _fn(text: str, name: str) -> str:
    start = text.index("function %s(" % name)
    return text[start:text.index("\nfunction ", start + 1)]


@pytest.mark.parametrize("rel", ["index.html", "ui_parts/app.html"])
def test_the_start_fresh_action_is_in_the_file_access_panel(rel):
    panel = _fn((ROOT / rel).read_text(encoding="utf-8"), "FileAccessPanel")
    assert "post('/api/privacy/file-grants/start-fresh')" in panel, rel
    assert "Start file access fresh" in panel and TEXT in panel, rel


def test_the_card_says_plainly_what_starting_fresh_does():
    out = fgr.request_fresh_start()
    card = approvals.get_approval(out["approval_id"])
    assert card["status"] == "pending" and card["kind"] == fgr.RESET_KIND
    assert TEXT in card["description"]


@pytest.mark.parametrize("surface", ["owner", "owner:/voice/", "owner:sms", "owner:chat", "friday"])
def test_without_an_on_screen_click_starting_fresh_does_nothing(tmp_path, surface):
    _broken_ledger(tmp_path)
    before = fg._ledger_path().read_bytes()
    out = fgr.request_fresh_start()

    rec, won = approvals.decide_with_outcome(out["approval_id"], "approve", decided_by=surface)

    assert not won and rec["status"] == "pending"
    assert fg._ledger_path().read_bytes() == before
    assert _set_aside(tmp_path) == []
    assert fg.status()["suspended"] is True
    assert fg.start_fresh(confirmed_by=surface)["ok"] is False
    assert fg._ledger_path().read_bytes() == before


def test_approved_on_screen_the_old_file_is_kept_byte_for_byte_and_nothing_is_granted(tmp_path):
    p = _broken_ledger(tmp_path)
    # An unverified line already set aside also holds the suspension.
    unverified = fg.list_unverified()[0]["line_sha256"]
    assert fg.dismiss_unverified(unverified, confirmed_by="owner")["ok"]
    assert fg._load_state(force=True).suspended is True
    before = fg._ledger_path().read_bytes()
    out = fgr.request_fresh_start()

    rec, won = approvals.decide_with_outcome(out["approval_id"], "approve", decided_by="owner:ui")

    assert won
    (aside,) = _set_aside(tmp_path)
    assert aside.read_bytes() == before, "the old ledger was not preserved byte for byte"
    assert fg._ledger_path().read_text(encoding="utf-8") == ""
    state = fg._load_state(force=True)
    assert state.suspended is False and state.grants == {} and state.dropped == 0
    assert fg.list_grants() == []
    assert fg.check_grant(p, sha256_hex=_sha(p)).state == "none"
    assert approvals.get_approval(out["approval_id"])["consumed"] is True


def test_starting_fresh_keeps_never_send_marks_that_still_verify(tmp_path):
    p = _broken_ledger(tmp_path)
    keep = _file(tmp_path, "medical.txt", "Private.")
    deny = fg.create_deny_mark(str(keep), "file")
    out = fgr.request_fresh_start()

    approvals.decide_with_outcome(out["approval_id"], "approve", decided_by="owner:ui")

    state = fg._load_state(force=True)
    assert state.suspended is False and state.grants == {}
    assert list(state.denies) == [deny["id"]]
    assert fg.check_grant(keep, sha256_hex=_sha(keep)).state == "denied"
    assert fg.check_grant(p, sha256_hex=_sha(p)).state == "none"


def test_no_model_tool_can_start_fresh(tmp_path):
    _broken_ledger(tmp_path)
    before = fg._ledger_path().read_bytes()
    for action in ("start_fresh", "reset", "start-fresh"):
        said = fgr.handle({"action": action})
        assert said.startswith("file_access error")
    assert fg._ledger_path().read_bytes() == before
    assert approvals.list_approvals(kind=fgr.RESET_KIND) == []


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))

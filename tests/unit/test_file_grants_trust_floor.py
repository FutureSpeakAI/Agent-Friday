"""The file-grants trust floor.

  * A new, distinct ledger failure is announced even while an older alarm is
    still unread, through the REAL notifications engine and its dedupe.
  * An unauthenticated line is never trusted for what it says it is: hiding
    its notice, setting it aside, or approving a card never ends suspension.
  * A deny that a retired key vouches for is honoured.
  * A path from a model is refused before the filesystem is touched when it is
    a network, device, web or relative path; large files are refused without
    being read whole; a folder grant on a whole drive or the home folder is
    refused.
  * Only an explicit on-screen click ("owner:ui") approves a file-access card;
    a text-message YES is told so plainly and changes nothing.
"""
from __future__ import annotations

import hashlib
import json
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

import pytest

from agent_friday.services import approvals
from agent_friday.services import file_grant_requests as fgr
from agent_friday.services import file_grants as fg
from tests.unit.phone_fakes import (fake_twilio, phone_home, quiet,  # noqa: F401
                                    verify_owner)

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
    yield
    fg._invalidate_cache()


@pytest.fixture
def notifications(tmp_path, monkeypatch):
    """The real notifications engine, writing to this test's own queue."""
    import agent_friday.notifications_engine as ne
    import agent_friday.services.voice_engine as ve
    monkeypatch.setattr(ne, "NOTIF_FILE", tmp_path / "notifications.json")
    monkeypatch.setattr(ve, "_notif_engine", ne)
    return lambda: [n for n in ne._load() if n.get("source") == "file_grants"]


def _file(tmp_path, name="cv.txt", body="Senior AI leadership experience."):
    p = tmp_path / name
    p.write_text(body, encoding="utf-8")
    return p


def _line(event: dict, key: bytes) -> str:
    ledger = fg._ledger_path()
    ledger.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps({"event": event, "hmac": fg._hmac_hex(event, key)},
                      sort_keys=True, separators=(",", ":"))
    with open(ledger, "a", encoding="utf-8", newline="\n") as f:
        f.write(line + "\n")
    fg._invalidate_cache()
    return line


def _sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


# ── 1. Alarms: one per distinct failure, through the real engine ─────────────

def test_a_new_distinct_failure_is_announced_while_an_older_alarm_is_unread(tmp_path, notifications):
    fg.create_file_grant(str(_file(tmp_path)))
    ledger = fg._ledger_path()
    with open(ledger, "a", encoding="utf-8") as f:
        f.write("garbage line one\n")
    fg._load_state(force=True)
    assert len(notifications()) == 1

    with open(ledger, "a", encoding="utf-8") as f:
        f.write("garbage line two\n")
    fg._invalidate_cache()
    fg._load_state(force=True)
    fg._invalidate_cache()
    fg._load_state(force=True)

    told = notifications()
    assert len(told) == 2, "the second, different failure was swallowed by the first alarm"
    assert len({n["dedupe_key"] for n in told}) == 2


def _a_corrupt_line(tmp_path):
    fg.create_file_grant(str(_file(tmp_path)))
    with open(fg._ledger_path(), "a", encoding="utf-8") as f:
        f.write("garbage line\n")
    sha = hashlib.sha256(b"garbage line").hexdigest()
    return sha, "file_grants_ledger_failure:%s" % sha


def test_an_alarm_that_folds_into_its_waiting_card_is_told(tmp_path, notifications, monkeypatch):
    """The engine folds a repeat into the undismissed card with the same key:
    that card takes this alarm's words and its count goes up, so the user is
    told, and the alarm is recorded as told."""
    import agent_friday.notifications_engine as ne
    sha, key = _a_corrupt_line(tmp_path)
    ne.push(title="older", body="", source="file_grants", dedupe_key=key)

    fg._load_state(force=True)

    (card,) = [n for n in notifications() if n.get("dedupe_key") == key]
    assert card.get("count") == 2 and "ledger corrupted" in card["title"], card
    assert sha in fg._read_notices()["alarmed"]


def test_an_alarm_the_engine_suppressed_is_not_marked_as_told(tmp_path, notifications, monkeypatch):
    """A card the owner dismissed is not shown again at the same rank: that
    alarm reached no one, so it is not recorded as told and the next read
    tries again."""
    import agent_friday.notifications_engine as ne
    sha, key = _a_corrupt_line(tmp_path)
    older = ne.push(title="older", body="", source="file_grants", priority="high",
                    kind="warning", dedupe_key=key)
    assert ne.dismiss(older["id"])

    fg._load_state(force=True)

    assert sha not in fg._read_notices()["alarmed"], (
        "a push the engine suppressed was recorded as the user being told")


# ── 2. An unauthenticated line is never trusted ──────────────────────────────

def _tampered(kind: str, p: Path) -> dict:
    ev = {"event": kind, "id": "x-%s" % kind, "type": "file", "path": str(p.resolve()),
          "created_ts": time.time()}
    if kind == "grant_file":
        ev["sha256"] = _sha(p)
    return ev


@pytest.mark.parametrize("claims", ["deny", "grant_file"])
def test_hiding_an_unverified_notice_never_lifts_the_suspension(tmp_path, claims):
    other = _file(tmp_path, "other.txt", "Other notes.")
    fg.create_file_grant(str(other))
    fg._secret_bytes()
    line = _line(_tampered(claims, _file(tmp_path)), b"a key nobody has")
    assert fg.status()["suspended"] is True

    (notice,) = [n for n in fg.notices() if n["in_ledger"]]
    assert notice["reason"] == "unverified" and notice["path"] is None
    assert "stay paused" in notice["resolves"]
    assert fg.resolve_notice(notice["id"], "dismissed", confirmed_by="owner:ui")["ok"]

    assert fg.status()["suspended"] is True
    assert fg.check_grant(other, sha256_hex=_sha(other)).state == "none"
    assert line in fg._ledger_path().read_text(encoding="utf-8")


@pytest.mark.parametrize("claims", ["deny", "grant_file"])
def test_setting_an_unverified_line_aside_keeps_grants_suspended(tmp_path, claims):
    other = _file(tmp_path, "other.txt", "Other notes.")
    fg.create_file_grant(str(other))
    fg._secret_bytes()
    line = _line(_tampered(claims, _file(tmp_path)), b"a key nobody has")
    sha = hashlib.sha256(line.encode("utf-8")).hexdigest()

    out = fg.dismiss_unverified(sha, confirmed_by="owner")

    assert out["ok"]
    assert line not in fg._ledger_path().read_text(encoding="utf-8")
    assert fg._load_state(force=True).suspended is True, (
        "moving an unauthenticated line aside re-enabled every paused grant")
    assert fg.check_grant(other, sha256_hex=_sha(other)).state == "none"


def test_an_unverified_line_cannot_be_re_granted_through_a_card(tmp_path):
    fg._secret_bytes()
    _line(_tampered("grant_file", _file(tmp_path)), b"a key nobody has")
    (notice,) = fg.notices()

    out = fgr.regrant_notice(notice["id"])

    assert out["ok"] is False
    assert approvals.list_approvals(kind=fgr.KIND) == []
    assert fg.status()["suspended"] is True


def test_reattest_refuses_a_line_no_known_key_signed(tmp_path):
    fg._secret_bytes()
    line = _line(_tampered("grant_file", _file(tmp_path)), b"a key nobody has")
    sha = hashlib.sha256(line.encode("utf-8")).hexdigest()

    out = fg.reattest(sha, confirmed_by="owner")

    assert out["ok"] is False
    text = fg._ledger_path().read_text(encoding="utf-8")
    assert "reattested_from" not in text and line in text


# ── 4. A retired-key deny is honoured ────────────────────────────────────────

def test_a_deny_signed_with_the_retired_key_is_honoured(tmp_path, monkeypatch):
    monkeypatch.setenv("FRIDAY_SECRET_KEY", OLD_KEY)
    p = _file(tmp_path)
    fg._secret_bytes()
    fg.create_file_grant(str(p))
    _line({"event": "deny", "id": "old-deny", "type": "file", "path": str(p.resolve()),
           "created_ts": time.time()}, OLD_KEY.encode("utf-8"))

    state = fg._load_state(force=True)

    assert "old-deny" in state.denies
    assert fg.check_grant(p, sha256_hex=_sha(p)).state == "denied"


# ── 3. Paths from a model ────────────────────────────────────────────────────

class _Touched(BaseException):
    """Raised if describe_item reaches the filesystem; not an Exception, so no
    broad `except Exception` can swallow it."""


@pytest.mark.parametrize("text", [
    "\\\\fileserver\\share\\cv.txt", "//fileserver/share/cv.txt",
    "\\\\.\\PhysicalDrive0", "\\\\?\\C:\\Windows\\win.ini",
    "https://example.com/cv.txt", "relative/cv.txt",
])
def test_unsafe_paths_are_refused_before_the_filesystem_is_touched(text, monkeypatch):
    def _no_path(*a, **k):
        raise _Touched(text)
    monkeypatch.setattr(fgr, "Path", _no_path)

    out = fgr.describe_item({"path": text})

    assert out["ok"] is False and out["error"]


def test_a_large_file_is_refused_without_being_read_whole(tmp_path, monkeypatch):
    monkeypatch.setattr(fg, "MAX_GRANT_BYTES", 1024)
    big = tmp_path / "big.bin"
    big.write_bytes(b"x" * 4096)

    out = fgr.describe_item({"path": str(big)})

    assert out["ok"] is False and "too large" in out["error"]
    with pytest.raises(ValueError):
        fg.create_file_grant(str(big))


def test_a_folder_grant_on_a_drive_root_or_the_home_folder_is_refused(tmp_path):
    for broad in (Path(tmp_path.anchor), Path.home()):
        out = fgr.describe_item({"path": str(broad), "type": "folder"})
        assert out["ok"] is False and "too broad" in out["error"], broad
        with pytest.raises(ValueError):
            fg.create_scope_grant(str(broad), "folder", 7)


def test_a_local_path_whose_link_leads_to_the_network_is_refused(tmp_path, monkeypatch):
    (tmp_path / "link").mkdir()
    target = _file(tmp_path / "link", "cv.txt")
    unc = chr(92) * 2 + "fileserver" + chr(92) + "share"
    monkeypatch.setattr(fg, "_link_target",
                        lambda prefix: unc if prefix.lower().endswith("link") else None)

    out = fgr.describe_item({"path": str(target)})

    assert out["ok"] is False and "network" in out["error"]
    with pytest.raises(ValueError):
        fg.create_file_grant(str(target))
    monkeypatch.setattr(fg, "_link_target", lambda prefix: None)
    assert fgr.describe_item({"path": str(target)})["ok"] is True


# ── Only the on-screen click approves ────────────────────────────────────────

def test_the_bare_owner_label_does_not_approve_a_file_card(tmp_path):
    card = fgr.request_access([{"path": str(_file(tmp_path))}])

    rec, won = approvals.decide_with_outcome(card["approval_id"], "approve", decided_by="owner")

    assert not won and rec["status"] == "pending" and fg.list_grants() == []


@pytest.mark.parametrize("rel", ["index.html", "ui_parts/app.html"])
def test_the_page_labels_its_approval_clicks_as_on_screen(rel):
    text = (ROOT / rel).read_text(encoding="utf-8")
    start = text.index("function fridayDecideApproval(")
    body = text[start:text.index("\nfunction ", start + 1)]
    assert re.search(r"decided_by: 'owner:ui'", body), rel


def test_a_text_message_yes_is_told_file_access_approves_only_on_screen(
        phone_home, fake_twilio, monkeypatch, quiet):  # noqa: F811
    from agent_friday.phone import config, service
    monkeypatch.setattr(approvals, "_notify_pending", lambda rec: None)
    verify_owner(monkeypatch)
    config.update({"sms_approvals": True})
    monkeypatch.setattr(service, "_gate_text", lambda t: t)
    card = approvals.create_approval(kind=fgr.KIND, subject_type=fgr.SUBJECT_TYPE,
                                     subject_id="req-sms", title="Let cloud models read this file",
                                     action_class="outward", force_gate=True)
    assert service.offer_pending_approvals() == 1
    code = re.search(r"\b(\d{6})\b", fake_twilio.sent_bodies()[-1]).group(1)

    said = service.handle_approval_reply("YES %s" % code)

    assert "only on screen" in said
    assert approvals.get_approval(card["approval_id"])["status"] == "pending"
    assert service.handle_approval_reply("NO %s" % code).startswith("Denied")


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))

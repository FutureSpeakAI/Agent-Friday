"""
OFW monitor — daily scan of Our Family Wizard via Claude-in-Chrome MCP.

This module is built so the "browser bits" are pluggable: server.py
injects a `Session` that wraps the actual MCP calls; tests / CLI use
the in-memory `LocalSession` stub. Everything else — sentiment, archive,
dedup, notifications — runs without a browser.

Privacy invariant: NO message content ever leaves this machine. The
classifier is lexicon-based, the LLM summarizer is OFF by default, and
the `_assert_local_only()` guard fails loud if anything tries to call
remote.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import os
import re
import secrets
import threading
import time
from dataclasses import dataclass, field, asdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional, Tuple

try:
    import yaml
except ImportError:
    yaml = None  # type: ignore


log = logging.getLogger("friday.skills.ofw_monitor")

HERE = Path(__file__).parent
DEFAULT_CONFIG_PATH = HERE / "config.yaml"
USER_OVERRIDE = Path.home() / ".friday" / "skills" / "ofw_monitor.local.yaml"
STATE_PATH = Path.home() / ".friday" / "skills" / "ofw_monitor.state.json"


# ════════════════════════════════════════════════════════════════════════
#  Config
# ════════════════════════════════════════════════════════════════════════

def load_config(path: Optional[Path] = None) -> Dict[str, Any]:
    if yaml is None:
        raise RuntimeError("PyYAML required for ofw_monitor config")
    cfg_path = Path(path) if path else DEFAULT_CONFIG_PATH
    with cfg_path.open(encoding="utf-8") as f:
        base = yaml.safe_load(f) or {}
    if USER_OVERRIDE.exists():
        try:
            with USER_OVERRIDE.open(encoding="utf-8") as f:
                base = _deep_merge(base, yaml.safe_load(f) or {})
        except Exception as e:
            log.warning("ofw user override merge failed: %s", e)
    return base


def _deep_merge(a: Dict[str, Any], b: Dict[str, Any]) -> Dict[str, Any]:
    out = dict(a)
    for k, v in b.items():
        if k in out and isinstance(out[k], dict) and isinstance(v, dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


# ════════════════════════════════════════════════════════════════════════
#  Data types
# ════════════════════════════════════════════════════════════════════════

@dataclass
class OFWMessage:
    msg_id: str = ""
    sender: str = ""
    subject: str = ""
    received_at: str = ""              # ISO; captured timezone preserved
    body_excerpt: str = ""             # first ~300 chars, redacted
    unread: bool = True
    parent_thread: str = ""
    raw_html: str = ""                 # only kept in archive, never notified
    sentiment: str = "neutral"         # cooperative | neutral | passive-aggressive | hostile
    sentiment_score: float = 0.0       # [-1, 1]


@dataclass
class OFWCalendarEvent:
    event_id: str = ""
    title: str = ""
    start: str = ""
    end: str = ""
    type: str = ""                     # custody | swap | activity | medical
    party: str = ""
    last_modified_at: str = ""
    needs_action: bool = False


@dataclass
class OFWExpense:
    expense_id: str = ""
    submitted_at: str = ""
    amount: float = 0.0
    currency: str = "USD"
    category: str = ""
    submitter: str = ""
    status: str = ""                    # pending | approved | disputed
    receipt_attached: bool = False


@dataclass
class ScanResult:
    started_at: str = ""
    finished_at: str = ""
    duration_ms: int = 0
    mode: str = "full"
    scanned_messages: int = 0
    new_messages: int = 0
    calendar_changes: int = 0
    new_expenses: int = 0
    overdue_responses: int = 0
    tone_shifts: List[Dict[str, Any]] = field(default_factory=list)
    archive_path: str = ""
    notifications_sent: int = 0
    errors: List[str] = field(default_factory=list)
    consent_required: bool = False


# ════════════════════════════════════════════════════════════════════════
#  Session — pluggable browser layer
# ════════════════════════════════════════════════════════════════════════

class Session:
    """
    Wraps Claude-in-Chrome MCP. server.py supplies a concrete one;
    `LocalSession` below is a no-network stub used by tests / CLI.
    """

    def ensure_logged_in(self) -> bool: ...
    def fetch_inbox(self) -> List[OFWMessage]: ...
    def fetch_calendar(self) -> List[OFWCalendarEvent]: ...
    def fetch_expenses(self) -> List[OFWExpense]: ...
    def take_screenshot(self, area: str) -> bytes: ...


class LocalSession(Session):
    """Test/CLI stub — returns no records, never touches the network."""

    def ensure_logged_in(self) -> bool: return True
    def fetch_inbox(self) -> List[OFWMessage]: return []
    def fetch_calendar(self) -> List[OFWCalendarEvent]: return []
    def fetch_expenses(self) -> List[OFWExpense]: return []
    def take_screenshot(self, area: str) -> bytes: return b""


# ════════════════════════════════════════════════════════════════════════
#  Privacy redaction
# ════════════════════════════════════════════════════════════════════════

_PHONE_RE = re.compile(
    r"(?<!\d)(?:\+?1[\s.-]?)?\(?\d{3}\)?[\s.-]?\d{3}[\s.-]?\d{4}(?!\d)"
)
_STREET_RE = re.compile(
    r"\b\d{1,6}\s+[A-Z][A-Za-z]+(?:\s[A-Z][A-Za-z]+)*\s"
    r"(?:Street|St|Avenue|Ave|Drive|Dr|Lane|Ln|Road|Rd|Court|Ct|Way|Blvd|Boulevard)\b"
)


def redact(text: str, cfg: Dict[str, Any]) -> str:
    """Strip phone numbers, addresses, and minor's name before any non-vault use."""
    if not text:
        return text
    out = text
    privacy = cfg.get("privacy", {})
    if privacy.get("redact_phone_numbers", True):
        out = _PHONE_RE.sub("[PHONE]", out)
    if privacy.get("redact_street_addresses", True):
        out = _STREET_RE.sub("[ADDRESS]", out)
    if privacy.get("redact_minor_full_name", True):
        minor_full = privacy.get("minor_full_name", "")
        if minor_full and minor_full in out:
            first = minor_full.split()[0] if minor_full else ""
            out = out.replace(minor_full, first or "[MINOR]")
    return out


def _assert_local_only(cfg: Dict[str, Any]):
    """Hard guard. Anything that flips this is a bug or an attack."""
    if not cfg.get("privacy", {}).get("never_send_to_remote", True):
        raise RuntimeError(
            "OFW monitor refuses to run — privacy.never_send_to_remote is False. "
            "This guard exists to prevent OFW content from leaving the machine."
        )


# ════════════════════════════════════════════════════════════════════════
#  Sentiment — lexicon-based local classifier
# ════════════════════════════════════════════════════════════════════════

def classify_sentiment(text: str, cfg: Dict[str, Any]) -> Tuple[str, float]:
    """
    Returns (label, score in [-1, 1]).

    Lexicon-based; never touches remote LLMs. Honest about its limits —
    flags passive-aggressive patterns separately from raw polarity.
    """
    sent_cfg = cfg.get("sentiment", {})
    if sent_cfg.get("engine") != "lexicon_local":
        # Refuse anything other than local lexicon
        log.warning("Unknown sentiment engine; defaulting to local lexicon")
    lower = (text or "").lower()
    coop = sent_cfg.get("cooperative_triggers", [])
    hostile = sent_cfg.get("hostile_triggers", [])
    pa = sent_cfg.get("passive_aggressive_triggers", [])

    coop_hits = sum(1 for kw in coop if kw in lower)
    hostile_hits = sum(1 for kw in hostile if kw in lower)
    pa_hits = sum(1 for kw in pa if kw in lower)

    # Polarity score — bounded
    score = (coop_hits * 0.25) - (hostile_hits * 0.4)
    score = max(-1.0, min(1.0, score))

    thresholds = sent_cfg.get("thresholds", {})
    if pa_hits >= thresholds.get("passive_aggressive_signal_min", 2) and hostile_hits == 0:
        return "passive-aggressive", score
    if score <= thresholds.get("hostile_min", -0.30):
        return "hostile", score
    if score >= thresholds.get("cooperative_min", 0.30):
        return "cooperative", score
    return "neutral", score


# ════════════════════════════════════════════════════════════════════════
#  Archive — HMAC chain
# ════════════════════════════════════════════════════════════════════════

class HMACChainArchive:
    """
    Append-only JSONL. Each record carries:
      - prev_hash:  SHA-256 of the previous record's hmac
      - hmac:       HMAC-SHA256(key, canonical_json)

    Tampering with any record breaks the chain on every subsequent record.
    """

    def __init__(self, path: Path, key: bytes):
        self.path = Path(os.path.expanduser(str(path)))
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.key = key
        self._lock = threading.Lock()

    def append(self, record: Dict[str, Any]) -> Dict[str, Any]:
        with self._lock:
            prev_hash = self._tail_hmac()
            payload = dict(record)
            payload["prev_hash"] = prev_hash
            payload["ts"] = _now_iso()
            canonical = json.dumps(payload, sort_keys=True, ensure_ascii=False)
            mac = hmac.new(self.key, canonical.encode("utf-8"), hashlib.sha256).hexdigest()
            payload["hmac"] = mac
            with self.path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(payload, ensure_ascii=False) + "\n")
            return payload

    def _tail_hmac(self) -> str:
        if not self.path.exists():
            return "GENESIS"
        last = ""
        with self.path.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    last = line
        if not last:
            return "GENESIS"
        try:
            return json.loads(last).get("hmac", "GENESIS")
        except Exception:
            return "GENESIS"

    def verify(self) -> Tuple[bool, int, str]:
        """Walk the chain, return (ok, records_checked, message)."""
        if not self.path.exists():
            return True, 0, "empty archive"
        prev = "GENESIS"
        count = 0
        with self.path.open("r", encoding="utf-8") as f:
            for i, line in enumerate(f):
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except Exception as e:
                    return False, count, f"line {i}: bad JSON ({e})"
                if rec.get("prev_hash") != prev:
                    return False, count, f"line {i}: chain broken (prev mismatch)"
                mac_claimed = rec.pop("hmac", None)
                canonical = json.dumps(rec, sort_keys=True, ensure_ascii=False)
                mac_actual = hmac.new(self.key, canonical.encode("utf-8"),
                                       hashlib.sha256).hexdigest()
                if mac_claimed != mac_actual:
                    return False, count, f"line {i}: HMAC mismatch"
                prev = mac_actual
                count += 1
        return True, count, "ok"


def _archive_key() -> bytes:
    """Stable HMAC key from Friday vault. Generated once, never logged."""
    key_path = Path.home() / ".friday" / "vault" / ".ofw_hmac_key"
    if key_path.exists():
        return base64.b64decode(key_path.read_text("utf-8").strip())
    key_path.parent.mkdir(parents=True, exist_ok=True)
    raw = secrets.token_bytes(32)
    key_path.write_text(base64.b64encode(raw).decode("ascii"), encoding="utf-8")
    try:
        os.chmod(key_path, 0o600)
    except OSError:
        pass
    return raw


# ════════════════════════════════════════════════════════════════════════
#  Dedup state
# ════════════════════════════════════════════════════════════════════════

def _load_state() -> Dict[str, Any]:
    if not STATE_PATH.exists():
        return {"seen_msg_ids": [], "seen_event_ids": [], "seen_expense_ids": [],
                "calendar_snapshot": {}, "last_run_at": None,
                "response_baselines": {}}
    try:
        return json.loads(STATE_PATH.read_text("utf-8"))
    except Exception:
        return {"seen_msg_ids": [], "seen_event_ids": [], "seen_expense_ids": [],
                "calendar_snapshot": {}, "last_run_at": None,
                "response_baselines": {}}


def _save_state(state: Dict[str, Any]):
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    STATE_PATH.write_text(json.dumps(state, indent=2), encoding="utf-8")


# ════════════════════════════════════════════════════════════════════════
#  Notifications
# ════════════════════════════════════════════════════════════════════════

def _notify_payload(rule: str, *, title: str, body: str,
                     icon: str = "🟡", priority: str = "normal",
                     meta: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    return {
        "channel": "chat",
        "icon": icon,
        "priority": priority,
        "title": title,
        "body": body.strip(),
        "summary": title,
        "meta": {"kind": rule, "source": "ofw_monitor", **(meta or {})},
    }


def _rule(cfg: Dict[str, Any], name: str) -> Dict[str, Any]:
    return (cfg.get("notifications", {}).get("rules", {}) or {}).get(name, {})


# ════════════════════════════════════════════════════════════════════════
#  Scan
# ════════════════════════════════════════════════════════════════════════

def scan(*, config: Optional[Dict[str, Any]] = None,
         session: Optional[Session] = None,
         notify: Optional[Callable[[Dict[str, Any]], None]] = None,
         mode: str = "full") -> Dict[str, Any]:
    """Run one OFW monitoring cycle. Returns ScanResult dict."""
    cfg = config or load_config()
    _assert_local_only(cfg)

    session = session or LocalSession()
    notify = notify or _log_notify

    started = time.monotonic()
    result = ScanResult(started_at=_now_iso(), mode=mode)
    state = _load_state()
    archive = HMACChainArchive(
        Path(cfg["archive"]["vault_path"]).expanduser(),
        _archive_key(),
    )
    result.archive_path = str(archive.path)

    if not session.ensure_logged_in():
        result.consent_required = True
        result.errors.append("login required")
        notify(_notify_payload(
            "consent_required",
            title="OFW · login required",
            body="The Chrome session for OFW expired. Open the tab and re-auth before the next scan.",
            **{k: v for k, v in _rule(cfg, "consent_required").items()
               if k in ("priority", "icon")},
        ))
        result.notifications_sent += 1
        return _finish(result, started)

    # ── Messages ──
    if mode in ("full", "messages"):
        try:
            messages = session.fetch_inbox() or []
            result.scanned_messages = len(messages)
            new = [m for m in messages if m.msg_id not in state["seen_msg_ids"]]
            for m in new:
                # Local sentiment + redaction
                lbl, score = classify_sentiment(m.body_excerpt, cfg)
                m.sentiment = lbl
                m.sentiment_score = score
                m.body_excerpt = redact(m.body_excerpt, cfg)
                m.sender = redact(m.sender, cfg)
                # Archive (full raw kept in vault only)
                archive.append({"kind": "message", "data": asdict(m)})
                # Notification (redacted)
                rule = _rule(cfg, "new_message")
                notify(_notify_payload(
                    "new_message",
                    title=f"OFW · new from {m.sender}",
                    body=f"**{m.subject or '(no subject)'}**\n\n"
                         f"{m.body_excerpt}\n\n"
                         f"_sentiment: {m.sentiment} ({m.sentiment_score:+.2f})_",
                    priority=rule.get("priority", "high"),
                    icon=rule.get("icon", "📬"),
                    meta={"msg_id": m.msg_id, "sentiment": m.sentiment},
                ))
                result.notifications_sent += 1
                state["seen_msg_ids"].append(m.msg_id)
            result.new_messages = len(new)
            # Tone shift detection
            result.tone_shifts = _detect_tone_shifts(messages, state, cfg)
            for shift in result.tone_shifts:
                rule = _rule(cfg, "tone_shift")
                notify(_notify_payload(
                    "tone_shift",
                    title=f"OFW · tone shift",
                    body=(f"Recent tone trend: **{shift['from']}** → **{shift['to']}**.\n"
                          f"Window: last {shift['window_days']} days.\n"
                          f"_No action required — surfaced for awareness._"),
                    priority=rule.get("priority", "low"),
                    icon=rule.get("icon", "🌡️"),
                    meta=shift,
                ))
                result.notifications_sent += 1
            # Overdue responses
            overdue = _detect_overdue(messages, state, cfg)
            result.overdue_responses = len(overdue)
            if overdue:
                rule = _rule(cfg, "response_overdue")
                notify(_notify_payload(
                    "response_overdue",
                    title=f"OFW · {len(overdue)} message(s) > 72h",
                    body=("\n".join(f"  · {o['subject']} from {o['sender']}"
                                    f"  ({o['age_hours']:.0f}h ago)"
                                    for o in overdue[:5])),
                    priority=rule.get("priority", "critical"),
                    icon=rule.get("icon", "⏰"),
                    meta={"count": len(overdue)},
                ))
                result.notifications_sent += 1
        except Exception as e:
            log.exception("message scan failed")
            result.errors.append(f"messages: {e}")

    # ── Calendar ──
    if mode in ("full", "calendar"):
        try:
            events = session.fetch_calendar() or []
            previous = state.get("calendar_snapshot") or {}
            current = {ev.event_id: asdict(ev) for ev in events}
            diff = _calendar_diff(previous, current)
            result.calendar_changes = sum(len(v) for v in diff.values())
            if result.calendar_changes:
                rule = _rule(cfg, "calendar_change")
                lines: List[str] = []
                for kind, items in diff.items():
                    for it in items[:3]:
                        lines.append(f"  · [{kind}] {it.get('title','(no title)')} — "
                                     f"{it.get('start','')}")
                notify(_notify_payload(
                    "calendar_change",
                    title="OFW · custody schedule changed",
                    body="\n".join(lines) or "Calendar updated.",
                    priority=rule.get("priority", "high"),
                    icon=rule.get("icon", "📅"),
                    meta={"diff_counts": {k: len(v) for k, v in diff.items()}},
                ))
                result.notifications_sent += 1
                archive.append({"kind": "calendar_diff", "data": diff})
            state["calendar_snapshot"] = current
        except Exception as e:
            log.exception("calendar scan failed")
            result.errors.append(f"calendar: {e}")

    # ── Expenses ──
    if mode in ("full", "expenses"):
        try:
            expenses = session.fetch_expenses() or []
            new_exp = [e for e in expenses if e.expense_id not in state["seen_expense_ids"]]
            for e in new_exp:
                archive.append({"kind": "expense", "data": asdict(e)})
                rule = _rule(cfg, "expense_submitted")
                notify(_notify_payload(
                    "expense_submitted",
                    title=f"OFW · {e.category or 'expense'} ${e.amount:.2f}",
                    body=(f"Submitted by **{redact(e.submitter, cfg)}** on "
                          f"{e.submitted_at}. Status: `{e.status}`."),
                    priority=rule.get("priority", "medium"),
                    icon=rule.get("icon", "💵"),
                    meta={"expense_id": e.expense_id, "status": e.status},
                ))
                result.notifications_sent += 1
                state["seen_expense_ids"].append(e.expense_id)
            result.new_expenses = len(new_exp)
        except Exception as e:
            log.exception("expense scan failed")
            result.errors.append(f"expenses: {e}")

    state["last_run_at"] = _now_iso()
    _save_state(state)

    # SkillOpt record
    _record_to_skillopt(result, cfg)

    return _finish(result, started)


def _finish(result: ScanResult, started: float) -> Dict[str, Any]:
    result.finished_at = _now_iso()
    result.duration_ms = int((time.monotonic() - started) * 1000)
    return asdict(result)


# ── Detectors ──────────────────────────────────────────────────────────

def _detect_tone_shifts(messages: List[OFWMessage], state: Dict[str, Any],
                        cfg: Dict[str, Any]) -> List[Dict[str, Any]]:
    sent_cfg = cfg.get("sentiment", {})
    window = sent_cfg.get("tone_shift_window_days", 14)
    cutoff = datetime.utcnow() - timedelta(days=window)
    recent = [m for m in messages if _parse_iso(m.received_at) >= cutoff]
    if len(recent) < 6:
        return []
    half = len(recent) // 2
    before_avg = sum(m.sentiment_score for m in recent[:half]) / max(half, 1)
    after_avg = sum(m.sentiment_score for m in recent[half:]) / max(len(recent) - half, 1)
    if after_avg - before_avg < -0.25:
        return [{"from": _bucket(before_avg), "to": _bucket(after_avg),
                 "window_days": window, "before_avg": before_avg,
                 "after_avg": after_avg, "sample_size": len(recent)}]
    return []


def _bucket(score: float) -> str:
    if score >= 0.3: return "cooperative"
    if score <= -0.3: return "hostile"
    return "neutral"


def _detect_overdue(messages: List[OFWMessage], state: Dict[str, Any],
                    cfg: Dict[str, Any]) -> List[Dict[str, Any]]:
    overdue_h = cfg.get("deadlines", {}).get("response_overdue_hours", 72)
    cutoff = datetime.utcnow() - timedelta(hours=overdue_h)
    out: List[Dict[str, Any]] = []
    for m in messages:
        recv = _parse_iso(m.received_at)
        age_h = (datetime.utcnow() - recv).total_seconds() / 3600.0
        # We can't tell from the inbox alone if Stephen replied — caller
        # may provide that. For now, treat any unread + age > cutoff as overdue.
        if m.unread and recv < cutoff:
            out.append({"msg_id": m.msg_id,
                        "sender": redact(m.sender, cfg),
                        "subject": redact(m.subject, cfg),
                        "age_hours": age_h})
    return out


def _calendar_diff(prev: Dict[str, Any], curr: Dict[str, Any]) -> Dict[str, List[Dict[str, Any]]]:
    added: List[Dict[str, Any]] = []
    removed: List[Dict[str, Any]] = []
    changed: List[Dict[str, Any]] = []
    for k, v in curr.items():
        if k not in prev:
            added.append(v)
        elif prev[k] != v:
            changed.append(v)
    for k, v in prev.items():
        if k not in curr:
            removed.append(v)
    return {"added": added, "removed": removed, "changed": changed}


# ── SkillOpt integration ───────────────────────────────────────────────

def _record_to_skillopt(result: ScanResult, cfg: Dict[str, Any]):
    try:
        from skillopt_engine import record_skill_run
    except ImportError:
        return
    name = cfg.get("skillopt", {}).get("skill_name", "ofw_monitor")
    completeness = 1.0
    if result.errors:
        completeness = max(0.0, 1.0 - 0.2 * len(result.errors))
    try:
        record_skill_run(
            skill_name=name,
            inputs={"mode": result.mode},
            outputs={
                "new_messages": result.new_messages,
                "calendar_changes": result.calendar_changes,
                "new_expenses": result.new_expenses,
                "overdue_responses": result.overdue_responses,
                "tone_shifts": len(result.tone_shifts),
            },
            metrics={
                "accuracy": 1.0 if not result.errors else 0.6,
                "completeness": completeness,
                "user_satisfaction": 0.0,  # set later from feedback
            },
            duration_ms=result.duration_ms,
            cost_usd=0.0,
            error=("; ".join(result.errors) if result.errors else None),
        )
    except Exception as e:
        log.warning("ofw_monitor skillopt record failed: %s", e)


# ── Utilities ──────────────────────────────────────────────────────────

def _log_notify(payload: Dict[str, Any]):
    log.info("[ofw notify] %s", payload.get("title", "(no title)"))


def _now_iso() -> str:
    return datetime.utcnow().replace(microsecond=0).isoformat() + "Z"


def _parse_iso(s: str) -> datetime:
    if not s:
        return datetime.utcfromtimestamp(0)
    try:
        return datetime.fromisoformat(s.rstrip("Z"))
    except Exception:
        return datetime.utcfromtimestamp(0)


# ════════════════════════════════════════════════════════════════════════
#  CLI
# ════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    import argparse
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s")
    parser = argparse.ArgumentParser(prog="ofw_monitor")
    sub = parser.add_subparsers(dest="cmd")

    p_scan = sub.add_parser("scan", help="Run one scan (LocalSession by default)")
    p_scan.add_argument("--mode", default="full",
                        choices=["full", "messages", "calendar", "expenses"])

    p_verify = sub.add_parser("verify", help="Verify HMAC archive chain")

    args = parser.parse_args()
    cfg = load_config()
    if args.cmd == "scan" or args.cmd is None:
        out = scan(config=cfg, mode=args.mode)
        print(json.dumps(out, indent=2))
    elif args.cmd == "verify":
        archive = HMACChainArchive(
            Path(cfg["archive"]["vault_path"]).expanduser(),
            _archive_key(),
        )
        ok, n, msg = archive.verify()
        print(("✓ " if ok else "✗ ") + f"{n} records  ·  {msg}")

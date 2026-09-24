"""User-granted cloud egress permissions for local files.

Grounding: "Just fair warning, I can't bring sensitive personal info from
that resume up to the cloud." The gate makes Friday least useful on exactly
the work the user cares most about — analyzing their own documents with a
frontier model — and the realistic
alternative to a grant is worse than a grant: pasting the same content into
the chat box by hand, which crosses the wire anyway with no registry, no
audit, no receipt. A grant inside the system with an audit trail beats a
manual bypass outside it.

THE MECHANISM: a second feeder of the span registry the news fix already
uses. No send-time exemption API exists here — nothing accepts a flag on a
call. `on_file_read()` is called by read_file at the moment a file is
actually read (NOT by search_files — a content-search snippet from a
granted file still gates normally; file_search.py's own `_search_content()`
discloses this as its "KNOWN GAP," failing toward gating rather than
leaking); if the resolved path carries a live grant, it
registers that read's exact paragraphs with `egress_gate.register_public_text
(text, origin="user-grant:<id>")`, exactly as news_engine registers a
fetched article. A prompt-injected model cannot register spans: the only way
content becomes sendable is that the real file at the granted path was
really read, here, just now.

WHO GRANTS. Nobody but the user, through an authenticated HTTP endpoint
driven by UI chrome (routes/control.py). There is no grant tool anywhere in
CLAUDE_TOOLS — no surface's model can call one. A spoken "yes" can never
create a grant; voice can only point at a pending chip.

GRANULARITY. File grants are content-pinned (SHA-256 at grant time); a later
read with a different hash is `stale` and gates normally. Folder/glob grants
cannot be content-pinned, so they REQUIRE an expiry, enforced here (not just
in a UI) at a hard 30-day maximum.

PRECEDENCE. A deny mark beats any grant at any specificity, no exceptions —
`check_grant()` checks denies before it ever looks at a grant.

DURABILITY. Append-only JSONL at ~/.friday/privacy/file_grants.jsonl,
deliberately separate from settings.json (so the BOM/factory-reset failure
mode that once nuked 83 settings keys cannot touch it). Each line carries an
HMAC over its event, keyed by the app's own secret_key. A line that fails to
parse or fails HMAC is dropped and counted. Dropped GRANT events fail safe
(fewer grants -> normal gating). Dropped DENY events are the dangerous
direction, so: any drop at all puts the whole ledger into SUSPENDERS MODE —
every grant treated as absent, every deny mark still enforced from whatever
folded cleanly, and one high-priority notification. A corrupted ledger can
only ever tighten.
"""
from __future__ import annotations

import fnmatch
import hashlib
import hmac
import json
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path

_MAX_EXPIRY_DAYS = 30
_APPEND_LOCK = threading.Lock()
_STATE_LOCK = threading.Lock()
_STATE_CACHE: dict = {"mtime": -2.0, "state": None}


def _ledger_path() -> Path:
    # Resolved lazily (not at import time) so tests' HOME redirection in
    # conftest.py is honored no matter when this module is first imported.
    from agent_friday.core import FRIDAY_DIR
    return Path(FRIDAY_DIR) / "privacy" / "file_grants.jsonl"


#: The ledger's OWN signing key, deliberately not the Flask session secret.
#:
#: Those two have opposite requirements and used to be the same value. A session
#: secret is allowed to rotate -- rotating it just logs everyone out. A LEDGER
#: signing key must never change, or the ledger's own history stops verifying.
#:
#: `core._load_or_create_secret()` prefers the FRIDAY_SECRET_KEY environment
#: variable over the persisted `~/.friday/secret_key`, so removing that variable
#: from a launcher silently swaps the session secret. A ledger signed with it
#: then stops verifying even though every line is intact and correctly signed
#: under the old key; Friday reports corruption and suspends every grant. That
#: is the right reaction to a moved key, so the key must not be able to move.
_SIGNING_KEY_CACHE: dict = {}


def _signing_key_path() -> Path:
    return _ledger_path().parent / "ledger_signing.key"


def _secret_bytes() -> bytes:
    """The HMAC key for this ledger: persisted once, protected, never from env.

    Stored through `credential_store`, so it lands under the vault key or DPAPI
    when either is available rather than sitting in plaintext next to the file it
    protects. Minted on first use -- callers that short-circuit on a nonexistent
    ledger never create one.
    """
    cached = _SIGNING_KEY_CACHE.get("key")
    if cached:
        return cached
    from agent_friday.services import credential_store as _cs
    path = _signing_key_path()
    key = None
    if path.exists():
        try:
            key = _cs.read_secret(path)
        except Exception as e:
            # A key we cannot read is NOT a reason to mint a new one: that would
            # invalidate every existing line and look exactly like this
            # incident. Fail loudly instead.
            raise RuntimeError(
                "the file-grants signing key exists but could not be read (%s). "
                "Grants stay suspended rather than be re-signed under a new key."
                % e)
    if not key:
        import secrets as _secrets
        key = _secrets.token_bytes(32)
        path.parent.mkdir(parents=True, exist_ok=True)
        _cs.write_secret(path, key)
        try:
            _cs.harden_permissions(path)
        except Exception:
            pass
    _SIGNING_KEY_CACHE["key"] = key
    return key


def _iso_now() -> str:
    """UTC ISO-8601, for the human-readable fields on quarantine records."""
    import datetime as _dt
    return _dt.datetime.now(_dt.timezone.utc).isoformat()


def _hmac_hex(event: dict, key: bytes) -> str:
    canon = json.dumps(event, sort_keys=True, separators=(",", ":"), default=str)
    return hmac.new(key, canon.encode("utf-8"), hashlib.sha256).hexdigest()


# ── Ledger read: verify, fold, and fail toward tightening ─────────────────────

@dataclass
class LedgerState:
    grants: dict = field(default_factory=dict)   # id -> event dict
    denies: dict = field(default_factory=dict)    # id -> event dict
    suspended: bool = False
    dropped: int = 0


def _read_verified_events(path: Path) -> tuple[list[dict], int]:
    """Parse and HMAC-verify every line. Returns (events, dropped_count).

    A nonexistent ledger (never created) is the honest "no grants yet" case:
    0 events, 0 dropped. A ledger that EXISTS but cannot even be decoded
    (the BOM/encoding corruption class) counts as 1 dropped line rather than
    silently returning empty — corruption must be visible, not indistinguishable
    from "nothing was ever granted".
    """
    if not path.exists():
        return [], 0
    key = _secret_bytes()
    try:
        raw = path.read_text(encoding="utf-8", errors="strict")
    except Exception:
        return [], 1
    events: list[dict] = []
    dropped = 0
    for raw_line in raw.split("\n"):
        line = raw_line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
            ev = rec["event"]
            sig = rec["hmac"]
            if not isinstance(ev, dict) or not isinstance(sig, str):
                raise ValueError("malformed record")
        except Exception:
            dropped += 1
            continue
        expected = _hmac_hex(ev, key)
        if not hmac.compare_digest(sig, expected):
            dropped += 1
            continue
        events.append(ev)
    return events, dropped


def _fold(events: list[dict]) -> tuple[dict, dict]:
    grants: dict = {}
    denies: dict = {}
    for ev in events:
        et = ev.get("event")
        eid = ev.get("id")
        if not eid:
            continue
        if et in ("grant_file", "grant_scope"):
            grants[eid] = ev
        elif et == "deny":
            denies[eid] = ev
        elif et == "revoke":
            tgt = ev.get("target_id")
            grants.pop(tgt, None)
            denies.pop(tgt, None)
    return grants, denies


def _notify_corruption(dropped: int) -> None:
    try:
        from agent_friday.services.voice_engine import _notif_engine
    except Exception:
        return
    if not _notif_engine:
        return
    try:
        _notif_engine.push(
            title="File-permission ledger corrupted — grants suspended",
            body=(f"{dropped} line(s) of the file-grants ledger failed to "
                  f"verify and were dropped. Every file grant is suspended "
                  f"until this is resolved; your never-send deny marks still "
                  f"apply. Nothing was silently un-denied."),
            priority="high", source="file_grants", kind="warning",
            dedupe_key="file_grants_ledger_corruption",
        )
    except Exception:
        pass


def _load_state(force: bool = False) -> LedgerState:
    path = _ledger_path()
    try:
        mtime = path.stat().st_mtime if path.exists() else -1.0
    except Exception:
        mtime = -1.0
    with _STATE_LOCK:
        cached = _STATE_CACHE.get("state")
        if not force and cached is not None and _STATE_CACHE.get("mtime") == mtime:
            return cached
        events, dropped = _read_verified_events(path)
        grants, denies = _fold(events)
        suspended = dropped > 0
        if suspended:
            grants = {}   # suspenders mode: ALL grants suspended
            _notify_corruption(dropped)
        state = LedgerState(grants=grants, denies=denies, suspended=suspended, dropped=dropped)
        _STATE_CACHE["mtime"] = mtime
        _STATE_CACHE["state"] = state
        return state


def _invalidate_cache() -> None:
    with _STATE_LOCK:
        _STATE_CACHE["mtime"] = -2.0
        _STATE_CACHE["state"] = None


def _append_event(event: dict) -> dict:
    key = _secret_bytes()
    sig = _hmac_hex(event, key)
    rec = {"event": event, "hmac": sig}
    line = json.dumps(rec, sort_keys=True, separators=(",", ":"), default=str)
    path = _ledger_path()
    with _APPEND_LOCK:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a", encoding="utf-8", newline="\n") as f:
            f.write(line + "\n")
    _invalidate_cache()
    return event


# ── Path matching ──────────────────────────────────────────────────────────────

def _same_path(a: str, b: Path) -> bool:
    try:
        return Path(a).resolve() == b.resolve()
    except Exception:
        return str(a) == str(b)


def _covers(rule: dict, path: Path) -> bool:
    t = rule.get("type")
    rp = rule.get("path", "")
    if t == "file":
        return _same_path(rp, path)
    if t == "folder":
        try:
            path.resolve().relative_to(Path(rp).resolve())
            return True
        except (ValueError, OSError):
            return False
    if t == "glob":
        return fnmatch.fnmatch(str(path).replace("\\", "/"), rp.replace("\\", "/"))
    return False


def _split_paragraphs(text: str) -> list[str]:
    """Mirror egress_gate._gate_text_span's own split exactly, so a span
    registered here is the same string the gate will look up."""
    sep = "\n\n" if "\n\n" in text else ("\n" if "\n" in text else None)
    paras = text.split(sep) if sep else [text]
    return [p.strip() for p in paras if p.strip()]


# ── Grant / deny creation ──────────────────────────────────────────────────────

def scan_path(path: Path) -> dict:
    """Classifier findings for a file, for the grant dialog. Generated from
    the system's OWN scan — never parametrized by model text, so a
    prompt-injected file cannot shape what the consent screen shows."""
    from agent_friday.services.file_extraction import extract_text
    from agent_friday.services.sensitivity_classifier import classify, Tier
    from agent_friday.services import judgment_gate as _jg

    result = extract_text(path)
    if result.text is None:
        return {
            "path": str(path), "extractable": False, "error": result.error,
            "tier_counts": {}, "never_send_matches": [], "paragraph_count": 0,
            "summary": result.error,
        }
    paras = _split_paragraphs(result.text)
    tier_counts = {"TIER_1": 0, "TIER_2": 0, "TIER_3": 0}
    never_matches: set = set()
    for p in paras:
        t = classify(p, default=Tier.PRIVATE, egress=True)
        tier_counts[Tier.NAMES.get(t, "TIER_1")] += 1
        never_matches.update(_jg.never_send_hits(p))
    summary = (f"{len(paras)} paragraph(s): {tier_counts['TIER_1']} public, "
               f"{tier_counts['TIER_2']} private, {tier_counts['TIER_3']} sensitive")
    if never_matches:
        summary += f"; {len(never_matches)} item(s) on your never-send list"
    return {
        "path": str(path), "extractable": True, "error": None,
        "tier_counts": tier_counts,
        "never_send_matches": sorted(never_matches),
        "paragraph_count": len(paras),
        "summary": summary,
    }


def create_file_grant(path: str, never_send_override: bool = False,
                       ack_never_send_matches: list | None = None) -> dict:
    """Create a content-pinned grant for one file.

    `never_send_override` may be True ONLY when the caller has already shown
    the user the specific never-send matches and recorded their acknowledgment
    in `ack_never_send_matches` — the endpoint's job, not this function's; this
    function just persists what was acknowledged so the ledger carries the
    consent record, and refuses to record an override with nothing behind it.
    """
    p = Path(path).expanduser().resolve()
    if not p.exists() or not p.is_file():
        raise FileNotFoundError(f"{p} does not exist or is not a file")
    data = p.read_bytes()
    sha = hashlib.sha256(data).hexdigest()
    findings = scan_path(p)
    matches = findings.get("never_send_matches") or []
    override = bool(never_send_override) and bool(matches)
    event = {
        "event": "grant_file",
        "id": str(uuid.uuid4()),
        "type": "file",
        "path": str(p),
        "sha256": sha,
        "created_ts": time.time(),
        "never_send_override": override,
        "ack_never_send_matches": list(ack_never_send_matches or []) if override else [],
        "findings_summary": findings.get("summary"),
    }
    return _append_event(event)


def create_scope_grant(path_or_pattern: str, kind: str, expiry_days: float) -> dict:
    """Create a folder or glob grant. Expiry is REQUIRED and capped at 30 days
    — enforced here at the API, not merely suggested by the UI, because a
    folder/glob grant cannot be content-pinned and a permanent one is exactly
    the quiet gate-bypass this feature must not become."""
    if kind not in ("folder", "glob"):
        raise ValueError("kind must be 'folder' or 'glob'")
    if not expiry_days or expiry_days <= 0 or expiry_days > _MAX_EXPIRY_DAYS:
        raise ValueError(f"expiry_days is required and must be in (0, {_MAX_EXPIRY_DAYS}]")
    if kind == "folder":
        p = str(Path(path_or_pattern).expanduser().resolve())
    else:
        p = str(Path(path_or_pattern).expanduser())
    event = {
        "event": "grant_scope",
        "id": str(uuid.uuid4()),
        "type": kind,
        "path": p,
        "created_ts": time.time(),
        "expires_ts": time.time() + expiry_days * 86400.0,
        "never_send_override": False,   # override is file-grant only
    }
    return _append_event(event)


def create_deny_mark(path_or_pattern: str, kind: str) -> dict:
    if kind not in ("file", "folder", "glob"):
        raise ValueError("kind must be 'file', 'folder', or 'glob'")
    if kind == "glob":
        p = str(Path(path_or_pattern).expanduser())
    else:
        p = str(Path(path_or_pattern).expanduser().resolve())
    event = {"event": "deny", "id": str(uuid.uuid4()), "type": kind,
              "path": p, "created_ts": time.time()}
    return _append_event(event)


def revoke(target_id: str) -> dict:
    """Revoke a grant OR a deny mark by id — one action, either registry."""
    event = {"event": "revoke", "id": str(uuid.uuid4()),
              "target_id": target_id, "created_ts": time.time()}
    return _append_event(event)


# ── Read-time check + feeder ────────────────────────────────────────────────────

@dataclass
class GrantCheck:
    state: str                    # 'active' | 'stale' | 'denied' | 'none'
    grant_id: str | None = None
    deny_id: str | None = None
    never_send_override: bool = False


def check_grant(path: Path, sha256_hex: str | None = None) -> GrantCheck:
    """Deny beats any grant at any specificity, no exceptions — checked first,
    unconditionally, before any grant (file or scope) is even examined."""
    state = _load_state()
    for d in state.denies.values():
        if _covers(d, path):
            return GrantCheck(state="denied", deny_id=d["id"])
    for g in state.grants.values():
        if g.get("event") == "grant_file" and _same_path(g.get("path", ""), path):
            if sha256_hex is not None and g.get("sha256") != sha256_hex:
                return GrantCheck(state="stale", grant_id=g["id"])
            return GrantCheck(state="active", grant_id=g["id"],
                               never_send_override=bool(g.get("never_send_override")))
    now = time.time()
    for g in state.grants.values():
        if g.get("event") == "grant_scope" and _covers(g, path):
            expires = g.get("expires_ts")
            if expires and now > expires:
                continue
            return GrantCheck(state="active", grant_id=g["id"], never_send_override=False)
    return GrantCheck(state="none")


# A granted paragraph is page-sized prose (extract_text joins pages on
# "\n\n"), not a headline — register_public_text's 2000-char default is a
# NEWS constraint that would silently drop 3 of 4 pages of a real CV: the
# grant looks live (ledger entry, check_grant='active') while most of the
# document still gates normally.
_GRANT_SPAN_MAX_LEN = 50_000


def _register_grant_spans(text: str, grant_id: str, never_send_override: bool) -> None:
    from agent_friday.services import egress_gate as _eg
    origin = f"user-grant:{grant_id}"
    for p in _split_paragraphs(text):
        _eg.register_public_text(p, origin=origin, max_len=_GRANT_SPAN_MAX_LEN)
        if never_send_override:
            _eg.register_override_text(p, origin=origin, max_len=_GRANT_SPAN_MAX_LEN)


def _register_deny_spans(text: str) -> None:
    from agent_friday.services import judgment_gate as _jg
    for p in _split_paragraphs(text):
        _jg.register_deny_span(p)


def on_file_read(path: Path, text: str) -> GrantCheck:
    """The read-time feeder (the central move). Call this — and only this
    — after a file's content is actually extracted, before returning it to a
    tool caller. There is no other path into the grant span registries: a
    caller cannot hand the gate a flag, and a model cannot register spans by
    describing a file it never read.
    """
    try:
        sha = hashlib.sha256(path.read_bytes()).hexdigest()
    except Exception:
        sha = None
    result = check_grant(path, sha256_hex=sha)
    if result.state == "denied":
        _register_deny_spans(text)
    elif result.state == "active":
        _register_grant_spans(text, result.grant_id, result.never_send_override)
    return result


# ── Listing / audit ─────────────────────────────────────────────────────────────

def list_grants() -> list[dict]:
    return list(_load_state().grants.values())


def list_denies() -> list[dict]:
    return list(_load_state().denies.values())


def status() -> dict:
    s = _load_state()
    return {"suspended": s.suspended, "dropped_lines": s.dropped,
            "grant_count": len(s.grants), "deny_count": len(s.denies)}


def list_pending_reapproval() -> list[dict]:
    """File grants whose content no longer matches the pinned hash — the
    chip re-raise case. Derived live from the actual files, not from a
    hand-maintained 'is this stale' flag."""
    state = _load_state()
    pending: list[dict] = []
    for g in state.grants.values():
        if g.get("event") != "grant_file":
            continue
        p = Path(g.get("path", ""))
        if not p.exists():
            pending.append({**g, "reason": "file_missing"})
            continue
        try:
            cur = hashlib.sha256(p.read_bytes()).hexdigest()
        except Exception:
            continue
        if cur != g.get("sha256"):
            pending.append({**g, "reason": "content_changed",
                             "current_sha256": cur, "fresh_findings": scan_path(p)})
    return pending

# ── Re-attestation after a key change ───────────────────────────────────────
#
# A line that fails to verify is never re-signed automatically. Doing so would
# turn a tampered line into a valid one, which defeats the reason for signing.
# Instead its CONTENT is listed for review and re-signed only when the user
# confirms it, as a NEW event that records the hash of what it replaced.

def list_unverified() -> list[dict]:
    """Every ledger line that does not verify, with its content, for review.

    `verified: False` on every row, so no caller can mistake one of these for a
    live grant. The `line_sha256` is the handle `reattest()` takes -- content
    addressed, so confirming one line can never re-sign a different one.
    """
    path = _ledger_path()
    if not path.exists():
        return []
    try:
        raw = path.read_text(encoding="utf-8", errors="strict")
    except Exception:
        return [{"event": None, "verified": False, "line_sha256": "",
                 "why": "the ledger could not be decoded at all"}]
    key = _secret_bytes()
    out: list[dict] = []
    for raw_line in raw.split("\n"):
        line = raw_line.strip()
        if not line:
            continue
        sha = hashlib.sha256(line.encode("utf-8")).hexdigest()
        try:
            rec = json.loads(line)
            ev = rec["event"]
            sig = rec["hmac"]
            if not isinstance(ev, dict) or not isinstance(sig, str):
                raise ValueError("malformed record")
        except Exception as e:
            out.append({"event": None, "verified": False, "line_sha256": sha,
                        "why": "the line could not be parsed (%s)" % e})
            continue
        if hmac.compare_digest(sig, _hmac_hex(ev, key)):
            continue
        out.append({
            "event": ev, "verified": False, "line_sha256": sha,
            "why": ("the signature does not match this ledger's signing key -- "
                    "either the key changed or the line was altered"),
        })
    return out


def _quarantine_path() -> Path:
    return _ledger_path().parent / "file_grants.quarantine.jsonl"


def _quarantine_line(line_sha256: str, why: str, who: str) -> bool:
    """Move one unverifiable line OUT of the active ledger, verbatim.

    Not deleted -- appended to `file_grants.quarantine.jsonl` exactly as it was,
    with a header recording who moved it and why. That keeps the evidence (a
    tampered line stays readable for inspection) while letting the active ledger
    verify cleanly again.

    This is the step that actually lifts suspenders mode. Without it, a single
    unverifiable line suspends every grant forever, including one the user has
    just re-attested -- correct but unrecoverable, which is its own kind of
    broken.
    """
    path = _ledger_path()
    if not path.exists():
        return False
    try:
        raw = path.read_text(encoding="utf-8", errors="strict")
    except Exception:
        return False
    keep, moved = [], None
    for raw_line in raw.split("\n"):
        line = raw_line.strip()
        if not line:
            continue
        if hashlib.sha256(line.encode("utf-8")).hexdigest() == line_sha256:
            moved = line
            continue
        keep.append(line)
    if moved is None:
        return False
    with _APPEND_LOCK:
        q = _quarantine_path()
        q.parent.mkdir(parents=True, exist_ok=True)
        with open(q, "a", encoding="utf-8", newline="\n") as f:
            f.write(json.dumps({
                "quarantined_at": _iso_now(),
                "quarantined_by": who,
                "line_sha256": line_sha256,
                "why": why,
                "line": moved,
            }, sort_keys=True, separators=(",", ":")) + "\n")
        tmp = path.with_suffix(".jsonl.tmp")
        tmp.write_text(("\n".join(keep) + "\n") if keep else "", encoding="utf-8",
                       newline="\n")
        tmp.replace(path)
    _invalidate_cache()
    return True


def reattest(line_sha256: str, *, confirmed_by: str) -> dict:
    """Re-sign one reviewed line, on an explicit confirmation.

    Two things happen under the one confirmation, in this order:
      1. a NEW event is written under the current key, carrying
         `reattested_from` (the hash of the line it replaces) and
         `reattested_by`;
      2. the original, unverifiable line is QUARANTINED -- moved verbatim to
         file_grants.quarantine.jsonl, never deleted and never re-signed.

    The original is never rewritten in place. Re-signing whatever failed would
    turn a tampered line into a valid one; quarantining it keeps the evidence
    and lets the active ledger verify again so grants can come back.
    """
    who = (confirmed_by or "").strip()
    if not who:
        return {"ok": False, "error": "a re-attestation needs an explicit "
                                      "confirmation from the user"}
    target = None
    for row in list_unverified():
        if row.get("line_sha256") == line_sha256 and isinstance(row.get("event"), dict):
            target = row
            break
    if target is None:
        return {"ok": False, "error": "no unverified line with that hash"}

    ev = dict(target["event"])
    et = ev.get("event")
    if et not in ("grant_file", "grant_scope", "deny"):
        return {"ok": False,
                "error": "only a grant or deny line can be re-attested (got %r)" % et}
    ev["id"] = "reattest-%s" % uuid.uuid4().hex[:12]
    ev["reattested_from"] = line_sha256
    ev["reattested_by"] = who
    ev["reattested_at"] = _iso_now()
    _append_event(ev)
    _quarantine_line(line_sha256, "re-attested by the user under the current "
                                  "signing key", who)
    _invalidate_cache()
    return {"ok": True, "id": ev["id"], "event": et,
            "quarantined": line_sha256}


def dismiss_unverified(line_sha256: str, *, confirmed_by: str) -> dict:
    """Quarantine an unverifiable line WITHOUT re-signing it.

    For a line the user does not recognise or does not want back. The grant it
    described simply does not return; the line is preserved for inspection.
    """
    who = (confirmed_by or "").strip()
    if not who:
        return {"ok": False, "error": "a dismissal needs an explicit "
                                      "confirmation from the user"}
    known = {r.get("line_sha256") for r in list_unverified()}
    if line_sha256 not in known:
        return {"ok": False, "error": "no unverified line with that hash"}
    ok = _quarantine_line(line_sha256, "dismissed by the user; not re-attested", who)
    return {"ok": bool(ok), "quarantined": line_sha256 if ok else None}

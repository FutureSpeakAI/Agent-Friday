"""
Agent Friday — Community Content Policy Packs (Layer 3)
FutureSpeak.AI · Asimov's Mind

Stackable, shareable content filtering rules published as signed packs.
The H1-H4 harm floor always applies first, regardless of pack configuration.

Rule stacking logic:
  - BLOCK from any subscribed pack  -> content is blocked (first match wins)
  - TAG from any pack               -> tags are additive across all packs
  - WARN from any pack              -> warnings are additive
  - ALLOW                           -> explicit allow (skips further rule checks
                                       for that category from this pack only)

Built-in packs (always present, cannot be deleted):
  "asimov-standard"  — H1-H4 hard floor; always subscribed, cannot be removed
  "family-safe"      — Blocks NSFW, violence, strong language; free-only marketplace
  "creator-commons"  — Tags NSFW, warns on unverified claims, permissive otherwise
  "journalism"       — Strict on unverified/manipulated content, permissive on speech

Public API
----------
get_pack(pack_id)                                     -> pack dict | None
get_available_packs()                                 -> list of pack dicts
get_subscribed_packs()                                -> list of subscribed packs
subscribe(pack_id)                                    -> bool
unsubscribe(pack_id)                                  -> bool (False for asimov-standard)
create_pack(name, description, rules, ...)            -> pack dict | None
evaluate_content(content_metadata, packs=None)        -> verdict dict

Storage: ~/.friday/content_policies.db (SQLite, WAL)
"""
from __future__ import annotations

import json
import sqlite3
import threading
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import agent_friday.core as core
from agent_friday.core import FRIDAY_DIR

DB_PATH = FRIDAY_DIR / "content_policies.db"
_LOCK = threading.RLock()

ALWAYS_ON_PACK = "asimov-standard"

# ─────────────────────────────────────────────────────────────────────────────
#  BUILT-IN PACK DEFINITIONS
# ─────────────────────────────────────────────────────────────────────────────

BUILTIN_PACKS: List[Dict[str, Any]] = [
    {
        "pack_id": "asimov-standard",
        "name": "Asimov Standard",
        "description": (
            "The H1-H4 cLaws hard floor: blocks CSAM, non-consensual deepfakes, "
            "doxxing, and violence incitement. Always active — the constitutional minimum. "
            "Cannot be removed."
        ),
        "creator_pubkey": "friday-builtin",
        "version": "1.0.0",
        "builtin": True,
        "always_on": True,
        "rules": [
            {
                "category": "CSAM",
                "action": "BLOCK",
                "severity_threshold": 0.0,
                "description": "Block child sexual abuse material (H1)",
            },
            {
                "category": "real_person_deepfake",
                "action": "BLOCK",
                "severity_threshold": 0.0,
                "description": "Block non-consensual deepfakes of real people (H2)",
            },
            {
                "category": "doxxing",
                "action": "BLOCK",
                "severity_threshold": 0.0,
                "description": "Block doxxing and PII exposure (H3)",
            },
            {
                "category": "violence_incitement",
                "action": "BLOCK",
                "severity_threshold": 0.0,
                "description": "Block violence incitement and WMD instructions (H4)",
            },
        ],
        "subscribers": 0,
        "signature": "builtin",
    },
    {
        "pack_id": "family-safe",
        "name": "Family Safe",
        "description": (
            "Suitable for households with children. Blocks NSFW and adult content, "
            "strong language, and excessive violence. Restricts marketplace to free items."
        ),
        "creator_pubkey": "friday-builtin",
        "version": "1.0.0",
        "builtin": True,
        "always_on": False,
        "rules": [
            {
                "category": "nsfw",
                "action": "BLOCK",
                "severity_threshold": 0.0,
                "description": "Block NSFW content",
            },
            {
                "category": "adult_content",
                "action": "BLOCK",
                "severity_threshold": 0.0,
                "description": "Block adult content",
            },
            {
                "category": "explicit_language",
                "action": "BLOCK",
                "severity_threshold": 0.5,
                "description": "Block strong language above threshold",
            },
            {
                "category": "violence",
                "action": "WARN",
                "severity_threshold": 0.3,
                "description": "Warn on depictions of violence",
            },
            {
                "category": "marketplace_paid",
                "action": "BLOCK",
                "severity_threshold": 0.0,
                "description": "Restrict marketplace to free items only",
            },
        ],
        "subscribers": 0,
        "signature": "builtin",
    },
    {
        "pack_id": "creator-commons",
        "name": "Creator Commons",
        "description": (
            "Balanced pack for creators. Allows most content, tags NSFW, warns on "
            "unverified claims. Transparency over restriction."
        ),
        "creator_pubkey": "friday-builtin",
        "version": "1.0.0",
        "builtin": True,
        "always_on": False,
        "rules": [
            {
                "category": "nsfw",
                "action": "TAG",
                "severity_threshold": 0.0,
                "description": "Tag NSFW content",
            },
            {
                "category": "adult_content",
                "action": "TAG",
                "severity_threshold": 0.0,
                "description": "Tag adult content",
            },
            {
                "category": "unverified_claim",
                "action": "WARN",
                "severity_threshold": 0.5,
                "description": "Warn on likely unverified claims",
            },
            {
                "category": "manipulated_media",
                "action": "TAG",
                "severity_threshold": 0.3,
                "description": "Tag manipulated or synthetic media",
            },
        ],
        "subscribers": 0,
        "signature": "builtin",
    },
    {
        "pack_id": "journalism",
        "name": "Journalism Standards",
        "description": (
            "Designed for news and journalism. Strict on unverified claims and "
            "manipulated media. Permissive on political speech and satire."
        ),
        "creator_pubkey": "friday-builtin",
        "version": "1.0.0",
        "builtin": True,
        "always_on": False,
        "rules": [
            {
                "category": "manipulated_media",
                "action": "BLOCK",
                "severity_threshold": 0.6,
                "description": "Block likely-manipulated media",
            },
            {
                "category": "unverified_claim",
                "action": "BLOCK",
                "severity_threshold": 0.8,
                "description": "Block high-confidence unverified claims",
            },
            {
                "category": "unverified_claim",
                "action": "WARN",
                "severity_threshold": 0.4,
                "description": "Warn on possible unverified claims",
            },
            {
                "category": "satire",
                "action": "ALLOW",
                "severity_threshold": 0.0,
                "description": "Explicitly allow satire",
            },
            {
                "category": "political_speech",
                "action": "ALLOW",
                "severity_threshold": 0.0,
                "description": "Explicitly allow political speech",
            },
        ],
        "subscribers": 0,
        "signature": "builtin",
    },
]

# ─────────────────────────────────────────────────────────────────────────────
#  SCHEMA
# ─────────────────────────────────────────────────────────────────────────────

_SCHEMA = """
CREATE TABLE IF NOT EXISTS packs (
    pack_id         TEXT PRIMARY KEY,
    name            TEXT NOT NULL,
    description     TEXT DEFAULT '',
    creator_pubkey  TEXT DEFAULT '',
    version         TEXT DEFAULT '1.0.0',
    builtin         INTEGER DEFAULT 0,
    always_on       INTEGER DEFAULT 0,
    rules_json      TEXT DEFAULT '[]',
    signature       TEXT DEFAULT '',
    subscribers     INTEGER DEFAULT 0,
    created_at      TEXT
);

CREATE TABLE IF NOT EXISTS subscriptions (
    pack_id         TEXT PRIMARY KEY,
    subscribed_at   TEXT
);

CREATE INDEX IF NOT EXISTS idx_packs_creator ON packs(creator_pubkey);
"""

# ─────────────────────────────────────────────────────────────────────────────
#  CONNECTION
# ─────────────────────────────────────────────────────────────────────────────

def _conn() -> sqlite3.Connection:
    FRIDAY_DIR.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(str(DB_PATH), check_same_thread=False, timeout=10)
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA foreign_keys=ON")
    con.row_factory = sqlite3.Row
    return con


def _ensure_schema() -> None:
    with _LOCK:
        with _conn() as con:
            con.executescript(_SCHEMA)
        _seed_builtins()


def _seed_builtins() -> None:
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    with _LOCK:
        with _conn() as con:
            for pack in BUILTIN_PACKS:
                con.execute(
                    """INSERT INTO packs
                       (pack_id, name, description, creator_pubkey, version,
                        builtin, always_on, rules_json, signature, subscribers, created_at)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?)
                       ON CONFLICT(pack_id) DO UPDATE SET
                         name        = excluded.name,
                         description = excluded.description,
                         version     = excluded.version,
                         builtin     = excluded.builtin,
                         always_on   = excluded.always_on,
                         rules_json  = excluded.rules_json
                    """,
                    (
                        pack["pack_id"],
                        pack["name"],
                        pack["description"],
                        pack["creator_pubkey"],
                        pack["version"],
                        1 if pack.get("builtin") else 0,
                        1 if pack.get("always_on") else 0,
                        json.dumps(pack["rules"]),
                        pack.get("signature", ""),
                        pack.get("subscribers", 0),
                        now,
                    ),
                )
            # always-on pack is always subscribed
            con.execute(
                "INSERT OR IGNORE INTO subscriptions (pack_id, subscribed_at) VALUES (?,?)",
                (ALWAYS_ON_PACK, now),
            )


if not getattr(core, "_TESTING", False):
    _ensure_schema()


# ─────────────────────────────────────────────────────────────────────────────
#  HELPERS
# ─────────────────────────────────────────────────────────────────────────────

def _get_engine():
    try:
        from agent_friday.governance.proof_of_integrity import get_integrity_engine
        return get_integrity_engine()
    except Exception:
        return None


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _sign_pack(pack: Dict[str, Any]) -> str:
    try:
        engine = _get_engine()
        if not engine:
            return ""
        body = {k: v for k, v in pack.items() if k not in ("signature", "subscribers")}
        payload = json.dumps(body, sort_keys=True).encode()
        return engine.sign_payload(payload) or ""
    except Exception:
        return ""


def _row_to_pack(row: Any) -> Dict[str, Any]:
    d = dict(row) if not isinstance(row, dict) else row.copy()
    try:
        d["rules"] = json.loads(d.get("rules_json") or "[]")
    except Exception:
        d["rules"] = []
    d.pop("rules_json", None)
    d["builtin"] = bool(d.get("builtin", 0))
    d["always_on"] = bool(d.get("always_on", 0))
    return d


# ─────────────────────────────────────────────────────────────────────────────
#  PACK QUERIES
# ─────────────────────────────────────────────────────────────────────────────

def get_pack(pack_id: str) -> Optional[Dict[str, Any]]:
    try:
        with _conn() as con:
            row = con.execute(
                "SELECT * FROM packs WHERE pack_id=?", (pack_id,)
            ).fetchone()
            return _row_to_pack(row) if row else None
    except Exception:
        return None


def get_available_packs() -> List[Dict[str, Any]]:
    """All known packs, built-ins first then by subscriber count."""
    try:
        with _conn() as con:
            rows = con.execute(
                "SELECT * FROM packs ORDER BY always_on DESC, builtin DESC, subscribers DESC"
            ).fetchall()
        return [_row_to_pack(r) for r in rows]
    except Exception:
        return [dict(p) for p in BUILTIN_PACKS]


def get_subscribed_packs() -> List[Dict[str, Any]]:
    """All subscribed packs; always includes asimov-standard."""
    try:
        with _conn() as con:
            rows = con.execute(
                """SELECT p.* FROM packs p
                   JOIN subscriptions s ON p.pack_id = s.pack_id
                   ORDER BY p.always_on DESC, p.builtin DESC"""
            ).fetchall()
        return [_row_to_pack(r) for r in rows]
    except Exception:
        return [p for p in BUILTIN_PACKS if p.get("always_on")]


# ─────────────────────────────────────────────────────────────────────────────
#  SUBSCRIPTION MANAGEMENT
# ─────────────────────────────────────────────────────────────────────────────

def subscribe(pack_id: str) -> bool:
    """Subscribe to a pack. Returns True on success."""
    try:
        pack = get_pack(pack_id)
        if not pack:
            return False
        with _LOCK:
            with _conn() as con:
                con.execute(
                    "INSERT OR IGNORE INTO subscriptions (pack_id, subscribed_at) VALUES (?,?)",
                    (pack_id, _now()),
                )
                con.execute(
                    "UPDATE packs SET subscribers = subscribers + 1 WHERE pack_id=?",
                    (pack_id,),
                )
        return True
    except Exception as e:
        print(f"  [content_policies] subscribe failed: {e}")
        return False


def unsubscribe(pack_id: str) -> bool:
    """
    Unsubscribe from a pack.

    Returns False (silently) for asimov-standard or any always_on pack —
    those cannot be removed.
    """
    if pack_id == ALWAYS_ON_PACK:
        return False
    pack = get_pack(pack_id)
    if pack and pack.get("always_on"):
        return False
    try:
        with _LOCK:
            with _conn() as con:
                con.execute("DELETE FROM subscriptions WHERE pack_id=?", (pack_id,))
                con.execute(
                    "UPDATE packs SET subscribers = MAX(0, subscribers - 1) WHERE pack_id=?",
                    (pack_id,),
                )
        return True
    except Exception as e:
        print(f"  [content_policies] unsubscribe failed: {e}")
        return False


# ─────────────────────────────────────────────────────────────────────────────
#  PACK CREATION
# ─────────────────────────────────────────────────────────────────────────────

_VALID_ACTIONS = frozenset({"BLOCK", "TAG", "WARN", "ALLOW"})


def create_pack(
    name: str,
    description: str,
    rules: List[Dict[str, Any]],
    creator_pubkey: Optional[str] = None,
    version: str = "1.0.0",
) -> Optional[Dict[str, Any]]:
    """
    Create a new community content policy pack.

    Each rule: {category: str, action: BLOCK|TAG|WARN|ALLOW,
                severity_threshold: float 0-1, description: str}

    Returns the pack dict on success, None on validation failure.
    """
    if not name or not rules or not isinstance(rules, list):
        return None

    validated: List[Dict[str, Any]] = []
    for r in rules:
        action = str(r.get("action") or "").upper()
        if action not in _VALID_ACTIONS:
            continue
        validated.append({
            "category": str(r.get("category") or ""),
            "action": action,
            "severity_threshold": max(0.0, min(1.0, float(r.get("severity_threshold") or 0.0))),
            "description": str(r.get("description") or ""),
        })

    if not validated:
        return None

    creator = creator_pubkey or _get_our_pubkey()
    pack_id = str(uuid.uuid4())
    now = _now()

    pack: Dict[str, Any] = {
        "pack_id": pack_id,
        "name": name,
        "description": description,
        "creator_pubkey": creator,
        "version": version,
        "builtin": False,
        "always_on": False,
        "rules": validated,
        "signature": "",
        "subscribers": 0,
        "created_at": now,
    }
    pack["signature"] = _sign_pack(pack)

    try:
        with _LOCK:
            with _conn() as con:
                con.execute(
                    """INSERT INTO packs
                       (pack_id, name, description, creator_pubkey, version,
                        builtin, always_on, rules_json, signature, subscribers, created_at)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                    (
                        pack_id, name, description, creator, version,
                        0, 0,
                        json.dumps(validated),
                        pack["signature"],
                        0, now,
                    ),
                )
        return pack
    except Exception as e:
        print(f"  [content_policies] create_pack failed: {e}")
        return None


def _get_our_pubkey() -> str:
    try:
        from agent_friday.governance.proof_of_integrity import get_integrity_engine
        eng = get_integrity_engine()
        return eng.get_public_key_hex() or "local" if eng else "local"
    except Exception:
        return "local"


# ─────────────────────────────────────────────────────────────────────────────
#  CONTENT EVALUATION ENGINE
# ─────────────────────────────────────────────────────────────────────────────

def evaluate_content(
    content_metadata: Dict[str, Any],
    subscribed_packs: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """
    Evaluate content against all subscribed policy packs.

    *content_metadata* recognized fields:
      - categories: list[str]  — content category labels
      - severity: float 0-1
      - tags: list[str]        — existing tags (additive)
      - title, description: str
      - price_mpsi: int        — > 0 adds "marketplace_paid" category
      - nsfw: bool             — adds "nsfw" category
      - adult_content: bool    — adds "adult_content" category

    The H1-H4 hard floor is always checked via moderation.scan() first.

    Returns:
      {
        blocked: bool,
        verdict: "clean" | "tagged" | "warned" | "blocked",
        applied_packs: [str],
        tags: [str],
        warnings: [str],
        blocking_rule: {pack_id, rule} | None,
        reason: str | None,
      }
    """
    if subscribed_packs is None:
        subscribed_packs = get_subscribed_packs()

    tags: List[str] = list(content_metadata.get("tags") or [])
    warnings: List[str] = []
    applied_packs: List[str] = []

    # ── H1-H4 hard floor ─────────────────────────────────────────────────────
    scan_text = " ".join(filter(None, [
        str(content_metadata.get("title") or ""),
        str(content_metadata.get("description") or ""),
    ])).strip()

    if scan_text:
        try:
            from services import moderation
            harm = moderation.scan(content_text=scan_text)
            if harm.get("blocked"):
                return {
                    "blocked": True,
                    "verdict": "blocked",
                    "applied_packs": [ALWAYS_ON_PACK],
                    "tags": harm.get("tags") or [],
                    "warnings": [],
                    "blocking_rule": {
                        "pack_id": ALWAYS_ON_PACK,
                        "rule": {
                            "category": harm.get("harm_level") or "H1-H4",
                            "action": "BLOCK",
                        },
                    },
                    "reason": harm.get("reason"),
                }
        except Exception:
            pass

    # ── Build content category set ────────────────────────────────────────────
    content_cats: set = set(
        str(c).lower() for c in (content_metadata.get("categories") or [])
    )
    content_severity = float(content_metadata.get("severity") or 0.0)

    if content_metadata.get("nsfw") or "nsfw" in tags:
        content_cats.add("nsfw")
    if content_metadata.get("adult_content"):
        content_cats.add("adult_content")
    if int(content_metadata.get("price_mpsi") or 0) > 0:
        content_cats.add("marketplace_paid")

    # ── Evaluate each subscribed pack ─────────────────────────────────────────
    for pack in subscribed_packs:
        pack_id = pack.get("pack_id") or ""
        if pack_id == ALWAYS_ON_PACK:
            continue  # already handled above

        for rule in (pack.get("rules") or []):
            cat = str(rule.get("category") or "").lower()
            action = str(rule.get("action") or "").upper()
            threshold = float(rule.get("severity_threshold") or 0.0)

            if cat not in content_cats:
                continue
            if action == "ALLOW":
                continue  # explicit permit — skip rest of loop for this category

            severity_ok = content_severity >= threshold

            if action == "BLOCK" and severity_ok:
                return {
                    "blocked": True,
                    "verdict": "blocked",
                    "applied_packs": applied_packs + [pack_id],
                    "tags": tags,
                    "warnings": warnings,
                    "blocking_rule": {"pack_id": pack_id, "rule": rule},
                    "reason": rule.get("description"),
                }

            if action == "TAG":
                tag_val = cat
                if tag_val not in tags:
                    tags.append(tag_val)
                if pack_id not in applied_packs:
                    applied_packs.append(pack_id)

            if action == "WARN" and severity_ok:
                warn_msg = rule.get("description") or f"{cat} warning"
                if warn_msg not in warnings:
                    warnings.append(warn_msg)
                if pack_id not in applied_packs:
                    applied_packs.append(pack_id)

    verdict = "clean"
    if warnings:
        verdict = "warned"
    if tags:
        verdict = "tagged"

    return {
        "blocked": False,
        "verdict": verdict,
        "applied_packs": applied_packs,
        "tags": tags,
        "warnings": warnings,
        "blocking_rule": None,
        "reason": None,
    }

"""
Agent Friday — Content Moderation (Layer 3)
FutureSpeak.AI · Asimov's Mind

Harm-based floor (always enforced, no override):
  H1: CSAM — instant block, no appeal
  H2: Real-person deepfakes without consent — block
  H3: Doxxing / PII exposure — block
  H4: Violence incitement / WMD instructions — block

Adult content (consensual, no real people) → ALLOWED, tagged NSFW
Family mode (minor accounts) → blocks NSFW, restricts marketplace to free items

All moderation runs locally on the creator's agent — no central authority.
Results stored with content credentials.

Public API
----------
scan(content_text=None, content_path=None, content_type="text", metadata=None)
    → {ok, blocked, verdict, harm_level, tags:[], reason, scan_id}
get_policy()  → current moderation policy dict
update_policy(policy_dict) → updated policy
is_nsfw_allowed()  → bool (True unless family mode)
"""
from __future__ import annotations

import json
import re
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

from core import FRIDAY_DIR

# ─────────────────────────────────────────────────────────────────────────────
#  CONSTANTS
# ─────────────────────────────────────────────────────────────────────────────

HARM_LEVELS: Dict[str, str] = {
    "H1": "CSAM",
    "H2": "real_person_deepfake",
    "H3": "doxxing",
    "H4": "violence_incitement",
}

DEFAULT_POLICY: Dict[str, Any] = {
    "family_mode": False,
    "allow_nsfw": True,
    "allow_adult_content": True,
    "marketplace_free_only_in_family_mode": True,
}

_POLICY_PATH = FRIDAY_DIR / "moderation_policy.json"

# ─────────────────────────────────────────────────────────────────────────────
#  HARM PATTERNS (compiled once at import)
# ─────────────────────────────────────────────────────────────────────────────

# H1: CSAM indicators — serious indicator phrases only, no false-positive traps
_H1_PATTERNS: List[re.Pattern] = [
    re.compile(p, re.I) for p in [
        r"\bcsam\b",
        r"\bchild\s+sexual\s+abuse\s+material\b",
        r"\bchild\s+porn(?:ography)?\b",
        r"\bkiddie\s+porn\b",
        r"\bpedo(?:phile)?\s+content\b",
        r"\bminor\s+nude[sd]?\b",
        r"\bchild\s+exploit(?:ation)?\s+material\b",
        r"\bsexual(?:ly)?\s+exploit\w*\s+minor\b",
        r"\bunderage\s+sex(?:ual)?\b",
        r"\bjailbait\s+(?:nude|sex|porn)\b",
        r"\bgenerat\w+\s+(?:nude|naked)\s+(?:child|minor|kid)\b",
        r"\bchild\s+(?:nude|naked)\s+image\b",
        r"\bsex(?:ual)?\s+content\s+(?:of|with|involving)\s+(?:a\s+)?(?:child|minor|kid)\b",
        r"\blolicon\b",
        r"\bshota(?:con)?\b",
    ]
]

# H2: Real-person deepfakes
_H2_PATTERNS: List[re.Pattern] = [
    re.compile(p, re.I) for p in [
        r"\bdeepfake\b",
        r"\bface[\s_-]?swap\b",
        r"\bsynth(?:etic)?\s+face\s+of\b",
        r"\bfake\s+video\s+of\s+(?:a\s+)?real\s+person\b",
        r"\bnon-?consensual\s+(?:deepfake|synthetic|ai.generated)\b",
        r"\bnon-?consensual\s+intimate\s+image\b",
        r"\bncii\b",
        r"\bai[\s-]?generated\s+(?:nude|naked)\s+(?:of\s+)?\w+\s+(?:celebrity|person|politician|actor)\b",
        r"\bput\s+\w+['s]*\s+face\s+on\s+(?:a\s+)?(?:porn|nude|naked)\b",
        r"\breplace\s+face\s+with\s+\w+\s+(?:in\s+)?(?:porn|nude|explicit)\b",
    ]
]

# H3: Doxxing / PII exposure
_H3_PATTERNS: List[re.Pattern] = [
    re.compile(p, re.I) for p in [
        r"\bdox(?:x)?(?:ing)?\b",
        r"\bhome\s+address\s+of\b",
        r"\breal\s+address\s+of\b",
        r"\bpersonal\s+address\s+of\b",
        r"\bssn\b",
        r"\bsocial\s+security\s+number\b",
        r"\bpublish\w*\s+(?:private|personal)\s+(?:info|information|details|address|phone)\b",
        r"\bexpos(?:e|ing)\s+(?:personal|private)\s+(?:info|information|address|details)\b",
        r"\bfind\s+(?:where\s+(?:they|he|she)\s+live|(?:their|his|her)\s+address)\b",
        r"\btrack\w*\s+(?:someone|person|individual)\s+(?:down|location|whereabouts)\b",
        r"\bidentify\s+(?:and\s+)?(?:harass|stalk|target)\b",
        r"\bpersonal\s+phone\s+number\s+of\b",
        r"\bleak\w*\s+(?:personal|private)\s+(?:info|data|details|address)\b",
    ]
]

# H4: Violence incitement / WMD instructions
_H4_PATTERNS: List[re.Pattern] = [
    re.compile(p, re.I) for p in [
        r"\bhow\s+to\s+make\s+a\s+bomb\b",
        r"\bsynthesize\s+(?:sarin|vx|novichok|mustard\s+gas|nerve\s+agent|ricin|anthrax)\b",
        r"\bmass\s+shoot(?:ing)?\s+plan\b",
        r"\bterror(?:ist)?\s+attack\s+(?:plan|guide|how.to)\b",
        r"\bmanifest(?:o)?\s+(?:for\s+)?(?:killing|murder|attack)\b",
        r"\bincite\w*\s+(?:violence|murder|attack|genocide)\b",
        r"\bkill\s+(?:as\s+many|all\s+(?:the\s+)?(?:jews|muslims|christians|blacks|whites|gays))\b",
        r"\bweapon\s+of\s+mass\s+destruction\s+(?:instructions|guide|how.to|recipe|synthesis)\b",
        r"\bbiolog(?:ical)?\s+weapon\s+(?:synthesis|creation|recipe|how.to)\b",
        r"\bchemical\s+weapon\s+(?:synthesis|creation|recipe|how.to)\b",
        r"\bnuclear\s+(?:bomb|device|weapon)\s+(?:instructions|blueprint|design|how.to)\b",
        r"\bexplosive\s+(?:device|bomb)\s+(?:instructions|recipe|build|how.to)\b",
        r"\bshoot(?:ing)?\s+up\s+(?:a\s+)?(?:school|church|mosque|synagogue|concert|crowd)\b",
    ]
]

# NSFW patterns for tagging (not blocking unless family_mode)
_NSFW_PATTERNS: List[re.Pattern] = [
    re.compile(p, re.I) for p in [
        r"\bnsfw\b",
        r"\bexplicit\s+(?:sexual|adult)\s+content\b",
        r"\bpornograph(?:y|ic)\b",
        r"\badult\s+content\b",
        r"\berotica\b",
    ]
]

_HARM_CHECKS = [
    ("H1", _H1_PATTERNS),
    ("H2", _H2_PATTERNS),
    ("H3", _H3_PATTERNS),
    ("H4", _H4_PATTERNS),
]

# ─────────────────────────────────────────────────────────────────────────────
#  POLICY I/O
# ─────────────────────────────────────────────────────────────────────────────

def get_policy() -> Dict[str, Any]:
    """Read from moderation_policy.json; merge with DEFAULT_POLICY."""
    try:
        if _POLICY_PATH.exists():
            stored = json.loads(_POLICY_PATH.read_text(encoding="utf-8"))
            merged = {**DEFAULT_POLICY, **stored}
            return merged
    except Exception:
        pass
    return dict(DEFAULT_POLICY)


def update_policy(policy_dict: Dict[str, Any]) -> Dict[str, Any]:
    """Write merged policy to moderation_policy.json."""
    try:
        current = get_policy()
        current.update(policy_dict)
        FRIDAY_DIR.mkdir(parents=True, exist_ok=True)
        _POLICY_PATH.write_text(json.dumps(current, indent=2), encoding="utf-8")
        return current
    except Exception as e:
        print(f"  [moderation] update_policy failed: {e}")
        return get_policy()


def is_nsfw_allowed() -> bool:
    """True unless family_mode is active."""
    return not get_policy().get("family_mode", False)


# ─────────────────────────────────────────────────────────────────────────────
#  PATTERN ENGINE
# ─────────────────────────────────────────────────────────────────────────────

def _apply_harm_floor(text: str):
    """Run H1-H4 checks. Returns (harm_level|None, reason|None)."""
    for level, patterns in _HARM_CHECKS:
        for pat in patterns:
            m = pat.search(text)
            if m:
                return level, f"{HARM_LEVELS[level]}: matched '{m.group(0)}'"
    return None, None


def _check_nsfw(text: str) -> bool:
    """Return True if text contains NSFW signals."""
    return any(pat.search(text) for pat in _NSFW_PATTERNS)


# ─────────────────────────────────────────────────────────────────────────────
#  PUBLIC SCAN
# ─────────────────────────────────────────────────────────────────────────────

def scan(
    content_text: Optional[str] = None,
    content_path: Optional[str] = None,
    content_type: str = "text",
    metadata: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Scan content for harm. Returns verdict dict."""
    scan_id = str(uuid.uuid4())
    tags: List[str] = []
    try:
        text = content_text or ""

        # Load file content if path given
        if content_path and not text:
            try:
                p = Path(content_path)
                if p.exists() and p.stat().st_size < 10 * 1024 * 1024:
                    text = p.read_text(encoding="utf-8", errors="replace")
            except Exception:
                pass

        # Merge metadata-provided NSFW hint into text signal
        meta = metadata or {}
        meta_nsfw = bool(meta.get("nsfw") or meta.get("adult") or meta.get("explicit"))
        if meta_nsfw:
            tags.append("nsfw")

        # H1–H4 harm floor — always enforced
        harm_level, reason = _apply_harm_floor(text)
        if harm_level:
            return {
                "ok": True,
                "blocked": True,
                "verdict": "blocked",
                "harm_level": harm_level,
                "tags": [HARM_LEVELS[harm_level]],
                "reason": reason,
                "scan_id": scan_id,
            }

        # NSFW detection
        is_nsfw = meta_nsfw or _check_nsfw(text)
        if is_nsfw and "nsfw" not in tags:
            tags.append("nsfw")

        # Policy: family mode blocks NSFW
        policy = get_policy()
        if is_nsfw and policy.get("family_mode", False):
            return {
                "ok": True,
                "blocked": True,
                "verdict": "blocked",
                "harm_level": None,
                "tags": tags,
                "reason": "nsfw content blocked in family mode",
                "scan_id": scan_id,
            }

        verdict = "nsfw_flagged" if is_nsfw else "clean"
        return {
            "ok": True,
            "blocked": False,
            "verdict": verdict,
            "harm_level": None,
            "tags": tags,
            "reason": None,
            "scan_id": scan_id,
        }
    except Exception as e:
        return {
            "ok": False,
            "blocked": False,
            "verdict": "error",
            "harm_level": None,
            "tags": [],
            "reason": str(e),
            "scan_id": scan_id,
        }

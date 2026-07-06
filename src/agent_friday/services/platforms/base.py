"""
Platform adapter contract (docs/CONTENT_PIPELINE_SPEC.md §4.1).

A PlatformAdapter bridges the content pipeline to one outbound social platform.
Adapters own transport and translation, never governance — moderation, egress
gating, and provenance all happen in ``publisher.py`` *before* an adapter sees
the payload. Mirrors ``services/channels/base.py`` in spirit, but outbound.

Credential conventions (§4.1 / §12.2):
  * Structured OAuth state (access + refresh + expiry + scopes + account) is one
    encrypted blob per platform at ``~/.friday/platforms/<name>.cred`` via
    ``credential_store.write_secret()`` (the google_accounts.py pattern).
  * Single-string secrets (app password, personal token) use
    ``credential_store.set_provider_key("platform_<name>", …)`` (the
    channel-token pattern).
  * Every auth action emits ``credential_store.audit_event("platform", …)``.
    Nothing tokenish is ever logged or returned from ``status()``.

Rate budgeting (§4.1): each adapter keeps a local daily budget persisted in
``~/.friday/platforms/rate_budget.json`` — "never trust ourselves to remember".
The publisher defers targets that would exceed it.

Degradation ladder (§4.14): every adapter declares its rung and NEVER silently
falls to a lower one — ``degrade()`` builds the surface-the-choice envelope.

Import-safe under FRIDAY_TESTING: stdlib only at import time, no threads, no
network; credential_store is lazy-imported inside methods.
"""
from __future__ import annotations

import copy
import json
import os
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

_HOME = Path(os.environ.get("FRIDAY_HOME") or Path.home())
FRIDAY_DIR = _HOME / ".friday"
PLATFORMS_DIR = FRIDAY_DIR / "platforms"          # encrypted .cred blobs live here
BUDGET_PATH = PLATFORMS_DIR / "rate_budget.json"  # local rate-budget bookkeeping

_BUDGET_LOCK = threading.Lock()

# §4.14 — the degradation ladder, best rung first. A target that cannot publish
# at its adapter's declared rung surfaces the choice; it never silently falls.
DEGRADATION_LADDER = ("api", "api_constrained", "assisted_handoff", "clipboard")

AUTH_MODES = ("oauth2", "oauth2_pkce", "app_password", "token", "manual")

# Baseline capability envelope — every key the composer/UI relies on (§4.1).
# Adapters override with their real limits; unknown stays honest, not optimistic.
DEFAULT_CAPABILITIES: Dict[str, Any] = {
    "formats": ["post"],
    "char_limit": 5000,
    "title_limit": None,
    "media": {
        "images": {"max": 4, "formats": ["png", "jpeg"],
                   "max_bytes": 5 * 1024 * 1024, "aspect": (0.5, 2.0)},
        "video": None,
        "alt_text": False,
    },
    "thread": False,
    "native_schedule": False,
    "native_delete": False,
    "analytics": "none",          # full | counts | none
    "hashtags_max": None,
    "notes": [],
}


# ── time helpers ──────────────────────────────────────────────────────────────
def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _parse_iso(value) -> Optional[datetime]:
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except Exception:
        return None


def _next_utc_midnight(now: datetime) -> str:
    nxt = (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    return nxt.strftime("%Y-%m-%dT%H:%M:%SZ")


# ── budget persistence (shared file, one entry per platform) ─────────────────
def _read_budget_state() -> Dict[str, Any]:
    try:
        if BUDGET_PATH.exists():
            data = json.loads(BUDGET_PATH.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                return data
    except Exception:
        pass
    return {}


def _write_budget_state(state: Dict[str, Any]) -> None:
    try:
        BUDGET_PATH.parent.mkdir(parents=True, exist_ok=True)
        tmp = BUDGET_PATH.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(state, indent=2), encoding="utf-8")
        os.replace(tmp, BUDGET_PATH)
    except Exception:
        pass


class PlatformAdapter:
    """Base class for one outbound platform (§4.1).

    Every public method returns a dict envelope (or a documented scalar) and
    never raises — the publisher and routes rely on that.
    """

    name: str = "platform"          # "linkedin"
    label: str = "Platform"         # "LinkedIn"
    auth_mode: str = "manual"       # oauth2 | oauth2_pkce | app_password | token | manual
    automation_tier: str = "api"    # declared §4.14 rung
    default_daily_limit: int = 25   # conservative local post budget (per day)

    def __init__(self) -> None:
        self._config: Dict[str, Any] = {}
        self._last_error: Optional[str] = None

    # ── configuration ────────────────────────────────────────────────────────
    def configure(self, config: Dict[str, Any]) -> None:
        """Apply non-secret options from ~/.friday/platforms.json.

        Secrets never ride config — they live in the credential store.
        """
        self._config = dict(config or {})

    # ── capability declaration (drives composer + UI, §5) ────────────────────
    def capabilities(self) -> Dict[str, Any]:
        """{formats, char_limit, title_limit, media, thread, native_schedule,
        native_delete, analytics, hashtags_max, notes} — deep-copied so callers
        can annotate without corrupting the declaration."""
        return copy.deepcopy(DEFAULT_CAPABILITIES)

    # ── credential conventions (§4.1 / §12.2) ────────────────────────────────
    def _cred_path(self) -> Path:
        return PLATFORMS_DIR / f"{self.name}.cred"

    def _audit(self, event: str, **fields) -> None:
        """Best-effort audit trail entry — never token material, only facts."""
        try:
            from agent_friday.services import credential_store
            credential_store.audit_event("platform", event, platform=self.name, **fields)
        except Exception:
            pass

    def save_credentials(self, blob: Dict[str, Any]) -> Dict[str, Any]:
        """Encrypt + persist structured auth state (one blob per platform)."""
        try:
            from agent_friday.services import credential_store
            method = credential_store.write_secret(
                self._cred_path(), json.dumps(blob or {}).encode("utf-8"))
            self._audit("credentials_stored", protection=method)
            return {"ok": True, "protection": method}
        except Exception as e:
            self._last_error = str(e)
            return {"ok": False, "error": str(e)}

    def load_credentials(self) -> Optional[Dict[str, Any]]:
        """Decrypt the stored auth blob; None when absent/unreadable."""
        try:
            path = self._cred_path()
            if not path.exists():
                return None
            from agent_friday.services import credential_store
            data = json.loads(credential_store.read_secret(path).decode("utf-8"))
            return data if isinstance(data, dict) else None
        except Exception as e:
            self._last_error = str(e)
            return None

    def clear_credentials(self) -> Dict[str, Any]:
        """Purge the blob AND any simple provider-key secret for this platform."""
        removed = False
        try:
            path = self._cred_path()
            if path.exists():
                path.unlink()
                removed = True
        except Exception as e:
            self._last_error = str(e)
        try:
            from agent_friday.services import credential_store
            if credential_store.delete_provider_key(f"platform_{self.name}"):
                removed = True
        except Exception:
            pass
        self._audit("credentials_cleared", removed=removed)
        return {"ok": True, "removed": removed}

    def simple_secret(self) -> Optional[str]:
        """Single-string secret (app password / personal access token)."""
        try:
            from agent_friday.services import credential_store
            return credential_store.get_provider_key(f"platform_{self.name}")
        except Exception:
            return None

    def set_simple_secret(self, value: str) -> Dict[str, Any]:  # pragma: allowlist secret
        try:
            from agent_friday.services import credential_store
            method = credential_store.set_provider_key(f"platform_{self.name}", value)
            self._audit("secret_stored", protection=method)
            return {"ok": True, "protection": method}
        except Exception as e:
            self._last_error = str(e)
            return {"ok": False, "error": str(e)}

    def has_credentials(self) -> bool:
        return self.load_credentials() is not None or bool(self.simple_secret())

    # ── auth lifecycle ────────────────────────────────────────────────────────
    def connect_url(self, state: str) -> Optional[str]:
        """Start OAuth: the authorize URL, or None for tokenless/manual modes."""
        return None

    def handle_callback(self, params: Dict[str, Any]) -> Dict[str, Any]:
        """OAuth code→tokens exchange; encrypt; return status(). The default has
        no OAuth flow — subclasses with auth_mode oauth2* must override."""
        self._audit("callback", supported=False)
        return self.status()

    def refresh(self) -> bool:
        """Refresh credentials if expiring. True = usable. Default: nothing to
        refresh; usable iff credentials exist."""
        return self.has_credentials()

    def revoke(self) -> bool:
        """Platform-side revoke where supported + local purge. Default: local
        purge only (no platform API to call)."""
        self.clear_credentials()
        self._audit("revoke", platform_side=False)
        return True

    def status(self) -> Dict[str, Any]:
        """Connection facts only — never token material (§12.2)."""
        out = {
            "name": self.name,
            "label": self.label,
            "auth_mode": self.auth_mode,
            "connected": False,
            "account": None,
            "scopes": [],
            "expires_at": None,
            "tier": self.automation_tier,
            "last_error": self._last_error,
        }
        try:
            creds = self.load_credentials()
            connected = creds is not None or bool(self.simple_secret())
            out["connected"] = connected
            if isinstance(creds, dict):
                out["account"] = creds.get("account")
                out["scopes"] = list(creds.get("scopes") or [])
                out["expires_at"] = creds.get("expires_at")
        except Exception as e:
            out["last_error"] = str(e)
        return out

    # ── publish path ──────────────────────────────────────────────────────────
    def prepare(self, target: Dict[str, Any], post: Dict[str, Any]) -> Dict[str, Any]:
        """Validate the target against capabilities and build the closed
        PreparedPost struct. Subclasses extend this with media upload; the
        default is transport-free hard validation (§5.2 step 6).

        Returns {ok, prepared, warnings: [...]} — never raises.
        """
        try:
            target = dict(target or {})
            post = dict(post or {})
            caps = self.capabilities() or {}
            warnings: List[str] = []

            body = target.get("adapted_body") or post.get("body") or ""
            segments = [s for s in (target.get("segments") or []) if isinstance(s, str)]
            title = target.get("adapted_title") or ""

            char_limit = target.get("char_limit") or caps.get("char_limit")
            if isinstance(char_limit, int) and char_limit > 0:
                if segments:
                    over = [i for i, s in enumerate(segments) if len(s) > char_limit]
                    if over:
                        return {"ok": False, "warnings": warnings,
                                "error": f"segment(s) {over} exceed char_limit ({char_limit})"}
                elif len(body) > char_limit:
                    return {"ok": False, "warnings": warnings,
                            "error": f"body exceeds char_limit ({len(body)}/{char_limit})"}

            title_limit = caps.get("title_limit")
            if isinstance(title_limit, int) and title_limit > 0 and len(title) > title_limit:
                return {"ok": False, "warnings": warnings,
                        "error": f"title exceeds title_limit ({len(title)}/{title_limit})"}

            media = caps.get("media") or {}
            assets = [a for a in (target.get("adapted_assets") or post.get("assets") or [])
                      if isinstance(a, dict)]
            images = [a for a in assets
                      if (a.get("kind") or (a.get("source") or {}).get("kind") or "image") == "image"]
            img_caps = media.get("images") or {}
            img_max = img_caps.get("max")
            if isinstance(img_max, int) and len(images) > img_max:
                return {"ok": False, "warnings": warnings,
                        "error": f"too many images ({len(images)}/{img_max})"}
            if media.get("alt_text"):
                missing = [a for a in images
                           if not (a.get("alt_text") or (a.get("source") or {}).get("alt_text"))]
                if missing:
                    warnings.append(f"{len(missing)} image(s) missing alt text")

            prepared = {
                "platform": self.name,
                "target_id": target.get("id"),
                "post_id": post.get("id"),
                "format": target.get("format") or "post",
                "title": title,
                "body": body,
                "segments": segments,
                "assets": assets,
                "hashtags": list(target.get("hashtags") or []),
                "mentions": list(target.get("mentions") or []),
                "options": dict(target.get("options") or {}),
            }
            return {"ok": True, "prepared": prepared, "warnings": warnings}
        except Exception as e:
            self._last_error = str(e)
            return {"ok": False, "error": str(e), "warnings": []}

    def publish(self, prepared: Dict[str, Any]) -> Dict[str, Any]:
        """→ {ok, post_url, platform_post_id, raw}. The base cannot publish."""
        return {"ok": False, "error": "not_implemented"}

    def delete(self, platform_post_id: str) -> Dict[str, Any]:
        """Best-effort takedown → {ok}. Default: platform has no delete API."""
        return {"ok": False, "error": "not_supported"}

    # ── analytics path ────────────────────────────────────────────────────────
    def fetch_metrics(self, platform_post_id: str) -> Optional[Dict[str, Any]]:
        """Normalized PlatformEngagement.metrics, or None (can't report)."""
        return None

    def fetch_account_metrics(self) -> Optional[Dict[str, Any]]:
        """Follower counts etc., or None."""
        return None

    # ── local rate budgeting (never trust ourselves to remember) ─────────────
    def _budget_limit(self) -> int:
        try:
            return int(self._config.get("daily_post_limit"))
        except (TypeError, ValueError):
            return int(self.default_daily_limit)

    def rate_budget(self) -> Dict[str, Any]:
        """{window, used, limit, reset_at} — persisted; the window rolls at the
        next UTC midnight. limit <= 0 means unlimited (open protocols)."""
        limit = self._budget_limit()
        try:
            now = _now_utc()
            with _BUDGET_LOCK:
                state = _read_budget_state()
                entry = state.get(self.name)
                entry = entry if isinstance(entry, dict) else None
                reset = _parse_iso(entry.get("reset_at")) if entry else None
                if entry is None or reset is None or reset <= now:
                    entry = {"window": "day", "used": 0,
                             "reset_at": _next_utc_midnight(now)}
                    state[self.name] = entry
                    _write_budget_state(state)
                return {"window": entry.get("window", "day"),
                        "used": int(entry.get("used", 0) or 0),
                        "limit": limit,
                        "reset_at": entry.get("reset_at")}
        except Exception as e:
            self._last_error = str(e)
            return {"window": "day", "used": 0, "limit": limit, "reset_at": None}

    def budget_would_exceed(self, n: int = 1) -> bool:
        """True when publishing n more would break the local budget — the
        publisher defers the target to reset_at instead of burning an attempt."""
        b = self.rate_budget()
        limit = b.get("limit", 0)
        if not isinstance(limit, int) or limit <= 0:
            return False
        return b.get("used", 0) + n > limit

    def consume_budget(self, n: int = 1) -> Dict[str, Any]:
        """Record n successful publishes against the window. Returns the
        refreshed budget envelope with ok."""
        try:
            now = _now_utc()
            with _BUDGET_LOCK:
                state = _read_budget_state()
                entry = state.get(self.name)
                entry = entry if isinstance(entry, dict) else None
                reset = _parse_iso(entry.get("reset_at")) if entry else None
                if entry is None or reset is None or reset <= now:
                    entry = {"window": "day", "used": 0,
                             "reset_at": _next_utc_midnight(now)}
                entry["used"] = int(entry.get("used", 0) or 0) + max(0, int(n))
                state[self.name] = entry
                _write_budget_state(state)
            return {"ok": True, "window": entry.get("window", "day"),
                    "used": entry["used"], "limit": self._budget_limit(),
                    "reset_at": entry.get("reset_at")}
        except Exception as e:
            self._last_error = str(e)
            return {"ok": False, "error": str(e)}

    # ── §4.14 degradation ladder ──────────────────────────────────────────────
    def degradation_options(self) -> List[str]:
        """The rungs below this adapter's declared rung, best first."""
        try:
            i = DEGRADATION_LADDER.index(self.automation_tier)
        except ValueError:
            i = 0
        return list(DEGRADATION_LADDER[i + 1:])

    def degrade(self, reason: str) -> Dict[str, Any]:
        """Build the loud surface-the-choice envelope for a target that cannot
        publish at the declared rung. NEVER silently falls a rung — the caller
        (publisher) turns this into a notification + Queue action (§4.14)."""
        return {
            "ok": False,
            "error": "degraded",
            "degraded": True,
            "reason": str(reason or ""),
            "declared_rung": self.automation_tier,
            "options": self.degradation_options(),
            "requires_user_choice": True,
        }

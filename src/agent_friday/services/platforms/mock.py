"""
Mock platform adapter — the Phase-1 publish target and fault-injection tool.

Publishes to an in-memory list (no transport at all), with configurable
failures, latency, and capability overrides so publisher/contract tests can
exercise retry ladders, budget deferral, and degradation without a network.

Config keys (via ~/.friday/platforms.json → "mock" section, or configure()):
  connected: bool        — pretend-auth state (default True)
  latency_s: float       — sleep per prepare/publish call (capped at 5 s)
  fail_prepare: int      — fail the next N prepare() calls
  fail_publish: int      — fail the next N publish() calls
  fail_always: bool      — every publish fails until cleared
  publish_error: str     — error string used for injected publish failures
  capabilities: dict     — shallow override of the declared capabilities
  daily_post_limit: int  — local rate budget (base convention)
"""
from __future__ import annotations

import threading
import time
from typing import Any, Dict, List, Optional

from agent_friday.services.platforms.base import PlatformAdapter


class MockPlatformAdapter(PlatformAdapter):
    name = "mock"
    label = "Mock Platform"
    auth_mode = "token"
    automation_tier = "api"
    default_daily_limit = 100

    def __init__(self) -> None:
        super().__init__()
        self._lock = threading.Lock()
        self.published: List[Dict[str, Any]] = []
        self.deleted: List[str] = []
        self.prepare_calls = 0
        self.publish_calls = 0
        self._connected = True
        self._fail_prepare = 0
        self._fail_publish = 0
        self._fail_always = False
        self._latency_s = 0.0
        self._caps_override: Dict[str, Any] = {}
        self._metrics: Dict[str, Dict[str, Any]] = {}
        self._counter = 0

    # ── configuration / test controls ─────────────────────────────────────────
    def configure(self, config: Dict[str, Any]) -> None:
        super().configure(config)
        c = self._config
        self._connected = bool(c.get("connected", True))
        try:
            self._latency_s = min(5.0, max(0.0, float(c.get("latency_s", 0) or 0)))
        except (TypeError, ValueError):
            self._latency_s = 0.0
        try:
            self._fail_prepare = max(0, int(c.get("fail_prepare", 0) or 0))
        except (TypeError, ValueError):
            self._fail_prepare = 0
        try:
            self._fail_publish = max(0, int(c.get("fail_publish", 0) or 0))
        except (TypeError, ValueError):
            self._fail_publish = 0
        self._fail_always = bool(c.get("fail_always", False))
        caps = c.get("capabilities")
        self._caps_override = dict(caps) if isinstance(caps, dict) else {}

    def set_failures(self, prepare: int = 0, publish: int = 0,
                     always: bool = False) -> None:
        """Fault injection: fail the next N prepare/publish calls."""
        with self._lock:
            self._fail_prepare = max(0, int(prepare))
            self._fail_publish = max(0, int(publish))
            self._fail_always = bool(always)

    def set_metrics(self, platform_post_id: str, metrics: Dict[str, Any]) -> None:
        with self._lock:
            self._metrics[str(platform_post_id)] = dict(metrics or {})

    def reset(self) -> None:
        with self._lock:
            self.published.clear()
            self.deleted.clear()
            self._metrics.clear()
            self.prepare_calls = 0
            self.publish_calls = 0
            self._fail_prepare = 0
            self._fail_publish = 0
            self._fail_always = False
            self._counter = 0
            self._last_error = None

    def _sleep(self) -> None:
        if self._latency_s > 0:
            time.sleep(self._latency_s)

    # ── capability declaration ────────────────────────────────────────────────
    def capabilities(self) -> Dict[str, Any]:
        caps = super().capabilities()
        caps.update({
            "formats": ["post", "thread", "image_post"],
            "char_limit": 500,
            "title_limit": 100,
            "thread": True,
            "native_schedule": False,
            "native_delete": True,
            "analytics": "counts",
            "hashtags_max": 5,
            "notes": ["in-memory mock — nothing leaves the machine"],
        })
        media = caps.get("media") or {}
        media["alt_text"] = True
        caps["media"] = media
        caps.update(self._caps_override)
        return caps

    # ── auth lifecycle (in-memory pretend-auth) ───────────────────────────────
    def connect_url(self, state: str) -> Optional[str]:
        return None  # tokenless — connect is instant

    def handle_callback(self, params: Dict[str, Any]) -> Dict[str, Any]:
        self._connected = True
        self._audit("connected", mode="mock")
        return self.status()

    def refresh(self) -> bool:
        return self._connected

    def revoke(self) -> bool:
        self._connected = False
        self._audit("revoke", platform_side=True)
        return True

    def status(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "label": self.label,
            "auth_mode": self.auth_mode,
            "connected": self._connected,
            "account": "mock_account" if self._connected else None,
            "scopes": ["publish", "analytics"] if self._connected else [],
            "expires_at": None,
            "tier": self.automation_tier,
            "last_error": self._last_error,
        }

    # ── publish path ──────────────────────────────────────────────────────────
    def prepare(self, target: Dict[str, Any], post: Dict[str, Any]) -> Dict[str, Any]:
        self._sleep()
        with self._lock:
            self.prepare_calls += 1
            if self._fail_prepare > 0:
                self._fail_prepare -= 1
                self._last_error = "injected_prepare_failure"
                return {"ok": False, "error": "injected_prepare_failure", "warnings": []}
        return super().prepare(target, post)

    def publish(self, prepared: Dict[str, Any]) -> Dict[str, Any]:
        try:
            self._sleep()
            with self._lock:
                self.publish_calls += 1
                if not self._connected:
                    self._last_error = "not_connected"
                    return {"ok": False, "error": "not_connected"}
                if self._fail_always or self._fail_publish > 0:
                    if self._fail_publish > 0:
                        self._fail_publish -= 1
                    msg = str(self._config.get("publish_error")
                              or "injected_publish_failure")
                    self._last_error = msg
                    return {"ok": False, "error": msg}
                self._counter += 1
                pid = f"mock-{self._counter}"
                entry = dict(prepared or {})
                entry["platform_post_id"] = pid
                entry["published_ts"] = time.time()
                self.published.append(entry)
            self.consume_budget()
            return {"ok": True, "post_url": f"mock://post/{pid}",
                    "platform_post_id": pid, "raw": {"id": pid}}
        except Exception as e:
            self._last_error = str(e)
            return {"ok": False, "error": str(e)}

    def delete(self, platform_post_id: str) -> Dict[str, Any]:
        pid = str(platform_post_id)
        with self._lock:
            before = len(self.published)
            self.published = [p for p in self.published
                              if p.get("platform_post_id") != pid]
            removed = len(self.published) < before
            if removed:
                self.deleted.append(pid)
        return {"ok": True, "deleted": removed}

    # ── analytics path ────────────────────────────────────────────────────────
    def fetch_metrics(self, platform_post_id: str) -> Optional[Dict[str, Any]]:
        pid = str(platform_post_id)
        with self._lock:
            known = any(p.get("platform_post_id") == pid for p in self.published)
            if not known and pid not in self.deleted:
                return None
            metrics = {"impressions": 0, "likes": 0, "comments": 0, "shares": 0}
            metrics.update(self._metrics.get(pid) or {})
            return metrics

    def fetch_account_metrics(self) -> Optional[Dict[str, Any]]:
        with self._lock:
            return {"followers": 42, "posts": len(self.published)}


ADAPTER = MockPlatformAdapter

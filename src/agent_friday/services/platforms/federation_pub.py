"""Friday Federation publisher adapter (docs/CONTENT_PIPELINE_SPEC.md §4.13, §13).

No third-party transport — this adapter publishes onto Friday's own rails:

  1. Ensure the asset is registered in the ownership index
     (``ownership.register()`` auto-builds the signed manifest if missing;
     a text-only post is staged to a local markdown file first).
  2. ``marketplace.create_listing(asset_id, price_mpsi, license_offered,
     visibility, title, description=adapted_body, …)`` — free-commons
     (CC0/CC-BY/CC-BY-SA) or priced in Positrons. The license is per-piece
     (from the post), never an account-wide default (§13.1).
  3. Announce an encrypted ``CONTENT_OFFER`` via ``federation_transport`` to
     trusted peers (trust-graph filtered by ``overall_score``). Zero peers is
     a perfectly fine outcome — the listing still stands.
  4. ``post_url`` = the local listing URL; ``platform_post_id`` = listing id.

Engagement (§8.2 Federation column) = listing views (local counter), peer
fetches, purchases (``complete_purchase`` receipts — counted read-only from
the marketplace DB; the ψ already moved through ``economy.transfer()``), and
ψ tips. Local counters live in a small WAL sidecar DB; ``record_event()`` is
the public increment API for the routes/inbox that observe those events.

Policy-pack tags travel with the listing: persisted alongside the local
stats row and embedded in every CONTENT_OFFER payload so peer nodes'
``content_policies`` filters work (§4.13 moderation note).

Import-safe under FRIDAY_TESTING: stdlib only at import, no threads, no
network, no federation-service imports. Services resolve lazily through
``_svc()`` — under FRIDAY_TESTING they resolve to None (clean error
envelopes) unless a test injects stubs into ``_services``. Every public
method returns a dict envelope (or documented scalar) and never raises.
"""
from __future__ import annotations

import copy
import importlib
import json
import os
import sqlite3
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from agent_friday.services.platforms import base as pbase
from agent_friday.services.platforms.base import PlatformAdapter

# ── fixed, content-free error strings (§12.5) ────────────────────────────────
ERR_SERVICES_UNAVAILABLE = "federation services unavailable"
ERR_NOTHING_TO_PUBLISH = "nothing to publish: empty body and no assets"
ERR_STAGE_WRITE = "could not stage post body for registration"
ERR_ASSET_REGISTRATION = "asset registration failed"
ERR_LISTING_FAILED = "marketplace listing creation failed"
ERR_DELETE_FAILED = "listing removal failed"
ERR_BAD_EVENT = "unknown event or missing listing id"

# Announce payloads stay polite — the listing itself holds the full body.
OFFER_DESC_LIMIT = 1000

_VISIBILITIES = ("public", "unlisted", "private")

# §8.2 Federation column: local engagement events → counter columns.
_EVENT_COLUMNS = {
    "view": "views",                 # → impressions
    "fetch": "fetches",              # → clicks
    "tip": "tips_count",             # → likes (+ tips_mpsi)
    "peer_message": "peer_messages",  # → comments
    "peer_relay": "peer_relays",     # → shares
    "new_peer": "new_peers",         # → follows_gained
}

_STATS_LOCK = threading.RLock()

_STATS_SCHEMA = """
CREATE TABLE IF NOT EXISTS listing_stats (
    listing_id    TEXT PRIMARY KEY,
    views         INTEGER DEFAULT 0,
    fetches       INTEGER DEFAULT 0,
    tips_count    INTEGER DEFAULT 0,
    tips_mpsi     INTEGER DEFAULT 0,
    peer_messages INTEGER DEFAULT 0,
    peer_relays   INTEGER DEFAULT 0,
    new_peers     INTEGER DEFAULT 0,
    tags          TEXT DEFAULT '[]',
    created_at    TEXT,
    updated_at    TEXT
);
"""

# Test-injection point: tests place stub modules/objects here by service name
# ("ownership", "marketplace", "federation", "federation_transport",
#  "provenance"). Under FRIDAY_TESTING nothing real is ever imported.
_services: Dict[str, Any] = {}


def _testing() -> bool:
    return bool(os.environ.get("FRIDAY_TESTING"))


def _svc(name: str):
    """Lazily resolve a federation service; None = unavailable (degrade)."""
    if name in _services:
        return _services[name]
    if _testing():
        return None
    try:
        return importlib.import_module(f"agent_friday.services.{name}")
    except Exception:
        return None


# ── small helpers ─────────────────────────────────────────────────────────────
def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _to_int(value: Any, default: int = 0) -> int:
    try:
        if isinstance(value, bool):
            return default
        return int(value)
    except (TypeError, ValueError):
        return default


def _license_terms(license_field: Any) -> Optional[str]:
    """Post license may be {'terms': …} or a plain string."""
    if isinstance(license_field, dict):
        terms = license_field.get("terms")
        return str(terms) if terms else None
    if isinstance(license_field, str) and license_field.strip():
        return license_field.strip()
    return None


def _collect_tags(*sources: Any) -> List[str]:
    """Union of policy-pack tag lists, order-preserving, de-duplicated."""
    out: List[str] = []
    for src in sources:
        if not isinstance(src, (list, tuple)):
            continue
        for t in src:
            s = str(t).strip()
            if s and s not in out:
                out.append(s)
    return out


# ── local stats sidecar (SQLite WAL + RLock) ─────────────────────────────────
def _stats_db_path() -> Path:
    # Derived at call time from base so test isolation (monkeypatched
    # PLATFORMS_DIR) covers this DB too.
    return pbase.PLATFORMS_DIR / "federation_pub.db"


def _stats_conn() -> sqlite3.Connection:
    path = _stats_db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(str(path), check_same_thread=False, timeout=10)
    con.execute("PRAGMA journal_mode=WAL")
    con.row_factory = sqlite3.Row
    con.executescript(_STATS_SCHEMA)
    return con


def _init_stats_row(listing_id: str, tags: List[str]) -> bool:
    try:
        now = _now_iso()
        with _STATS_LOCK:
            con = _stats_conn()
            try:
                con.execute(
                    """INSERT INTO listing_stats (listing_id, tags, created_at, updated_at)
                       VALUES (?,?,?,?)
                       ON CONFLICT(listing_id) DO UPDATE SET
                         tags = excluded.tags, updated_at = excluded.updated_at""",
                    (listing_id, json.dumps(list(tags or [])), now, now),
                )
                con.commit()
            finally:
                con.close()
        return True
    except Exception:
        return False


def _read_stats(listing_id: str) -> Optional[Dict[str, Any]]:
    try:
        with _STATS_LOCK:
            con = _stats_conn()
            try:
                row = con.execute(
                    "SELECT * FROM listing_stats WHERE listing_id=?", (listing_id,)
                ).fetchone()
            finally:
                con.close()
        if row is None:
            return None
        rec = dict(row)
        try:
            rec["tags"] = json.loads(rec.get("tags") or "[]")
        except Exception:
            rec["tags"] = []
        return rec
    except Exception:
        return None


def record_event(listing_id: str, event: str, n: int = 1,
                 amount_mpsi: int = 0) -> Dict[str, Any]:
    """Record a local engagement event against a listing (§4.13 engagement).

    Events: view | fetch | tip | peer_message | peer_relay | new_peer.
    ``amount_mpsi`` only applies to tips. Returns an envelope, never raises.
    """
    try:
        lid = str(listing_id or "").strip()
        ev = str(event or "").strip().lower()
        col = _EVENT_COLUMNS.get(ev)
        if not lid or col is None:
            return {"ok": False, "error": ERR_BAD_EVENT}
        n = max(0, _to_int(n, 0))
        tip_amount = max(0, _to_int(amount_mpsi, 0)) if ev == "tip" else 0
        now = _now_iso()
        with _STATS_LOCK:
            con = _stats_conn()
            try:
                con.execute(
                    """INSERT OR IGNORE INTO listing_stats
                       (listing_id, created_at, updated_at) VALUES (?,?,?)""",
                    (lid, now, now),
                )
                # column names come from the fixed _EVENT_COLUMNS map only
                con.execute(
                    f"""UPDATE listing_stats
                        SET {col} = {col} + ?, tips_mpsi = tips_mpsi + ?,
                            updated_at = ?
                        WHERE listing_id = ?""",
                    (n, tip_amount, now, lid),
                )
                con.commit()
            finally:
                con.close()
        return {"ok": True, "listing_id": lid, "event": ev, "recorded": n}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def _count_completed_purchases(mk: Any, listing_id: str) -> Tuple[Optional[int], Optional[int]]:
    """(purchases, revenue_mpsi) from the marketplace DB, read-only.

    Purchases are `complete_purchase` receipts; the ψ already moved via
    economy.transfer(). (None, None) when the DB isn't reachable.
    """
    try:
        db_path = getattr(mk, "DB_PATH", None)
        if not db_path or not Path(str(db_path)).exists():
            return None, None
        con = sqlite3.connect(str(db_path), timeout=10)
        try:
            row = con.execute(
                """SELECT COUNT(*), COALESCE(SUM(amount_mpsi), 0)
                   FROM purchases
                   WHERE listing_id = ?
                     AND (status = 'completed' OR completed_at IS NOT NULL)""",
                (listing_id,),
            ).fetchone()
        finally:
            con.close()
        if row is None:
            return None, None
        return _to_int(row[0], 0), _to_int(row[1], 0)
    except Exception:
        return None, None


# ── the adapter ───────────────────────────────────────────────────────────────
class FederationAdapter(PlatformAdapter):
    """Publish to Friday's own federation: ownership → listing → announce."""

    name = "federation"
    label = "Friday Federation"
    auth_mode = "token"            # the local Ed25519 identity, no user entry
    automation_tier = "api"        # native — it is ours (§4.13)
    default_daily_limit = 0        # our rails: unlimited (<=0 = no local cap)

    CAPABILITIES: Dict[str, Any] = {
        "formats": ["listing", "post", "article", "image", "video"],
        "char_limit": None,        # full fidelity — no char limit
        "title_limit": None,
        "media": {
            "images": {"max": None, "formats": ["png", "jpeg", "webp", "gif"],
                       "max_bytes": None, "aspect": None},
            "video": {"max_s": None, "formats": ["mp4", "webm", "mov"],
                      "max_bytes": None},
            "alt_text": True,
        },
        "thread": False,
        "native_schedule": False,  # the marketplace has no scheduled_at — a
                                   # listing goes live the instant it is
                                   # created, so the publisher must send
                                   # federation targets at the due instant
                                   # itself (advertising True made arm-time
                                   # delegation publish every scheduled
                                   # listing EARLY)
        "native_delete": True,     # remove_listing()
        "analytics": "full",       # our own counters + purchase receipts
        "hashtags_max": None,
        "notes": [
            "Friday's own rails — no third-party API, no character limits.",
            "Publish = signed marketplace listing; price via options.price_mpsi "
            "(0 = free commons), license per piece from the post.",
            "Encrypted CONTENT_OFFER announced to trust-graph-filtered peers; "
            "zero peers is fine — the listing still stands.",
            "Policy-pack tags travel with the listing and every offer so peer "
            "content_policies filters work.",
        ],
    }

    # ── capability declaration ────────────────────────────────────────────────
    def capabilities(self) -> Dict[str, Any]:
        return copy.deepcopy(self.CAPABILITIES)

    # ── identity / status ─────────────────────────────────────────────────────
    def _identity(self) -> Optional[str]:
        """Our Ed25519 agent id (public key hex), or None when unavailable."""
        try:
            pv = _svc("provenance")
            return pv.agent_id() if pv else None
        except Exception:
            return None

    def status(self) -> Dict[str, Any]:
        ident = self._identity()
        return {
            "name": self.name,
            "label": self.label,
            "auth_mode": self.auth_mode,
            "connected": ident is not None,
            "account": (ident[:16] + "…") if ident else None,
            "scopes": [],
            "expires_at": None,        # Ed25519 identity does not expire
            "tier": self.automation_tier,
            "last_error": self._last_error,
        }

    def refresh(self) -> bool:
        return self._identity() is not None

    # ── prepare: enrich the base validation with federation options ─────────
    def prepare(self, target: Dict[str, Any], post: Dict[str, Any]) -> Dict[str, Any]:
        res = super().prepare(target, post)
        try:
            if not res.get("ok"):
                return res
            post = dict(post or {})
            prepared = res["prepared"]
            opts = prepared.setdefault("options", {})

            if not prepared.get("title"):
                prepared["title"] = str(post.get("title") or "")

            # per-piece license: target option beats post license (§13.1)
            lic = _license_terms(opts.get("license")) or _license_terms(post.get("license"))
            if lic:
                opts["license"] = lic

            opts["price_mpsi"] = max(0, _to_int(opts.get("price_mpsi"), 0))
            vis = str(opts.get("visibility") or "public").lower()
            opts["visibility"] = vis if vis in _VISIBILITIES else "public"

            # policy-pack tags travel with the listing (§4.13)
            opts["tags"] = _collect_tags(opts.get("tags"), post.get("tags"))
            return res
        except Exception as e:
            self._last_error = str(e)
            return {"ok": False, "error": str(e), "warnings": []}

    # ── publish path ──────────────────────────────────────────────────────────
    def _first_asset_path(self, assets: Any) -> Optional[Path]:
        for a in assets or []:
            if not isinstance(a, dict):
                continue
            src = a.get("source") if isinstance(a.get("source"), dict) else {}
            for key_holder in (a, src):
                for k in ("path", "file", "file_path"):
                    v = key_holder.get(k)
                    if v:
                        try:
                            p = Path(str(v))
                            if p.exists() and p.is_file():
                                return p
                        except Exception:
                            continue
        return None

    def _stage_body(self, prepared: Dict[str, Any]) -> Optional[Path]:
        """Write a text-only post to a local markdown file so it can be
        registered (the manifest signs file bytes, not strings)."""
        try:
            staging = pbase.PLATFORMS_DIR / "federation_staging"
            staging.mkdir(parents=True, exist_ok=True)
            stem = str(prepared.get("post_id") or prepared.get("target_id")
                       or uuid.uuid4().hex[:12])
            safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in stem)
            path = staging / f"{safe}.md"
            title = prepared.get("title") or ""
            body = prepared.get("body") or ""
            text = (f"# {title}\n\n{body}" if title else body)
            path.write_text(text, encoding="utf-8")
            return path
        except Exception as e:
            self._last_error = str(e)
            return None

    def _listing_url(self, listing_id: str) -> str:
        base = str(self._config.get("base_url") or "http://localhost:3000").rstrip("/")
        return f"{base}/api/marketplace/listing/{listing_id}"

    def _announce(self, offer: Dict[str, Any]) -> Dict[str, Any]:
        """Encrypted CONTENT_OFFER to trust-graph-filtered peers. Best-effort:
        zero peers, missing transport, and per-peer failures never fail the
        publish — the listing already stands."""
        result: Dict[str, Any] = {"attempted": 0, "announced": 0, "failed": 0}
        try:
            if self._config.get("announce") is False:
                result["skipped"] = "announce disabled by config"
                return result
            fed = _svc("federation")
            tx = _svc("federation_transport")
            if fed is None or tx is None:
                result["skipped"] = "federation transport unavailable"
                return result
            msg_type = (getattr(tx, "MSG_TYPES", None) or {}).get(
                "CONTENT_OFFER", "content_offer")
            try:
                min_trust = float(self._config.get("min_peer_trust", 0.5))
            except (TypeError, ValueError):
                min_trust = 0.5

            peers = fed.get_peers() or []
            trusted = []
            for p in peers:
                if not isinstance(p, dict) or not p.get("agent_id"):
                    continue
                try:
                    score = float(p.get("overall_score") or 0.0)
                except (TypeError, ValueError):
                    score = 0.0
                if score >= min_trust:
                    trusted.append(p)

            result["attempted"] = len(trusted)
            for peer in trusted:
                sent = False
                try:
                    envelope = tx.build_message(msg_type, offer, peer["agent_id"])
                    endpoints = peer.get("endpoints") or []
                    if isinstance(endpoints, str):
                        try:
                            endpoints = json.loads(endpoints)
                        except Exception:
                            endpoints = []
                    if isinstance(envelope, dict) and not envelope.get("error"):
                        for ep in endpoints:
                            try:
                                r = tx.send_to_peer(str(ep), envelope)
                                if isinstance(r, dict) and r.get("ok"):
                                    sent = True
                                    break
                            except Exception:
                                continue
                except Exception:
                    sent = False
                if sent:
                    result["announced"] += 1
                else:
                    result["failed"] += 1
            return result
        except Exception as e:
            result["error"] = str(e)
            return result

    def publish(self, prepared: Dict[str, Any]) -> Dict[str, Any]:
        """1 ensure asset registered → 2 create listing → 3 announce →
        4 {ok, post_url: local listing URL, platform_post_id: listing id}."""
        try:
            prepared = dict(prepared or {})
            opts = dict(prepared.get("options") or {})
            body = str(prepared.get("body") or "")
            assets = prepared.get("assets") or []

            own = _svc("ownership")
            mk = _svc("marketplace")
            if own is None or mk is None:
                return {"ok": False, "error": ERR_SERVICES_UNAVAILABLE}

            title = str(prepared.get("title") or "").strip()
            tags = _collect_tags(opts.get("tags"))

            # 1 — ensure the asset is registered (auto-build manifest if missing)
            asset = None
            asset_id_opt = str(opts.get("asset_id") or "").strip()
            if asset_id_opt:
                asset = own.get_asset(asset_id_opt)
            if asset is None:
                path = self._first_asset_path(assets)
                if path is None:
                    if not body.strip():
                        return {"ok": False, "error": ERR_NOTHING_TO_PUBLISH}
                    path = self._stage_body(prepared)
                    if path is None:
                        return {"ok": False, "error": ERR_STAGE_WRITE}
                asset = own.register(path, title=title or None)
            if not isinstance(asset, dict) or not asset.get("id"):
                return {"ok": False, "error": ERR_ASSET_REGISTRATION}

            # 2 — marketplace listing (per-piece license, price in mψ)
            license_offered = (_license_terms(opts.get("license"))
                               or _license_terms(asset.get("license"))
                               or "all-rights-reserved")
            price_mpsi = max(0, _to_int(opts.get("price_mpsi"), 0))
            vis = str(opts.get("visibility") or "public").lower()
            visibility = vis if vis in _VISIBILITIES else "public"

            listing = mk.create_listing(
                asset["id"],
                price_mpsi=price_mpsi,
                license_offered=license_offered,
                visibility=visibility,
                title=title or str(asset.get("title") or "Untitled"),
                description=body,
                preview_url=str(opts.get("preview_url") or "") or None,
                media_type=str(asset.get("media_type") or "") or None,
            )
            if not isinstance(listing, dict) or not listing.get("id"):
                return {"ok": False, "error": ERR_LISTING_FAILED}
            listing_id = str(listing["id"])
            post_url = self._listing_url(listing_id)

            # policy-pack tags travel with the listing (local sidecar + offer)
            _init_stats_row(listing_id, tags)

            # 3 — encrypted CONTENT_OFFER to trusted peers (zero peers is fine)
            offer = {
                "listing_id": listing_id,
                "asset_id": asset["id"],
                "content_hash": asset.get("content_hash"),
                "title": listing.get("title"),
                "description": body[:OFFER_DESC_LIMIT],
                "license": license_offered,
                "price_mpsi": price_mpsi,
                "currency": "PSI",
                "visibility": visibility,
                "media_type": listing.get("media_type") or "",
                "preview_url": listing.get("preview_url") or "",
                "tags": tags,
                "listing_url": post_url,
                "created_at": listing.get("created_at") or _now_iso(),
            }
            announce = self._announce(offer)

            self.consume_budget(1)
            self._audit("publish", listing_id=listing_id,
                        announced=announce.get("announced", 0))

            # 4 — local listing URL + listing id
            return {
                "ok": True,
                "post_url": post_url,
                "platform_post_id": listing_id,
                "raw": {
                    "listing": listing,
                    "asset_id": asset["id"],
                    "tags": tags,
                    "announce": announce,
                },
            }
        except Exception as e:
            self._last_error = str(e)
            return {"ok": False, "error": str(e)}

    def delete(self, platform_post_id: str) -> Dict[str, Any]:
        """Native takedown: remove the marketplace listing."""
        try:
            lid = str(platform_post_id or "").strip()
            mk = _svc("marketplace")
            if mk is None:
                return {"ok": False, "error": ERR_SERVICES_UNAVAILABLE}
            if not lid:
                return {"ok": False, "error": ERR_DELETE_FAILED}
            removed = bool(mk.remove_listing(lid))
            if removed:
                self._audit("delete", listing_id=lid)
                return {"ok": True, "deleted": True}
            return {"ok": False, "deleted": False, "error": ERR_DELETE_FAILED}
        except Exception as e:
            self._last_error = str(e)
            return {"ok": False, "error": str(e)}

    # ── analytics path (§8.2 Federation column) ──────────────────────────────
    def fetch_metrics(self, platform_post_id: str) -> Optional[Dict[str, Any]]:
        """listing_views→impressions, tips→likes, peer_messages→comments,
        peer_relays→shares, fetches→clicks, new_peers→follows_gained; plus
        purchases/revenue from complete_purchase receipts. None = unknown id."""
        try:
            lid = str(platform_post_id or "").strip()
            if not lid:
                return None
            stats = _read_stats(lid)
            mk = _svc("marketplace")
            listing = None
            if mk is not None:
                try:
                    listing = mk.get_listing(lid)
                except Exception:
                    listing = None
            if stats is None and listing is None:
                return None                      # unknown listing — honest None

            s = stats or {}
            out: Dict[str, Any] = {
                "impressions": _to_int(s.get("views"), 0),
                "likes": _to_int(s.get("tips_count"), 0),
                "comments": _to_int(s.get("peer_messages"), 0),
                "shares": _to_int(s.get("peer_relays"), 0),
                "clicks": _to_int(s.get("fetches"), 0),
                "follows_gained": _to_int(s.get("new_peers"), 0),
                "tips_count": _to_int(s.get("tips_count"), 0),
                "tips_mpsi": _to_int(s.get("tips_mpsi"), 0),
            }
            # saves / video_views / watch_time_s are unreportable here —
            # absent, not zero (§8.2: missing ≠ zero).
            if mk is not None:
                purchases, revenue = _count_completed_purchases(mk, lid)
                if purchases is not None:
                    out["purchases"] = purchases
                    out["revenue_mpsi"] = revenue
            return out
        except Exception as e:
            self._last_error = str(e)
            return None

    def fetch_account_metrics(self) -> Optional[Dict[str, Any]]:
        """Audience = the peer graph."""
        try:
            fed = _svc("federation")
            if fed is None:
                return None
            peers = fed.get_peers() or []
            try:
                min_trust = float(self._config.get("min_peer_trust", 0.5))
            except (TypeError, ValueError):
                min_trust = 0.5
            trusted = 0
            for p in peers:
                try:
                    if float((p or {}).get("overall_score") or 0.0) >= min_trust:
                        trusted += 1
                except (TypeError, ValueError):
                    continue
            return {"peers": len(peers), "trusted_peers": trusted}
        except Exception:
            return None

    # ── engagement event API (used by routes / federation inbox) ─────────────
    def record_event(self, listing_id: str, event: str, n: int = 1,
                     amount_mpsi: int = 0) -> Dict[str, Any]:
        return record_event(listing_id, event, n=n, amount_mpsi=amount_mpsi)


ADAPTER = FederationAdapter

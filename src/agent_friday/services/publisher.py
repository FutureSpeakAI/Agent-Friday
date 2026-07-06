"""
Agent Friday — Content Publication Engine (docs/CONTENT_PIPELINE_SPEC.md §7)
FutureSpeak.AI · Asimov's Mind

The dispatch half of the pipeline: one scheduler builtin (`content_publisher`,
1-minute interval — D2) whose `tick()` claims due targets from the content
store (mark-before-run, §7.2) and runs each through the §7.1 gate chain:

    1 GATE    moderation.scan (H1–H4)          blocked → FAILED, no retry
    2 GATE    egress classification of the     non-PUBLIC / gate error →
              final title/body/segments        HELD (D3: hold, never mangle)
    3 PREPARE adapter.prepare (media, hard validation)
    4 BUDGET  adapter.rate_budget              would exceed → defer, no
                                               attempt burned (§7.1 step 5)
    5 PUBLISH adapter.publish → post_url + platform_post_id
    6 RECORD  target SENT→CONFIRMED · publish_log.jsonl · provenance
              publication entry (best-effort) · ψ earn once per post (§8.7)
    7 SURFACE notification (§7.6)

Failure classes per §7.5: permanent (validation/policy) → FAILED immediately;
auth (401/not_connected) → one refresh() then FAILED with a re-auth hint;
transient (429/5xx/network) → exponential backoff ladder (300 s ×4, Retry-After
honored) up to RETRY_MAX attempts. An *ambiguous* failure (timeout after the
POST left) runs the §7.2 verify-before-retry probe: the adapter searches its
recent posts for the payload fingerprint (`verify_recent_post(prepared)`);
found → CONFIRMED without re-sending; not-found → the transient ladder; no
probe available → FAILED (a blind retry is never worth a double post).

Also here: §6.5 native-schedule delegation (arm-time push for adapters that
advertise `native_schedule` when the instant is ≥10 min out), §6.7 recurrence
cloning (each confirmed occurrence schedules the next as a fresh post with a
composer freshness hint, DST-correct via zoneinfo), and the §6.8
conflict-detection helper.

Design notes / deliberate deviations:
· Targets dispatch SEQUENTIALLY inside one tick (grouped per platform) rather
  than on per-target daemon threads — deterministic under test, and the
  running-set still guarantees one attempt in flight per target. The scheduler
  already runs the tick off the request path.
· Staleness recompose (§7.1 step 3): editing a SCHEDULED post normally pulls
  it back to DRAFT in the store (§3.2 edit edge), so the recompose here is an
  explicit-marker path (options.needs_recompose) run BEFORE the gates so they
  always scan the final outbound text.
· Recurrence (§6.7): the spec clones-then-publishes and re-arms the parent;
  we publish the due occurrence and schedule the NEXT one as the clone
  (source {kind:"recurrence", ref}, carrying the recurrence spec forward) —
  same series semantics, fresh ids per occurrence, expires_at retires it.

House rules: import-safe under FRIDAY_TESTING (no threads, no network, no
scheduler registration at import time — `start()` does that at boot); every
public helper returns an envelope and never raises; heavy deps lazy-imported.
`kick()` is inert under FRIDAY_TESTING — tests call `tick()` directly.
"""
from __future__ import annotations

import logging
import os
import threading
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from agent_friday.services import content_pipeline as store
from agent_friday.services import platforms as platform_registry

_log = logging.getLogger("friday.publisher")

# ── §7.5 retry envelope (scheduler shape): 5 min → 20 min → 80 min ───────────
RETRY_MAX = 3
RETRY_BACKOFF_S = 300
RETRY_MULTIPLIER = 4

# Fixed adapter error strings (§12.5) classified per §7.5.
_TRANSIENT_ERRORS = {"rate_limited", "network_error", "platform_http_error"}
_AUTH_ERRORS = {"auth_error", "not_connected"}
_BUDGET_ERRORS = {"rate_budget_exhausted"}
# Ambiguous outcomes (§7.2): the POST left but no answer came back — the
# verify-before-retry probe decides between CONFIRMED and a safe retry.
_AMBIGUOUS_ERRORS = {"ambiguous_timeout", "timeout_ambiguous"}

# §6.5: delegate to the platform's native scheduler when the instant is
# at least this far out.
NATIVE_DELEGATE_MIN_S = 10 * 60

# One publish attempt in flight per target (§7.2), and one tick at a time.
_RUNNING_LOCK = threading.Lock()
_RUNNING: set = set()
_TICK_LOCK = threading.Lock()


def _testing() -> bool:
    return bool(os.environ.get("FRIDAY_TESTING"))


# ─────────────────────────────────────────────────────────────────────────────
#  Gate seams — resolved lazily at call time so test monkeypatching lands
# ─────────────────────────────────────────────────────────────────────────────

def _moderation_scan(text: str) -> Optional[Dict[str, Any]]:
    """H1–H4 harm floor (§7.1 step 1). None = scanner unavailable (the caller
    fails closed → HELD, §7.3: a gate that cannot prove itself operational
    blocks publishing)."""
    from agent_friday.services import moderation
    verdict = moderation.scan(content_text=text)
    return verdict if isinstance(verdict, dict) else None


def _gate(text: str, provider: str, field: str) -> str:
    """Egress classification of a final outbound string (§7.3)."""
    from agent_friday.services import egress_gate
    return egress_gate.gate_text(text, provider, field)


# ─────────────────────────────────────────────────────────────────────────────
#  Best-effort side rails — notifications, provenance, ψ
# ─────────────────────────────────────────────────────────────────────────────

def _notify(title: str, body: str = "", priority: str = "medium",
            actions: Optional[List[Dict[str, Any]]] = None,
            dedupe: Optional[str] = None) -> None:
    try:
        import agent_friday.notifications_engine as _ne
        _ne.push(title=title, body=body, priority=priority, source="content",
                 kind="content_publish", actions=actions, dedupe_key=dedupe)
    except Exception:
        pass


def _queue_action() -> List[Dict[str, Any]]:
    return [{"label": "Review in Content → Queue", "action": "navigate",
             "target": {"workspace": "content", "tab": "queue"}}]


def _self_agent_id() -> str:
    try:
        from agent_friday.services.federation import get_identity
        return str((get_identity() or {}).get("agent_id") or "self")
    except Exception:
        return "self"


def _record_provenance(post: Dict[str, Any], target: Dict[str, Any],
                       pub: Dict[str, Any]) -> None:
    """§7.7: sign a publication entry onto the asset's manifest history when
    the provenance API exposes it (add_publication is spec-NEW; absent = skip,
    the publish_log line remains the local record either way)."""
    try:
        content_hash = str(post.get("provenance_hash") or "")
        if not content_hash:
            return
        from agent_friday.services import provenance
        fn = getattr(provenance, "add_publication", None)
        if callable(fn):
            fn(content_hash, {
                "platform": target.get("platform"),
                "post_url": pub.get("post_url"),
                "platform_post_id": pub.get("platform_post_id"),
                "target_id": target.get("id"),
                "published_at": store._now_iso(),
            })
    except Exception:
        _log.debug("provenance publication entry failed", exc_info=True)


def _record_distribution(post: Dict[str, Any], target: Dict[str, Any],
                         pub: Dict[str, Any]) -> None:
    """§7.7(3): the asset's ownership record gains a distribution row so
    verify() and the provenance-chain UI show where copies legitimately live.
    Best-effort seam — absent API = skip (the publish_log line remains)."""
    try:
        content_hash = str(post.get("provenance_hash") or "")
        if not content_hash:
            return
        from agent_friday.services import ownership
        fn = (getattr(ownership, "add_distribution", None)
              or getattr(ownership, "record_distribution", None))
        if not callable(fn):
            return
        asset = None
        get_by_hash = getattr(ownership, "get_asset_by_hash", None)
        if callable(get_by_hash):
            asset = get_by_hash(content_hash)
        fn(str((asset or {}).get("id") or content_hash), {
            "platform": target.get("platform"),
            "url": pub.get("post_url"),
            "platform_post_id": pub.get("platform_post_id"),
            "date": store._now_iso(),
        })
    except Exception:
        _log.debug("ownership distribution event failed", exc_info=True)


def _earn_publish_psi(post: Dict[str, Any], target: Dict[str, Any]) -> None:
    """PSI_CREATE_CONTENT fires once per post on its first confirmation —
    idempotent via the psi_awards table (§8.7 key discipline)."""
    try:
        from agent_friday.services.economy import PSI_CREATE_CONTENT, earn
        res = store.award_psi(str(post.get("id")), "publish", 0,
                              PSI_CREATE_CONTENT)
        if res.get("ok") and res.get("awarded"):
            earn(_self_agent_id(), PSI_CREATE_CONTENT,
                 reason=f"content:publish:{target.get('platform')}")
    except Exception:
        _log.debug("publish ψ earn failed", exc_info=True)


def _license_ok(post: Dict[str, Any]) -> Optional[str]:
    """§7.7(5) license guard, best-effort: returns a block reason or None.
    Only assets actually present in the ownership registry are guarded —
    an ordinary unregistered creation must never be blocked (the registry's
    'not found' verdict is allowed=False, which is about derivation rights,
    not about publishing your own work)."""
    try:
        from agent_friday.services import ownership
        get_asset = getattr(ownership, "get_asset", None)
        check = getattr(ownership, "check_license_compat", None)
        if not (callable(get_asset) and callable(check)):
            return None
        content_hash = str(post.get("provenance_hash") or "")
        if not content_hash:
            return None
        asset = get_asset(content_hash)
        if not asset:
            return None
        wanted = str(((post.get("license") or {}).get("terms"))
                     or asset.get("license") or "all-rights-reserved")
        verdict = check(content_hash, wanted)
        if isinstance(verdict, dict) and verdict.get("allowed") is False:
            return str(verdict.get("note") or "license incompatible")
    except Exception:
        pass                          # guard unavailable → don't block
    return None


def _log_attempt(target: Dict[str, Any], outcome: str, *,
                 duration_s: float = 0.0, url: Optional[str] = None,
                 error: Optional[str] = None) -> None:
    store.append_publish_log({
        "event": "publish", "outcome": outcome,
        "target_id": target.get("id"), "post_id": target.get("post_id"),
        "platform": target.get("platform"),
        "attempt": target.get("attempt"),
        "duration_s": round(float(duration_s), 3),
        "url": url, "error": error,
        "ok": outcome == "confirmed",
    })


def _epoch(value) -> Optional[float]:
    dt = store._parse_instant(value)
    return dt.timestamp() if dt else None


# ─────────────────────────────────────────────────────────────────────────────
#  Lifecycle
# ─────────────────────────────────────────────────────────────────────────────

def start() -> Dict[str, Any]:
    """Boot hook (server.py): register the §6.2 scheduler builtins. Never
    raises; safe to call more than once. Also starts the §8 analytics
    collector's builtins so one boot line wires the whole pipeline."""
    out: Dict[str, Any] = {"ok": True, "registered": []}
    try:
        from agent_friday.services.scheduler import register_builtin_task
        register_builtin_task(
            "content_publisher", tick, label="Content publisher",
            default_trigger="interval", default_spec={"every_minutes": 1},
            notify="silent")
        out["registered"].append("content_publisher")
    except Exception as e:
        out["ok"] = False
        out["error"] = str(e)
    try:
        from agent_friday.services import analytics_collector
        res = analytics_collector.start()
        out["registered"] += list((res or {}).get("registered") or [])
    except Exception as e:
        out.setdefault("warnings", []).append(f"analytics collector: {e}")
    return out


def kick() -> Dict[str, Any]:
    """Best-effort immediate dispatch (publish-now / release routes): a
    one-shot background tick so the HTTP response never blocks on platform
    I/O. Inert under FRIDAY_TESTING (house rule: no threads in tests — the
    suite calls tick() directly)."""
    if _testing():
        return {"kicked": False,
                "reason": "inert under FRIDAY_TESTING — call tick() directly"}
    try:
        threading.Thread(target=tick, name="content-publisher-kick",
                         daemon=True).start()
        return {"kicked": True, "via": "thread"}
    except Exception as e:
        return {"kicked": False, "reason": str(e)}


# ─────────────────────────────────────────────────────────────────────────────
#  The tick (§6.2 / §7.1)
# ─────────────────────────────────────────────────────────────────────────────

def tick(now=None) -> Dict[str, Any]:
    """One publisher pass: expire stale holds, recover crashed claims, claim
    due targets, dispatch each through the gate chain. Returns a summary
    envelope; never raises. Re-entrant calls coalesce (one tick at a time)."""
    if not _TICK_LOCK.acquire(blocking=False):
        return {"ok": True, "skipped": "tick already running"}
    try:
        if platform_registry.publishing_paused():
            return {"ok": True, "paused": True, "claimed": 0}
        store.expire_stale_holds(now)                     # §3.2 7-day rail
        with _RUNNING_LOCK:
            live = set(_RUNNING)
        store.recover_stale_claims(now, exclude=live)     # crash recovery
        # §7.2 restart-time verify: a crash between SENT and confirm/fail
        # would otherwise wedge the target forever (and lose a publish that
        # actually landed platform-side). Resolved via the verify probe,
        # never a blind retry.
        sent_counts: Dict[str, int] = {}
        sent_confirmed_posts: List[str] = []
        try:
            sent_counts, sent_confirmed_posts = _recover_sent_limbo(
                now, exclude=live)
        except Exception:
            _log.debug("SENT limbo recovery failed", exc_info=True)
        delegated = 0
        try:
            delegated = _delegate_native(now)             # §6.5 arm-time push
        except Exception:
            _log.debug("native delegation pass failed", exc_info=True)
        res = store.claim_due_targets(now)
        if not res.get("ok"):
            return res
        targets = list(res.get("targets") or [])
        # Per-platform serialization: group so one platform's slowness never
        # interleaves badly with its own thread jitter.
        targets.sort(key=lambda t: (str(t.get("platform")), str(t.get("id"))))
        counts: Dict[str, int] = {}
        confirmed_posts: List[str] = []
        for t in targets:
            outcome = _dispatch_target(t)
            counts[outcome] = counts.get(outcome, 0) + 1
            if outcome == "confirmed":
                confirmed_posts.append(str(t.get("post_id")))
        # §6.7 recurrence: once per post that confirmed this pass, schedule
        # the next occurrence as a fresh clone (one bad target never kills
        # the series — rearm handles the rest). Limbo-recovered confirmations
        # count too — their publish really happened.
        for pid in dict.fromkeys(confirmed_posts + sent_confirmed_posts):
            try:
                _handle_recurrence(pid, now)
            except Exception:
                _log.debug("recurrence clone failed for %s", pid, exc_info=True)
        out = {"ok": True, "claimed": len(targets), "outcomes": counts}
        if delegated:
            out["native_delegated"] = delegated
        if sent_counts:
            out["sent_recovered"] = sent_counts
        return out
    except Exception as e:
        _log.debug("publisher tick failed", exc_info=True)
        return {"ok": False, "error": str(e)}
    finally:
        _TICK_LOCK.release()


run_once = tick                        # route/back-compat alias


def _dispatch_target(target: Dict[str, Any]) -> str:
    tid = str(target.get("id") or "")
    with _RUNNING_LOCK:
        if tid in _RUNNING:
            return "in_flight"
        _RUNNING.add(tid)
    try:
        return _run_target(target)
    except Exception as e:
        # Belt-and-suspenders: _run_target shouldn't raise, but a surprise
        # must not leave the target stuck PREPARING (finding: crash limbo).
        try:
            store.set_target_status(tid, "FAILED", error=f"publisher error: {e}")
        except Exception:
            pass
        return "failed"
    finally:
        with _RUNNING_LOCK:
            _RUNNING.discard(tid)


def _recover_sent_limbo(now=None, exclude=None):
    """§7.2 restart-time verify for targets stuck SENT: the process died
    after the adapter call but before confirm/fail, so the platform may or
    may not have published. NEVER blind-retry from here — probe the
    adapter's recent posts: found → CONFIRMED (recording the publish that
    actually happened); probe says nothing landed → the transient ladder;
    no probe → FAILED loudly (a wedged target beats a double post, and
    FAILED is one-tap re-armable while SENT was uncancellable limbo).

    Returns (outcome_counts, confirmed_post_ids)."""
    counts: Dict[str, int] = {}
    confirmed_posts: List[str] = []
    res = store.list_stale_sent_targets(now, exclude=exclude)
    if not res.get("ok"):
        return counts, confirmed_posts
    for target in res.get("targets") or []:
        tid = str(target.get("id"))
        platform = str(target.get("platform") or "")
        adapter = platform_registry.get_adapter(platform)
        probe = getattr(adapter, "verify_recent_post", None) \
            if adapter is not None else None
        found = None
        if callable(probe):
            try:
                # The stored payload is the probe's fingerprint (§7.2).
                found = probe({"title": target.get("adapted_title"),
                               "snippet": {"title": target.get("adapted_title")},
                               "body": target.get("adapted_body")})
            except Exception:
                found = None
        if isinstance(found, dict) and (found.get("platform_post_id")
                                        or found.get("post_url")):
            post = store.get_post(str(target.get("post_id"))).get("post") or {}
            _confirm(target, post, adapter,
                     {"ok": True, "post_url": found.get("post_url"),
                      "platform_post_id": found.get("platform_post_id")},
                     time.time())
            counts["confirmed"] = counts.get("confirmed", 0) + 1
            confirmed_posts.append(str(target.get("post_id")))
            continue
        attempt = int(target.get("attempt") or 1)
        if callable(probe) and attempt < RETRY_MAX:
            err = "interrupted mid-send; verify probe found no post"
            store.set_target_status(tid, "PENDING", error=err,
                                    not_before=time.time() + RETRY_BACKOFF_S)
            _log_attempt(target, "retrying", error=err)
            counts["retrying"] = counts.get("retrying", 0) + 1
            continue
        reason = ("interrupted mid-send"
                  + ("; retries exhausted" if callable(probe)
                     else "; no verify probe, refusing blind retry (§7.2)"))
        store.set_target_status(tid, "FAILED", error=reason)
        _log_attempt(target, "failed", error=reason)
        _notify(f"Publish failed — {platform}", reason, priority="high",
                actions=_queue_action(), dedupe=f"content-failed:{tid}")
        counts["failed"] = counts.get("failed", 0) + 1
    return counts, confirmed_posts


def _final_texts(target: Dict[str, Any], post: Dict[str, Any]) -> List[str]:
    """The exact outbound strings the gates must clear (§12.1) — including
    every fallback the adapters apply (youtube/federation_pub fall back to
    post.title when adapted_title is empty) and the tag/mention side-channels
    (youtube snippet tags, federation listing tags) that egress as text."""
    opts = target.get("options") or {}
    texts = [str(target.get("adapted_title") or post.get("title") or ""),
             str(target.get("adapted_body") or post.get("body") or "")]
    texts += [str(s) for s in (target.get("segments") or []) if s]
    extras = ([str(h) for h in (target.get("hashtags") or [])]
              + [str(m) for m in (target.get("mentions") or [])]
              + [str(t) for t in (opts.get("tags") or [])])
    extras = [x for x in extras if x.strip()]
    if extras:
        texts.append(" ".join(extras))
    return [t for t in texts if t.strip()]


def _hold(target: Dict[str, Any], reason: str) -> str:
    """D3: hold-for-review, never silent redaction. Fail-closed on gate
    errors too."""
    tid = str(target.get("id"))
    store.set_target_status(tid, "HELD", error=reason)
    store.append_publish_log({
        "event": "held", "target_id": tid, "post_id": target.get("post_id"),
        "platform": target.get("platform"), "reason": reason, "ok": False})
    _notify("Post held — review before publishing",
            f"{target.get('platform')}: {reason}", priority="high",
            actions=_queue_action(), dedupe=f"content-held:{tid}")
    return "held"


def _run_target(target: Dict[str, Any]) -> str:
    tid = str(target.get("id"))
    platform = str(target.get("platform") or "")
    started = time.time()

    got = store.get_post(str(target.get("post_id")))
    if not got.get("ok"):
        store.set_target_status(tid, "FAILED", error="post not found")
        return "failed"
    post = got["post"]

    # 3 STALENESS (§7.1 — run FIRST so the gates always scan the final text):
    # edits normally pull a SCHEDULED post back to DRAFT (§3.2), so this is
    # the explicit-marker path — a target flagged options.needs_recompose is
    # re-adapted by the composer before dispatch. Best-effort: a recompose
    # failure dispatches the existing payload rather than wedging the target.
    if (target.get("options") or {}).get("needs_recompose"):
        try:
            from agent_friday.services import content_composer
            content_composer.adapt(post, [target.get("platform")],
                                   regenerate=True)
            refreshed = store.get_target(tid).get("target")
            if refreshed:
                target = refreshed
        except Exception:
            _log.debug("staleness recompose failed", exc_info=True)

    texts = _final_texts(target, post)

    # §7.3 human release ('publish anyway — this is intentional'): honored by
    # the H3 hold rail and the egress-divergence hold ONLY. The hard harm
    # floor (H1/H2/H4) and gate-infrastructure failures are never releasable —
    # without this marker a released target just re-holds forever.
    released = bool((target.get("options") or {}).get("released"))

    # 1 GATE — moderation H1–H4 (§7.1). Blocked = permanent, no retry (§7.5);
    # an inoperable scanner fails closed to HELD (§7.3).
    try:
        verdict = _moderation_scan("\n\n".join(texts))
    except Exception:
        verdict = None
    if verdict is None:
        return _hold(target, "moderation scan unavailable — held fail-closed")
    if verdict.get("blocked"):
        reason = f"moderation: {verdict.get('reason') or 'blocked'}"
        if verdict.get("harm_level") == "H3":
            # H3 = doxxing / PII exposure — exactly the §7.3/§12.7
            # hold-for-review class ('publish anyway, this is intentional'
            # is a legitimate human release for e.g. an educational post).
            # The hard harm floors (H1/H2/H4) stay permanent FAILED below.
            if not released:
                return _hold(target, reason)
            store.append_publish_log({
                "event": "release_override", "target_id": tid,
                "post_id": target.get("post_id"), "platform": platform,
                "reason": reason, "ok": True})
        else:
            store.set_target_status(tid, "FAILED", error=reason)
            _log_attempt(target, "blocked", duration_s=time.time() - started,
                         error=reason)
            _notify("Post blocked by the harm floor",
                    f"{platform}: {verdict.get('reason') or 'blocked'}",
                    priority="high", actions=_queue_action(),
                    dedupe=f"content-blocked:{tid}")
            return "failed"

    # 2 GATE — egress classification of every final outbound string (§7.3).
    # Any divergence (or gate error) → HELD; explicit human release required.
    provider = f"platform_{platform}"
    try:
        for txt in texts:
            if _gate(txt, provider, "post.body") != txt:
                if released:
                    store.append_publish_log({
                        "event": "release_override", "target_id": tid,
                        "post_id": target.get("post_id"),
                        "platform": platform,
                        "reason": "egress divergence — released by user",
                        "ok": True})
                    break
                return _hold(target,
                             "post held — it looks like it contains private data")
    except Exception:
        return _hold(target, "egress gate error — held fail-closed")

    # (3 STALENESS — see module notes: content edits pull SCHEDULED posts back
    #  to DRAFT in the store, so a claimed payload cannot be stale.)

    adapter = platform_registry.get_adapter(platform)
    if adapter is None:
        reason = platform_registry.import_error(platform) \
            or f"adapter unavailable: {platform}"
        store.set_target_status(tid, "FAILED", error=reason)
        _log_attempt(target, "failed", error=reason)
        return "failed"

    # License guard (§7.7.5) — incompatible license blocks with the reason.
    license_block = _license_ok(post)
    if license_block:
        store.set_target_status(tid, "FAILED", error=f"license: {license_block}")
        _log_attempt(target, "failed", error=f"license: {license_block}")
        _notify("Post blocked — license incompatible", license_block,
                priority="high", actions=_queue_action())
        return "failed"

    # 5 BUDGET pre-check — defer without burning the attempt (§7.1 step 5).
    try:
        if adapter.budget_would_exceed():
            budget = adapter.rate_budget()
            store.defer_target(tid, _epoch(budget.get("reset_at"))
                               or time.time() + 3600)
            return "deferred"
    except Exception:
        pass

    # 4 PREPARE — media transforms/uploads + the adapter's hard validation
    # (asset staging order per §7.4 and outbound-copy credential embedding
    # per §7.7.1 live inside the adapter's prepare()).
    prep = adapter.prepare(target, post)
    prep = prep if isinstance(prep, dict) else {"ok": False, "error": "prepare_failed"}
    if not prep.get("ok"):
        return _handle_failure(target, adapter, prep, stage="prepare",
                               started=started, post=post)
    prepared = prep.get("prepared") or {}

    # §6.5 native delegation: hand the platform its own scheduled instant.
    native = bool(target.get("_native_delegate"))
    if native and prepared.get("native_scheduled") is False:
        # The adapter declined platform-side scheduling (its minimum lead
        # decayed while the gates ran). Publishing now would go live EARLY —
        # refund the claim and let the due-scan send it at the real instant.
        store.defer_target(tid, 0)
        return "deferred"
    if native:
        prepared = dict(prepared)
        prepared["scheduled_at"] = target.get("publish_at") \
            or (post.get("schedule") or {}).get("publish_at")
        opts = dict(prepared.get("options") or {})
        opts["scheduled_at"] = prepared["scheduled_at"]
        prepared["options"] = opts

    # 6 PUBLISH — mark SENT first (§3.2), then the adapter call.
    store.set_target_status(tid, "SENT")
    pub = adapter.publish(prepared)
    pub = pub if isinstance(pub, dict) else {"ok": False, "error": "publish_failed"}
    if pub.get("ok"):
        # The adapter's word beats the arm-time flag: a publish the platform
        # did NOT schedule must never be recorded or announced as native.
        return _confirm(target, post, adapter, pub, started,
                        native=native and pub.get("native_scheduled") is not False)
    return _handle_failure(target, adapter, pub, stage="publish",
                           started=started, post=post, prepared=prepared)


def _confirm(target: Dict[str, Any], post: Dict[str, Any], adapter,
             pub: Dict[str, Any], started: float, *, native: bool = False) -> str:
    """§7.1 step 7–8 RECORD + SURFACE: CONFIRMED status (rollup + published_at
    in the store), publish_log line, signed provenance publication entry,
    ownership distribution event, ψ earn once per post, notification. The §8.1
    analytics poll plan is stateless — confirmation itself arms it (snapshot
    count vs published_at drives the collector's decaying cadence)."""
    tid = str(target.get("id"))
    platform = str(target.get("platform") or "")
    if native:
        try:  # before CONFIRMED — a confirmed target is immutable (§7.2)
            opts = dict(target.get("options") or {})
            opts["native_scheduled"] = True
            store.update_target(tid, {"options": opts})
        except Exception:
            pass
    store.set_target_status(
        tid, "CONFIRMED",
        post_url=pub.get("post_url"),
        platform_post_id=pub.get("platform_post_id"))
    _log_attempt(target, "confirmed", duration_s=time.time() - started,
                 url=pub.get("post_url"))
    _record_provenance(post, target, pub)                 # §7.7.2
    _record_distribution(post, target, pub)               # §7.7.3
    _earn_publish_psi(post, target)                       # §8.7 / D10
    label = getattr(adapter, "label", platform)
    _notify((f"Scheduled natively on {label}" if native
             else f"✓ Published to {label}"),
            str(pub.get("post_url") or ""), priority="low",
            dedupe=f"content-published:{tid}")
    return "confirmed"


def _handle_failure(target: Dict[str, Any], adapter, env: Dict[str, Any],
                    *, stage: str, started: float,
                    post: Optional[Dict[str, Any]] = None,
                    prepared: Optional[Dict[str, Any]] = None) -> str:
    """§7.5 error-class routing. `stage` tells us the target's current state:
    prepare failures leave it PREPARING, publish failures leave it SENT."""
    tid = str(target.get("id"))
    err = str(env.get("error") or f"{stage}_failed")
    attempt = int(target.get("attempt") or 1)
    duration = time.time() - started

    # §7.2 ambiguous outcome (the POST left, no answer): verify before ANY
    # retry. Probe finds the post → CONFIRMED, never re-sent. Probe says
    # nothing landed → the transient ladder is safe. No probe → FAILED; a
    # blind retry is never worth a double post.
    if stage == "publish" and (err in _AMBIGUOUS_ERRORS or env.get("ambiguous")):
        probe = getattr(adapter, "verify_recent_post", None)
        if callable(probe):
            found = None
            try:
                found = probe(prepared or {})
            except Exception:
                found = None
            if isinstance(found, dict) and (found.get("platform_post_id")
                                            or found.get("post_url")):
                return _confirm(target, post or {}, adapter,
                                {"ok": True,
                                 "post_url": found.get("post_url"),
                                 "platform_post_id": found.get("platform_post_id")},
                                started)
            if attempt < RETRY_MAX:      # verified not-landed → safe retry
                backoff = RETRY_BACKOFF_S * (RETRY_MULTIPLIER ** max(attempt - 1, 0))
                store.set_target_status(tid, "PENDING", error=err,
                                        not_before=time.time() + backoff)
                _log_attempt(target, "retrying", duration_s=duration, error=err)
                return "retrying"
        reason = (f"{err} — ambiguous outcome"
                  + ("" if callable(probe)
                     else "; no verify probe, refusing blind retry (§7.2)"))
        store.set_target_status(tid, "FAILED", error=reason)
        _log_attempt(target, "failed", duration_s=duration, error=reason)
        _notify(f"Publish failed — {target.get('platform')}", reason,
                priority="high", actions=_queue_action(),
                dedupe=f"content-failed:{tid}")
        return "failed"

    if err in _BUDGET_ERRORS:
        # Never burns an attempt: the adapter refused before sending anything.
        defer_until = _epoch(env.get("defer_until")) or time.time() + 3600
        if stage == "prepare":
            store.defer_target(tid, defer_until)
        else:
            store.set_target_status(tid, "PENDING", error=err,
                                    not_before=defer_until)
        return "deferred"

    if err in _AUTH_ERRORS:
        # One refresh() attempt (§7.5 auth class), then FAILED with a
        # re-auth deep link — expiry should surface loudly, not spin.
        refreshed = False
        try:
            refreshed = bool(adapter.refresh())
        except Exception:
            refreshed = False
        if refreshed and attempt <= RETRY_MAX:
            store.set_target_status(tid, "PENDING", error=err,
                                    not_before=time.time() + 5)
            _log_attempt(target, "retrying", duration_s=duration, error=err)
            return "retrying"
        reason = f"{err} — reconnect {target.get('platform')} in Accounts"
        store.set_target_status(tid, "FAILED", error=reason)
        _log_attempt(target, "failed", duration_s=duration, error=reason)
        _notify(f"Publish failed — {target.get('platform')} needs re-auth",
                reason, priority="high",
                actions=[{"label": "Open Accounts", "action": "navigate",
                          "target": {"workspace": "content", "tab": "accounts"}}],
                dedupe=f"content-auth:{target.get('platform')}")
        return "failed"

    if err in _TRANSIENT_ERRORS and attempt < RETRY_MAX:
        # Backoff ladder ×4, platform Retry-After honored (§7.5). A network
        # error only lands here when the adapter is sure nothing was
        # published (an adapter whose send MAY have landed sets
        # env["ambiguous"] and takes the §7.2 verify branch above; adapter
        # idempotency — thread resume, Idempotency-Key — covers the rest).
        try:
            backoff = float(env.get("retry_after"))
        except (TypeError, ValueError):
            backoff = RETRY_BACKOFF_S * (RETRY_MULTIPLIER ** max(attempt - 1, 0))
        store.set_target_status(tid, "PENDING", error=err,
                                not_before=time.time() + backoff)
        _log_attempt(target, "retrying", duration_s=duration, error=err)
        return "retrying"

    # Permanent (validation/policy/4xx) or retries exhausted → FAILED loudly
    # with one-tap re-arm guidance (§7.5).
    store.set_target_status(tid, "FAILED", error=err)
    _log_attempt(target, "failed", duration_s=duration, error=err)
    _notify(f"Publish failed — {target.get('platform')}", err, priority="high",
            actions=_queue_action(), dedupe=f"content-failed:{tid}")
    return "failed"


# ─────────────────────────────────────────────────────────────────────────────
#  §6.3/§6.7 recurrence — DST-correct timezone math via zoneinfo
# ─────────────────────────────────────────────────────────────────────────────

def _zone(name: Optional[str]):
    from zoneinfo import ZoneInfo
    try:
        return ZoneInfo(name or "UTC")
    except Exception:
        return timezone.utc


def _cron_fields(expr: str) -> Optional[List[set]]:
    """Parse a 5-field cron (min hour dom mon dow) into value sets.
    Supports *, N, N-M, */S, N-M/S, comma lists. None = unparseable."""
    ranges = [(0, 59), (0, 23), (1, 31), (1, 12), (0, 6)]
    parts = str(expr or "").split()
    if len(parts) != 5:
        return None
    out: List[set] = []
    for part, (lo, hi) in zip(parts, ranges):
        vals: set = set()
        try:
            for piece in part.split(","):
                step = 1
                if "/" in piece:
                    piece, s = piece.split("/", 1)
                    step = max(1, int(s))
                if piece in ("*", ""):
                    a, b = lo, hi
                elif "-" in piece:
                    a, b = (int(x) for x in piece.split("-", 1))
                else:
                    a = b = int(piece)
                vals.update(v for v in range(a, b + 1, step) if lo <= v <= hi)
        except (TypeError, ValueError):
            return None
        if not vals:
            return None
        out.append(vals)
    return out


def _cron_next(expr: str, tz, after: datetime) -> Optional[datetime]:
    """First instant matching the cron expr strictly after `after`, evaluated
    in the post's timezone (minute floor, §6.7). Bounded ~370-day scan."""
    fields = _cron_fields(expr)
    if fields is None:
        return None
    mins, hours, doms, mons, dows = fields
    local = after.astimezone(tz)
    day = local.date()
    for _ in range(371):
        # cron dow: 0=Sunday … 6=Saturday; Python weekday(): 0=Monday.
        if day.month in mons and day.day in doms \
                and ((day.weekday() + 1) % 7) in dows:
            for h in sorted(hours):
                for m in sorted(mins):
                    try:
                        cand = datetime(day.year, day.month, day.day, h, m,
                                        tzinfo=tz)
                    except (ValueError, OverflowError):
                        continue
                    cu = cand.astimezone(timezone.utc)
                    if cu > after:
                        return cu
        day = day + timedelta(days=1)
    return None


def _next_occurrence(sched: Dict[str, Any], after: datetime) -> Optional[datetime]:
    """Next UTC instant of a recurring schedule strictly after `after`.
    Expansion happens on the wall clock of the post's IANA timezone, then
    converts — daylight-saving correct by construction (§6.3)."""
    rec = str(sched.get("recurrence") or "none")
    if rec == "none":
        return None
    tz = _zone(sched.get("timezone"))
    base = store._parse_instant(sched.get("publish_at")) or after
    if rec == "custom_cron":
        expr = (sched.get("recurrence_spec") or {}).get("cron", "")
        return _cron_next(expr, tz, max(after, base))
    step = 7 if rec == "weekly" else 1
    local = base.astimezone(tz)
    day = local.date()
    for _ in range(400):
        day = day + timedelta(days=step)
        try:
            cand = datetime(day.year, day.month, day.day,
                            local.hour, local.minute, tzinfo=tz)
        except (ValueError, OverflowError):
            continue
        cu = cand.astimezone(timezone.utc)
        if cu > after and cu > base:
            return cu
    return None


FRESHNESS_HINT = ("vary the phrasing — don't repeat the previous occurrence's "
                  "wording (platform duplicate-content guard)")


def _handle_recurrence(post_id: str, now=None) -> Optional[str]:
    """§6.7: a confirmed recurring post spawns its next occurrence as a fresh
    clone (source {kind:"recurrence", ref}, carrying the recurrence forward),
    optionally recomposed with a freshness hint. expires_at retires the
    series. Idempotent per (parent, instant). Returns the clone id or None."""
    got = store.get_post(post_id)
    if not got.get("ok"):
        return None
    post = got["post"]
    sched = dict(post.get("schedule") or {})
    if str(sched.get("recurrence") or "none") == "none":
        return None
    now_dt = store._parse_instant(now) or store._now_dt()
    nxt = _next_occurrence(sched, now_dt)
    if nxt is None:
        return None
    exp = store._parse_instant(sched.get("expires_at"))
    if exp and nxt > exp:
        return None                              # template retired (§6.7)
    nxt_iso = nxt.strftime("%Y-%m-%dT%H:%M:%SZ")
    # Idempotency: one clone per (parent, instant) — SQL-side probe with no
    # pagination window (a LIMIT-blinded list_posts scan missed old clones
    # and forked the series into permanent double posts). Fail-CLOSED: an
    # unreadable probe skips this occurrence rather than risk a fork; the
    # next confirmation simply probes again.
    try:
        probe = store.find_recurrence_clone(post_id, nxt_iso)
        if probe.get("found") or not probe.get("ok"):
            return None
    except Exception:
        return None
    platforms = [str(t.get("platform")) for t in (post.get("targets") or [])
                 if t.get("platform")]
    res = store.create_post(
        title=post.get("title") or "", body=post.get("body") or "",
        assets=post.get("assets"), platforms=platforms,
        schedule=dict(sched, publish_at=nxt_iso),
        source={"kind": "recurrence", "ref": post_id,
                "freshness_hint": FRESHNESS_HINT},
        provenance=({"content_hash": post.get("provenance_hash")}
                    if post.get("provenance_hash") else None),
        license=post.get("license"), tags=post.get("tags"))
    if not res.get("ok"):
        return None
    clone = res["post"]
    # Freshness recompose (§6.7 variation guard) — best-effort, engine-direct,
    # and never under FRIDAY_TESTING (no LLM calls in the unit suite).
    if not _testing():
        try:
            from agent_friday.services import content_composer
            content_composer.adapt(clone, platforms, regenerate=True)
        except Exception:
            _log.debug("recurrence freshness recompose failed", exc_info=True)
    arm = store.schedule_post(clone["id"])
    return clone["id"] if arm.get("ok") else None


# ─────────────────────────────────────────────────────────────────────────────
#  §6.5 native-schedule delegation (arm-time push)
# ─────────────────────────────────────────────────────────────────────────────

def _delegate_native(now=None) -> int:
    """Push targets ≥10 min out to platforms that advertise native_schedule
    (YouTube publishAt, Mastodon scheduled_at): the platform holds the post,
    so the machine can be asleep at the instant. The target runs the full
    gate chain now and confirms with options.native_scheduled=true. Returns
    the number delegated; never raises past the tick's guard."""
    now_dt = store._parse_instant(now) or store._now_dt()
    horizon = now_dt + timedelta(seconds=NATIVE_DELEGATE_MIN_S)
    try:
        with store._connect() as con:
            rows = con.execute(
                """SELECT t.id, t.platform, t.publish_at FROM targets t
                   JOIN posts p ON p.id = t.post_id
                   WHERE t.status='PENDING' AND t.publish_at IS NOT NULL
                     AND p.status IN ('SCHEDULED','PUBLISHING')""").fetchall()
    except Exception:
        return 0
    count = 0
    caps_cache: Dict[str, bool] = {}
    for r in rows:
        when = store._parse_instant(r["publish_at"])
        if when is None or when < horizon:
            continue
        plat = str(r["platform"])
        if plat not in caps_cache:
            ok = False
            adapter = platform_registry.get_adapter(plat)
            if adapter is not None:
                try:
                    ok = bool((adapter.capabilities() or {}).get("native_schedule"))
                except Exception:
                    ok = False
            caps_cache[plat] = ok
        if not caps_cache[plat]:
            continue
        tgt = (store.get_target(str(r["id"])) or {}).get("target")
        if not tgt or (tgt.get("options") or {}).get("native_scheduled"):
            continue
        claim = store.set_target_status(str(r["id"]), "PREPARING")
        if not claim.get("ok"):
            continue                              # another worker won the row
        tgt = claim.get("target") or tgt
        tgt["_native_delegate"] = True
        if _dispatch_target(tgt) == "confirmed":
            count += 1
    return count


# ─────────────────────────────────────────────────────────────────────────────
#  §6.8 conflict detection
# ─────────────────────────────────────────────────────────────────────────────

def detect_conflicts(platform: str, publish_at, window_hours: Optional[float] = None,
                     exclude_post: Optional[str] = None) -> Dict[str, Any]:
    """Targets on the SAME platform landing within the conflict window
    (settings content.conflict_window_hours, default 2 h). Warnings, never
    blocks — cross-platform simultaneity is explicitly fine (§6.8)."""
    try:
        when = store._parse_instant(publish_at)
        if when is None:
            return {"ok": False, "error": "invalid publish_at"}
        if window_hours is None:
            try:
                from agent_friday import core
                cw = ((core._load_settings() or {}).get("content")
                      or {}).get("conflict_window_hours")
                window_hours = float(cw)
            except Exception:
                window_hours = 0.0
            if not window_hours or window_hours <= 0:
                window_hours = 2.0
        lo = when - timedelta(hours=float(window_hours))
        hi = when + timedelta(hours=float(window_hours))
        with store._connect() as con:
            rows = con.execute(
                """SELECT id, post_id, publish_at, status FROM targets
                   WHERE platform=? AND publish_at IS NOT NULL
                     AND status IN ('PENDING','PREPARING','SENT','CONFIRMED')""",
                (str(platform),)).fetchall()
        hits = []
        for r in rows:
            dt = store._parse_instant(r["publish_at"])
            if dt is None or not (lo <= dt <= hi):
                continue
            if exclude_post and r["post_id"] == exclude_post:
                continue
            hits.append({"target_id": r["id"], "post_id": r["post_id"],
                         "publish_at": r["publish_at"], "status": r["status"]})
        return {"ok": True, "conflicts": hits,
                "window_hours": float(window_hours)}
    except Exception as e:
        return {"ok": False, "error": str(e)}

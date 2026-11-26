"""The owner's split: quick turns on FridayWeaver, private turns on Bonsai (Stephen via Dispatch, 2026-11-26).

Setting `local_turn_split`, off by default and only ever the owner's choice:

  "off"                               Friday routes as it always has.
  "fridayweaver_quick_bonsai_private" Laya decides per turn, automatically and invisibly:
                                      * quick commands and short, non-private turns -> FridayWeaver 2, which stays
                                        resident on the GPU (it becomes the "Quick reflexes" seat);
                                      * vault work and anything the privacy rules mark private -> Bonsai 2, through the
                                        arbiter's `private_turn` lease; and the REST of a conversation that went private
                                        stays on Bonsai (its history is private too, and it saves swapping back);
                                      * every other turn routes exactly as it would with the split off.

No prompt, no stop, no "please wait" card (Stephen, 11-26: "why can't Friday know ... so I don't have to see a stop").
If a swap is needed the only visible sign is Friday's normal thinking state; the model label under the answer says who
wrote it. Laya starts deciding while the owner is still typing or speaking (`observe_draft`): a draft that reads as
private pre-loads Bonsai, a draft that turns away cancels that pre-load; the final route is decided on send. Draft
text never leaves this computer and is never logged or stored.

THE INVARIANT: private content never goes to the cloud, even when Bonsai is slow to load. If Bonsai truly cannot come
up, the turn is refused here (a failure, not a routine stop).

What counts as private is the router's own `needs_vault_access` (vault tools, the sensitivity classifier's PRIVATE and
SENSITIVE tiers, the vault keywords: the same privacy label every other route uses), and every later turn of a
conversation that went private (`in_private_conversation`), since its history is private. What counts as quick is the Laya 2 tier-1 verdict
(services/reflex_turn): a reflex, conversation or approval shape, scored confidently and in scope, on a short message.
Governance is untouched: a quick command still raises its card.
"""
from __future__ import annotations

import threading
import time
from typing import Optional

MODE_OFF = "off"
MODE_SPLIT = "fridayweaver_quick_bonsai_private"
MODES = (MODE_OFF, MODE_SPLIT)
DEFAULT_MODE = MODE_OFF

QUICK_MODEL = "gemma4:e2b-fridayweaver-2.0-r1"
PRIVATE_MODEL = "bonsai2:27b"

#: Shapes that may go to the quick seat (laya2_seeds.SHAPES).
QUICK_SHAPES = ("reflex_open", "reflex_navigate", "reflex_organise", "conversation", "approval_reply")
#: A quick turn is a short one.
QUICK_MAX_CHARS = 280
#: How long Bonsai stays loaded after the last turn of a private conversation before FridayWeaver comes back.
PRIVATE_IDLE_S = 600
#: How long a private turn waits, silently, for Bonsai (a slow load is still never a reason to use the cloud).
PRIVATE_WAIT_S = 300

LABELS = {
    MODE_OFF: "Off: Friday picks the model as usual",
    MODE_SPLIT: "Quick turns on FridayWeaver 2, private turns on Bonsai",
}


def _settings(settings: Optional[dict] = None) -> dict:
    if settings is not None:
        return settings
    try:
        from agent_friday.core import _load_settings
        return _load_settings() or {}
    except Exception:
        return {}


def mode(settings: Optional[dict] = None) -> str:
    v = str((_settings(settings) or {}).get("local_turn_split") or DEFAULT_MODE).strip().lower()
    return v if v in MODES else DEFAULT_MODE


def _installed(model_id: str) -> bool:
    try:
        from agent_friday.services import model_store
        return model_id in (model_store.available() or {})
    except Exception:
        return False


def active(settings: Optional[dict] = None) -> bool:
    """On, and the quick model is actually installed (an uninstalled FW2 leaves Friday routing as before)."""
    return mode(settings) == MODE_SPLIT and _installed(QUICK_MODEL)


def seat_override(settings: Optional[dict] = None) -> dict:
    """{residency role: model} the split adds to the plan: FridayWeaver is the pinned Quick reflexes seat."""
    return {"sidekick": QUICK_MODEL} if active(settings) else {}


def is_quick(verdict: Optional[dict], text: str) -> bool:
    if not isinstance(verdict, dict) or verdict.get("degraded") or verdict.get("oos"):
        return False
    if len((text or "").strip()) > QUICK_MAX_CHARS:
        return False
    try:
        from agent_friday.services import reflex_turn as rt
        act, margin = rt.ACT_THRESHOLD, rt.MARGIN_THRESHOLD
    except Exception:
        act, margin = 0.55, 0.10
    return (str(verdict.get("shape") or "") in QUICK_SHAPES
            and float(verdict.get("shape_conf") or 0.0) >= act
            and float(verdict.get("margin") or 0.0) >= margin)


def _serving(model_id: str) -> bool:
    try:
        from agent_friday.services.residency_arbiter import owned_endpoint
        return bool(owned_endpoint(model_id))
    except Exception:
        return False


def _last_user(messages) -> str:
    for m in reversed(messages or []):
        if m.get("role") == "user":
            c = m.get("content", "")
            return c if isinstance(c, str) else ""
    return ""


# ── Conversations that went private stay on Bonsai (in memory only: conversation ids and times, no text) ──

_PRIVATE_CONVS: dict = {}
_CONV_LOCK = threading.Lock()


def note_private(conversation_id) -> None:
    if conversation_id:
        with _CONV_LOCK:
            _PRIVATE_CONVS[str(conversation_id)] = time.time()


def in_private_conversation(conversation_id) -> bool:
    if not conversation_id:
        return False
    with _CONV_LOCK:
        t = _PRIVATE_CONVS.get(str(conversation_id))
        if t is None:
            return False
        if time.time() - t > PRIVATE_IDLE_S:
            _PRIVATE_CONVS.pop(str(conversation_id), None)
            return False
        _PRIVATE_CONVS[str(conversation_id)] = time.time()      # every turn keeps the conversation on Bonsai
        return True


def is_private(router, messages, ctx: dict) -> bool:
    return bool(ctx.get("private")) or bool(router.needs_vault_access(messages, ctx))


def route_for_turn(router, messages, ctx: dict, settings: Optional[dict] = None) -> Optional[dict]:
    """The split's decision for one turn, finalized by the router, or None to route as usual."""
    if not active(settings):
        return None
    if ctx.get("is_background_task") or ctx.get("scheduled"):
        # Background work is nobody's live turn: it routes as before (a vault turn still goes local through the
        # router's own vault rule) and never swaps the owner's seats.
        return None
    from agent_friday.routing.model_router import TaskType
    conv = ctx.get("conversation_id")
    if is_private(router, messages, ctx):
        note_private(conv)
        return router._finalize({
            "provider": "local", "model": PRIVATE_MODEL, "task_type": TaskType.VAULT_ACCESS,
            "local_split": "private",
            "reason": "Private: Bonsai on this computer (your split setting; never the cloud)",
        }, vault_access=True)
    if in_private_conversation(conv):
        # The history of this conversation is private; the rest of it stays on Bonsai, local.
        return router._finalize({
            "provider": "local", "model": PRIVATE_MODEL, "task_type": TaskType.VAULT_ACCESS,
            "local_split": "private_conversation",
            "reason": "Private conversation: staying on Bonsai on this computer",
        }, vault_access=True)
    text = ctx.get("user_text") or _last_user(messages)
    verdict = ctx.get("turn_shape")
    if verdict is None and "turn_shape" not in ctx and text:
        # The agent and voice paths do not compute the tier-1 verdict; classify here (bounded at 50 ms, a slow or
        # missing encoder degrades, which reads as "not quick").
        try:
            from agent_friday.services import reflex_turn as _rt
            verdict = _rt.classify(text)
        except Exception:
            verdict = None
    if not is_quick(verdict, text):
        return None
    # Bonsai holds the card (a private conversation in another window): answer there rather than swap.
    model = PRIVATE_MODEL if (_serving(PRIVATE_MODEL) and not _serving(QUICK_MODEL)) else QUICK_MODEL
    if not _serving(model):
        return None                     # the quick seat is not up: a non-private turn may route as usual
    return router._finalize({
        "provider": "local", "model": model, "task_type": TaskType.TOOL_USE, "local_split": "quick",
        "reason": ("Quick turn: FridayWeaver 2 (your split setting)" if model == QUICK_MODEL
                   else "Quick turn: Bonsai, already loaded"),
    })


def _arbiter(arbiter=None):
    if arbiter is not None:
        return arbiter
    from agent_friday.services.residency_arbiter import get_arbiter
    return get_arbiter()


def ensure_private_seat(*, wait_s: float = PRIVATE_WAIT_S, arbiter=None, for_turn: bool = True) -> dict:
    """Bonsai serving, through the arbiter's `private_turn` lease (renewed while the conversation continues). Silent:
    the caller's normal thinking state is the only sign of a load."""
    arbiter = _arbiter(arbiter)
    if arbiter is None:
        return {"ok": False, "error": "the local model manager is not running"}
    lease = getattr(arbiter, "lease", None) or {}
    if lease.get("kind") in ("voice_call", "podcast_voice"):
        # A local voice call has the card; queueing behind it would be minutes of silence. Not the cloud either.
        return {"ok": False, "error": "a local voice call is using the graphics card"}
    if lease.get("kind") == "private_turn" and _serving(PRIVATE_MODEL):
        arbiter.renew("private_turn", PRIVATE_IDLE_S)
        if for_turn:
            _PREWARM.mark_used()
        return {"ok": True, "waited_s": 0.0, "renewed": True}
    if _serving(PRIVATE_MODEL) and not lease:
        return {"ok": True, "waited_s": 0.0, "resident": True}
    t0 = time.time()
    out = dict(arbiter.grant("private_turn", role=PRIVATE_MODEL, ttl_s=PRIVATE_IDLE_S, wait_s=wait_s) or {})
    out["waited_s"] = round(time.time() - t0, 2)
    if out.get("ok") and not _serving(PRIVATE_MODEL):
        out.update(ok=False, error="Bonsai's lease was granted but it is not answering")
    if out.get("ok") and for_turn:
        _PREWARM.mark_used()
    return out


REFUSAL = ("Bonsai couldn't load to answer this privately ({why}). I didn't send it anywhere else: private messages "
           "stay on this computer under your split setting.")

_BONSAI_ROUTES = ("private", "private_conversation")


def prepare_turn(route: dict, *, ensure=None) -> dict:
    """Chat's and the agent's step after routing: a private route gets Bonsai serving, silently, or (only if Bonsai
    truly cannot load) a local refusal. Never turns a private route into a cloud one."""
    if not isinstance(route, dict) or route.get("local_split") not in _BONSAI_ROUTES:
        return route
    got = (ensure or ensure_private_seat)()
    route = dict(route)
    if got.get("ok"):
        route["private_seat_wait_s"] = got.get("waited_s")
        return route
    route.update(refuse=True, warning=REFUSAL.format(why=str(got.get("error") or "no reason given").rstrip(".")),
                 offer_cloud_switch=False, local_unavailable=False)
    return route


# ── Laya on the draft: reversible prep while the owner is still typing or speaking ─────────────────────────────

#: A draft is re-checked at most this often per conversation (the page also waits ~300 ms after a pause).
DRAFT_MIN_INTERVAL_S = 0.25
#: A pre-load started for a draft is cancelled when the draft has read as not private for this long.
DRAFT_CANCEL_AFTER_S = 3.0
#: Drafts shorter than this say nothing yet.
DRAFT_MIN_CHARS = 8


class _Prewarm:
    """At most one draft-started pre-load. Holds no text: a conversation id, times and flags."""

    def __init__(self):
        self.lock = threading.Lock()
        self.conv = None
        self.started = 0.0
        self.thread = None
        self.used = False
        self.cancel = False
        self.not_private_since = None

    def mark_used(self):
        with self.lock:
            self.used = True

    def active(self):
        with self.lock:
            return self.thread is not None and not self.used and not self.cancel


_PREWARM = _Prewarm()
_DRAFT_LAST: dict = {}
_DRAFT_STATS = {"checks": 0, "private": 0, "prewarms": 0, "cancels": 0, "ms": []}


def _router_for_drafts():
    from agent_friday.routing.model_router import ModelRouter
    return ModelRouter(config=(_settings(None).get("model_routing") or {}))


def _start_prewarm(conv, arbiter=None):
    with _PREWARM.lock:
        if _PREWARM.thread is not None and _PREWARM.thread.is_alive():
            _PREWARM.cancel = False
            _PREWARM.not_private_since = None
            return False
        _PREWARM.conv, _PREWARM.started, _PREWARM.used = conv, time.time(), False
        _PREWARM.cancel, _PREWARM.not_private_since = False, None

        def run():
            arb = _arbiter(arbiter)
            ensure_private_seat(arbiter=arb, wait_s=PRIVATE_WAIT_S, for_turn=False)
            with _PREWARM.lock:
                cancel = _PREWARM.cancel
            if cancel:
                _release_prewarm(arb)

        _PREWARM.thread = threading.Thread(target=run, daemon=True, name="split-prewarm")
        _PREWARM.thread.start()
    _DRAFT_STATS["prewarms"] += 1
    return True


def _release_prewarm(arbiter=None):
    """Give the card back to FridayWeaver, unless a private turn or private conversation has since claimed Bonsai."""
    with _PREWARM.lock:
        if _PREWARM.used or (_PREWARM.conv and _conv_is_private_now(_PREWARM.conv)):
            return False
        _PREWARM.thread = None
    arb = _arbiter(arbiter)
    lease = (getattr(arb, "lease", None) or {}) if arb is not None else {}
    if lease.get("kind") == "private_turn":
        arb.release("private_turn")
    _DRAFT_STATS["cancels"] += 1
    return True


def _conv_is_private_now(conv) -> bool:
    with _CONV_LOCK:
        t = _PRIVATE_CONVS.get(str(conv))
    return t is not None and time.time() - t <= PRIVATE_IDLE_S


def observe_draft(text: str, conversation_id=None, *, source: str = "typing", settings: Optional[dict] = None,
                  arbiter=None) -> dict:
    """Laya on a draft (typed text after a pause, or a partial voice transcript). Reversible prep only: pre-load
    Bonsai for a draft that reads as private, cancel that pre-load when the draft turns away. The text is classified
    in memory and dropped; it is never logged, stored or sent anywhere."""
    if not active(settings):
        return {"ok": True, "prep": "none", "why": "split off"}
    key = str(conversation_id or "")
    now = time.time()
    if now - _DRAFT_LAST.get(key, 0.0) < DRAFT_MIN_INTERVAL_S:
        return {"ok": True, "prep": "skipped", "why": "too soon"}
    _DRAFT_LAST[key] = now
    text = text or ""
    if len(text.strip()) < DRAFT_MIN_CHARS:
        return {"ok": True, "prep": "none", "why": "too short"}
    t0 = time.perf_counter()
    try:
        private = is_private(_router_for_drafts(), [{"role": "user", "content": text}], {})
    except Exception:
        private = False
    ms = round((time.perf_counter() - t0) * 1000.0, 2)
    _DRAFT_STATS["checks"] += 1
    _DRAFT_STATS["ms"] = (_DRAFT_STATS["ms"] + [ms])[-200:]
    del text
    if private:
        _DRAFT_STATS["private"] += 1
        started = False
        if not _serving(PRIVATE_MODEL):
            started = _start_prewarm(conversation_id, arbiter)
        return {"ok": True, "prep": "prewarm_bonsai" if started else "bonsai_ready_or_loading", "ms": ms}
    with _PREWARM.lock:
        mine = _PREWARM.thread is not None and not _PREWARM.used
        if mine and _PREWARM.not_private_since is None:
            _PREWARM.not_private_since = now
        due = mine and now - (_PREWARM.not_private_since or now) >= DRAFT_CANCEL_AFTER_S
        if due:
            _PREWARM.cancel = True
        still_loading = due and _PREWARM.thread is not None and _PREWARM.thread.is_alive()
    if due and not still_loading:
        _release_prewarm(arbiter)
        return {"ok": True, "prep": "cancelled_prewarm", "ms": ms}
    return {"ok": True, "prep": "cancel_pending" if due else "keep", "ms": ms}


_ASYNC = {"lock": threading.Lock(), "pending": None, "thread": None}


def observe_draft_async(text: str, conversation_id=None, *, source: str = "voice") -> None:
    """`observe_draft` off the caller's thread (the voice loop must never wait on it). One worker; only the newest
    draft is kept, older ones are dropped unread."""
    if mode() != MODE_SPLIT:
        return
    with _ASYNC["lock"]:
        _ASYNC["pending"] = (text, conversation_id, source)
        if _ASYNC["thread"] is not None and _ASYNC["thread"].is_alive():
            return

        def run():
            while True:
                with _ASYNC["lock"]:
                    job, _ASYNC["pending"] = _ASYNC["pending"], None
                    if job is None:
                        _ASYNC["thread"] = None
                        return
                try:
                    observe_draft(job[0], job[1], source=job[2])
                except Exception:
                    pass

        _ASYNC["thread"] = threading.Thread(target=run, daemon=True, name="split-draft")
        _ASYNC["thread"].start()


def draft_stats() -> dict:
    ms = sorted(_DRAFT_STATS["ms"])

    def pct(q):
        return ms[min(len(ms) - 1, int(q * len(ms)))] if ms else None
    return {k: v for k, v in _DRAFT_STATS.items() if k != "ms"} | {"check_ms_p50": pct(0.5), "check_ms_p95": pct(0.95)}


# ── Changing it: Settings writes through POST /api/settings; voice and chat through set_setting ──

SPLIT_PATH = "settings.models.quick_private_split"
_ON_WORDS = ("on", "yes", "true", "split", "fridayweaver", "turn it on", "enable", MODE_SPLIT)
_OFF_WORDS = ("off", "no", "false", "default", "bonsai", "turn it off", "disable", MODE_OFF)


def parse_mode(value) -> Optional[str]:
    v = " ".join(str(value if value is not None else "").strip().lower().replace("_", " ").split())
    if v in (w.replace("_", " ") for w in _ON_WORDS):
        return MODE_SPLIT
    if v in (w.replace("_", " ") for w in _OFF_WORDS):
        return MODE_OFF
    return None


def apply_mode(new_mode: str) -> dict:
    """Write the setting and make the card match (the same replan and re-seat POST /api/settings runs)."""
    from agent_friday.core import _save_settings
    _save_settings({"local_turn_split": new_mode})
    try:
        from agent_friday.services.residency_arbiter import get_arbiter
        from agent_friday.services import residency_catalog as _rc
        from agent_friday.services import seat_binding as _sb
        arb = get_arbiter()
        if arb is None:
            return {"ok": True, "note": "no arbiter in this process"}
        arb.entries = _rc.installed_entries(arb.profile)
        arb.compute_plan(_sb.overrides_from_settings(_settings(None)))
        return arb.reseat_to_plan("local_turn_split changed by asking")
    except Exception as e:
        return {"ok": False, "error": str(e)}

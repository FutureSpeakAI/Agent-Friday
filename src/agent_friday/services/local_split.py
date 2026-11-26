"""The owner's split: quick turns on FridayWeaver, private turns on Bonsai (Stephen via Dispatch, 2026-11-26).

Setting `local_turn_split`, off by default and only ever the owner's choice:

  "off"                               Friday routes as it always has.
  "fridayweaver_quick_bonsai_private" * quick commands and short, non-private turns are answered by FridayWeaver 2,
                                        which stays resident on the GPU (it becomes the "Quick reflexes" seat);
                                      * vault work and anything the privacy rules mark private is answered by Bonsai 2,
                                        loaded on demand through the arbiter's `private_turn` lease;
                                      * a private turn NEVER goes to the cloud because Bonsai is not loaded: it waits for
                                        Bonsai and says so on screen, and if Bonsai cannot come up it is refused here.
                                      * every other turn routes exactly as it would with the split off.

What counts as private is the router's own `needs_vault_access` (vault tools, the sensitivity classifier's PRIVATE and
SENSITIVE tiers, the vault keywords) plus an explicit `private` flag on the turn. What counts as quick is the Laya 2
tier-1 verdict already computed for every turn (services/reflex_turn): a reflex, conversation or approval shape, scored
confidently and in scope, on a short message. Governance is untouched: a quick command still raises its card.

While Bonsai holds the card for a private conversation (the lease idles out after PRIVATE_IDLE_S), quick turns are
answered by Bonsai too rather than swapping models back and forth; the lease's expiry restores FridayWeaver.
"""
from __future__ import annotations

import time
from typing import Callable, Optional

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
#: How long Bonsai stays loaded after the last private turn before FridayWeaver comes back.
PRIVATE_IDLE_S = 600
#: How long a private turn waits for Bonsai to load (a cold load measured ~40 s on the reference machine).
PRIVATE_WAIT_S = 240

LABELS = {
    MODE_OFF: "Off: Friday picks the model as usual",
    MODE_SPLIT: "Quick turns on FridayWeaver 2, private turns on Bonsai",
}
WAIT_NOTE = "Loading Bonsai for this private message; it stays on this computer…"


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


def route_for_turn(router, messages, ctx: dict, settings: Optional[dict] = None) -> Optional[dict]:
    """The split's decision for one turn, finalized by the router, or None to route as usual."""
    if not active(settings):
        return None
    from agent_friday.routing.model_router import TaskType
    private = bool(ctx.get("private")) or router.needs_vault_access(messages, ctx)
    if private:
        return router._finalize({
            "provider": "local", "model": PRIVATE_MODEL, "task_type": TaskType.VAULT_ACCESS,
            "local_split": "private",
            "reason": "Private turn: Bonsai on this computer (your split setting; never the cloud)",
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
    # Bonsai holds the card for a private conversation: answer there rather than swap models mid-conversation.
    model = PRIVATE_MODEL if (_serving(PRIVATE_MODEL) and not _serving(QUICK_MODEL)) else QUICK_MODEL
    if not _serving(model):
        return None                     # the quick seat is not up: a non-private turn may route as usual
    return router._finalize({
        "provider": "local", "model": model, "task_type": TaskType.TOOL_USE, "local_split": "quick",
        "reason": ("Quick turn: FridayWeaver 2 (your split setting)" if model == QUICK_MODEL
                   else "Quick turn: Bonsai, already loaded for your private conversation"),
    })


def ensure_private_seat(on_wait: Optional[Callable[[str], None]] = None, *, wait_s: float = PRIVATE_WAIT_S,
                        arbiter=None) -> dict:
    """Bonsai serving, through the arbiter's `private_turn` lease (renewed while the conversation continues)."""
    if arbiter is None:
        from agent_friday.services.residency_arbiter import get_arbiter
        arbiter = get_arbiter()
    if arbiter is None:
        return {"ok": False, "error": "the local model manager is not running"}
    lease = getattr(arbiter, "lease", None) or {}
    if lease.get("kind") == "private_turn" and _serving(PRIVATE_MODEL):
        arbiter.renew("private_turn", PRIVATE_IDLE_S)
        return {"ok": True, "waited_s": 0.0, "renewed": True}
    if _serving(PRIVATE_MODEL) and not lease:
        return {"ok": True, "waited_s": 0.0, "resident": True}
    if on_wait:
        try:
            on_wait(WAIT_NOTE)
        except Exception:
            pass
    t0 = time.time()
    out = arbiter.grant("private_turn", role=PRIVATE_MODEL, ttl_s=PRIVATE_IDLE_S, wait_s=wait_s)
    out = dict(out or {})
    out["waited_s"] = round(time.time() - t0, 2)
    if out.get("ok") and not _serving(PRIVATE_MODEL):
        out.update(ok=False, error="Bonsai's lease was granted but it is not answering")
    return out


def _narrate(note: str) -> None:
    try:
        from agent_friday.services import model_router as _mr
        sink = _mr.TOOL_SINK.get()
        if sink:
            sink({"name": "private_seat", "label": "Bonsai", "note": note})
    except Exception:
        pass


REFUSAL = ("Bonsai couldn't load to answer this privately ({why}). I didn't send it anywhere else: private messages "
           "stay on this computer under your split setting. Try again in a minute, or turn the split off in Settings.")


def prepare_turn(route: dict, *, ensure=None, narrate=None) -> dict:
    """Chat's step after routing: a private split turn gets Bonsai loaded (with an on-screen note) or a local refusal.

    Never turns a private route into a cloud one."""
    if not isinstance(route, dict) or route.get("local_split") != "private":
        return route
    ensure = ensure or ensure_private_seat
    narrate = narrate or _narrate
    got = ensure(narrate)
    if got.get("ok"):
        route = dict(route)
        route["private_seat_wait_s"] = got.get("waited_s")
        return route
    route = dict(route)
    route.update(refuse=True, warning=REFUSAL.format(why=str(got.get("error") or "no reason given").rstrip(".")),
                 offer_cloud_switch=False, local_unavailable=False)
    return route


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

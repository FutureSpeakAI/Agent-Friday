"""No silent switch from one cloud provider to another (amendment A6).

The two generation ladders (`model_router._generate_text` and
`agent._generate_agent`) carry briefings, the digest, editorials, scheduled
work, subagents, tasks and `/api/chat/send`. When their first cloud leg failed
(Anthropic down, a 429, a 5xx, a bad key) they went straight to the other cloud
provider (the OpenAI-compatible endpoint, OpenRouter by default) and the only
trace was a journal line and a small warning sign on the model badge.

A6 says there is no silent substitution. Now:

  * a cloud leg whose provider differs from the cloud provider tried before it
    stays in the ladder only when the owner allowed that move in advance
    (``model_routing.cloud_fallback_order``, set by a press in Settings) or chose
    it for this one call (``chosen(provider)``, the offer's button);
  * otherwise, when the first provider fails, the call ends with
    ``ProviderSwitchWithheld``: it names what failed and the alternative it did
    NOT use, carries an offer the page renders as buttons, and is announced as a
    notification so background work that failed is visible too;
  * every move that does happen (pre-set or chosen) is announced the same way,
    so even an allowed switch is never silent.

Local legs are not this module's business: local-to-cloud is governed by
``cloud_intent`` and cloud-to-local by the routing mode filter.
"""
from __future__ import annotations

import contextlib
import contextvars
import logging
import threading
import time
from typing import Any, Dict, List, Optional, Sequence, Tuple

_log = logging.getLogger("friday.provider_fallback")

#: Ladder leg name -> the cloud provider it is.
LEG_PROVIDER = {"cloud": "anthropic", "openai": "openai"}
#: The provider -> its ladder leg.
PROVIDER_LEG = {v: k for k, v in LEG_PROVIDER.items()}
LABEL = {"anthropic": "Anthropic (Claude)", "openai": "your OpenAI-compatible provider"}

#: The stored choices (a string, so a Settings select round-trips it) and the order each means.
ORDERS: Dict[str, Tuple[str, ...]] = {
    "ask": (),
    "anthropic_then_openai": ("anthropic", "openai"),
    "openai_then_anthropic": ("openai", "anthropic"),
}
DEFAULT_ORDER = "ask"
SETTINGS_ACTION = {"label": "Set what happens when a cloud provider fails",
                   "workspace": "settings", "tab": "privacy"}

#: The owner's one-call choice from an offer: a provider name, or None.
CHOSEN: "contextvars.ContextVar[Optional[str]]" = contextvars.ContextVar(
    "friday_cloud_fallback_choice", default=None)

_ANNOUNCED: Dict[str, float] = {}
_ANNOUNCE_LOCK = threading.Lock()
_ANNOUNCE_EVERY_S = 300.0


def provider_label(provider: str) -> str:
    if provider == "openai":
        try:
            import agent_friday.core as core
            cfg = (core._load_settings() or {}).get("model_routing") or {}
            base = str(cfg.get("openai_base_url") or "")
            if "openrouter" in base.lower() or not base:
                return "OpenRouter"
        except Exception:
            pass
    return LABEL.get(provider, provider)


def stored_order(routing_cfg: Optional[dict] = None) -> str:
    if routing_cfg is None:
        try:
            import agent_friday.core as core
            routing_cfg = (core._load_settings() or {}).get("model_routing") or {}
        except Exception:
            routing_cfg = {}
    v = str((routing_cfg or {}).get("cloud_fallback_order") or DEFAULT_ORDER).strip().lower()
    return v if v in ORDERS else DEFAULT_ORDER


def owner_order(routing_cfg: Optional[dict] = None) -> Tuple[str, ...]:
    return ORDERS[stored_order(routing_cfg)]


def allowed(frm: str, to: str, routing_cfg: Optional[dict] = None) -> Tuple[bool, str]:
    """May a call that failed on cloud provider ``frm`` move to cloud provider ``to``?
    Returns (allowed, why) where why is "chosen" or "owner_order"."""
    if not frm or not to or frm == to:
        return True, "same_provider"
    if CHOSEN.get() == to:
        return True, "chosen"
    order = owner_order(routing_cfg)
    if frm in order and to in order and order.index(frm) < order.index(to):
        return True, "owner_order"
    return False, ""


@contextlib.contextmanager
def chosen(provider: Optional[str]):
    """The owner chose ``provider`` for this one call (the offer's button)."""
    p = str(provider or "").strip().lower()
    tok = CHOSEN.set(p if p in PROVIDER_LEG else None)
    try:
        yield
    finally:
        CHOSEN.reset(tok)


class ProviderSwitchWithheld(RuntimeError):
    """The cloud provider failed and Friday did not move to the other one on her own."""

    def __init__(self, failed: str, alternative: str, error: str = "") -> None:
        self.failed = failed
        self.alternative = alternative
        self.error = str(error or "")[:300]
        super().__init__(self.text())

    def text(self) -> str:
        f, a = provider_label(self.failed), provider_label(self.alternative)
        why = (": " + self.error) if self.error else ""
        return (f"{f} did not answer{why}. I did not switch to {a} on my own. "
                f"Choose “Use {a} for this”, or set an order in Settings → Privacy "
                f"(When a cloud provider fails).")

    def offer(self) -> Dict[str, Any]:
        a = provider_label(self.alternative)
        return {"offer": True, "kind": "cloud_provider_offer", "failed": self.failed,
                "alternative": self.alternative, "reason": self.error, "text": self.text(),
                "choices": [
                    {"id": "use_alternative", "label": f"Use {a} for this", "recommended": False,
                     "provider": self.alternative},
                    {"id": "retry_same", "label": f"Try {provider_label(self.failed)} again",
                     "recommended": False},
                ]}


def filter_ladder(attempts: Sequence[tuple], routing_cfg: Optional[dict] = None
                  ) -> Tuple[List[tuple], List[tuple]]:
    """Split a ladder into (kept, withheld). The head is never touched. A later cloud
    leg of a different provider than the first cloud leg is kept only when the owner
    allowed that move (in advance, or for this call)."""
    attempts = list(attempts or [])
    if not attempts:
        return attempts, []
    kept, withheld = [attempts[0]], []
    first_cloud = LEG_PROVIDER.get(attempts[0][0])
    for leg in attempts[1:]:
        prov = LEG_PROVIDER.get(leg[0])
        if prov is None:
            kept.append(leg)
            continue
        if first_cloud is None:
            first_cloud = prov
            kept.append(leg)
            continue
        ok, _why = allowed(first_cloud, prov, routing_cfg)
        (kept if ok else withheld).append(leg)
    return kept, withheld


_PROVIDER_WORDS = ("overloaded", "rate limit", "rate_limit", "too many requests", "timed out",
                   "timeout", "connection", "service unavailable", "bad gateway", "internal server error",
                   " 429", " 500", " 502", " 503", " 504", " 529", "api key", "authentication")


def looks_like_provider_failure(exc: BaseException) -> bool:
    """Did the provider (not Friday's own code) fail? An SDK or HTTP error, or the words
    providers use. A bug in a tool is not offered as a reason to change provider."""
    mod = type(exc).__module__ or ""
    name = type(exc).__name__
    if mod.split(".")[0] in ("anthropic", "httpx", "requests", "urllib3", "openai"):
        return True
    if any(k in name for k in ("APIError", "APIStatusError", "RateLimit", "Overloaded",
                               "APIConnection", "Timeout", "ConnectionError")):
        return True
    text = " " + str(exc).lower()
    return any(w in text for w in _PROVIDER_WORDS)


class LadderWatch:
    """The ladder loop's bookkeeping: which cloud provider failed first, announcing a
    move that does happen, and refusing at the end when a move was withheld."""

    def __init__(self, withheld: Sequence[tuple], routing_cfg: Optional[dict] = None) -> None:
        self.withheld = list(withheld or [])
        self.cfg = routing_cfg
        self.failed: Optional[str] = None
        self.error = ""

    def leg_failed(self, leg_name: str, error: Any) -> None:
        prov = LEG_PROVIDER.get(leg_name)
        if prov and self.failed is None:
            self.failed, self.error = prov, str(error or "")[:300]

    def leg_succeeded(self, leg_name: str) -> None:
        prov = LEG_PROVIDER.get(leg_name)
        if self.failed and prov and prov != self.failed:
            _ok, why = allowed(self.failed, prov, self.cfg)
            announce("switched", self.failed, prov, self.error, why=why)

    def exhausted(self) -> None:
        """Every leg that was allowed failed. If a cloud provider failed and the move to
        another one was withheld, say so with the offer instead of a generic error."""
        if self.failed and self.withheld:
            alt = LEG_PROVIDER.get(self.withheld[0][0])
            if alt:
                announce("withheld", self.failed, alt, self.error)
                raise ProviderSwitchWithheld(self.failed, alt, self.error)


def announce(kind: str, failed: str, alternative: str, error: str = "", *, why: str = "") -> None:
    """Make a provider failure (and any move away from it) visible: a notification and a
    journal line. Never raises. Repeats of the same announcement within five minutes are
    folded into the first, so a run of background calls makes one notice, not fifty."""
    key = f"{kind}:{failed}:{alternative}"
    now = time.time()
    with _ANNOUNCE_LOCK:
        last = _ANNOUNCED.get(key, 0.0)
        if now - last < _ANNOUNCE_EVERY_S:
            return
        _ANNOUNCED[key] = now
    f, a = provider_label(failed), provider_label(alternative)
    if kind == "switched":
        title = f"{f} failed; this answer came from {a}"
        body = (f"{f} did not answer ({error[:160]}). " +
                ("You chose " + a + " for this." if why == "chosen" else
                 "Your fallback order in Settings allows " + a + " after " + f + ".")) if error else \
               f"Moved from {f} to {a} ({why})."
    else:
        title = f"{f} did not answer; I did not switch to {a}"
        body = ProviderSwitchWithheld(failed, alternative, error).text()
    try:
        from agent_friday.services import task_journal as _tj
        _tj.decision("cloud_provider_" + kind, alternative if kind == "switched" else failed,
                     reason=(error or why)[:200], alternatives=[alternative])
    except Exception:
        pass
    try:
        # The seat notice's place (E31: "answered by a different model than you chose"),
        # with one tap to the order the owner sets.
        from agent_friday.notifications_engine import push
        push(title=title, body=body, priority="high", source="routing", kind="seat_notice",
             actions=[SETTINGS_ACTION], emitter="E31",
             dedupe_key="cloud_provider:%s:%s:%s" % (kind, failed, alternative))
    except Exception as exc:
        _log.warning("provider notice not shown (%s): %s | %s", exc, title, body)
    _log.warning("%s | %s", title, body)


def _reset_for_tests() -> None:
    with _ANNOUNCE_LOCK:
        _ANNOUNCED.clear()

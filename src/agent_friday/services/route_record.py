"""The route record: why this reply was answered where it was (north star 12.3).

Every routing decision (``ModelRouter.route``, and the chat route's final decision
after its per-turn overrides) produces one structured record:

    seat              local | cloud | none (refused, nothing sent)
    provider, model   what was chosen
    reason            the router's own reason, in its words
    task_type, mode   what the router was judging, and under which routing mode
    alternatives      what was NOT chosen, each with why: the other seat, and the
                      other cloud provider (which A6 never switches to silently)
    expected_cost     an estimate for what is sent (prompt tokens at Friday's price
                      table; a local seat costs nothing), with its basis in words
    context           how much is sent: messages and an estimated token count
    fallback_policy   what happens if this choice fails: cloud to cloud (ask, or
                      the owner's order), local to cloud, cloud to local, the vault
    latency_ms        filled when the reply is built: routing to reply

The record is built from what the router already decided. It never probes a
model server or the network: building it cannot change or slow the route.

``services/attribution`` keeps the turn's records; the chat routes put the one
that matches the model that actually answered on the reply (``route`` on the
message), and the page shows it compactly with the full record one press away.
"""
from __future__ import annotations

import time
from typing import Any, Dict, List, Optional

#: Bounded, so a record is always small enough to persist with a message.
_MAX_TEXT = 300


def _clip(s: Any, n: int = _MAX_TEXT) -> str:
    # Error text is scrubbed by public_result only while it keeps its marker type;
    # turned into a plain str here it would reach the page and the history raw.
    try:
        from agent_friday.user_errors import ExceptionText
        if isinstance(s, ExceptionText):
            return "(error)"
    except Exception:
        pass
    s = " ".join(str(s or "").split())
    return s if len(s) <= n else s[: n - 1] + "…"


def _prompt_tokens(messages, system: Optional[str] = None) -> int:
    """chars/4 over the text that would be sent. An estimate, labelled as one."""
    n = len(system or "")
    for m in messages or []:
        if not isinstance(m, dict):
            continue
        c = m.get("content")
        if isinstance(c, str):
            n += len(c)
        elif isinstance(c, list):
            for part in c:
                if isinstance(part, dict) and isinstance(part.get("text"), str):
                    n += len(part["text"])
    return n // 4


def _cloud_family(route: dict) -> str:
    """anthropic | openai, for the A6 policy (services/provider_fallback)."""
    # provider_name is set for Anthropic decisions resolved through the registry
    # too, so only the ladder's own leg name decides.
    return "openai" if str(route.get("provider") or "").lower() == "openai" else "anthropic"


def _rate_per_1k(model: str) -> Optional[float]:
    try:
        from agent_friday.routing.model_router import CLOUD_COST_PER_1K
        return CLOUD_COST_PER_1K.get(str(model or ""))
    except Exception:
        return None


def _expected_cost(seat: str, model: str, tokens: int) -> Dict[str, Any]:
    if seat == "local":
        return {"usd": 0.0, "basis": "runs on this machine; no per-call charge"}
    if seat != "cloud":
        return {"usd": 0.0, "basis": "nothing was sent"}
    rate = _rate_per_1k(model)
    if rate is None:
        return {"usd": None, "basis": "no price on file for %s; the provider bills it" % (model or "this model")}
    usd = round(tokens / 1000.0 * rate, 4)
    return {"usd": usd,
            "basis": ("about %d tokens sent at $%.3f per 1,000 (the built-in price table); "
                      "the reply adds to this" % (tokens, rate))}


def _policy(mode: str, route: dict, routing_cfg: Optional[dict]) -> Dict[str, Any]:
    from agent_friday.services import cloud_intent as _ci
    out: Dict[str, Any] = {"mode": mode or "cloud_only"}
    try:
        from agent_friday.services import provider_fallback as _pf
        order = _pf.owner_order(routing_cfg)
        if order:
            out["cloud_to_cloud"] = ("if %s fails, %s, and you are told"
                                     % (_pf.provider_label(order[0]),
                                        " then ".join(_pf.provider_label(p) for p in order[1:])))
        else:
            out["cloud_to_cloud"] = ("if a cloud provider fails, you are asked before "
                                     "another one is tried")
        out["cloud_fallback_order"] = list(order)
    except Exception:
        out["cloud_to_cloud"] = "if a cloud provider fails, you are asked before another one is tried"
    m = str(mode or "").lower()
    if _ci.is_local_mode(m):
        out["local_to_cloud"] = ("never without your yes for this turn"
                                 if m == "local_only" else
                                 "only with a recorded choice (yours, or a task you set to the cloud)")
    else:
        out["local_to_cloud"] = "not applicable in this mode"
    if m == "cloud_only":
        out["cloud_to_local"] = "not used: the mode is cloud only"
    elif m == "cloud_preferred":
        out["cloud_to_local"] = "used when the cloud is out, and the reply says so"
    else:
        out["cloud_to_local"] = "local is the first choice in this mode"
    if route.get("vault_access"):
        out["vault"] = "this turn touches the vault; it never leaves this machine"
    return out


def _alternatives(seat: str, route: dict, mode: str, routing_cfg: Optional[dict]) -> List[Dict[str, Any]]:
    alts: List[Dict[str, Any]] = []
    reason = _clip(route.get("reason") or route.get("why") or "", 160)
    m = str(mode or "").lower()
    if seat == "cloud":
        if route.get("override") == "computer_control" and route.get("overridden_local_model"):
            alts.append({"seat": "local", "model": route.get("overridden_local_model"),
                         "why": "Computer Control is on and the local model cannot see or drive the screen"})
        elif m == "cloud_only":
            alts.append({"seat": "local", "why": "the routing mode is cloud only, so local models are not used"})
        elif route.get("route_override"):
            alts.append({"seat": "local", "why": _clip(route.get("route_override"), 160)})
        else:
            alts.append({"seat": "local", "why": "not chosen: %s" % (reason or "the router chose the cloud")})
        fam = _cloud_family(route)
        other = "openai" if fam == "anthropic" else "anthropic"
        try:
            from agent_friday.services import provider_fallback as _pf
            ok, _w = _pf.allowed(fam, other, routing_cfg)
            label = _pf.provider_label(other)
        except Exception:
            ok, label = False, other
        alts.append({"seat": "cloud", "provider": other,
                     "why": ("next in your cloud order: used only if this provider fails, "
                             "and you are told" if ok else
                             "not used, even if this provider fails, unless you say yes "
                             "(no silent switch)")})
        alts[-1]["label"] = label
    elif seat == "local":
        withheld = route.get("cloud_withheld") or {}
        if route.get("vault_access"):
            alts.append({"seat": "cloud", "why": "this turn touches the vault, which never leaves this machine"})
        elif withheld.get("model"):
            alts.append({"seat": "cloud", "model": withheld.get("model"),
                         "why": "%s mode and no cloud intent for this call" % (mode or "local")})
        elif m == "local_only":
            alts.append({"seat": "cloud", "why": "local only: the cloud is not consulted"})
        else:
            alts.append({"seat": "cloud", "why": "not chosen: %s" % (reason or "the router kept it local")})
    else:
        alts.append({"seat": "local", "why": _clip(route.get("warning") or "no local model is serving", 200)})
        alts.append({"seat": "cloud", "why": "not sent: a cloud call needs your yes here"})
    return alts


def build(route: Optional[dict], *, messages=None, system: Optional[str] = None,
          routing_cfg: Optional[dict] = None, source: str = "router") -> Dict[str, Any]:
    """The record for one routing decision. Never raises; a field it cannot work out is
    None, never invented."""
    route = dict(route or {})
    if routing_cfg is None:
        try:
            import agent_friday.core as core
            routing_cfg = (core._load_settings() or {}).get("model_routing") or {}
        except Exception:
            routing_cfg = {}
    try:
        from agent_friday.services.cloud_intent import normalize_mode
        mode = normalize_mode((routing_cfg or {}).get("mode"))
    except Exception:
        mode = str((routing_cfg or {}).get("mode") or "cloud_only").strip().lower()
    refused = bool(route.get("refuse"))
    if refused:
        seat = "none"
    elif route.get("is_local") or route.get("provider") == "local":
        seat = "local"
    else:
        seat = "cloud"
    model = str(route.get("model") or "")
    tokens = _prompt_tokens(messages, system)
    rec: Dict[str, Any] = {
        "v": 1,
        "source": source,
        "decided_at": round(time.time(), 3),
        "seat": seat,
        "provider": (route.get("provider_name") or route.get("provider") or None),
        "model": model or None,
        "task_type": str(route.get("task_type") or "") or None,
        "mode": mode,
        "reason": _clip(route.get("reason") or route.get("why") or route.get("route_override")
                        or route.get("warning") or "") or None,
        "refused": refused,
        "vault_access": bool(route.get("vault_access")),
        "alternatives": [],
        "expected_cost": {"usd": None, "basis": ""},
        "context": {"messages": len(messages or []), "prompt_tokens_estimate": tokens},
        "fallback_policy": {},
        "latency_ms": None,
    }
    if route.get("override") == "computer_control":
        rec["reason"] = _clip("Computer Control is on: the cloud runs the screen tool loop"
                              + ("; the router had chosen %s" % route.get("overridden_local_model")
                                 if route.get("overridden_local_model") else ""))
    for name, fn in (("alternatives", lambda: _alternatives(seat, route, mode, routing_cfg)),
                     ("expected_cost", lambda: _expected_cost(seat, model, tokens)),
                     ("fallback_policy", lambda: _policy(mode, route, routing_cfg))):
        try:
            rec[name] = fn()
        except Exception:
            pass
    return rec


def summary(rec: Optional[dict]) -> str:
    """One line for a badge or a log: 'cloud claude-x: <reason>'."""
    if not isinstance(rec, dict):
        return ""
    head = "nothing sent" if rec.get("seat") == "none" else "%s %s" % (rec.get("seat"), rec.get("model") or "")
    return _clip("%s: %s" % (head.strip(), rec.get("reason") or ""), 200)

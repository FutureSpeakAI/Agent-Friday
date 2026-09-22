"""
Channel manager — registry, config, lifecycle, and the shared inbound funnel.

Every inbound channel message goes through ``handle_incoming`` which:
  1. checks the channel is enabled and the chat is on the allowlist,
  2. runs Friday's shared agent loop (services.agent._generate_agent),
  3. gates the reply through the egress gate (a channel is an egress),
before the adapter sends it. This guarantees a channel is a front-end, never a
governance bypass.

Config (non-secret) lives in ``~/.friday/channels.json``. Bot tokens live in the
credential store (``channel_<name>``), never in config.
"""
from __future__ import annotations

import json
import os
import threading
import time
from typing import Any, Dict, Optional
from agent_friday.paths import friday_home

# Friday's state root. Resolved centrally so FRIDAY_HOME redirects this
# module along with everything else (see agent_friday/paths.py).
FRIDAY_DIR = friday_home()
CONFIG_PATH = FRIDAY_DIR / "channels.json"

_LOCK = threading.Lock()
_ADAPTERS: Dict[str, Any] = {}   # name -> adapter instance (lazy)

#: The channels that exist. One tuple, declared once.
#:
#: This was the literal ("telegram", "discord") written out inside
#: `configure_channel`, `_make_adapter` and `status`, so adding a third channel
#: meant finding three places and the compiler could not help. The publishing
#: platforms next door solved the same problem with a declared module table
#: (`platforms/__init__.py:ADAPTER_MODULES`) and that package's own header says
#: it "mirrors services/channels/manager.py" - the mirror only ever went one
#: way. See docs/design/connector-ecosystem.md §2.4.
CHANNELS: tuple = ("telegram", "discord")

_SYSTEM_HINT = (
    "You are replying to the user over a messaging channel. Keep replies concise "
    "and plain-text friendly (no huge code dumps unless asked)."
)


# ── config I/O ────────────────────────────────────────────────────────────────
def _default_config() -> Dict[str, Any]:
    return {
        "enabled": False,
        "telegram": {"enabled": False, "allowlist": [], "poll_interval": 3.0},
        "discord": {"enabled": False, "allowlist": [], "poll_interval": 3.0},
    }


def load_config() -> Dict[str, Any]:
    try:
        if CONFIG_PATH.exists():
            cfg = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
            base = _default_config()
            base.update(cfg or {})
            return base
    except Exception:
        pass
    return _default_config()


def save_config(cfg: Dict[str, Any]) -> Dict[str, Any]:
    with _LOCK:
        try:
            FRIDAY_DIR.mkdir(parents=True, exist_ok=True)
            tmp = CONFIG_PATH.with_suffix(".json.tmp")
            tmp.write_text(json.dumps(cfg, indent=2), encoding="utf-8")
            os.replace(tmp, CONFIG_PATH)
            return {"ok": True}
        except Exception as e:
            return {"ok": False, "error": str(e)}


def configure_channel(name: str, opts: Dict[str, Any], *, token: Optional[str] = None) -> Dict[str, Any]:  # pragma: allowlist secret
    """Set a channel's non-secret options; store its token in the credential store."""
    name = _norm(name)
    if name not in CHANNELS:
        return {"ok": False, "error": f"unknown channel: {name}"}
    cfg = load_config()
    ch = cfg.get(name) or {}
    opts = opts or {}
    # Validate every caller-supplied option — this arrives straight from a JSON
    # body on the configure endpoint, so types are attacker-controllable.
    if "enabled" in opts:
        ch["enabled"] = bool(opts["enabled"])
    if "allowlist" in opts:
        raw = opts["allowlist"]
        if not isinstance(raw, (list, tuple)):
            return {"ok": False, "error": "allowlist must be a list of chat ids"}
        ch["allowlist"] = [str(c).strip()[:64] for c in raw if str(c).strip()][:100]
    if "poll_interval" in opts:
        try:
            ch["poll_interval"] = max(1.0, min(300.0, float(opts["poll_interval"])))
        except (TypeError, ValueError):
            return {"ok": False, "error": "poll_interval must be a number"}
    cfg[name] = ch
    res = save_config(cfg)
    if token:
        try:
            from agent_friday.services import credential_store
            credential_store.set_provider_key(f"channel_{name}", token)
        except Exception as e:
            return {"ok": False, "error": f"token store failed: {e}"}
    # push options to a live adapter if one exists
    a = _ADAPTERS.get(name)
    if a is not None:
        a.configure(ch)
    return res


# ── registry / lifecycle ──────────────────────────────────────────────────────
def _make_adapter(name: str):
    if name == "telegram":
        from agent_friday.services.channels.telegram_bridge import TelegramBridge
        return TelegramBridge()
    if name == "discord":
        from agent_friday.services.channels.discord_bridge import DiscordBridge
        return DiscordBridge()
    return None


def get_adapter(name: str):
    name = _norm(name)
    a = _ADAPTERS.get(name)
    if a is not None:
        return a
    # Double-checked locking: without this two concurrent requests (Flask is
    # threaded) can both build an adapter and both spawn a poll/gateway thread
    # for the same bot, double-dispatching every message.
    with _LOCK:
        a = _ADAPTERS.get(name)
        if a is not None:
            return a
        a = _make_adapter(name)
        if a is None:
            return None
        cfg = load_config().get(name) or {}
        a.configure(cfg)
        a.on_message(handle_incoming)
        _ADAPTERS[name] = a
        return a


def start_channel(name: str) -> Dict[str, Any]:
    cfg = load_config()
    if not cfg.get("enabled", False):
        return {"ok": False, "error": "channels disabled in settings"}
    a = get_adapter(name)
    if a is None:
        return {"ok": False, "error": f"unknown channel: {name}"}
    a.configure(cfg.get(_norm(name)) or {})
    return a.start()


def stop_channel(name: str) -> Dict[str, Any]:
    a = _ADAPTERS.get(_norm(name))
    if a is None:
        return {"ok": True, "already_stopped": True}
    return a.stop()


def test_channel(name: str, chat_id: str, text: str = "Friday here — channel test ✅") -> Dict[str, Any]:
    a = get_adapter(name)
    if a is None:
        return {"ok": False, "error": f"unknown channel: {name}"}
    return a.send(str(chat_id), text)


def _channel_health(name: str, running: bool, cfg: Dict[str, Any]):
    """One verdict for one channel, whether or not its adapter is instantiated.

    THERE WERE TWO IMPLEMENTATIONS OF THIS IN ONE PACKAGE. `ChannelAdapter.
    status()` returned `running` / `has_token` / `dependency_ok` / `enabled`
    as four loose booleans with no verdict, and `manager.status()` had a second
    code path for the not-yet-instantiated case that used
    `provider_key_status` for `has_token` and dropped `dependency_ok`
    entirely - so the same channel answered differently depending on which
    function you asked and whether a background thread had started yet.

    Derived here, once, so both paths agree. The token question is asked of
    the credential store either way, which is also what distinguishes a token
    that is MISSING from one that is present and undecryptable - the
    distinction that cost four provider keys their visibility.
    """
    from agent_friday.services import connector_health as _ch
    from agent_friday.services import credential_store

    ch = cfg.get(name) or {}
    if not ch.get("enabled", False):
        return _ch.Health(state=_ch.ABSENT, source="channels",
                          source_state="disabled",
                          summary="%s is switched off" % name.title())
    try:
        key_state = credential_store.provider_key_status("channel_%s" % name)
    except Exception as e:
        return _ch.unknown(detail="%s: %s" % (type(e).__name__, e),
                           source="channels")
    h = _ch.from_provider_key_status(key_state, name)
    if not h.healthy:
        return h
    if not running:
        # Configured, credential readable, nothing listening. Not a user
        # problem and not a failure - it has simply not been started.
        return _ch.Health(state=_ch.ABSENT, source="channels",
                          source_state="not-running", action="start",
                          summary="%s has a token but is not running"
                                  % name.title())
    return _ch.Health(state=_ch.WORKING, source="channels",
                      source_state="running",
                      summary="%s is running" % name.title())


def status() -> Dict[str, Any]:
    cfg = load_config()
    out = {"enabled": cfg.get("enabled", False), "channels": {}}
    for name in CHANNELS:
        a = _ADAPTERS.get(name)
        base = a.status() if a is not None else {
            "name": name, "running": False, "last_error": None,
        }
        base.setdefault("enabled", (cfg.get(name) or {}).get("enabled", False))
        health = _channel_health(name, bool(base.get("running")), cfg)
        # Asked of the credential store either way, so the instantiated and
        # not-yet-instantiated paths cannot disagree - which they did.
        try:
            from agent_friday.services import credential_store
            base["has_token"] = credential_store.provider_key_status(
                "channel_%s" % name) == "connected"
        except Exception:
            base["has_token"] = False
        base["health"] = health.as_dict()
        base["connected"] = health.healthy
        out["channels"][name] = base
    return out


# ── the shared inbound funnel ─────────────────────────────────────────────────
def _allowed(name: str, chat_id: str) -> bool:
    ch = load_config().get(name) or {}
    allow = [str(c) for c in (ch.get("allowlist") or [])]
    # Empty allowlist = closed by default (won't answer strangers).
    return str(chat_id) in allow


def _run_agent(text: str) -> str:
    """Run Friday's shared agent loop for a channel message. Isolated so tests
    can monkeypatch it without importing the heavy agent stack."""
    from agent_friday.services.agent import _generate_agent

    # `_system_prompt` predicts a provider and gates for it ONCE. A single
    # baked prompt must not be handed to _generate_agent's fallback ladder on
    # its own — the ladder can land on a DIFFERENT provider than predicted
    # when the first leg fails operationally, which would reuse a prompt
    # gated for the wrong destination (see the 2026-09 gauntlet audit in
    # docs/history/audits/).
    # `_gated_system_prompt(provider, ...)` builds it for an EXPLICIT
    # provider (no internal prediction) and is passed as `system_builder`, so
    # _generate_agent re-gates the prompt for whichever provider each leg —
    # first attempt and every fallback — actually is.
    reply, _trace = _generate_agent(
        [{"role": "user", "content": text}],
        system=_system_prompt(keywords=text),
        system_builder=lambda provider: _gated_system_prompt(provider, keywords=text),
        workspace="chat")
    return reply or ""


def _gated_system_prompt(provider: str, keywords: str = "") -> str:
    """Build the channel system prompt gated for an EXPLICIT provider — no
    internal prediction. Used as `_generate_agent`'s `system_builder` so every
    fallback leg gets a prompt gated for the provider it actually calls."""
    try:
        from agent_friday.services.model_router import (
            _gated_vault_control, _get_friday_system_prompt)
        return _get_friday_system_prompt(
            keywords=keywords, workspace="chat",
            provider=provider,
            vault_control=_gated_vault_control()) + "\n\n" + _SYSTEM_HINT
    except Exception:
        return _SYSTEM_HINT


def _system_prompt(keywords: str = "") -> str:
    """Predict the provider this message will route to and gate for it.
    Kept for the initial (pre-dispatch) prompt and backward compatibility;
    `_run_agent` also passes `_gated_system_prompt` as `system_builder` so
    every ladder leg is re-gated for the provider it actually calls."""
    try:
        from agent_friday.services.model_router import _predict_route_provider
        provider = _predict_route_provider(
            keywords=keywords, workspace="chat", has_tools=True)
    except Exception:
        provider = "cloud"
    return _gated_system_prompt(provider, keywords=keywords)


def gate_reply(text: str, channel: str) -> str:
    """Pass a channel reply through the egress gate (a channel is an egress)."""
    if not text:
        return text
    try:
        from agent_friday.services import egress_gate
        sealed = egress_gate.seal_outbound(
            {"messages": [{"role": "assistant", "content": text}]},
            provider=f"channel_{channel}")
        gated = sealed["messages"][0]["content"]
        if isinstance(gated, str):
            return gated or "[withheld: reply contained private content]"
        return text
    except Exception:
        # Fail-CLOSED backstop: the primary gate errored, so the content is
        # unverified. Release it ONLY if the classifier positively rates it
        # PUBLIC; on ANY non-public tier OR a second classifier failure, withhold.
        # (classify() returns an int Tier where PUBLIC == 1 — there is no tier 0,
        # so the previous `not in (0,)` check both misread the constant and, worse,
        # fell through to `return text` on double-failure, leaking ungated text.)
        try:
            from agent_friday.services import sensitivity_classifier as sc
            if int(sc.classify(text)) == sc.Tier.PUBLIC:
                return text
        except Exception:
            pass
        return "[withheld: reply could not be safety-checked]"


def handle_incoming(channel: str, chat_id: str, text: str) -> Optional[str]:
    """Funnel one inbound message → agent loop → egress gate → reply text."""
    channel = _norm(channel)
    if not isinstance(text, str) or not text.strip():
        return None
    text = text[:8000]  # bound hostile/oversized payloads before the agent loop
    cfg = load_config()
    if not cfg.get("enabled", False):
        return None
    if not (cfg.get(channel) or {}).get("enabled", False):
        return None
    if not _allowed(channel, chat_id):
        return None
    # DM pairing: channel messages deepen the relationship the same way the chat
    # UI does — feed the LOCAL user model so Friday personalizes across surfaces.
    # Best-effort; never blocks or fails the reply.
    try:
        from agent_friday.services import user_model as _um
        _um.observe_message(text, role="user", workspace="chat")
    except Exception:
        pass
    try:
        reply = _run_agent(text)
    except Exception as e:
        # NEVER leak the raw exception to an external channel — it can embed vault
        # paths, PII, or system-prompt fragments. Log the detail locally; send a
        # fixed, content-free notice.
        import logging
        logging.getLogger("friday.channels").warning("channel agent error: %s", e)
        return "(Friday hit an internal error handling that message.)"
    return gate_reply(reply, channel)


def _log_event(kind: str, **fields) -> None:
    fields.update({"kind": kind, "ts": time.time()})


def _norm(name: str) -> str:
    return (name or "").strip().lower()

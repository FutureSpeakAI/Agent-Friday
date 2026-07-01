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
from pathlib import Path
from typing import Any, Dict, Optional

_HOME = Path(os.environ.get("FRIDAY_HOME") or Path.home())
FRIDAY_DIR = _HOME / ".friday"
CONFIG_PATH = FRIDAY_DIR / "channels.json"

_LOCK = threading.Lock()
_ADAPTERS: Dict[str, Any] = {}   # name -> adapter instance (lazy)

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
    if name not in ("telegram", "discord"):
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


def status() -> Dict[str, Any]:
    cfg = load_config()
    out = {"enabled": cfg.get("enabled", False), "channels": {}}
    for name in ("telegram", "discord"):
        a = _ADAPTERS.get(name)
        if a is not None:
            out["channels"][name] = a.status()
        else:
            # not yet instantiated — report config + token presence only
            token_present = False
            try:
                from agent_friday.services import credential_store
                token_present = credential_store.provider_key_status(
                    f"channel_{name}") == "connected"
            except Exception:
                pass
            ch = cfg.get(name) or {}
            out["channels"][name] = {
                "name": name, "running": False, "has_token": token_present,
                "enabled": ch.get("enabled", False), "last_error": None,
            }
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
    reply, _trace = _generate_agent(
        [{"role": "user", "content": text}],
        system=_system_prompt(), workspace="chat")
    return reply or ""


def _system_prompt() -> str:
    try:
        from agent_friday.services.model_router import _get_friday_system_prompt
        return _get_friday_system_prompt(workspace="chat") + "\n\n" + _SYSTEM_HINT
    except Exception:
        return _SYSTEM_HINT


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

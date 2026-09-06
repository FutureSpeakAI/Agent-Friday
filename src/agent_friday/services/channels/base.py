"""
Channel adapter interface.

A ChannelAdapter bridges one messaging platform to Friday's agent loop. Adapters
own transport (polling / webhooks) and translation; they do NOT own the agent or
governance — inbound messages are handed to ``manager.handle_incoming`` which
runs the shared agent loop and gates the reply through the egress gate before
the adapter sends it.
"""
from __future__ import annotations

import threading
from typing import Any, Callable, Dict, Optional

_CHANNEL_SEND_WITHHELD = ("[withheld by egress gate — this message stayed "
                          "on this device]")


def _gate_channel_text(text: str, channel: str) -> str:
    """Gate any text before it reaches a channel adapter's transport.
    FAIL-CLOSED: any gate exception, including NeverSendBlocked, withholds
    rather than sends the raw text."""
    if not text:
        return text
    try:
        from agent_friday.services import egress_gate as _eg
    except Exception:
        return _CHANNEL_SEND_WITHHELD
    try:
        gated = _eg._gate_text(text, f"channel_{channel}", "channel.send")
        return gated if gated else _CHANNEL_SEND_WITHHELD
    except _eg.NeverSendBlocked:
        return _CHANNEL_SEND_WITHHELD
    except Exception:
        return _CHANNEL_SEND_WITHHELD


class ChannelAdapter:
    """Base class for a messaging-platform bridge.

    Subclasses implement ``_poll_once`` (or override ``start``/``stop``) and
    ``send``. Everything else — the poll loop, the inbound handler wiring,
    status — is provided here.
    """

    name: str = "channel"

    def __init__(self) -> None:
        self._config: Dict[str, Any] = {}
        self._handler: Optional[Callable[[str, str, str], Optional[str]]] = None
        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()
        self._running = False
        self._last_error: Optional[str] = None

    # ── configuration ────────────────────────────────────────────────────────
    def configure(self, config: Dict[str, Any]) -> None:
        """Apply non-secret options (allowlist, poll interval, etc.).

        Secrets (bot tokens) are pulled from the credential store by ``token()``,
        never passed in the clear here.
        """
        self._config = dict(config or {})

    def on_message(self, handler: Callable[[str, str, str], Optional[str]]) -> None:
        """Register the inbound handler: fn(channel_name, chat_id, text) -> reply|None."""
        self._handler = handler

    # ── secret access (via credential store) ─────────────────────────────────
    def token(self) -> Optional[str]:
        try:
            from agent_friday.services import credential_store
            return credential_store.get_provider_key(f"channel_{self.name}")
        except Exception:
            return None

    def has_token(self) -> bool:
        return bool(self.token())

    # ── dependency probe (subclasses override when a lib is needed) ───────────
    def dependency_ok(self) -> bool:
        return True

    # ── lifecycle ────────────────────────────────────────────────────────────
    def start(self) -> Dict[str, Any]:
        if self._running:
            return {"ok": True, "already": True}
        if not self.dependency_ok():
            return {"ok": False, "error": "missing_dependency"}
        if not self.has_token():
            return {"ok": False, "error": "no_token"}
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name=f"channel-{self.name}",
                                        daemon=True)
        self._running = True
        self._thread.start()
        return {"ok": True}

    def stop(self) -> Dict[str, Any]:
        self._stop.set()
        self._running = False
        return {"ok": True}

    def _loop(self) -> None:
        try:
            interval = float(self._config.get("poll_interval", 3.0))
        except (TypeError, ValueError):
            interval = 3.0
        # Clamp: a zero/negative interval would busy-spin the poll thread and
        # hammer the platform API; an absurd one would wedge the channel.
        interval = max(1.0, min(300.0, interval))
        while not self._stop.is_set():
            try:
                self._poll_once()
            except Exception as e:  # a transient poll error must not kill the loop
                self._last_error = str(e)
            self._stop.wait(interval)

    # ── to implement ─────────────────────────────────────────────────────────
    def _poll_once(self) -> None:
        raise NotImplementedError

    def _send_raw(self, chat_id: str, text: str) -> Dict[str, Any]:
        """Adapter transport — implement this, not ``send()``."""
        raise NotImplementedError

    # security-boundary.md §19 row 6: this used to be the abstract method
    # every adapter overrode directly, so only the reply path
    # (manager.handle_incoming -> gate_reply -> self.send()) was gated —
    # any OTHER caller of adapter.send() (manager.test_channel(), wired to
    # the /test route with a caller-supplied `text`) bypassed the gate
    # entirely. Gating HERE, once, means the reply path is no longer the
    # only guarded door, and no future adapter can add a new one.
    def send(self, chat_id: str, text: str) -> Dict[str, Any]:
        gated = _gate_channel_text(text, self.name)
        return self._send_raw(chat_id, gated)

    # ── inbound dispatch (adapters call this per received message) ────────────
    def _dispatch(self, chat_id: str, text: str) -> None:
        if not self._handler:
            return
        if not isinstance(text, str):
            text = "" if text is None else str(text)
        # Truncate hostile/oversized inbound payloads before the agent loop —
        # adapters also cap, but the shared funnel must not rely on it.
        reply = self._handler(self.name, str(chat_id), text[:8000])
        if reply:
            try:
                self.send(str(chat_id), reply)
            except Exception as e:
                self._last_error = str(e)

    # ── status ───────────────────────────────────────────────────────────────
    def status(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "running": self._running,
            "has_token": self.has_token(),
            "dependency_ok": self.dependency_ok(),
            "last_error": self._last_error,
            "enabled": bool(self._config.get("enabled", False)),
        }

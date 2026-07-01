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
        interval = float(self._config.get("poll_interval", 3.0))
        while not self._stop.is_set():
            try:
                self._poll_once()
            except Exception as e:  # a transient poll error must not kill the loop
                self._last_error = str(e)
            self._stop.wait(interval)

    # ── to implement ─────────────────────────────────────────────────────────
    def _poll_once(self) -> None:
        raise NotImplementedError

    def send(self, chat_id: str, text: str) -> Dict[str, Any]:
        raise NotImplementedError

    # ── inbound dispatch (adapters call this per received message) ────────────
    def _dispatch(self, chat_id: str, text: str) -> None:
        if not self._handler:
            return
        reply = self._handler(self.name, str(chat_id), text or "")
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

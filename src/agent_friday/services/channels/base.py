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


def _mask_chat(chat_id) -> str:
    """A chat id in a receipt: enough to tell two chats apart, not to find one."""
    c = str(chat_id or "")
    return ("…" + c[-4:]) if len(c) > 4 else "…"


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


_CHANNEL_HOSTS = {"telegram": "api.telegram.org", "discord": "discord.com"}


def record_channel_send(channel: str, chat_id: str, text: str, ok: bool, unsure: bool = False,
                        decision=None) -> None:
    """A message to a chat channel in the egress ledger and on the privacy map
    (§26.7): which platform, the gated words fingerprinted and, by default
    (A20), kept encrypted. A send that may have arrived (a timeout) is
    sent_unconfirmed and keeps its words. `decision`: the checkpoint's
    decision for the send; the row names its action id (A10). Never raises."""
    try:
        from agent_friday.services import egress_gate as _eg
        _eg.record_service_send("chat_channel", f"channel:{channel}"[:80],
                                destination=_CHANNEL_HOSTS.get(channel, channel),
                                content={"chat": str(chat_id)[:80], "text": text},
                                result="sent" if ok else ("sent_unconfirmed" if unsure else "failed"),
                                recipients=1, part="message", action_id=_eg.action_id_of(decision))
    except Exception:
        pass


def send_unsure(exc) -> bool:
    """True when a send that raised may still have reached the platform (a
    timeout, a dropped connection, a server error), so it is recorded as
    sent_unconfirmed and its words kept. Never raises."""
    try:
        from agent_friday.services import egress_gate as _eg
        return _eg.result_for_error(exc) == "sent_unconfirmed"
    except Exception:
        return True


class ChannelAdapter:
    """Base class for a messaging-platform bridge.

    Subclasses implement ``_poll_once`` (or override ``start``/``stop``) and
    ``send``. Everything else — the poll loop, the inbound handler wiring,
    status — is provided here.
    """

    name: str = "channel"
    #: The most characters the platform's send carries; the record keeps
    #: what was sent, not words that were cut off (0: no limit).
    max_chars: int = 0

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
        from agent_friday.services.cloud_intent import outbound_held
        _held = outbound_held("connector", "channel " + str(self.name))   # A16a
        if _held:
            return {"ok": False, "error": "route_not_chosen", "detail": _held}
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
        from agent_friday.services.cloud_intent import outbound_held
        _held = outbound_held("connector", "channel send " + str(self.name))   # A16a
        if _held:
            return {"ok": False, "error": "route_not_chosen", "detail": _held}
        gated = _gate_channel_text(text, self.name)
        passed, decision = self._checkpoint_ref(chat_id)
        if not passed:
            return {"ok": False, "error": "held by the action checkpoint"}
        from agent_friday.governance import action_gate as _gate
        try:
            res = self._send_raw(chat_id, gated)
        except Exception:
            _gate.close_external(decision, False, detail="transport_error")
            raise
        ok = bool(isinstance(res, dict) and res.get("ok"))
        unsure = bool(isinstance(res, dict) and res.get("unsure"))
        # the service's own id for the message is its confirmation
        verified = ok and bool(isinstance(res, dict) and (res.get("message_id") or res.get("id")))
        self.report_outbound(decision, ok, unsure, verified=verified)
        record_channel_send(self.name, chat_id, gated[:self.max_chars] if self.max_chars else gated,
                            ok, unsure, decision=decision)
        return res

    @staticmethod
    def report_outbound(decision, ok: bool, unsure: bool = False, *, verified: bool = False) -> None:
        """The send's outcome for its checkpoint decision: sent -> executed
        (completed_verified when the service returned the message's id); not
        sent -> failed; a send the transport could not confirm either way ->
        unconfirmed (not terminal, and never counted as done)."""
        from agent_friday.governance import action_gate as _gate
        if unsure:
            _gate.close_external(decision, True, detail="unsure", outcome=_gate.UNCONFIRMED)
        else:
            _gate.close_external(decision, ok, detail="sent" if ok else "not_sent",
                                 verified=bool(ok and verified))

    def prepare_outbound_ref(self, chat_id: str, text: str):
        """(text, decision) for an adapter that sends on its own (a gateway's
        reply): the egress gate's text after the action checkpoint's receipt,
        or (None, None) when the checkpoint holds it. The adapter reports the
        send with `report_outbound(decision, ok, unsure)`."""
        gated = _gate_channel_text(text, self.name)
        passed, decision = self._checkpoint_ref(chat_id)
        return (gated, decision) if passed else (None, None)

    def prepare_outbound(self, chat_id: str, text: str):
        """`prepare_outbound_ref` for a caller that cannot report the send: its
        decision is marked unconfirmed at once (nobody will say what became of
        it), so it is never left open nor counted as done."""
        gated, decision = self.prepare_outbound_ref(chat_id, text)
        if gated is not None:
            from agent_friday.governance import action_gate as _gate
            _gate.close_external(decision, True, detail="not_reported", outcome=_gate.UNCONFIRMED)
        return gated

    def _checkpoint(self, chat_id: str) -> bool:
        """The action checkpoint's signed receipt for this send
        (action_gate.record_external: the cLaws intact, no hold)."""
        return self._checkpoint_ref(chat_id)[0]

    def _checkpoint_ref(self, chat_id: str):
        """`_checkpoint` as (passed, the decision for the send's outcome)."""
        try:
            from agent_friday.governance import action_gate as _gate
            return True, _gate.record_external("channel:%s:send" % self.name, surface="channel",
                                               target=_mask_chat(chat_id))
        except Exception as e:  # Held, or the receipt could not be written
            self._last_error = "held: %s" % e
            return False, None

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

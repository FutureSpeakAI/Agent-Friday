"""
Channel adapter interface.

A ChannelAdapter bridges one messaging platform to Friday's agent loop. Adapters
own transport (polling / webhooks) and translation; they do NOT own the agent or
governance — inbound messages are handed to ``manager.handle_incoming`` which
runs the shared agent loop and gates the reply through the egress gate before
the adapter sends it.
"""
from __future__ import annotations

import logging
import threading
from typing import Any, Callable, Dict, Optional

_log = logging.getLogger("friday.channels")

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


def record_channel_send(channel: str, chat_id: str, text: str, ok: bool, unsure: bool = False) -> None:
    """A message to a chat channel in the egress ledger and on the privacy map
    (§26.7): which platform, the gated words fingerprinted and, by default
    (A20), kept encrypted. A send that may have arrived (a timeout) is
    sent_unconfirmed and keeps its words. Never raises."""
    try:
        from agent_friday.services import egress_gate as _eg
        _eg.record_service_send("chat_channel", f"channel:{channel}"[:80],
                                destination=_CHANNEL_HOSTS.get(channel, channel),
                                content={"chat": str(chat_id)[:80], "text": text},
                                result="sent" if ok else ("sent_unconfirmed" if unsure else "failed"),
                                recipients=1, part="message")
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
        self._turn_pool = None          # the turn worker's queue (submit_turn; BD-13)
        self._turn_lock = threading.Lock()

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
        with self._turn_lock:
            q, self._turn_pool = self._turn_pool, None
        if q is not None:
            # Turns still waiting are dropped, not run (their messages were already read);
            # the worker ends after the turn it is running, if any.
            while True:
                try:
                    item = q.get_nowait()
                except Exception:
                    break
                if item is not None:
                    item[0].cancel()
            try:
                q.put_nowait(None)
            except Exception:
                pass
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
        if not self._checkpoint(chat_id):
            return {"ok": False, "error": "held by the action checkpoint"}
        res = self._send_raw(chat_id, gated)
        ok = bool(isinstance(res, dict) and res.get("ok"))
        unsure = bool(isinstance(res, dict) and res.get("unsure"))
        record_channel_send(self.name, chat_id, gated[:self.max_chars] if self.max_chars else gated,
                            ok, unsure)
        return res

    def prepare_outbound(self, chat_id: str, text: str):
        """What may leave on this channel, for an adapter that sends on its
        own (a gateway's reply): the egress gate's text after the action
        checkpoint's receipt, or None when the checkpoint holds it."""
        gated = _gate_channel_text(text, self.name)
        return gated if self._checkpoint(chat_id) else None

    def _checkpoint(self, chat_id: str) -> bool:
        """The action checkpoint's signed receipt for this send
        (action_gate.record_external: the cLaws intact, no hold)."""
        try:
            from agent_friday.governance import action_gate as _gate
            _gate.record_external("channel:%s:send" % self.name, surface="channel",
                                  target=_mask_chat(chat_id))
            return True
        except Exception as e:  # Held, or the receipt could not be written
            self._last_error = "held: %s" % e
            return False

    # ── inbound dispatch (adapters call this per received message) ────────────
    #: Turns waiting behind a running one; more are refused (a flood behind a stuck turn).
    MAX_QUEUED_TURNS = 50

    def submit_turn(self, fn: Callable[..., Any], *args: Any):
        """Run `fn(*args)` on the adapter's one turn worker; returns a Future, or None when the
        turn was refused (the adapter is stopped, or too many are waiting).

        A turn runs there, not on the reading thread, so a slow or stuck turn never stops the
        next message being read: that message may be "stop everything" (BD-13). One worker keeps
        replies in order. It is a daemon thread: a stuck turn never holds Friday's exit."""
        import queue
        from concurrent.futures import Future
        fut: Future = Future()
        with self._turn_lock:
            if self._stop.is_set():
                self._last_error = "a message arrived after this channel stopped; not run"
                return None
            if self._turn_pool is None:
                q: "queue.Queue" = queue.Queue(maxsize=self.MAX_QUEUED_TURNS)
                threading.Thread(target=self._work, args=(q,), name="channel-%s-turns" % self.name,
                                 daemon=True).start()
                self._turn_pool = q
            try:
                self._turn_pool.put_nowait((fut, fn, args))
            except queue.Full:
                self._last_error = "too many messages waiting behind a slow reply; one was dropped"
                _log.warning("channel %s: turn queue full, a message was dropped", self.name)
                return None
        return fut

    @staticmethod
    def _work(q) -> None:
        while True:
            item = q.get()
            if item is None:
                return
            fut, fn, args = item
            if not fut.set_running_or_notify_cancel():
                continue
            try:
                fut.set_result(fn(*args))
            except BaseException as e:  # noqa: BLE001 - handed to whoever waits on the Future
                fut.set_exception(e)

    @staticmethod
    def is_stop(text: str) -> bool:
        try:
            from agent_friday.services import global_stop
            return global_stop.is_stop_everything(text)
        except Exception:
            return False

    def _dispatch(self, chat_id: str, text: str) -> None:
        if not self._handler:
            return
        if not isinstance(text, str):
            text = "" if text is None else str(text)
        # Truncate hostile/oversized inbound payloads before the agent loop —
        # adapters also cap, but the shared funnel must not rely on it.
        text = text[:8000]
        if self.is_stop(text):
            self._deliver(str(chat_id), text)        # at once, on the reading thread
            return
        self.submit_turn(self._deliver, str(chat_id), text)

    def _deliver(self, chat_id: str, text: str) -> None:
        try:
            reply = self._handler(self.name, chat_id, text)
        except Exception as e:  # noqa: BLE001 - one turn failing never stops the reader
            self._last_error = str(e)
            _log.warning("channel %s: a turn failed: %s", self.name, type(e).__name__)
            return
        if reply:
            try:
                self.send(chat_id, reply)
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

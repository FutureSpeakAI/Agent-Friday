"""
Telegram channel bridge.

Uses the Telegram Bot API over stdlib ``urllib`` (long-poll ``getUpdates``) — no
third-party dependency required, so it works on a bare install. The bot token is
read from the credential store (``channel_telegram``), never from config.
"""
from __future__ import annotations

import hashlib
import json
import os
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Dict, List

from agent_friday.services.channels.base import ChannelAdapter
from agent_friday.user_errors import exception_text

_API = "https://api.telegram.org/bot{token}/{method}"


class TelegramBridge(ChannelAdapter):
    name = "telegram"
    max_chars = 4096

    def __init__(self) -> None:
        super().__init__()
        self._offset = 0

    # ── transport ────────────────────────────────────────────────────────────
    def _call(self, method: str, params: Dict[str, Any], timeout: float = 30.0) -> Dict[str, Any]:
        token = self.token()  # pragma: allowlist secret
        if not token:
            return {"ok": False, "error": "no_token"}
        url = _API.format(token=token, method=method)
        data = urllib.parse.urlencode(params).encode("utf-8")
        req = urllib.request.Request(url, data=data)
        # egress: sendMessage is recorded by ChannelAdapter.send; getUpdates carries no owner content
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))

    # ── the confirmed offset, kept across restarts (NS-34.16-4) ──────────────
    # Telegram hands an update out again until getUpdates is asked from past it.
    # Held only in memory, a restart asked from 0 and answered every message it
    # had already answered a second time. The offset past an update is written
    # to disk BEFORE the update is acted on: a crash mid-reply loses that reply
    # (the owner can ask again) and never sends it twice.
    def _offset_file(self) -> Path:
        from agent_friday.paths import friday_home
        return Path(friday_home()) / "channels" / "telegram-offset.json"

    def _bot(self) -> str:
        """Which bot the offset belongs to (a hash of its token): another bot's
        updates are numbered on their own."""
        return hashlib.sha256((self.token() or "").encode("utf-8")).hexdigest()[:16]

    def _load_offset(self) -> None:
        """Once per bot: the kept offset, if it is this bot's. The file holds
        only a short hash of the token and a number (no message content)."""
        try:
            bot = self._bot()
        except Exception:  # noqa: BLE001 - the credential store could not be read
            bot = ""
        if getattr(self, "_offset_loaded", False) and bot == getattr(self, "_offset_bot", None):
            return
        if getattr(self, "_offset_loaded", False):
            self._offset = 0                     # another bot (the token changed): its own numbering
        self._offset_loaded = True
        self._offset_bot = bot
        try:
            kept = json.loads(self._offset_file().read_text(encoding="utf-8"))
            if isinstance(kept, dict) and kept.get("bot") == self._offset_bot:
                self._offset = max(int(getattr(self, "_offset", 0) or 0), int(kept.get("offset") or 0))
        except (OSError, ValueError, TypeError):
            pass

    def _save_offset(self) -> None:
        """Never raises: a write that fails leaves the poll as it was before
        (the offset held in memory), and says so once in the log."""
        try:
            p = self._offset_file()
            p.parent.mkdir(parents=True, exist_ok=True)
            tmp = p.with_name(p.name + ".tmp")
            tmp.write_text(json.dumps({"bot": getattr(self, "_offset_bot", "") or self._bot(),
                                       "offset": int(self._offset)}), encoding="utf-8")
            os.replace(tmp, p)
        except Exception as e:  # noqa: BLE001
            if not getattr(self, "_offset_warned", False):
                self._offset_warned = True
                import logging
                logging.getLogger(__name__).warning(
                    "telegram: the reply offset could not be kept on disk (%s); a restart may "
                    "answer the last messages again", type(e).__name__)

    def _poll_once(self) -> None:
        # Long-poll for new updates. Timeout is short here (the loop cadence
        # provides the spacing); a production deploy would raise it.
        self._load_offset()
        resp = self._call("getUpdates",
                          {"offset": self._offset, "timeout": 0, "limit": 20},
                          timeout=10.0)
        for update in resp.get("result", []) or []:
            if not isinstance(update, dict):
                continue  # a malformed update must not kill the whole batch
            try:
                self._offset = max(self._offset, int(update.get("update_id", 0)) + 1)
            except (TypeError, ValueError):
                pass
            self._save_offset()            # on disk before it is answered: never answered twice
            msg = update.get("message") or update.get("edited_message") or {}
            if not isinstance(msg, dict):
                continue
            text = msg.get("text")
            chat_obj = msg.get("chat")
            chat = chat_obj.get("id") if isinstance(chat_obj, dict) else None
            if isinstance(text, str) and text and chat is not None:
                self._dispatch(str(chat), text)

    def _send_raw(self, chat_id: str, text: str) -> Dict[str, Any]:
        try:
            r = self._call("sendMessage",
                          {"chat_id": chat_id, "text": text[:4096]})
            sent = r.get("result") if isinstance(r.get("result"), dict) else {}
            return {"ok": bool(r.get("ok")),
                    **({"message_id": sent["message_id"]} if r.get("ok") and sent.get("message_id") else {})}
        except Exception as e:
            self._last_error = exception_text(e)
            from agent_friday.services.channels.base import send_unsure
            return {"ok": False, "error": exception_text(e), "unsure": send_unsure(e)}

    # ── helpers for tests / callers ──────────────────────────────────────────
    @staticmethod
    def parse_updates(payload: Dict[str, Any]) -> List[Dict[str, str]]:
        """Extract (chat_id, text) pairs from a getUpdates payload. Pure."""
        out = []
        for update in (payload or {}).get("result", []) or []:
            if not isinstance(update, dict):
                continue
            msg = update.get("message") or update.get("edited_message") or {}
            if not isinstance(msg, dict):
                continue
            text = msg.get("text")
            chat_obj = msg.get("chat")
            chat = chat_obj.get("id") if isinstance(chat_obj, dict) else None
            if isinstance(text, str) and text and chat is not None:
                out.append({"chat_id": str(chat), "text": text})
        return out

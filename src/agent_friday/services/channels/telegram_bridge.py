"""
Telegram channel bridge.

Uses the Telegram Bot API over stdlib ``urllib`` (long-poll ``getUpdates``) — no
third-party dependency required, so it works on a bare install. The bot token is
read from the credential store (``channel_telegram``), never from config.
"""
from __future__ import annotations

import json
import urllib.parse
import urllib.request
from typing import Any, Dict, List

from agent_friday.services.channels.base import ChannelAdapter

_API = "https://api.telegram.org/bot{token}/{method}"


class TelegramBridge(ChannelAdapter):
    name = "telegram"

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
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))

    def _poll_once(self) -> None:
        # Long-poll for new updates. Timeout is short here (the loop cadence
        # provides the spacing); a production deploy would raise it.
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
            msg = update.get("message") or update.get("edited_message") or {}
            if not isinstance(msg, dict):
                continue
            text = msg.get("text")
            chat_obj = msg.get("chat")
            chat = chat_obj.get("id") if isinstance(chat_obj, dict) else None
            if isinstance(text, str) and text and chat is not None:
                self._dispatch(str(chat), text)

    def send(self, chat_id: str, text: str) -> Dict[str, Any]:
        try:
            r = self._call("sendMessage",
                          {"chat_id": chat_id, "text": text[:4096]})
            return {"ok": bool(r.get("ok"))}
        except Exception as e:
            self._last_error = str(e)
            return {"ok": False, "error": str(e)}

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

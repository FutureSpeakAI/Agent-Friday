"""
Discord channel bridge.

Uses ``discord.py`` when installed (gateway websocket). When the library isn't
present the bridge degrades gracefully — ``dependency_ok`` reports False and
``start`` returns ``missing_dependency`` rather than raising — so a bare install
never breaks. The bot token is read from the credential store
(``channel_discord``).

Install the optional dependency with:  pip install "agent-friday[channels]"
"""
from __future__ import annotations

from typing import Any, Dict

from agent_friday.services.channels.base import ChannelAdapter
from agent_friday.user_errors import ExceptionText, exception_text


def _discord_available() -> bool:
    try:
        import importlib.util
        return importlib.util.find_spec("discord") is not None
    except Exception:
        return False


class DiscordBridge(ChannelAdapter):
    name = "discord"
    max_chars = 2000

    def __init__(self) -> None:
        super().__init__()
        self._client = None

    def dependency_ok(self) -> bool:
        return _discord_available()

    def start(self) -> Dict[str, Any]:
        if not self.dependency_ok():
            self._last_error = "discord.py not installed"
            return {"ok": False, "error": "missing_dependency",
                    "hint": "pip install 'agent-friday[channels]'"}
        return super().start()

    def _loop(self) -> None:
        """Run the discord.py client in this thread's own event loop.

        discord.py is gateway-based, not poll-based, so we override the base
        poll loop and hand control to the library. Kept lazy + guarded so the
        module imports fine without the dependency.
        """
        try:
            import asyncio
            import discord  # type: ignore
        except Exception as e:
            self._last_error = ExceptionText(f"discord import failed: {e}")
            self._running = False
            return

        token = self.token()  # pragma: allowlist secret
        if not token:
            self._last_error = "no_token"
            self._running = False
            return

        intents = discord.Intents.default()
        intents.message_content = True
        client = discord.Client(intents=intents)
        self._client = client
        allowlist = set(str(c) for c in (self._config.get("allowlist") or []))

        @client.event
        async def on_message(message):  # noqa: ANN001
            if message.author == client.user:
                return
            chat_id = str(message.channel.id)
            if allowlist and chat_id not in allowlist:
                return
            if not self._handler:
                return
            text = (message.content or "")[:8000]
            if self.is_stop(text):
                # "stop everything" (BD-13): at once, on a thread of its own, not behind a turn
                # and without holding the gateway loop while the lanes are walked.
                reply = await asyncio.to_thread(self._handler, self.name, chat_id, text)
            else:
                # Any other turn runs on the adapter's turn worker, so a stuck turn never blocks
                # the gateway loop and the next message (perhaps a stop) is still read.
                fut = self.submit_turn(self._handler, self.name, chat_id, text)
                if fut is None:
                    return
                reply = await asyncio.wrap_future(fut)
            if reply:
                # the same door as every other send: the egress gate and the
                # action checkpoint's receipt (base.prepare_outbound), then the
                # send record (base.record_channel_send)
                gated = self.prepare_outbound(chat_id, reply)
                if gated:
                    from agent_friday.services.channels.base import record_channel_send, send_unsure
                    gated = gated[:2000]
                    try:
                        await message.channel.send(gated)
                    except Exception as e:
                        record_channel_send(self.name, chat_id, gated, False, send_unsure(e))
                        raise
                    record_channel_send(self.name, chat_id, gated, True)

        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(client.start(token))
        except Exception as e:
            self._last_error = exception_text(e)
        finally:
            self._running = False

    def _poll_once(self) -> None:  # not used — gateway-driven
        pass

    def _send_raw(self, chat_id: str, text: str) -> Dict[str, Any]:
        """Send outside the gateway loop (used by the /test endpoint).

        Uses the REST API directly via stdlib so a one-off test message works
        even before the gateway client is running. Best-effort.
        """
        token = self.token()  # pragma: allowlist secret
        if not token:
            return {"ok": False, "error": "no_token"}
        try:
            import json
            import urllib.request
            url = f"https://discord.com/api/v10/channels/{chat_id}/messages"
            data = json.dumps({"content": text[:2000]}).encode("utf-8")
            req = urllib.request.Request(url, data=data, method="POST")
            req.add_header("Authorization", f"Bot {token}")
            req.add_header("Content-Type", "application/json")
            # egress: recorded by ChannelAdapter.send, the only caller
            with urllib.request.urlopen(req, timeout=15) as resp:
                return {"ok": resp.status in (200, 201)}
        except Exception as e:
            self._last_error = exception_text(e)
            from agent_friday.services.channels.base import send_unsure
            return {"ok": False, "error": exception_text(e), "unsure": send_unsure(e)}

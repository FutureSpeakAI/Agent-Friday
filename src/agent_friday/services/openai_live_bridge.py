"""/ws/openai-live (services/openai_live_bridge): the browser <-> OpenAI Realtime bridge, the third voice engine.

Speaks the SAME browser contract as ``/ws/live`` and ``/ws/voice-local`` so the page's mic capture, the
friday-pcm-player, barge-in and the audio-reactive avatar are reused unchanged:

  browser -> server: {type:'audio', data:<b64 PCM16@16k>} | {type:'text', text} | {type:'barge'}
                     | {type:'speaking', on} | {type:'end'}
  server -> browser: {type:'status'} {type:'input_transcript'} {type:'text'} {type:'audio', data:<b64 PCM16@24k>}
                     {type:'interrupted'} {type:'turn_end'} {type:'cost'} {type:'error', error, detail, offer}

Why a server-side bridge and not browser WebRTC: every tool call runs here, in Friday's process, through the one
tool path the other voice engines use (``_live_card_tool`` -> ActionGate, approval cards, receipts), and every
word that leaves the machine (the instruction, each tool result, the mic audio) passes the egress gate or its
ledger first. The rules (may it answer, what a minute costs, the owner's cap, the visible hand-over) live in
``services/openai_realtime``; the TV app holds the same ones.

Nothing here starts on its own: the page connects only when the owner chose OpenAI voice (``voice_engine ==
"openai"``). A refusal is sent as an ``error`` frame with a sentence and an offer, never a silent engine swap.
"""
from __future__ import annotations

import asyncio
import base64
import json
import logging
import threading
import time
from typing import Any, Callable, Dict, Optional

from agent_friday.services import openai_realtime as orx

_log = logging.getLogger("friday.openai_live_bridge")

#: Process-wide: until when OpenAI is blocked because the account is out of credit.
_BLOCKED_UNTIL = [0.0]
_PURPOSE = "voice.openai"
_EGRESS_PROVIDER = "openai"


def _connect(url: str, headers: Dict[str, str]):
    """The upstream socket. A seam: tests replace it with a fake."""
    import simple_websocket
    return simple_websocket.Client.connect(url, headers=headers)


def month_spent_usd() -> float:
    """What OpenAI voice cost this month, from the cost meter's own rows."""
    try:
        from agent_friday.services import cost_meter
        row = (cost_meter.summary("month").get("by_purpose") or {}).get(_PURPOSE) or {}
        return float(row.get("usd") or 0.0)
    except Exception:
        return 0.0


def _settings() -> dict:
    try:
        from agent_friday.core import _load_settings
        return _load_settings() or {}
    except Exception:
        return {}


def _cap(settings: dict) -> Optional[float]:
    try:
        v = settings.get("voice_openai_monthly_cap_usd")
        return None if v in (None, "") else max(0.0, float(v))
    except (TypeError, ValueError):
        return None


def _local_only(settings: dict) -> bool:
    return str(((settings.get("model_routing") or {}).get("mode")) or "").strip().lower() == "local_only"


def refusal(settings: dict, *, now: Optional[float] = None) -> Optional[Dict[str, Any]]:
    """None when OpenAI may answer; else the ``error`` frame body: reason, a sentence, and the offer."""
    r = orx.readiness(key_status=orx.key_state(), local_only=_local_only(settings),
                      spent_month_usd=month_spent_usd(), cap_month_usd=_cap(settings),
                      blocked_until=_BLOCKED_UNTIL[0], now=time.time() if now is None else now)
    if r["ok"]:
        try:
            from agent_friday.services import spend_guard
            spend_guard.check("openai", what="OpenAI voice", model=orx.MODEL)
        except Exception as e:                      # SpendCapReached: the owner's global hard stop
            if type(e).__name__ == "SpendCapReached":
                return {"error": "spend_cap", "detail": str(e), "offer": None}
        return None
    off = orx.failover(r["reason"], gemini_ready=_gemini_ready(settings), local_ready=_local_ready(),
                       auto=bool(settings.get("voice_openai_auto_failover")))
    return {"error": r["reason"], "detail": _with_offer(r["say"], off), "offer": off}


def _with_offer(say: str, off: Optional[dict]) -> str:
    """The page shows ``detail`` and nothing else of an error frame, so the offer is said in it too."""
    return ("%s %s" % (say, off["say"])).strip() if off and off.get("say") else say


def _gemini_ready(settings: dict) -> bool:
    if _local_only(settings):
        return False
    try:
        from agent_friday.services.voice_engine import resolve_gemini_key
        return bool(resolve_gemini_key().get("valid"))
    except Exception:
        return False


def _local_ready() -> bool:
    try:
        from agent_friday.services.voice_engine import get_local_voice_engine
        return bool(get_local_voice_engine().available())
    except Exception:
        return False


def _gate(text: str, label: str, withheld: str) -> str:
    """Fail closed, as the Gemini Live twins do: a gate that cannot run withholds, it never sends raw."""
    try:
        from agent_friday.services import egress_gate as eg
        out = eg._gate_text(text, _EGRESS_PROVIDER, label)
        return out if out else withheld
    except Exception as e:
        _log.warning("OpenAI voice %s gating unavailable or blocked (%s): withheld", label, type(e).__name__)
        return withheld


WITHHELD_INSTRUCTION = ("You are Friday. Your full context could not be checked before leaving this PC, so "
                        "you have none. Say so briefly, and help only with what the owner says to you now.")
WITHHELD_RESULT = "(The result was held back by Friday's privacy check and is not shown.)"


def build_instruction(settings: dict) -> str:
    """The Friday persona + context, sealed and gated exactly like the other cloud voice engines' instruction."""
    from agent_friday.routes import voice as V
    prompt = V._build_voice_system_prompt(settings)[0]
    try:
        prompt += V._voice_tool_surface_note()
    except Exception:
        pass
    from agent_friday.services.action_policy import seal_system_prompt
    prompt = seal_system_prompt(prompt, "OpenAI Realtime prompt")
    return _gate(prompt, "voice_system_instruction", WITHHELD_INSTRUCTION)


def build_tools() -> list:
    from agent_friday.services.voice_engine import build_voice_tool_contract
    return build_voice_tool_contract(full=False, compact=False)["tools"]


class Bridge:
    """One voice session. ``up`` is the upstream socket (send/receive/close); ``send`` writes a frame to the page."""

    def __init__(self, up, send: Callable[[dict], bool], settings: dict, *,
                 run_tool: Optional[Callable[..., Any]] = None, model: str = orx.MODEL,
                 clock: Callable[[], float] = time.time):
        self.up, self.send, self.settings, self.model, self.clock = up, send, settings, model, clock
        self.run_tool = run_tool or _run_tool
        self.done = threading.Event()
        self.session_cost = 0.0
        self.bytes_in = 0
        self.session: Dict[str, Any] = {}
        self.item_id: Optional[str] = None
        self.audio_ms = 0.0
        self.first_audio_at = 0.0
        self.last_heard_at = self.clock()
        self._lock = threading.Lock()

    # page -> OpenAI
    def to_upstream(self, msg: dict) -> None:
        t = msg.get("type")
        if t == "audio":
            raw = base64.b64decode(msg.get("data") or "")
            self.bytes_in += len(raw)
            self._up(orx.audio_append(base64.b64encode(orx.resample_16k_to_24k(raw)).decode()))
        elif t == "text" and str(msg.get("text") or "").strip():
            self.session["owner_text"] = str(msg["text"])
            self._up({"type": "conversation.item.create", "item": {"type": "message", "role": "user",
                      "content": [{"type": "input_text", "text": _gate(str(msg["text"]), "live.text", "")}]}})
            self._up(orx.response_create())
        elif t == "barge":
            self._barge()
        elif t == "end":
            self.done.set()

    def _up(self, obj: dict) -> None:
        with self._lock:
            self.up.send(json.dumps(obj))

    def _barge(self) -> None:
        """The owner spoke over Friday (or pressed Escape): stop the model, tell it what was actually heard."""
        self.send({"type": "interrupted"})
        if self.item_id:
            heard = min(self.audio_ms, max(0.0, (self.clock() - self.first_audio_at) * 1000.0))
            try:
                self._up({"type": "response.cancel"})
                self._up(orx.truncate(self.item_id, int(heard)))
            except Exception:
                pass
        self.item_id, self.audio_ms, self.first_audio_at = None, 0.0, 0.0

    # OpenAI -> page
    def from_upstream(self, raw: Any) -> None:
        p = orx.parse(raw)
        if not p:
            return
        k = p["kind"]
        if k == "audio":
            if p.get("item_id") and p["item_id"] != self.item_id:
                self.item_id, self.audio_ms, self.first_audio_at = p["item_id"], 0.0, self.clock()
            try:
                self.audio_ms += len(base64.b64decode(p["data"])) / 2 / orx.SAMPLE_RATE * 1000.0
            except Exception:
                pass
            self.send({"type": "audio", "data": p["data"]})
        elif k == "transcript":
            self.send({"type": "text", "text": p["text"]})
        elif k == "heard":
            self.last_heard_at = self.clock()
            self.session["owner_text"] = p["text"]
            self.send({"type": "input_transcript", "text": p["text"]})
        elif k == "speech_started":
            self.last_heard_at = self.clock()
            self._barge()
        elif k == "done":
            self._done(p)
        elif k == "error":
            self._error(p)

    def _done(self, p: dict) -> None:
        if p["cost"]:
            self.session_cost += p["cost"]
            try:
                from agent_friday.services import cost_meter
                cost_meter.record("openai", self.model, 0, 0, cost_usd=p["cost"], purpose=_PURPOSE)
            except Exception:
                _log.warning("OpenAI voice cost could not be recorded")
            self.send({"type": "cost", "usd": round(self.session_cost, 4), "per_minute": orx.per_minute_line()})
            cap = _cap(self.settings)
            if cap is not None and month_spent_usd() >= cap:
                self.send({"type": "error", "error": orx.CAP_REACHED,
                           "detail": "You reached your monthly OpenAI voice limit ($%.2f). The call ended." % cap,
                           "offer": None})
                self.done.set()
                return
        if p["tool_calls"]:
            threading.Thread(target=self._tools, args=(p["tool_calls"],), daemon=True).start()
        elif not p["cancelled"]:
            self.send({"type": "turn_end"})

    def _tools(self, calls: list) -> None:
        for c in calls:
            try:
                result = self.run_tool(c["name"], c["args"], self.send, self.session)
            except Exception as e:                                  # a tool that fails says so, it does not hang the call
                result = "The %s tool failed: %s" % (c["name"], type(e).__name__)
            safe = _gate(str(result), "live.tool.%s" % c["name"], WITHHELD_RESULT)
            try:
                self._up(orx.function_output(c["call_id"], safe))
            except Exception:
                return
        try:
            self._up(orx.response_create())
        except Exception:
            pass

    def _error(self, p: dict) -> None:
        if p["no_credit"]:
            _BLOCKED_UNTIL[0] = self.clock() + orx.NO_CREDIT_BLOCK_S
            off = orx.failover(orx.NO_CREDIT, gemini_ready=_gemini_ready(self.settings), local_ready=_local_ready(),
                               auto=bool(self.settings.get("voice_openai_auto_failover")))
            self.send({"type": "error", "error": orx.NO_CREDIT, "detail": _with_offer(orx.readiness(
                key="x", blocked_until=_BLOCKED_UNTIL[0], now=self.clock())["say"], off), "offer": off})
            self.done.set()
        else:
            self.send({"type": "error", "error": "openai_error", "detail": p["message"] or "OpenAI voice had a problem.",
                       "offer": None})

    def silent_too_long(self) -> bool:
        return self.clock() - self.last_heard_at > orx.SILENCE_END_S


def _run_tool(name: str, args: dict, send: Callable, session: dict) -> Any:
    """The one tool path the other cloud voice engines use: ActionGate, approval cards, receipts."""
    from agent_friday.routes import voice as V
    return asyncio.run(V._live_card_tool(name, args, send, session))


def serve(ws, args: Dict[str, str], *, connect: Callable = None, settings: Optional[dict] = None) -> None:
    """One /ws/openai-live connection. The caller has already authenticated the socket."""
    connect = connect or _connect
    settings = _settings() if settings is None else settings
    lock = threading.Lock()

    def send(obj: dict) -> bool:
        try:
            with lock:
                ws.send(json.dumps(obj))
            return True
        except Exception:
            return False

    no = refusal(settings)
    if no:
        send(dict(no, type="error"))
        return
    key = orx.api_key()
    if not key:
        send({"type": "error", "error": orx.NO_KEY, "detail": "Add an OpenAI key to use this.", "offer": None})
        return
    model = orx.MODEL
    try:
        up = connect(orx.ws_url(model), orx.headers(key))
    except Exception as e:
        _log.warning("OpenAI voice could not connect (%s)", type(e).__name__)
        send({"type": "error", "error": "connect_failed", "detail": "OpenAI voice could not connect.", "offer": None})
        return
    b = Bridge(up, send, settings, model=model)
    _mic_ledger("open", 0)
    try:
        b._up(orx.session_update(build_instruction(settings), build_tools(), model=model))
        send({"type": "status", "text": orx.which_answers(engine="openai", model=model),
              "engine": "openai", "model": model, "per_minute": orx.per_minute_line()})
        reader = threading.Thread(target=_pump, args=(b,), daemon=True)
        reader.start()
        while not b.done.is_set():
            try:
                raw = ws.receive(timeout=0.5)
            except Exception:
                break
            if raw is not None:
                try:
                    b.to_upstream(json.loads(raw))
                except Exception:
                    _log.debug("OpenAI voice dropped a bad frame")
            if b.silent_too_long():
                send({"type": "status", "text": "Ended after %d seconds of silence (OpenAI bills quiet audio too)."
                      % orx.SILENCE_END_S})
                break
    finally:
        b.done.set()
        _mic_ledger("close", b.bytes_in)
        try:                                                  # "just this call": voice goes back to what it was
            from agent_friday.services import local_voice_readiness as lvr
            lvr.call_ended()
        except Exception:
            pass
        try:
            up.close()
        except Exception:
            pass


def _pump(b: Bridge) -> None:
    while not b.done.is_set():
        try:
            raw = b.up.receive(timeout=0.5)
        except Exception:
            b.done.set()
            return
        if raw is not None:
            try:
                b.from_upstream(raw)
            except Exception:
                _log.exception("OpenAI voice event failed")


def _mic_ledger(event: str, n: int) -> None:
    try:
        from agent_friday.services import egress_gate as eg
        eg.record_binary_egress(_EGRESS_PROVIDER, "mic_audio", action="allow",
                                reason="OpenAI voice session %s" % ("opened" if event == "open" else "closed"),
                                byte_len=n)
    except Exception as e:
        _log.warning("mic-audio ledger row failed (%s): %s", event, e)


def register(sock) -> None:
    """Called once from routes/voice.py beside the /ws/live route."""
    from agent_friday.services import reasoning_trace as rt

    @sock.route("/ws/openai-live")
    @rt.traced("voice", "OpenAI voice session")
    def ws_openai_live(ws):
        from flask import request
        from agent_friday.routes import voice as V
        tok = request.args.get("token", "")
        import hmac
        if V.FRIDAY_WS_TOKEN and not hmac.compare_digest(tok, V.FRIDAY_WS_TOKEN):
            ws.send(json.dumps({"type": "error", "error": "unauthorized"}))
            return
        if not V._ws_auth_ok(V._api_token_valid(request.args.get("t", ""))):
            ws.send(json.dumps({"type": "error", "error": "unauthorized"}))
            return
        if V._voice_refused_while_stopped(ws):
            return
        serve(ws, dict(request.args))

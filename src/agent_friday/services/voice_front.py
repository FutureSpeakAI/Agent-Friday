"""The voice front: a small, fast local model that answers live voice turns.

Local voice spec §4.1/§5 ("fast front, deep brain"). The 27B brain cannot
meet conversational latency on a 12 GB card: its prefill of a ~40-50K-token
voice contract took 63-199 s to a first token. The front is a small
tool-calling model (Qwen3-4B-Instruct-2507, or Qwen3-1.7B beside the brain)
on its OWN llama-server, holding a ~6-9K-token contract: the same curated
tool contract cloud voice declares (``voice_engine.build_voice_tool_contract``),
executed by the same executor (``voice_engine._voice_tool_run``), so local
and cloud voice can do the same things. Deep work goes to the brain through
``ask_friday`` / ``delegate_to_friday``.

Ownership: the seat is spawned and evicted by the residency Arbiter's own
llama backend (``ARBITER.llama.load`` / ``evict``), never from a tool shell,
and published to ``endpoints.json`` like every seat. It runs on
``VOICE_FRONT_PORT``, a port of its own: the Arbiter places a brain's
fallback engine on ``port + 1`` and the next pinned seat on
``8090 + len(procs)``, so 8091 would collide.

Zero telemetry: llama-server makes no network call at inference; the model
file is vendored (pinned sha256) and the process runs with HF_HUB_OFFLINE.
"""
from __future__ import annotations

import json
import logging
import threading
import time
from pathlib import Path

log = logging.getLogger("friday.voice_front")

#: Clear of the ports the Arbiter assigns by arithmetic. It is inside the
#: Arbiter's survey window (8090-8130), so a server that restarts while a
#: front is up REAPS it (adopt_or_reap keeps only planned seats); the next
#: call arms it again. local_seats.serving() leaves it out, so no other work
#: is routed onto the front's single cached slot.
VOICE_FRONT_PORT = 8125

#: Every request to the front: Qwen3 hybrids think before answering unless
#: told not to, and a thinking turn streams nothing speakable (it would trip
#: the first-token deadline). The 2507 Instruct model ignores the flag.
_NO_THINKING = {"enable_thinking": False}

#: What a spoken turn needs beside the standing prompt and the tool
#: declarations, in tokens: the chat template's margin, the largest spoken
#: reply (voice_delivery.reply_budget tops out at 1400) and a few turns of
#: history plus the volatile context block that rides in the user turn.
TEMPLATE_MARGIN_TOKENS = 512
REPLY_RESERVE_TOKENS = 1400
HISTORY_RESERVE_TOKENS = 1500
_ENVELOPE_SLACK_TOKENS = 64


def context_room(window: int, convo: list, tools) -> int:
    """Tokens left in ``window`` after ``convo`` and the tool declarations
    (an estimate: four characters a token, plus the template margin)."""
    return (int(window)
            - len(json.dumps({"messages": convo, "tools": tools or []})) // 4
            - TEMPLATE_MARGIN_TOKENS)


def system_budget_tokens(window: int, tools) -> int:
    """The most the standing system prompt may cost on a front served at
    ``window`` with ``tools`` declared, so a turn still has its reply and its
    history. The prompt builder spends it in priority order."""
    return max(0, int(window) - TEMPLATE_MARGIN_TOKENS
               - len(json.dumps(tools or [])) // 4
               - REPLY_RESERVE_TOKENS - HISTORY_RESERVE_TOKENS - _ENVELOPE_SLACK_TOKENS)


def model_for_label(label) -> str:
    """The FRONT_MODELS key whose label is ``label``; otherwise the model with
    the smallest window, so an unknown front is budgeted for the tightest."""
    for key, spec in FRONT_MODELS.items():
        if spec["label"] == label:
            return key
    return min(FRONT_MODELS, key=lambda k: FRONT_MODELS[k]["ctx"])


#: The models the front can serve. ``sha256`` is the pin the installer
#: verifies after download; a target whose pin is None is refused (an
#: unpinned file is never installed). Sizes are the published file sizes.
#: Licences were checked against the model cards (spec §4.1): Apache-2.0.
FRONT_MODELS = {
    # The PrismML ternary build of Qwen3-1.7B: the Qwen3 chat template and
    # tool format, a third of the 1.7B's memory. Its PQ2_0 tensors load only
    # on the PrismML fork of llama.cpp (stock rejects the type), so it
    # declares that engine and is never tried on stock (engine_for below).
    "ternary-bonsai:1.7b": {
        "label": "Ternary Bonsai 1.7B",
        "repo": "prism-ml/Ternary-Bonsai-1.7B-gguf",
        "file": "Ternary-Bonsai-1.7B-PQ2_0.gguf",
        "sha256": "de68ba48a8dacb21979915991e7741b917869d71410a370df951c0c3a237ae50",
        "size_mb": 442,
        "licence": "Apache-2.0",
        "ctx": 16384,
        "role": "co_resident",
        "engine": "prism-fork",
        "packing": "PQ2_0",
    },
    "qwen3-4b-instruct-2507": {
        "label": "Qwen3-4B-Instruct-2507",
        "repo": "unsloth/Qwen3-4B-Instruct-2507-GGUF",
        "file": "Qwen3-4B-Instruct-2507-Q4_K_M.gguf",
        "sha256": "3605803b982cb64aead44f6c1b2ae36e3acdb41d8e46c8a94c6533bc4c67e597",
        "size_mb": 2382,
        "licence": "Apache-2.0",
        "ctx": 16384,
        "role": "solo",            # V-B: the brain is parked for the call
    },
    "qwen3-1.7b": {
        "label": "Qwen3-1.7B",
        "repo": "ggml-org/Qwen3-1.7B-GGUF",
        "file": "Qwen3-1.7B-Q4_K_M.gguf",
        "sha256": "d2387ca2dbfee2ffabce7120d3770dadca0b293052bc2f0e138fdc940d9bc7b5",
        "size_mb": 1223,
        "licence": "Apache-2.0",
        "ctx": 16384,          # the curated tool contract alone needs ~10K
        "role": "co_resident",     # V-A: beside the brain in its voice profile
    },
}

DEFAULT_FRONT_MODEL = "ternary-bonsai:1.7b"

#: What a served front holds on the card beyond its file, in MiB: the q8_0 KV
#: cache for the full window (Qwen3-1.7B: 28 layers x 8 KV heads x 128 x 2,
#: ~60 KiB a token, ~950 MiB at 16K; the 4B's 36 layers ~1,220 MiB) plus
#: llama-server's compute buffers at -ub 512.
_FRONT_OVERHEAD_MIB = {"qwen3-4b-instruct-2507": 1550}
_DEFAULT_OVERHEAD_MIB = 1300


def required_engine(model: str):
    """The runtime a front must be served on (a model_download runtime name),
    or None for the Arbiter's default engines."""
    return (FRONT_MODELS.get(model) or {}).get("engine")


def vram_need_mib(model: str) -> int:
    """The card a served front takes: its file plus KV and buffers."""
    spec = FRONT_MODELS[model]
    return int(spec["size_mb"]) + _FRONT_OVERHEAD_MIB.get(model, _DEFAULT_OVERHEAD_MIB)

#: llama-server flags for the front, merged over the Arbiter's base command.
#: One slot (the session prefix stays pinned in its cache; a second slot would
#: halve the context and reprocess the prefix, as -np 2 did for the brain),
#: q8_0 KV, and the larger batch the short prefix prefills fastest with.
SERVE_ARGS = ("-np", "1", "--cache-type-k", "q8_0", "--cache-type-v", "q8_0",
              "-b", "2048", "-ub", "512")

#: Rounds of tool calls one spoken turn may take before it must answer.
MAX_TOOL_ROUNDS = 4


def seat_id(model: str) -> str:
    """The id the front is served and published under (distinct from any
    brain id, so routing never mistakes it for the brain)."""
    return "voice-front:" + str(model)


def model_path(model: str) -> Path:
    from agent_friday.services.model_store import store_dir
    return Path(store_dir()) / FRONT_MODELS[model]["file"]


def installed(model: str) -> bool:
    try:
        return model in FRONT_MODELS and model_path(model).is_file()
    except Exception:
        return False


def selected_model(settings: dict | None = None) -> str:
    s = settings or {}
    m = str(s.get("voice_front_model") or DEFAULT_FRONT_MODEL).strip().lower()
    return m if m in FRONT_MODELS else DEFAULT_FRONT_MODEL


# ── tool-call validation (the grammar's backstop) ──────────────────────────

_JSON_TYPES = {"string": str, "integer": int, "number": (int, float),
               "boolean": bool, "array": list, "object": dict}


def validate_tool_call(call: dict, contract: dict):
    """``(name, args, None)`` for a well-formed call, ``(name, None, why)``
    otherwise.

    llama-server constrains tool calls with the chat template's grammar; this
    is the check that runs anyway, so a malformed call (a name the contract
    does not hold, arguments that are not a JSON object, a missing required
    field, a wrong type) is never executed. The model is told why and may try
    again within the turn.
    """
    fn = (call or {}).get("function") or {}
    name = str(fn.get("name") or "")
    by_name = {t["function"]["name"]: t["function"] for t in contract.get("tools") or []}
    if name not in by_name:
        return name, None, f"there is no tool called {name!r} in this conversation"
    raw = fn.get("arguments")
    try:
        args = raw if isinstance(raw, dict) else json.loads(raw or "{}")
    except (TypeError, ValueError):
        return name, None, "the arguments were not valid JSON"
    if not isinstance(args, dict):
        return name, None, "the arguments must be a JSON object"
    schema = by_name[name].get("parameters") or {}
    props = schema.get("properties") or {}
    for req in schema.get("required") or []:
        if req not in args:
            return name, None, f"the required argument {req!r} is missing"
    for k, v in args.items():
        want = (props.get(k) or {}).get("type")
        py = _JSON_TYPES.get(want)
        if py is None:
            continue
        if want in ("integer", "number") and isinstance(v, bool):
            return name, None, f"argument {k!r} must be of type {want}"
        if not isinstance(v, py):
            return name, None, f"argument {k!r} must be of type {want}"
    return name, args, None


def _renderable(call: dict) -> dict:
    """``call`` with arguments a chat template can render: unchanged when they
    are a JSON object, ``{}`` otherwise (a truncated or malformed call)."""
    fn = dict((call or {}).get("function") or {})
    raw = fn.get("arguments")
    try:
        ok = isinstance(raw, dict) or isinstance(json.loads(raw or "{}"), dict)
    except (TypeError, ValueError):
        ok = False
    if ok:
        return call
    fn["arguments"] = "{}"
    return dict(call, function=fn)


# ── the seat ───────────────────────────────────────────────────────────────

class FrontSeat:
    """One front seat's lifecycle. Thread-safe; one per process (``get``).

    Holders are counted: each session (and a proof) arms with its own holder
    name and disarms with it, and the seat is evicted only when the last
    holder lets go, so one call ending never pulls the front out from under
    another, and a proof never leaves it loaded.
    """

    def __init__(self, port: int = VOICE_FRONT_PORT):
        self.port = int(port)
        self.model = None
        self._lock = threading.RLock()
        self._holders: set = set()
        self.armed_at = None
        self.prefill_ms = None

    def holders(self) -> int:
        with self._lock:
            return len(self._holders)

    @property
    def base(self) -> str:
        return "http://127.0.0.1:%d" % self.port

    def healthy(self) -> bool:
        import urllib.request
        try:
            with urllib.request.urlopen(self.base + "/health", timeout=2) as r:
                return r.status == 200
        except Exception:
            return False

    def arm(self, model: str, *, holder: str = "session", loader=None) -> dict:
        """Serve `model` on the front port for `holder` (reusing a healthy seat
        already serving it). ``loader`` is the Arbiter's llama backend; tests
        pass a fake. Raises with a sentence the owner can act on."""
        if model not in FRONT_MODELS:
            raise ValueError(f"unknown voice front model {model!r}")
        with self._lock:
            if self.model is not None and self.model != model and self._holders:
                raise RuntimeError(
                    f"the voice front is serving {FRONT_MODELS[self.model]['label']} "
                    f"for another call")
            if self.model == model and self.healthy():
                self._holders.add(str(holder))
                return {"ok": True, "model": model, "reused": True}
            if not installed(model):
                raise RuntimeError(
                    f"{FRONT_MODELS[model]['label']} is not installed. Install it "
                    f"from Settings > Voice (Setup & install).")
            if loader is None:
                from agent_friday.services import residency_arbiter as ra
                arb = ra.get_arbiter()
                if arb is None:
                    raise RuntimeError("the residency Arbiter is not running, so "
                                       "the voice front cannot be served")
                loader = arb.llama
            t0 = time.time()
            loader.load(seat_id(model), FRONT_MODELS[model]["ctx"],
                        gguf_path=str(model_path(model)), port=self.port)
            self.model = model
            self.armed_at = time.time()
            self._holders.add(str(holder))
            log.info("voice front %s serving on :%d in %.1f s", model, self.port,
                     time.time() - t0)
            return {"ok": True, "model": model, "reused": False,
                    "load_s": round(time.time() - t0, 2)}

    def disarm(self, *, holder: str = "session", loader=None) -> None:
        """Let go for `holder`; the seat is evicted when nobody holds it."""
        with self._lock:
            self._holders.discard(str(holder))
            if self.model is None or self._holders:
                return
            try:
                if loader is None:
                    from agent_friday.services import residency_arbiter as ra
                    arb = ra.get_arbiter()
                    loader = arb.llama if arb is not None else None
                if loader is not None:
                    loader.evict(seat_id(self.model))
            finally:
                self.model = None
                self.armed_at = None

    # ── turns ──────────────────────────────────────────────────────────────

    def _post(self, body: dict, stream: bool):
        import requests
        return requests.post(self.base + "/v1/chat/completions", json=body,
                             stream=stream, timeout=(5, 120))

    def prefill(self, system: str, contract: dict) -> dict:
        """Put the session prefix (system prompt + tool declarations) in the
        slot's cache with a one-token completion, the way a turn sends it."""
        t0 = time.perf_counter()
        body = {"model": seat_id(self.model or ""), "max_tokens": 1,
                "temperature": 0, "cache_prompt": True, "id_slot": 0,
                "messages": [{"role": "system", "content": system},
                             {"role": "user", "content": "OK."}],
                "tools": contract.get("tools") or None,
                "chat_template_kwargs": dict(_NO_THINKING)}
        r = self._post(body, stream=False)
        r.raise_for_status()
        timings = (r.json() or {}).get("timings") or {}
        self.prefill_ms = int((time.perf_counter() - t0) * 1000)
        return {"ms": self.prefill_ms, "prompt_n": timings.get("prompt_n")}

    def prefill_partial(self, system: str, messages: list, contract: dict) -> dict:
        """Speculative prefill: send the turn as it stands (a stable partial
        transcript) with a one-token answer, so the slot's cache already holds
        everything but the last words when the endpoint fires. The body has
        the SAME shape as ``run_turn``'s first round, so the real turn reuses
        the common prefix; a partial that later changed costs only the
        tokens after the point where it changed, and its one generated token
        is never spoken."""
        body = {"model": seat_id(self.model or ""), "max_tokens": 1,
                "cache_prompt": True, "id_slot": 0,
                "messages": [{"role": "system", "content": system}] + list(messages),
                "tools": contract.get("tools") or None,
                "chat_template_kwargs": dict(_NO_THINKING)}
        r = self._post(body, stream=False)
        r.raise_for_status()
        return (r.json() or {}).get("timings") or {}

    def run_turn(self, system: str, messages: list, contract: dict, *,
                 on_delta=None, run_tool=None, max_tokens: int = 400,
                 temperature=None, timings=None, allow_continuation=False,
                 admit_tool=None) -> str:
        """One spoken turn: stream text, run validated tool calls through
        ``run_tool(name, args) -> result``, and loop until the model answers
        (at most ``MAX_TOOL_ROUNDS`` tool rounds). The caller's turn cancel
        (``model_router.TURN_CANCEL``) closes the stream mid-generation.
        Returns the spoken text."""
        from agent_friday.services.model_router import (_consume_sse_completion,
                                                        turn_cancelled)
        convo = [{"role": "system", "content": system}] + list(messages)
        spoken = []
        continued = False
        from agent_friday.services.turn_budget import clamp_output
        window = FRONT_MODELS.get(self.model or DEFAULT_FRONT_MODEL, FRONT_MODELS[DEFAULT_FRONT_MODEL])["ctx"]
        # Fit the oldest history out before sacrificing this utterance. The
        # remaining estimate includes tools and a margin for the chat template.
        def room():
            return context_room(window, convo, contract.get("tools"))
        while len(convo) > 2 and room() < min(int(max_tokens), 1400):
            convo.pop(1)
        allowance = clamp_output(max_tokens, window)
        for rnd in range(MAX_TOOL_ROUNDS + 1):
            if turn_cancelled():
                break
            remaining = room()
            if remaining < 128:
                raise RuntimeError("The local voice context is full; start a fresh conversation or hand this work to the main agent.")
            body = {"model": seat_id(self.model or ""), "stream": True,
                    "max_tokens": min(int(allowance), remaining), "cache_prompt": True,
                    "id_slot": 0, "messages": convo,
                    "tools": contract.get("tools") or None,
                    "chat_template_kwargs": dict(_NO_THINKING)}
            if temperature is not None:
                body["temperature"] = temperature
            if rnd == MAX_TOOL_ROUNDS:
                body["tool_choice"] = "none"      # answer with what you have
            resp = self._post(body, stream=True)
            resp.raise_for_status()
            out = _consume_sse_completion(resp, on_delta=on_delta)
            if timings is not None and rnd == 0:
                timings.update(out.get("timings") or {})
            ch = (out.get("choices") or [{}])[0]
            msg = ch.get("message") or {}
            calls = msg.get("tool_calls") or []
            if admit_tool is not None:
                for call in calls:
                    refusal = admit_tool(str(((call or {}).get("function") or {}).get("name") or ""))
                    if refusal:
                        return refusal
            text = msg.get("content") or ""
            if text.strip():
                spoken.append(text.strip())
            if (not calls and ch.get("finish_reason") == "length" and allow_continuation
                    and not continued and rnd < MAX_TOOL_ROUNDS and not turn_cancelled()):
                continued = True
                convo.extend([{"role": "assistant", "content": text},
                              {"role": "user", "content": "Continue the unfinished thought naturally, without repeating what was already spoken. Finish when the requested substance is covered."}])
                continue
            if not calls or ch.get("finish_reason") == "cancelled":
                break
            # The history carries each call as the server must re-render it.
            # A call cut off by the token budget has arguments that are not
            # JSON ('{'); sent back verbatim, the chat template cannot render
            # it and the server fails the whole turn (HTTP 500). The model is
            # told the call was not run either way, below.
            convo.append({"role": "assistant", "content": text,
                          "tool_calls": [_renderable(c) for c in calls]})
            for c in calls:
                if admit_tool is not None:
                    refusal = admit_tool(str(((c or {}).get("function") or {}).get("name") or ""))
                    if refusal:
                        return refusal
                name, args, why = validate_tool_call(c, contract)
                if why is not None:
                    result = f"ERROR: that call was not run: {why}."
                elif run_tool is None:
                    result = "ERROR: tools are not available in this session."
                else:
                    try:
                        result = run_tool(name, args)
                    except Exception as e:   # the executor never raises; belt only
                        result = f"ERROR: {type(e).__name__}"
                if admit_tool is not None:
                    refusal = admit_tool(name)
                    if refusal:
                        return refusal
                if not isinstance(result, str):
                    result = json.dumps(result, ensure_ascii=False, default=str)
                convo.append({"role": "tool", "tool_call_id": c.get("id") or name,
                              "content": result})
        # Rounds are separate utterances ("Let me check." then the answer).
        return " ".join(spoken).strip()

    def routed_turn(self, system: str, messages: list, *, tool=None, args=None,
                    ack: str = "", question: str = "", run_tool=None, on_delta=None,
                    max_tokens: int = 400, temperature=None, timings=None) -> str:
        """One spoken turn whose tool, if any, system one already chose
        (services/laya_router). The front never sees a tool catalogue.

        ``question``: the router's one clarifying question is the reply.
        ``tool``: it starts on a thread through ``run_tool`` (the surface's
        governed runner) while ``ack`` is spoken; the front then answers from
        the result, which reaches it as the Qwen3 tool-result turn: its own
        acknowledgement carrying the call, then the result. A barge stops
        the wait; the read it started finishes on its own and is not spoken.
        Otherwise the front simply answers."""
        from agent_friday.services.model_router import turn_cancelled
        speak = (lambda s: on_delta(s) if on_delta else None)
        if question:
            speak(question)
            return question
        if not tool:
            return self.run_turn(system, messages, {"tools": []}, on_delta=on_delta,
                                 max_tokens=max_tokens, temperature=temperature,
                                 timings=timings)
        box = {}

        def _run():
            try:
                box["result"] = run_tool(tool, dict(args or {})) if run_tool else \
                    "ERROR: tools are not available in this session."
            except Exception as e:  # the governed runner never raises; belt only
                box["result"] = "ERROR: %s" % type(e).__name__
        worker = threading.Thread(target=_run, name="voice-routed-tool", daemon=True)
        worker.start()
        if ack:
            speak(ack + " ")
        while worker.is_alive():
            if turn_cancelled():
                return ack
            worker.join(0.05)
        result = box.get("result")
        if not isinstance(result, str):
            result = json.dumps(result, ensure_ascii=False, default=str)
        call = {"id": "laya-1", "type": "function",
                "function": {"name": tool, "arguments": json.dumps(dict(args or {}))}}
        convo = list(messages) + [
            {"role": "assistant", "content": ack, "tool_calls": [call]},
            {"role": "tool", "tool_call_id": "laya-1", "content": result}]
        answer = self.run_turn(system, convo, {"tools": []}, on_delta=on_delta,
                               max_tokens=max_tokens, temperature=temperature,
                               timings=timings)
        return (ack + " " + answer).strip()


_SEAT = None
_SEAT_LOCK = threading.Lock()


def get() -> FrontSeat:
    global _SEAT
    with _SEAT_LOCK:
        if _SEAT is None:
            _SEAT = FrontSeat()
        return _SEAT

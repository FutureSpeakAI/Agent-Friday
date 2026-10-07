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

#: The models the front can serve. ``sha256`` is the pin the installer
#: verifies after download; a target whose pin is None is refused (an
#: unpinned file is never installed). Sizes are the published file sizes.
#: Licences were checked against the model cards (spec §4.1): Apache-2.0.
FRONT_MODELS = {
    "qwen3-4b-instruct-2507": {
        "label": "Qwen3-4B-Instruct-2507",
        "repo": "Qwen/Qwen3-4B-Instruct-2507-GGUF",
        "file": "Qwen3-4B-Instruct-2507-Q4_K_M.gguf",
        "sha256": None,
        "size_mb": 2500,
        "licence": "Apache-2.0",
        "ctx": 16384,
        "role": "solo",            # V-B: the brain is parked for the call
    },
    "qwen3-1.7b": {
        "label": "Qwen3-1.7B",
        "repo": "Qwen/Qwen3-1.7B-GGUF",
        "file": "Qwen3-1.7B-Q4_K_M.gguf",
        "sha256": None,
        "size_mb": 1100,
        "licence": "Apache-2.0",
        "ctx": 12288,
        "role": "co_resident",     # V-A: beside the brain in its voice profile
    },
}

DEFAULT_FRONT_MODEL = "qwen3-4b-instruct-2507"

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
                 temperature=None, timings=None, admit_tool=None) -> str:
        """One spoken turn: stream text, run validated tool calls through
        ``run_tool(name, args) -> result``, and loop until the model answers
        (at most ``MAX_TOOL_ROUNDS`` tool rounds). The caller's turn cancel
        (``model_router.TURN_CANCEL``) closes the stream mid-generation.
        Returns the spoken text."""
        from agent_friday.services.model_router import (_consume_sse_completion,
                                                        turn_cancelled)
        convo = [{"role": "system", "content": system}] + list(messages)
        spoken = []
        for rnd in range(MAX_TOOL_ROUNDS + 1):
            if turn_cancelled():
                break
            body = {"model": seat_id(self.model or ""), "stream": True,
                    "max_tokens": int(max_tokens), "cache_prompt": True,
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
            if not calls or ch.get("finish_reason") == "cancelled":
                break
            convo.append({"role": "assistant", "content": text,
                          "tool_calls": calls})
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


_SEAT = None
_SEAT_LOCK = threading.Lock()


def get() -> FrontSeat:
    global _SEAT
    with _SEAT_LOCK:
        if _SEAT is None:
            _SEAT = FrontSeat()
        return _SEAT

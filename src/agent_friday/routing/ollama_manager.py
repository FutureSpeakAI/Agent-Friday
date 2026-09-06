"""
Ollama Manager — manages all local model interaction via Ollama.
Singleton, lazy-init, thread-safe.
"""

import json
import subprocess
import sys
import threading
import time
import urllib.request
import urllib.error

_POPEN_FLAGS = subprocess.CREATE_NO_WINDOW if sys.platform == 'win32' else 0

# The context a request gets when its caller did not decide one. Deliberately
# modest: a seat that fits and answers beats a seat sized by whatever maximum
# the artifact declares. gemma4 declares 262144, which on a 12 GB card means
# 9.9 GB resident and most of the model on the CPU. Callers that have planned a
# context (the arbiter, every seated role) pass it explicitly and are unaffected.
DEFAULT_NUM_CTX = 8192

# Nothing holds GPU memory forever without an owner. Ollama's own default
# outlives the turn that created it, so an unattended seat can sit on the card
# long after the work is done.
DEFAULT_KEEP_ALIVE = "5m"

_instance = None
_lock = threading.Lock()


def get_manager(base_url="http://localhost:11434"):
    global _instance
    if _instance is None:
        with _lock:
            if _instance is None:
                _instance = OllamaManager(base_url)
    return _instance


class OllamaManager:
    def __init__(self, base_url="http://localhost:11434"):
        self.base_url = base_url.rstrip("/")
        self._available = None
        self._available_ts = 0
        self._models_cache = None
        self._models_ts = 0
        self._running_cache = None
        self._running_ts = 0
        self._hardware_cache = None
        self._cache_ttl = 30
        # Installed/running model lists refresh fast (spec A1): a model pulled
        # mid-session must appear in /api/ollama/models and the picker within
        # ~5s, without a server restart. Availability keeps the longer TTL —
        # daemon up/down flaps slower than the inventory changes.
        self._models_ttl = 5
        self._running_ttl = 5

    def _get(self, path, timeout=5):
        url = f"{self.base_url}{path}"
        req = urllib.request.Request(url, method="GET")
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))

    def _post(self, path, body, timeout=30):
        url = f"{self.base_url}{path}"
        data = json.dumps(body).encode("utf-8")
        req = urllib.request.Request(
            url, data=data, method="POST",
            headers={"Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            # Ollama says WHY in the body; urllib's str() is only
            # "HTTP Error 400: Bad Request". With only the bare status, a
            # local turn that 400s falls back to the cloud with the visible
            # symptom "my local model answers as the cloud model" and no
            # stated cause.
            # An error that does not carry its reason is a defect of its own.
            try:
                detail = e.read().decode("utf-8", "replace")[:600]
            except Exception:
                detail = ""
            raise urllib.error.HTTPError(
                e.url, e.code, f"{e.reason}: {detail}" if detail else e.reason,
                e.headers, None) from None

    def _post_stream(self, path, body, timeout=600):
        url = f"{self.base_url}{path}"
        data = json.dumps(body).encode("utf-8")
        req = urllib.request.Request(
            url, data=data, method="POST",
            headers={"Content-Type": "application/json"},
        )
        resp = urllib.request.urlopen(req, timeout=timeout)
        for line in resp:
            line = line.decode("utf-8").strip()
            if line:
                try:
                    yield json.loads(line)
                except json.JSONDecodeError:
                    pass

    # ── Public API ──────────────────────────────────────────────

    def is_available(self):
        now = time.time()
        if self._available is not None and (now - self._available_ts) < self._cache_ttl:
            return self._available
        try:
            url = f"{self.base_url}/api/tags"
            req = urllib.request.Request(url, method="GET")
            with urllib.request.urlopen(req, timeout=3):
                pass
            self._available = True
        except Exception:
            self._available = False
        self._available_ts = now
        return self._available

    def list_models(self):
        now = time.time()
        if self._models_cache is not None and (now - self._models_ts) < self._models_ttl:
            return self._models_cache
        try:
            data = self._get("/api/tags")
            models = []
            for m in data.get("models", []):
                size_bytes = m.get("size", 0)
                size_gb = round(size_bytes / (1024 ** 3), 1) if size_bytes else 0
                params = m.get("details", {}).get("parameter_size", "")
                family = m.get("details", {}).get("family", "")
                quant = m.get("details", {}).get("quantization_level", "")
                models.append({
                    "name": m.get("name", ""),
                    "model": m.get("model", m.get("name", "")),
                    "size_gb": size_gb,
                    "parameter_size": params,
                    "family": family,
                    "quantization": quant,
                    "modified_at": m.get("modified_at", ""),
                })
            self._models_cache = models
            self._models_ts = now
            return models
        except Exception:
            return []

    def context_length(self, model):
        """Context window (tokens) the daemon reports for a model, or None.

        The GGUF metadata key is architecture-prefixed — `gemma4.context_length`,
        `qwen3.context_length` — so match on the suffix rather than guessing the
        architecture. Used by model_catalog.context_window_for (decision D3):
        without this, local models are the one class with NO context-window
        source at all, and they are exactly the class with small windows.
        """
        try:
            info = (self._post("/api/show", {"model": model}, timeout=10)
                    or {}).get("model_info") or {}
        except Exception:
            return None
        for k, v in info.items():
            if str(k).endswith(".context_length") and isinstance(v, int) and v > 0:
                return v
        return None

    def list_running(self):
        """Models currently loaded in Ollama memory (GET /api/ps).

        Short-TTL cached like list_models. Graceful [] when the daemon is
        unreachable or nothing is loaded — callers can't distinguish the two,
        which is fine: both mean "no model is running right now".
        """
        now = time.time()
        if self._running_cache is not None and (now - self._running_ts) < self._running_ttl:
            return self._running_cache
        try:
            data = self._get("/api/ps", timeout=3)
            running = []
            for m in data.get("models", []):
                running.append({
                    "name": m.get("name", ""),
                    "model": m.get("model", m.get("name", "")),
                    "size_vram": m.get("size_vram", 0),
                    "expires_at": m.get("expires_at", ""),
                })
            self._running_cache = running
            self._running_ts = now
            return running
        except Exception:
            return []

    def pull_model(self, name, progress_callback=None):
        try:
            for chunk in self._post_stream("/api/pull", {"name": name, "stream": True}):
                status = chunk.get("status", "")
                total = chunk.get("total", 0)
                completed = chunk.get("completed", 0)
                pct = (completed / total * 100) if total else 0
                if progress_callback:
                    progress_callback(status, pct)
                if status == "success":
                    self._models_cache = None
                    return True
            return True
        except Exception as e:
            if progress_callback:
                progress_callback(f"error: {e}", 0)
            return False

    def detect_hardware(self):
        if self._hardware_cache:
            return self._hardware_cache
        hw = {"gpu": None, "vram_gb": 0, "ram_gb": 0, "platform": sys.platform}
        try:
            result = subprocess.run(
                ["nvidia-smi", "--query-gpu=name,memory.total",
                 "--format=csv,noheader,nounits"],
                capture_output=True, text=True, timeout=10,
                creationflags=_POPEN_FLAGS,
            )
            if result.returncode == 0 and result.stdout.strip():
                parts = result.stdout.strip().split(",")
                hw["gpu"] = parts[0].strip()
                try:
                    hw["vram_gb"] = round(int(parts[1].strip()) / 1024, 1)
                except (ValueError, IndexError):
                    pass
        except Exception:
            pass
        try:
            import psutil
            hw["ram_gb"] = round(psutil.virtual_memory().total / (1024 ** 3), 1)
        except ImportError:
            try:
                import os
                if sys.platform == "win32":
                    result = subprocess.run(
                        ["wmic", "computersystem", "get", "totalphysicalmemory"],
                        capture_output=True, text=True, timeout=10,
                        creationflags=_POPEN_FLAGS,
                    )
                    for line in result.stdout.strip().split("\n"):
                        line = line.strip()
                        if line.isdigit():
                            hw["ram_gb"] = round(int(line) / (1024 ** 3), 1)
                else:
                    with open("/proc/meminfo") as f:
                        for line in f:
                            if line.startswith("MemTotal"):
                                kb = int(line.split()[1])
                                hw["ram_gb"] = round(kb / (1024 ** 2), 1)
                                break
            except Exception:
                pass
        self._hardware_cache = hw
        return hw

    #: Presentation strings for `recommend_models()`. Deliberately NOT part of
    #: `model_plan.BRAIN_MODELS` — that table is the family/hardware decision
    #: (single source of truth, per the maintainer's "keep the family
    #: selection in one place" instruction); this is just how each rung's TASK is
    #: described in a picker UI (short, written for an end user — BRAIN_MODELS'
    #: own `note` is written for an audit trail, not a picker). `tier` is NOT
    #: duplicated here — it is computed by rank in `recommend_models()` itself,
    #: so a ladder change (a row added, removed, or renamed) can't desync a
    #: hand-typed tier from where that row actually lands. A model id missing
    #: from this dict falls back to its own `note` rather than a blank task.
    _REC_LABELS = {
        "gemma4:e2b": "quick lookups, formatting, status checks",
        "gemma4:e4b": "chat, simple tasks, fast response",
        "gemma4:12b": "general purpose, code, analysis",
        "gemma4:26b": "code, research, complex reasoning",
    }

    def recommend_models(self, hardware=None):
        """A thin reader over `model_plan.BRAIN_MODELS` — headroom.md HR15.

        THIS MUST NOT BECOME A SECOND LADDER. Hand-typing rows here (name,
        VRAM/RAM threshold, task blurb) separate from `model_plan.BRAIN_MODELS`
        means the moment BRAIN_MODELS gains a row this table does not know
        about, this function silently disagrees with the planner about what
        the machine can actually run — the "two ladders" defect headroom.md
        §2.9 describes for `install.ps1`'s own hand-maintained `$brainLadder`.

        Now: every tool-capable row in `BRAIN_MODELS` (`_pickable`'s own
        filter — a model that cannot call tools is never suggested as
        someone's local model) is offered once its own footprint fits, by
        the SAME arithmetic `plan()` uses for the fit check — VRAM gate is
        the model's `vram_gib` (footprint under load) plus
        `DISPLAY_RESERVE_GIB`, rounded up to a whole GiB, so a suggestion is
        never a card the model cannot actually load into (over-claiming here
        is the direction that costs someone a stalled download); RAM gate is
        the model's own `min_ram_gib`, unmultiplied — this endpoint has never
        had `ram_avail` to work with, only the machine's raw total, so it
        stays the coarser of the two checks `plan()` itself makes.

        `tier` is a UI label, not a planner concept — model_plan has no
        notion of "tiny/small/medium/large". It is assigned by rank among
        the tool-capable rows: the smallest is always `tiny` (the
        unconditional floor — offered regardless of spec, same role
        `model_plan.FLOOR_MODEL` plays as "the smallest thing that works at
        all"), the largest is always `large`, and whatever sits between is
        split evenly across `small`/`medium` in ascending order. Added a row
        in `BRAIN_MODELS` moves the split; nothing here needs to be told.
        """
        from agent_friday.services import model_plan as mp
        import math

        hw = hardware or self.detect_hardware()
        vram = hw.get("vram_gb", 0) or 0
        ram = hw.get("ram_gb", 0) or 0
        # THE MODELS AND THEIR VRAM/RAM COSTS COME FROM
        # services/model_plan.BRAIN_MODELS — the one place that arithmetic and
        # that family choice live (Gemma 4 only — e2b/e4b/12b/26b — per
        # product decision; hardcoding model names here independently of
        # that table is exactly the "scattered choice" that lets one
        # module's default drift out of sync with everyone else's).
        #
        # Card size needed is the model's own footprint (weights + measured
        # runtime overhead) plus the display reserve, rounded UP to a whole
        # GiB — rounding down here is how a suggestion becomes an overclaim.
        # This ladder previously read 6/8/24 for its three gated tiers, which
        # was simply wrong: `vram >= 8` offered a 14B-class model to an 8 GB
        # card that couldn't hold it. Neither accounted for the display
        # reserve or the KV cache, which is the same "largest that fits"
        # mistake the residency planner made when it seated a 26B model on a
        # 12 GiB card.
        #
        # The RAM thresholds use each model's own `min_ram_gib` directly — a
        # judgement about running on the PROCESSOR, where throughput is
        # unmeasured for every model in the ladder.
        #
        # `tier` is computed by RANK, not read from `_REC_LABELS` — a static
        # id->tier mapping desyncs the moment a row is added or removed (an
        # unmapped id fell back to "unknown" rather than a real tier). Sorted
        # explicitly rather than trusting BRAIN_MODELS' own ascending order to
        # hold, since the split below is a rank position, not a value.
        capable = sorted((m for m in mp.BRAIN_MODELS if m.get("tools")),
                         key=lambda m: m["vram_gib"])
        n = len(capable)
        mid = capable[1:-1] if n > 2 else []
        split = math.ceil(len(mid) / 2)
        tiers = {}
        for i, m in enumerate(capable):
            if i == 0:
                tiers[m["id"]] = "tiny"
            elif i == n - 1 and n > 1:
                tiers[m["id"]] = "large"
            else:
                pos = i - 1  # index within `mid`
                tiers[m["id"]] = "small" if pos < split else "medium"

        # Largest-first: a "suggestion" list reads better biggest-to-smallest,
        # with the unconditional tiny floor last rather than first.
        recs = []
        for m in reversed(capable):
            card_needed = math.ceil(m["vram_gib"] + mp.DISPLAY_RESERVE_GIB)
            fits = vram >= card_needed or ram >= m["min_ram_gib"]
            if fits or tiers[m["id"]] == "tiny":   # smallest always offered
                # `task` prefers the UI-facing blurb in `_REC_LABELS` (short,
                # written for a picker, not an audit trail); a model added to
                # BRAIN_MODELS without a matching entry there falls back to
                # its own `note` rather than a blank task — see _REC_LABELS'
                # own docstring above.
                task = self._REC_LABELS.get(m["id"]) or m.get("note", "")
                recs.append({"name": m["id"], "task": task, "tier": tiers[m["id"]]})
        return recs

    def probe_generate(self, model, *, disable_thinking=False,
                       num_predict=10, timeout=30):
        """A real generation, with the timings needed to judge whether it was
        healthy or merely alive.

        Returns ms_per_token computed from Ollama's own `eval_count` /
        `eval_duration`, which EXCLUDE `load_duration`. That separation is what
        makes a latency threshold usable: a cold load of 20-55s on this host
        would otherwise look identical to a model paging against RAM, and the
        detector would fire RED on every cold start.

        `disable_thinking` matters more than it looks. Every gemma4 model
        declares the `thinking` capability, and a small budget is consumed
        entirely by reasoning: measured, num_predict=10 against gemma4:12b
        returns response='' with done_reason='length', while the same call with
        think:false returns 'Hello!'. Probing a thinking model without this
        reports a perfectly healthy model as dead.
        """
        body = {
            "model": model,
            "prompt": "Say hello in one word.",
            "stream": False,
            "options": {"num_predict": num_predict},
        }
        if disable_thinking:
            body["think"] = False
        try:
            resp = self._post("/api/generate", body, timeout=timeout) or {}
        except Exception as e:
            return {"ok": False, "error": "%s: %s" % (type(e).__name__, e),
                    "ms_per_token": None, "load_s": None}
        text = (resp.get("response") or "").strip()
        ec = resp.get("eval_count") or 0
        ed = resp.get("eval_duration") or 0
        return {
            "ok": bool(text),
            "text": text,
            "eval_count": ec,
            "ms_per_token": round((ed / 1e6) / ec, 2) if ec and ed else None,
            "load_s": round((resp.get("load_duration") or 0) / 1e9, 2),
            "done_reason": resp.get("done_reason"),
            "error": None,
        }

    def health_check(self, model, *, disable_thinking=False):
        """Bool form, kept for callers that only want liveness."""
        return bool(self.probe_generate(
            model, disable_thinking=disable_thinking).get("ok"))

    def chat_completion(self, messages, model, tools=None, temperature=0.7,
                        max_tokens=4096, num_ctx=None, timeout=120,
                        think=None, keep_alive=None):
        options = {"temperature": temperature, "num_predict": max_tokens}
        # An explicit context is a placement decision, not a detail. Left unset
        # gemma4 reports num_ctx 262144 and spills most of itself onto the CPU
        # (measured: 79% CPU at the default, 51% at 16384) — which is what made
        # 120s gate calls time out and score a healthy model 1/10.
        #
        # `None` must not mean "let the daemon decide": the top-bar dropdown
        # seats `model_routing.local_model` through here without a context,
        # and an unbounded default lets Ollama seat a 12B at its declared
        # 262144 maximum — ~10 GB resident, several GB spilled back to system
        # RAM, while the compositor is starved of the memory it needs to
        # drive the displays. The default is bounded. A caller that knows its
        # planned context still passes
        # one; a caller that does not gets a seat that fits rather than a seat
        # sized by whatever the artifact happens to declare.
        num_ctx = num_ctx or DEFAULT_NUM_CTX
        options["num_ctx"] = num_ctx
        body = {
            "model": model,
            "messages": messages,
            "stream": False,
            "options": options,
        }
        if tools:
            body["tools"] = tools

        # `options` is an OLLAMA-NATIVE field. The OpenAI-compatible endpoint
        # accepts the request and silently discards it — verified:
        #
        #   /v1/chat/completions  options.num_ctx=8192 -> ollama ps says 131072
        #   /api/chat             options.num_ctx=8192 -> ollama ps says 8192
        #
        # So when a caller has asked for a specific context, the native
        # endpoint is the only one that can honour it. Going to /v1 first and
        # calling the result "pinned to 8192" would be a claim the wire does
        # not support — and it is why the gate was still running the 26b at
        # 262144 with 79% of it on the CPU after the num_ctx "fix".
        #
        # Every request now carries a context, so the /v1 branch is unreachable
        # and has been removed rather than left as a trapdoor. It was never a
        # fast path worth having: its only distinguishing behaviour was
        # discarding the one option that decides how much of the machine the
        # seat takes.
        # Native path: honours `options`, and MUST carry `tools` too. It did
        # not, so any blip on the OpenAI-shaped path silently retried
        # tool-less — a model that cannot be given tools cannot emit a tool
        # call, so the retry was guaranteed to look like a tool-calling
        # failure. The response is normalised to the OpenAI shape callers
        # expect, tool_calls included.
        native = {
            "model": model,
            "messages": messages,
            "stream": False,
            "options": dict(options),
            # Nothing may hold GPU memory indefinitely with no owner. Without
            # this the daemon applies its own default and a seat nobody is
            # using outlives the turn that created it — which is how 9.9 GB sat
            # on the card for hours with no lease behind it.
            "keep_alive": keep_alive or DEFAULT_KEEP_ALIVE,
        }
        if tools:
            native["tools"] = tools
        if think is False:
            native["think"] = False
        resp = self._post("/api/chat", native, timeout=timeout)

        # ── The reserve is a guard here, not a gauge. ──
        # display_at_risk() used to be consulted in exactly one place: the
        # read-only /api/gpu/headroom endpoint. Nothing acted on it, so one
        # ordinary chat turn pinned ~8 GB for the full keep_alive and left the
        # card at 493 MiB free — under the display reserve, in the state that
        # already cost a monitor — for five minutes after the answer arrived
        # (measured: 9188 -> 493 -> 493 -> 493 MiB free across three turns).
        #
        # The dip while generating is the price of using the model at all; the
        # five pinned minutes afterwards are not. If the card is under the
        # reserve once the turn is done, release the seat immediately instead
        # of letting it squat. Next turn pays a reload (~2-4 s); the desktop
        # keeps its memory. Best-effort: a failure to check must never break
        # the answer we already have.
        try:
            from agent_friday.services.gpu_headroom import display_at_risk
            _risk = display_at_risk()
            if _risk.get("at_risk"):
                self._post("/api/generate", {"model": model, "prompt": "",
                                             "keep_alive": 0}, timeout=10)
                print(f"  [GPU-GUARD] released {model} after turn: {_risk.get('reason')}")
        except Exception:
            pass

        msg = resp.get("message", {}) or {}
        out_msg = {"role": "assistant", "content": msg.get("content", "")}
        if msg.get("tool_calls"):
            out_msg["tool_calls"] = msg["tool_calls"]
        return {
            "choices": [{
                "message": out_msg,
                "finish_reason": "stop",
            }],
            "model": model,
            "usage": {
                "prompt_tokens": resp.get("prompt_eval_count", 0),
                "completion_tokens": resp.get("eval_count", 0),
            },
        }

    def invalidate_cache(self):
        self._available = None
        self._models_cache = None
        self._running_cache = None

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
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))

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

    def recommend_models(self, hardware=None):
        hw = hardware or self.detect_hardware()
        vram = hw.get("vram_gb", 0)
        ram = hw.get("ram_gb", 0)
        recs = []
        if vram >= 24 or ram >= 64:
            recs.append({"name": "qwen3:32b", "task": "code, research, complex reasoning", "tier": "large"})
        if vram >= 8 or ram >= 32:
            recs.append({"name": "qwen3:14b", "task": "general purpose, code, analysis", "tier": "medium"})
        if vram >= 6 or ram >= 16:
            recs.append({"name": "qwen3:8b", "task": "chat, simple tasks, fast response", "tier": "small"})
        recs.append({"name": "qwen3:4b", "task": "quick lookups, formatting, status checks", "tier": "tiny"})
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
                        think=None):
        options = {"temperature": temperature, "num_predict": max_tokens}
        # An explicit context is a placement decision, not a detail. Left unset
        # gemma4 reports num_ctx 262144 and spills most of itself onto the CPU
        # (measured: 79% CPU at the default, 51% at 16384) — which is what made
        # 120s gate calls time out and score a healthy model 1/10.
        if num_ctx:
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
        # accepts the request and silently discards it — VERIFIED 2026-08-15:
        #
        #   /v1/chat/completions  options.num_ctx=8192 -> ollama ps says 131072
        #   /api/chat             options.num_ctx=8192 -> ollama ps says 8192
        #
        # So when a caller has asked for a specific context, the native
        # endpoint is the only one that can honour it. Going to /v1 first and
        # calling the result "pinned to 8192" would be a claim the wire does
        # not support — and it is why the gate was still running the 26b at
        # 262144 with 79% of it on the CPU after the num_ctx "fix".
        if not num_ctx:
            try:
                url = f"{self.base_url}/v1/chat/completions"
                data = json.dumps(body).encode("utf-8")
                req = urllib.request.Request(
                    url, data=data, method="POST",
                    headers={"Content-Type": "application/json"},
                )
                with urllib.request.urlopen(req, timeout=timeout) as resp:
                    return json.loads(resp.read().decode("utf-8"))
            except Exception:
                pass
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
        }
        if tools:
            native["tools"] = tools
        if think is False:
            native["think"] = False
        resp = self._post("/api/chat", native, timeout=timeout)
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

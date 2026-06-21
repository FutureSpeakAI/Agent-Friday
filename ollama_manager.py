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
        self._hardware_cache = None
        self._cache_ttl = 30

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
        if self._models_cache is not None and (now - self._models_ts) < self._cache_ttl:
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

    def health_check(self, model):
        try:
            resp = self._post("/api/generate", {
                "model": model,
                "prompt": "Say hello in one word.",
                "stream": False,
                "options": {"num_predict": 10},
            }, timeout=30)
            return bool(resp.get("response", "").strip())
        except Exception:
            return False

    def chat_completion(self, messages, model, tools=None, temperature=0.7,
                        max_tokens=4096):
        body = {
            "model": model,
            "messages": messages,
            "stream": False,
            "options": {
                "temperature": temperature,
                "num_predict": max_tokens,
            },
        }
        if tools:
            body["tools"] = tools
        try:
            url = f"{self.base_url}/v1/chat/completions"
            data = json.dumps(body).encode("utf-8")
            req = urllib.request.Request(
                url, data=data, method="POST",
                headers={"Content-Type": "application/json"},
            )
            with urllib.request.urlopen(req, timeout=120) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except Exception:
            resp = self._post("/api/chat", {
                "model": model,
                "messages": messages,
                "stream": False,
                "options": {
                    "temperature": temperature,
                    "num_predict": max_tokens,
                },
            }, timeout=120)
            content = resp.get("message", {}).get("content", "")
            return {
                "choices": [{
                    "message": {"role": "assistant", "content": content},
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

"""
Agent Friday — the Arbiter: runtime owner of the GPUs.

The policy decides what SHOULD be resident. The Arbiter is what makes it true
and keeps it true: it boots the default plan, grants capability leases, and
executes transitions serially with timeouts and rollback.

Why it owns processes rather than asking a daemon nicely (rule R9). Measured on
the reference instance 2026-08-14: loading gemma4:12b (8001 MiB) and then
gemma4:e2b (1763 MiB) against a 9997 MiB budget leaves ONLY the e2b resident.
Ollama evicted the model the policy considers pinned, at every num_ctx tried,
with no model-count limit reached and no report anywhere. A residency layer
cannot delegate placement to a scheduler that makes its own eviction decisions
on different criteria. So:

  * **pinned** seats run as llama-server processes the Arbiter spawns, health-
    checks, and terminates. Nothing else can evict them.
  * **leased** seats may use Ollama, because a lease is precisely the moment
    eviction is wanted.

Concurrency is deliberately absent. Transitions are serial under one lock: a
residency layer that races itself produces exactly the un-debuggable VRAM
exhaustion it exists to prevent.
"""
from __future__ import annotations

import json
import subprocess
import threading
import time
import urllib.request
from pathlib import Path

from agent_friday.core import runtime_dir
from agent_friday.services import hardware_profile as hwp
from agent_friday.services import residency_catalog as rc
from agent_friday.services import residency_policy as rp

STATE_DEFAULT = "DEFAULT"
STATE_TRANSITIONING = "TRANSITIONING"
STATE_LEASED = "LEASED"
STATE_ROLLING_BACK = "ROLLING_BACK"
STATE_DEGRADED = "DEGRADED"

OLLAMA_URL = "http://localhost:11434"
PORT_BASE = 8090          # one loopback port per pinned llama-server seat

# Transition timeouts are derived from the load-time estimator, never fixed: a
# measured 55 s cold load must not share a budget with a 21 s one.
TIMEOUT_FLOOR_S = 45.0
TIMEOUT_MULTIPLE = 3.0


class TransitionError(RuntimeError):
    pass


def _post(url, body, timeout=600):
    req = urllib.request.Request(
        url, data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode())


def _get(url, timeout=5):
    with urllib.request.urlopen(url, timeout=timeout) as r:
        return json.loads(r.read().decode())


# ─────────────────────────────────────────────────────────────────────────────
#  Backend drivers
# ─────────────────────────────────────────────────────────────────────────────

class OllamaBackend:
    """Leased seats only. Its scheduler evicts, which is fine during a lease."""

    name = "ollama"

    def __init__(self, base_url=OLLAMA_URL):
        self.base_url = base_url

    def resident(self):
        try:
            return {m["name"]: round((m.get("size_vram") or 0) / 1048576)
                    for m in (_get(self.base_url + "/api/ps") or {})
                    .get("models", [])}
        except Exception:
            return {}

    def load(self, model_id, num_ctx, keep_alive="15m", think=False):
        body = {"model": model_id, "prompt": "hi", "stream": False,
                "keep_alive": keep_alive,
                "options": {"num_ctx": num_ctx, "num_predict": 8}}
        if think is False:
            body["think"] = False
        _post(self.base_url + "/api/generate", body)

    def evict(self, model_id):
        try:
            _post(self.base_url + "/api/generate",
                  {"model": model_id, "prompt": "", "keep_alive": 0},
                  timeout=120)
        except Exception:
            pass

    def evict_all(self):
        for name in list(self.resident()):
            self.evict(name)


class LlamaServerBackend:
    """Pinned seats. One process per seat, owned end to end (R9)."""

    name = "llama-server"

    def __init__(self, binary: Path | None = None):
        self.binary = binary or (runtime_dir() / "llama.cpp" /
                                 "llama-server.exe")
        self.procs: dict = {}          # model_id -> (Popen, port)

    def resident(self):
        return {m: 0 for m in self.procs}

    def load(self, model_id, num_ctx, *, gguf_path, port,
             n_cpu_moe=None, timeout=300):
        cmd = [str(self.binary), "-m", str(gguf_path), "--alias", model_id,
               "--host", "127.0.0.1", "--port", str(port),
               "-ngl", "99", "--flash-attn", "on", "-c", str(num_ctx),
               "--jinja", "--no-webui"]
        if n_cpu_moe is not None:
            cmd += ["--n-cpu-moe", str(n_cpu_moe)]
        proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL,
                                stderr=subprocess.DEVNULL,
                                cwd=str(self.binary.parent))
        t0 = time.time()
        while time.time() - t0 < timeout:
            if proc.poll() is not None:
                raise TransitionError(
                    "llama-server for %s exited %s during load"
                    % (model_id, proc.returncode))
            try:
                with urllib.request.urlopen(
                        "http://127.0.0.1:%d/health" % port, timeout=3) as r:
                    if r.status == 200:
                        self.procs[model_id] = (proc, port)
                        return round(time.time() - t0, 2)
            except Exception:
                time.sleep(1.5)
        proc.terminate()
        raise TransitionError("llama-server for %s never became ready in %ss"
                              % (model_id, timeout))

    def evict(self, model_id):
        entry = self.procs.pop(model_id, None)
        if not entry:
            return
        proc, _ = entry
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=60)
            except Exception:
                proc.kill()

    def evict_all(self):
        for m in list(self.procs):
            self.evict(m)


class ComfyUIBackend:
    """Image generation. Started for a lease, stopped on release."""

    name = "comfyui"

    def __init__(self, root: Path | None = None, port=8188):
        self.root = root or (runtime_dir() / "ComfyUI")
        self.venv = runtime_dir() / "venv-comfy" / "Scripts" / "python.exe"
        self.port = port
        self.proc = None

    def running(self):
        try:
            urllib.request.urlopen(
                "http://127.0.0.1:%d/system_stats" % self.port, timeout=2)
            return True
        except Exception:
            return False

    def start(self, timeout=300):
        if self.running():
            return 0.0
        self.proc = subprocess.Popen(
            [str(self.venv), "main.py", "--port", str(self.port)],
            cwd=str(self.root), stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL)
        t0 = time.time()
        while time.time() - t0 < timeout:
            if self.proc.poll() is not None:
                raise TransitionError("ComfyUI exited %s" % self.proc.returncode)
            if self.running():
                return round(time.time() - t0, 2)
            time.sleep(2)
        self.stop()
        raise TransitionError("ComfyUI never became ready in %ss" % timeout)

    def stop(self):
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=60)
            except Exception:
                self.proc.kill()
        self.proc = None


# ─────────────────────────────────────────────────────────────────────────────
#  The Arbiter
# ─────────────────────────────────────────────────────────────────────────────

class Arbiter:
    def __init__(self, profile=None, entries=None, *, ollama=None,
                 llama=None, comfy=None, gguf_paths=None):
        self.profile = profile or hwp.get()
        self.entries = entries if entries is not None else \
            rc.installed_entries(self.profile)
        self.ollama = ollama or OllamaBackend()
        self.llama = llama or LlamaServerBackend()
        self.comfy = comfy or ComfyUIBackend()
        self.gguf_paths = dict(gguf_paths or {})
        self.state = STATE_DEFAULT
        self.plan = None
        self.lease = None
        self.transitions = []           # audit trail, with timings
        self._lock = threading.Lock()

    # ── planning ────────────────────────────────────────────────────────────

    def compute_plan(self, overrides=None):
        self.plan = rp.plan(self.profile, self.entries, overrides)
        return self.plan

    def timeout_for(self, entry):
        est = (entry or {}).get("est_load_s")
        if not est:
            return TIMEOUT_FLOOR_S * TIMEOUT_MULTIPLE
        return max(TIMEOUT_FLOOR_S, est * TIMEOUT_MULTIPLE)

    # ── headroom, before anything is loaded ─────────────────────────────────

    def admit(self, entry, *, current_host_mib=0):
        """Both refusals, checked before a load is attempted (R2 + R8).

        The disk charge for a LOAD is the host-RAM portion, not the artifact
        size. The artifact is already on disk — loading it does not consume
        the disk again. What it consumes is pagefile: Phase A A7 measured free
        disk falling 27.7 -> 7.0 GB while a 29 GB model was resident, and
        recovering when it unloaded. So the pagefile grows with the RESIDENT
        SET, and charging the artifact instead both refuses loads that are fine
        and misses the growth that is not.

        `check_disk_headroom` with the artifact size remains the right check
        for a DOWNLOAD, which is a different question.
        """
        total = 0
        gpu_portion = 0
        for m in (entry.get("measured") or []):
            if (m.get("total_mib") or 0) > total:
                total = m["total_mib"]
                gpu_portion = m.get("vram_mib") or 0
        ram = rp.check_ram_headroom(self.profile, total, current_host_mib)
        if not ram["ok"]:
            return ram
        host_mib = max(0, total - gpu_portion)
        disk = rp.check_disk_headroom(self.profile, host_mib)
        if not disk["ok"]:
            return disk
        return {"ok": True, "rule_id": None, "explanation": ""}

    # ── boot ────────────────────────────────────────────────────────────────

    def boot(self, *, measure_baseline=True):
        """Bring the machine to the default plan.

        Reconciles rather than reloading: a seat that is already resident and
        matches the plan is adopted, which on this host avoids a needless 20.5 s
        load every restart.
        """
        with self._lock:
            if measure_baseline:
                # The one moment we can honestly measure the idle GPU floor.
                self.ollama.evict_all()
                self.llama.evict_all()
                time.sleep(2)
                hwp.refresh_baseline(self.profile, assert_idle=True)
            self.compute_plan()
            self.state = STATE_TRANSITIONING
            t0 = time.time()
            try:
                already = self.ollama.resident()
                for role in ("interactive_brain", "sidekick"):
                    seat = (self.plan["seats"] or {}).get(role)
                    if not seat or seat.get("status") != "pinned":
                        continue
                    if seat["model_id"] in already:
                        self._record("adopt", role, seat["model_id"], 0.0)
                        continue
                    self._load_pinned(seat, role)
                emb = (self.plan["seats"] or {}).get("embedder")
                if emb and str(emb.get("device", "")).startswith("gpu"):
                    self._load_leased(emb, "embedder")
                self.state = STATE_DEFAULT
            except Exception as e:
                self.state = STATE_ROLLING_BACK
                self._rollback()
                self.state = STATE_DEGRADED
                raise TransitionError("boot failed: %s" % e)
            self._record("boot", "plan", None, round(time.time() - t0, 2))
            return self.plan

    # ── leases ──────────────────────────────────────────────────────────────

    def grant(self, kind, *, role=None, ttl_s=300):
        """Grant a capability lease, executing its transition serially."""
        with self._lock:
            if self.lease is not None:
                return {"ok": False, "error": "lease %s already held"
                        % self.lease["kind"]}
            if self.state not in (STATE_DEFAULT,):
                return {"ok": False, "error": "arbiter is %s" % self.state}
            self.state = STATE_TRANSITIONING
            t0 = time.time()
            try:
                if kind == "heavy_turn":
                    seat = self.plan["seats"].get("heavy_hitter")
                    if seat is None:
                        refusal = [r for r in self.plan["refusals"]
                                   if r["role"] == "heavy_hitter"]
                        self.state = STATE_DEFAULT
                        return {"ok": False,
                                "error": refusal[0]["explanation"]
                                if refusal else "no heavy seat"}
                    entry = self._entry(seat["model_id"])
                    adm = self.admit(entry)
                    if not adm["ok"]:
                        self.state = STATE_DEFAULT
                        return {"ok": False, "refused": adm,
                                "error": adm["explanation"]}
                    displaced = self._evict_pinned()
                    self._load_leased(seat, "heavy_hitter")
                    self.lease = {"kind": kind, "role": "heavy_hitter",
                                  "model_id": seat["model_id"],
                                  "displaced": displaced,
                                  "expires_at": time.time() + ttl_s}
                elif kind == "image_job":
                    displaced = self._evict_pinned()
                    self.ollama.evict_all()
                    took = self.comfy.start()
                    self._record("start", "image", "comfyui", took)
                    self.lease = {"kind": kind, "role": "image",
                                  "model_id": "z-image-turbo-fp8",
                                  "displaced": displaced,
                                  "expires_at": time.time() + ttl_s}
                else:
                    self.state = STATE_DEFAULT
                    return {"ok": False, "error": "unknown lease %r" % kind}
                self.state = STATE_LEASED
                el = round(time.time() - t0, 2)
                self._record("grant", kind, self.lease.get("model_id"), el)
                return {"ok": True, "lease": dict(self.lease),
                        "transition_s": el}
            except Exception as e:
                self.state = STATE_ROLLING_BACK
                self._rollback()
                return {"ok": False, "error": str(e), "rolled_back": True}

    def release(self):
        """Give the GPU back and restore the default plan."""
        with self._lock:
            if self.lease is None:
                return {"ok": True, "note": "no lease held"}
            kind = self.lease["kind"]
            self.state = STATE_TRANSITIONING
            t0 = time.time()
            try:
                if kind == "heavy_turn":
                    self.ollama.evict(self.lease["model_id"])
                    self.llama.evict(self.lease["model_id"])
                elif kind == "image_job":
                    self.comfy.stop()
                self._restore_pinned()
                self.lease = None
                self.state = STATE_DEFAULT
                el = round(time.time() - t0, 2)
                self._record("release", kind, None, el)
                return {"ok": True, "transition_s": el}
            except Exception as e:
                self.state = STATE_DEGRADED
                return {"ok": False, "error": str(e)}

    def expire_if_due(self):
        """A crashed lease holder must not strand the GPU."""
        if self.lease and time.time() > self.lease.get("expires_at", 0):
            return self.release()
        return {"ok": True, "note": "not due"}

    # ── internals ───────────────────────────────────────────────────────────

    def _entry(self, model_id):
        for e in self.entries:
            if e["model_id"] == model_id:
                return e
        return {"model_id": model_id}

    def _record(self, action, role, model_id, seconds):
        self.transitions.append({"action": action, "role": role,
                                 "model_id": model_id, "seconds": seconds,
                                 "state": self.state})

    def _load_pinned(self, seat, role):
        """R9: a pinned seat is a process we own, not a request to a daemon."""
        entry = self._entry(seat["model_id"])
        gguf = self.gguf_paths.get(seat["model_id"])
        t0 = time.time()
        if gguf:
            port = PORT_BASE + len(self.llama.procs)
            took = self.llama.load(seat["model_id"], seat["num_ctx"],
                                   gguf_path=gguf, port=port,
                                   timeout=self.timeout_for(entry))
        else:
            # No GGUF mapped: fall back to Ollama and say so, rather than
            # silently claiming a pin the backend will not honour.
            self.ollama.load(seat["model_id"], seat["num_ctx"])
            took = round(time.time() - t0, 2)
            seat["pin_unenforced"] = (
                "no GGUF mapped for %s, so this seat runs on Ollama and MAY BE "
                "EVICTED by the daemon's own scheduler (R9)" % seat["model_id"])
        self._record("load-pinned", role, seat["model_id"], took)
        return took

    def _load_leased(self, seat, role):
        """Leased seats may use either backend; a lease is when eviction is
        wanted, so Ollama's own scheduler is harmless here.

        The 26b runs on llama-server by measurement: within the R3 VRAM ceiling
        the two backends tie (27.95 Ollama vs 27.80 llama-server at
        --n-cpu-moe 20), and re-pulling Ollama's 17 GB copy would itself breach
        R8. llama-server also takes explicit --n-cpu-moe and -c, which is the
        control the offload placement needs.
        """
        entry = self._entry(seat["model_id"])
        gguf = self.gguf_paths.get(seat["model_id"])
        t0 = time.time()
        if gguf:
            port = PORT_BASE + 20 + len(self.llama.procs)
            took = self.llama.load(
                seat["model_id"], seat["num_ctx"] or 2048, gguf_path=gguf,
                port=port,
                n_cpu_moe=(seat.get("offload") or {}).get("n_cpu_moe", 20)
                if seat.get("is_moe") else None,
                timeout=self.timeout_for(entry))
        else:
            self.ollama.load(seat["model_id"], seat["num_ctx"] or 2048)
            took = round(time.time() - t0, 2)
        self._record("load-leased", role, seat["model_id"], took)
        return took

    def _evict_pinned(self):
        displaced = []
        for role in ("interactive_brain", "sidekick", "embedder"):
            seat = (self.plan["seats"] or {}).get(role)
            if not seat or not str(seat.get("device", "")).startswith("gpu"):
                continue
            t0 = time.time()
            self.llama.evict(seat["model_id"])
            self.ollama.evict(seat["model_id"])
            displaced.append(role)
            self._record("evict", role, seat["model_id"],
                         round(time.time() - t0, 2))
        return displaced

    def _restore_pinned(self):
        for role in ("interactive_brain", "sidekick"):
            seat = (self.plan["seats"] or {}).get(role)
            if seat and seat.get("status") == "pinned":
                self._load_pinned(seat, role)

    def _rollback(self):
        try:
            self.comfy.stop()
            self.llama.evict_all()
            self.ollama.evict_all()
            self._restore_pinned()
            self.lease = None
        except Exception:
            pass

    # ── introspection ───────────────────────────────────────────────────────

    def status(self):
        return {
            "state": self.state,
            "lease": dict(self.lease) if self.lease else None,
            "plan_seats": {r: (s or {}).get("model_id")
                           for r, s in (self.plan or {}).get("seats", {}).items()},
            "resident_ollama": self.ollama.resident(),
            "resident_llama_server": list(self.llama.procs),
            "transitions": list(self.transitions),
        }

    def shutdown(self):
        with self._lock:
            self.comfy.stop()
            self.llama.evict_all()
            self.state = STATE_DEFAULT

"""Measured beats estimated: the on-device benchmark that runs after a model
is installed, and the calibration it feeds back.

The screen's "about" speeds come from a bandwidth formula. One real run on
this machine replaces the estimate for that model with a measurement row
(the same rows the residency planner reads) and calibrates every other
row's "about" figure by measured ÷ estimated. The bench takes the card
through the arbiter's lease, so it never races the brain for memory, and it
writes a receipt that says exactly what ran and what it found.
"""
from __future__ import annotations

import json
import os
import subprocess
import threading
import time
from pathlib import Path

from agent_friday.core import runtime_dir

BENCH_PROMPT = 512
BENCH_GEN = 128
BENCH_REPS = 2
BENCH_TIMEOUT_S = 900


def receipts_dir() -> Path:
    return runtime_dir() / "models" / "bench"


def receipt_path(model_id: str) -> Path:
    safe = "".join(c if c.isalnum() or c in "-_." else "_" for c in model_id)
    return receipts_dir() / ("%s.json" % safe)


def last_receipt(model_id: str) -> dict | None:
    try:
        return json.loads(receipt_path(model_id).read_text(encoding="utf-8"))
    except Exception:
        return None


def bench_binary_for(engine: str | None) -> Path | None:
    """`llama-bench` next to the server binary that serves this model, so a
    Bonsai file is benchmarked by the fork that can load it."""
    candidates = []
    if engine:
        e = Path(engine)
        candidates.append(e.with_name(e.name.replace("llama-server", "llama-bench")))
    candidates.append(runtime_dir() / "llama.cpp-bonsai" / ("llama-bench.exe" if os.name == "nt" else "llama-bench"))
    candidates.append(runtime_dir() / "llama.cpp" / ("llama-bench.exe" if os.name == "nt" else "llama-bench"))
    for c in candidates:
        if c.exists():
            return c
    return None


def _gpu_used_mib() -> int | None:
    try:
        r = subprocess.run(["nvidia-smi", "--query-gpu=memory.used", "--format=csv,noheader,nounits"],
                           capture_output=True, text=True, timeout=5)
        return int(r.stdout.strip().splitlines()[0])
    except Exception:
        return None


def run_llama_bench(binary: Path, gguf: str, *, n_gpu_layers: int = 99, kv_type: str = "q8_0",
                    timeout_s: int = BENCH_TIMEOUT_S) -> dict:
    """One llama-bench run, parsed. Returns `{prompt_tok_s, decode_tok_s,
    peak_vram_delta_mib, wall_s, raw}`; raises on a refused file."""
    before = _gpu_used_mib()
    peak = {"v": before or 0}
    stop = threading.Event()

    def _sample():
        while not stop.is_set():
            v = _gpu_used_mib()
            if v is not None:
                peak["v"] = max(peak["v"], v)
            stop.wait(1.0)
    th = threading.Thread(target=_sample, daemon=True)
    th.start()
    t0 = time.time()
    try:
        r = subprocess.run([str(binary), "-m", gguf, "-ngl", str(n_gpu_layers), "-fa", "on",
                            "-ctk", kv_type, "-ctv", kv_type, "-p", str(BENCH_PROMPT), "-n", str(BENCH_GEN),
                            "-r", str(BENCH_REPS), "-o", "json"],
                           capture_output=True, text=True, timeout=timeout_s, cwd=str(binary.parent))
    finally:
        stop.set()
        th.join(timeout=2)
    wall = round(time.time() - t0, 1)
    if r.returncode != 0:
        raise RuntimeError("llama-bench exited %s: %s" % (r.returncode, (r.stderr or r.stdout)[-600:]))
    rows = _parse_bench_json(r.stdout)
    prompt = next((x for x in rows if int(x.get("n_prompt") or 0) > 0 and int(x.get("n_gen") or 0) == 0), None)
    decode = next((x for x in rows if int(x.get("n_gen") or 0) > 0 and int(x.get("n_prompt") or 0) == 0), None)
    return {"prompt_tok_s": float(prompt["avg_ts"]) if prompt else None,
            "decode_tok_s": float(decode["avg_ts"]) if decode else None,
            "peak_vram_delta_mib": (peak["v"] - before) if before is not None else None,
            "wall_s": wall, "raw": rows}


def _parse_bench_json(text: str) -> list:
    start = text.find("[")
    end = text.rfind("]")
    if start < 0 or end < 0:
        raise RuntimeError("llama-bench printed no JSON")
    return json.loads(text[start:end + 1])


def bench(model_id: str, *, profile: dict | None = None, runner=None, arbiter=None,
          through_lease: bool = True) -> dict:
    """Benchmark an installed model and record what was measured.

    `runner(binary, gguf, n_gpu_layers, kv_type) -> dict` replaces the real
    llama-bench in tests. The run goes through the arbiter's `bench_job`
    lease when an arbiter governs this process, so the brain stands down
    first and comes back verified afterwards.
    """
    from agent_friday.services import hardware_profile as hwp
    from agent_friday.services import model_fit as mf
    from agent_friday.services import model_store
    from agent_friday.services import residency_catalog as rc
    prof = profile or hwp.get()
    rec = model_store.get(model_id)
    if not rec or not rec.get("path"):
        return {"status": "error", "error": "%s is not in the store" % model_id}
    binary = bench_binary_for(rec.get("engine"))
    if runner is None and binary is None:
        return {"status": "error", "error": "no llama-bench binary is installed beside the engine"}
    gpu = (prof.get("gpus") or [None])[0]
    ngl = 99 if gpu else 0
    kv_type = "q8_0" if gpu else "f16"
    run = runner or (lambda b, g, n, k: run_llama_bench(b, g, n_gpu_layers=n, kv_type=k))

    def _job():
        return run(binary, rec["path"], ngl, kv_type)

    receipt = {"model_id": model_id, "file": rec.get("path"), "engine": rec.get("engine"),
               "bench": str(binary) if binary else "(injected runner)", "n_gpu_layers": ngl,
               "kv_type": kv_type, "started_at": time.strftime("%Y-%m-%dT%H:%M:%S"), "status": "running"}
    try:
        if through_lease:
            if arbiter is None:
                try:
                    from agent_friday.services.residency_arbiter import get_arbiter
                    arbiter = get_arbiter()
                except Exception:
                    arbiter = None
        if arbiter is not None and through_lease and hasattr(arbiter, "heavy_job"):
            hj = arbiter.heavy_job("bench_job", _job, timeout_s=BENCH_TIMEOUT_S,
                                   job_id="bench-%s" % model_id.replace(":", "-"), expect_files=False)
            receipt["swap"] = {"receipt": hj.get("receipt"), "restored": hj.get("restored"),
                               "verified": hj.get("verified")}
            if not hj.get("ok"):
                raise RuntimeError(hj.get("error") or "the bench did not finish")
            result = hj.get("result") or {}
        else:
            result = _job()
        receipt.update(status="measured", result={k: v for k, v in result.items() if k != "raw"})
        decode = result.get("decode_tok_s")
        est = mf.decode_estimate(int(rec.get("size_bytes") or 0), prof, gpu_fraction=1.0 if gpu else 0.0,
                                 gpu_name=(gpu or {}).get("name"))
        row = {"num_ctx": int(rec.get("serve_num_ctx") or 8192), "vram_mib": result.get("peak_vram_delta_mib"),
               "total_mib": result.get("peak_vram_delta_mib"), "pct_gpu": 100 if gpu else 0,
               "tok_s_median": decode, "prompt_tok_s": result.get("prompt_tok_s"),
               "backend": "llama-server", "bench": "llama-bench p%d n%d" % (BENCH_PROMPT, BENCH_GEN)}
        rc.record_measurement(model_id, rc.profile_fingerprint(prof), row)
        receipt["measurement"] = row
        if decode and est.get("tok_s") and est.get("basis") == "about":
            raw_estimate = est["tok_s"] / max(0.01, (est.get("calibration") or {}).get("factor", 1.0))
            receipt["calibration"] = mf.record_calibration(model_id, float(decode), float(raw_estimate))
            receipt["calibration"]["estimated_before"] = round(raw_estimate, 1)
    except Exception as e:
        receipt.update(status="failed", error="%s: %s" % (type(e).__name__, e))
    receipt["finished_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    try:
        receipts_dir().mkdir(parents=True, exist_ok=True)
        receipt_path(model_id).write_text(json.dumps(receipt, indent=1, default=str), encoding="utf-8")
    except Exception:
        pass
    return receipt


def after_install(job: dict) -> None:
    """The download pipeline's hook: a model counts as fully installed once
    it has a measurement. Runs on the download thread, after registration."""
    try:
        from agent_friday.core import process_register, process_update
        pid = "bench-%s" % (job.get("id") or "x")[3:]
        process_register(pid, name="Benchmark", label="Measuring %s on this computer" % job.get("label"),
                         category="monitoring", icon="⏱", model=job.get("model_id"))
    except Exception:
        pid = None
    out = bench(job["model_id"])
    if pid:
        try:
            from agent_friday.core import process_update
            ok = out.get("status") == "measured"
            r = out.get("result") or {}
            process_update(pid, status="completed" if ok else "error", progress=1.0,
                           label=("%s: %s tok/s measured" % (job.get("label"), r.get("decode_tok_s")))
                           if ok else "Measuring %s failed: %s" % (job.get("label"), out.get("error")))
        except Exception:
            pass

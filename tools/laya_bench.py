"""Measure Laya's CPU engines on this PC, and check they agree with fp32.

    python tools/laya_bench.py run --engine torch-fp32 --threads 8 --out fp32.json
    python tools/laya_bench.py build-onnx
    python tools/laya_bench.py ab --engines torch-fp32 onnx-fp32 --threads 4 --out ab.json
    python tools/laya_bench.py compare ab.json ab.json --engine onnx-fp32 --record

MEASURED 2026-09-29, i7-10700F, 4 threads, CPU ~74% busy with other work
(p50 ms; engines rotated in one process):
    workload     torch-fp32  onnx-fp32  onnx-int8
    gate_1q          477        371        274
    voice_1q         380        299        213
    voice_2q         774        730        496
    gate_3q         1471       1301        998
    long_1q         1527       1745       1351
    answers differing from fp32 (of 150):   0   31-58
onnx-int8 is fastest and wrong too often; onnx-fp32 ships.

One engine per process: fp32 alone is ~1.7 GB resident, and two engines in
one process would measure the memory pressure as much as the engine.

WORKLOADS are shaped like Friday's real asks, with no personal content:
  gate_1q        the approval gate's severity question on a tool call
  gate_3q        severity + leaves_machine + changes_outside, one pass
  voice_2q       touches_private + direct_command on a spoken turn
  voice_1q       direct_command alone on a spoken turn (the reflex check)
  long_1q        severity on a ~350-token described action

AGREEMENT: every engine answers the same cases (tools/severity_eval.py plus
the tools the owner labelled plus voice turns) and `compare` reports how many
choices differ from fp32 and the largest probability difference. `--record`
marks the ONNX artifact usable only if no choice differs.
"""
from __future__ import annotations

import argparse
import gc
import json
import os
import statistics
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))
os.environ.setdefault("USE_TF", "0")
os.environ.setdefault("HF_HUB_DISABLE_XET", "1")

#: Tools the owner labelled "reaches outside my machine" (2026-09-29), in the
#: form the Grants screen probes them.
OWNER_OUTWARD_TOOLS = [
    "mcp_github_search_users", "mcp_github_list_commits", "mcp_github_search_repositories",
    "mcp_github_get_pull_request", "mcp_github_list_pull_requests",
    "mcp_higgsfield_list_websites", "mcp_higgsfield_show_generations",
    "mcp_higgsfield_show_marketing_studio_generations",
    "mcp_higgsfield_list_project_assets", "mcp_higgsfield_get_preset_instructions",
]
VOICE_TURNS = [
    "what's on my calendar tomorrow morning",
    "turn the volume down",
    "play some jazz",
    "open the news workspace",
    "summarise my doctor's notes from last week for the appointment",
    "how much did we spend on groceries this month",
    "what's the capital of Portugal",
    "remind me to call the dentist at four",
    "pause",
    "write me a short poem about autumn",
]
LONG_STATE = (
    "Send mail as you.\n\nTo: <email>\nSubject: Quarterly planning follow-up\n\n"
    + " ".join(["Thanks for the time today. Here is the summary of what we agreed: "
                "the roadmap review moves to next Thursday, the budget draft goes "
                "to finance by Friday, and the hiring plan waits for the new headcount "
                "numbers."] * 6))
WORKLOADS = {
    "gate_1q": ("mcp_github_search_users {\"q\": \"octocat\"}", ["severity"]),
    "gate_3q": ("mcp_github_search_users {\"q\": \"octocat\"}",
                ["severity", "leaves_machine", "changes_outside"]),
    "voice_2q": ("turn the volume down", ["touches_private", "direct_command"]),
    "voice_1q": ("turn the volume down", ["direct_command"]),
    "long_1q": (LONG_STATE, ["severity"]),
}


def _cases():
    from tools.severity_eval import CASES
    out = [(c[0], ["severity", "leaves_machine", "changes_outside"]) for c in CASES]
    out += [(t + " {}", ["severity", "leaves_machine", "changes_outside"])
            for t in OWNER_OUTWARD_TOOLS]
    out += [(v, ["touches_private", "direct_command", "source"]) for v in VOICE_TURNS]
    return out


def _load(engine: str, threads: int):
    import laya
    from agent_friday.services import laya_runtime
    t0 = time.time()
    agent = laya.load("convaiinnovations/laya", device="cpu")
    loaded = time.time() - t0
    t1 = time.time()
    laya_runtime.apply_engine(agent, engine, threads=threads)
    gc.collect()
    return agent, loaded, time.time() - t1


def _rss_mb():
    try:
        import psutil
        return round(psutil.Process().memory_info().rss / 2**20)
    except Exception:
        return None


def cmd_run(a):
    from agent_friday.services import laya_questions
    agent, load_s, apply_s = _load(a.engine, a.threads)
    res = {"engine": a.engine, "threads": a.threads, "load_s": round(load_s, 1),
           "apply_s": round(apply_s, 1), "rss_mb": _rss_mb(), "workloads": {}, "answers": []}
    for name, (state, qids) in WORKLOADS.items():
        qs = laya_questions.select(qids)
        for _ in range(3):
            agent.predict(state, qs)
        ms = []
        for _ in range(a.runs):
            t = time.perf_counter()
            agent.predict(state, qs)
            ms.append((time.perf_counter() - t) * 1000)
        ms.sort()
        res["workloads"][name] = {"p50_ms": round(statistics.median(ms), 1),
                                  "p95_ms": round(ms[min(len(ms) - 1, int(len(ms) * .95))], 1),
                                  "min_ms": round(ms[0], 1), "runs": len(ms),
                                  "questions": len(qids)}
        print(a.engine, a.threads, name, res["workloads"][name], flush=True)
    for state, qids in _cases():
        r = agent.predict(state, laya_questions.select(qids))
        res["answers"].append({"state": state[:120], "answers": {
            q: {"choice": v.get("choice"), "probabilities": v.get("probabilities")}
            for q, v in (r.get("answers") or {}).items()}})
    res["rss_mb_after"] = _rss_mb()
    Path(a.out).write_text(json.dumps(res, indent=2), encoding="utf-8")
    print("wrote", a.out)


def cmd_ab(a):
    """All engines in ONE process, run in rotation, iteration by iteration.

    This PC is rarely idle: the brain's llama-server and other work keep the
    CPU busy. Timing engines one after another in separate processes measures
    whatever else was running at the time as much as the engine. Rotating
    them within each workload gives every engine the same contention.
    """
    import copy
    import torch
    import laya
    from agent_friday.services import laya_questions, laya_runtime
    agent = laya.load("convaiinnovations/laya", device="cpu")
    fp32 = agent.model
    # Each engine is (model, encoder): the ONNX engine replaces only the
    # encoder, so it shares fp32's head and swaps the encoder in and out.
    fp32_enc = fp32.encoder
    models = {"torch-fp32": (fp32, fp32_enc)}
    if "torch-int8" in a.engines:
        q = copy.deepcopy(fp32)
        q.encoder = torch.ao.quantization.quantize_dynamic(
            q.encoder, {torch.nn.Linear}, dtype=torch.qint8)
        models["torch-int8"] = (q, q.encoder)
    for eng, fname in (("onnx-int8", laya_runtime.INT8_NAME),
                       ("onnx-fp32", laya_runtime.FP32_NAME)):
        if eng in a.engines and (laya_runtime.artifacts_dir() / fname).exists():
            models[eng] = (fp32, laya_runtime._OrtEncoder(
                laya_runtime.artifacts_dir() / fname, a.threads,
                getattr(fp32_enc, "config", None)))

    def use(n):
        model, enc = models[n]
        model.encoder = enc
        agent.model = model
    torch.set_num_threads(a.threads)
    names = [e for e in a.engines if e in models]
    res = {"threads": a.threads, "engines": names, "rss_mb": _rss_mb(),
           "cpu_count": os.cpu_count(), "workloads": {}, "answers": {}}
    for wl, (state, qids) in WORKLOADS.items():
        qs = laya_questions.select(qids)
        times = {n: [] for n in names}
        for n in names:
            use(n)
            for _ in range(2):
                agent.predict(state, qs)
        for _ in range(a.runs):
            for n in names:
                use(n)
                t = time.perf_counter()
                agent.predict(state, qs)
                times[n].append((time.perf_counter() - t) * 1000)
        res["workloads"][wl] = {}
        for n in names:
            ms = sorted(times[n])
            res["workloads"][wl][n] = {"p50_ms": round(statistics.median(ms), 1),
                                       "p95_ms": round(ms[min(len(ms) - 1, int(len(ms) * .95))], 1),
                                       "min_ms": round(ms[0], 1)}
        print(wl, json.dumps(res["workloads"][wl]), flush=True)
    for n in names:
        use(n)
        res["answers"][n] = []
        for state, qids in _cases():
            r = agent.predict(state, laya_questions.select(qids))
            res["answers"][n].append({"state": state[:120], "answers": {
                q: {"choice": v.get("choice"), "probabilities": v.get("probabilities")}
                for q, v in (r.get("answers") or {}).items()}})
    Path(a.out).write_text(json.dumps(res, indent=2), encoding="utf-8")
    print("wrote", a.out)


def cmd_build(a):
    from agent_friday.services import laya_runtime
    agent, load_s, _ = _load("torch-fp32", a.threads)
    info = laya_runtime.build_onnx(
        agent, keep_fp32=True, quantize=a.int8, per_channel=a.per_channel,
        op_types=(["MatMul"] if a.matmul_only else None))
    print(json.dumps(info, indent=2))


def cmd_compare(a):
    ref = json.loads(Path(a.ref).read_text(encoding="utf-8"))
    got = json.loads(Path(a.other).read_text(encoding="utf-8"))
    # An `ab` result holds every engine's answers; compare within it.
    if isinstance(ref.get("answers"), dict):
        ref = {"answers": ref["answers"]["torch-fp32"]}
    if isinstance(got.get("answers"), dict):
        got = {"answers": got["answers"][a.engine]}
    differ, maxdiff, n = [], 0.0, 0
    for r, g in zip(ref["answers"], got["answers"]):
        for q, ra in r["answers"].items():
            ga = g["answers"].get(q) or {}
            n += 1
            if ra["choice"] != ga.get("choice"):
                differ.append({"state": r["state"], "question": q,
                               "fp32": ra["choice"], "other": ga.get("choice")})
            for k, p in (ra.get("probabilities") or {}).items():
                maxdiff = max(maxdiff, abs(float(p) - float((ga.get("probabilities") or {}).get(k, 0))))
    out = {"answers_compared": n, "choices_differ": len(differ), "differ": differ,
           "max_probability_diff": round(maxdiff, 4)}
    print(json.dumps(out, indent=2))
    if a.record:
        from agent_friday.services import laya_runtime
        laya_runtime.record_agreement(len(differ) == 0, out, engine=a.engine)
        print("recorded agreement_ok =", len(differ) == 0)


def main():
    p = argparse.ArgumentParser()
    sub = p.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run")
    r.add_argument("--engine", default="torch-fp32")
    r.add_argument("--threads", type=int, default=8)
    r.add_argument("--runs", type=int, default=20)
    r.add_argument("--out", required=True)
    r.add_argument("--force", action="store_true",
                   help="run onnx-int8 before its agreement is recorded")
    ab = sub.add_parser("ab")
    ab.add_argument("--engines", nargs="+",
                    default=["torch-fp32", "torch-int8", "onnx-int8"])
    ab.add_argument("--threads", type=int, default=4)
    ab.add_argument("--runs", type=int, default=15)
    ab.add_argument("--out", required=True)
    b = sub.add_parser("build-onnx")
    b.add_argument("--threads", type=int, default=8)
    b.add_argument("--int8", action="store_true",
                   help="also write an int8 copy (failed agreement on this checkpoint)")
    b.add_argument("--per-channel", action="store_true")
    b.add_argument("--matmul-only", action="store_true")
    c = sub.add_parser("compare")
    c.add_argument("ref")
    c.add_argument("other")
    c.add_argument("--record", action="store_true")
    c.add_argument("--engine", default="onnx-int8",
                   help="which engine's answers to compare, for an `ab` result")
    a = p.parse_args()
    if a.cmd == "run" and a.engine == "onnx-int8" and a.force:
        from agent_friday.services import laya_runtime
        m = laya_runtime.manifest()
        if not m.get("agreement_ok"):
            m["agreement_ok"] = True       # this process only; not written
            laya_runtime.manifest = lambda: m
    {"run": cmd_run, "ab": cmd_ab, "build-onnx": cmd_build,
     "compare": cmd_compare}[a.cmd](a)


if __name__ == "__main__":
    main()

"""Bench two llama-server builds (and optionally a draft head) on one GGUF, with a receipt.

What it measures, per build, on the same file and the same flags:
  * `llama-bench -p 512 -n 128 -r 2`           prefill and decode at an empty context
  * `llama-bench -p <long> -n 32 -r 1`         prefill at a long prompt (default 32768)
  * greedy identity: the same N prompts through llama-server at temperature 0,
    outputs compared byte for byte between builds (a faster build that answers
    differently is not adopted)
  * with `--draft <head.gguf>`: the second build again with
    `--spec-type draft-mtp -md <head> ...`, decode tok/s on the same prompts and
    whether two consecutive turns keep the prompt cache (`cache_n` on turn two)

The receipt (JSON) lands under `~/.friday/runtime/bench/`, and the verdict
follows the spec's rule: adopt the new build if prefill or decode improves by
at least 20% AND greedy output is identical on every prompt.

Resource discipline (this PC is shared with the live Friday):
  * refuses while `<live checkout>/.claude/SUITE_LOCK` exists (a suite owns the
    memory budget), unless `--ignore-lock` AND the lane granted a gap;
  * refuses when the brain seat answers on its port (the live seat must be parked
    or swapped out by the Arbiter's heavy_job first);
  * refuses under 8 GB of free RAM;
  * uses a port of its own, stops every process it started, and never touches
    the model record. Adoption is a separate, deliberate edit of `engine`.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

HOME = Path(os.environ.get("FRIDAY_HOME") or (Path.home() / ".friday"))
RUNTIME = HOME / "runtime"
LIVE_CHECKOUT = Path(os.environ.get("FRIDAY_LIVE_CHECKOUT") or
                     (Path.home() / "Projects" / "friday-desktop"))
SUITE_LOCK = LIVE_CHECKOUT / ".claude" / "SUITE_LOCK"

PROMPTS = [
    "Reply with the single word OK.",
    "List three prime numbers greater than 100, comma separated, nothing else.",
    "In one sentence, what does a prefix cache do for a language model server?",
    "Write a haiku about a quiet workshop.",
    "What is 17 multiplied by 23? Answer with the number only.",
    "Name the planets of the solar system in order from the sun, one line.",
    "Give a two-sentence summary of why batch size affects prompt processing speed.",
    "Translate 'good morning' into French, Spanish and German, one per line.",
    "Write a Python one-liner that reverses a string s.",
    "Explain in one sentence the difference between latency and throughput.",
    "What rhymes with 'orange'? Answer honestly in one sentence.",
    "Produce a JSON object with keys a, b, c and integer values 1, 2, 3.",
    "Which is heavier, a kilogram of feathers or a kilogram of steel? One sentence.",
    "State the boiling point of water at sea level in Celsius. Number only.",
    "Write one sentence that uses the word 'ternary' correctly.",
    "Sort these words alphabetically: pear, apple, mango, kiwi. One line.",
    "How many days are in a leap year? Number only.",
    "Describe a sunrise in exactly ten words.",
    "What is the capital of Portugal? One word.",
    "Finish the sequence 2, 4, 8, 16 with the next two numbers, comma separated.",
]


def free_ram_gib() -> float:
    try:
        import psutil
        return psutil.virtual_memory().available / (1 << 30)
    except Exception:
        out = subprocess.run(["powershell", "-NoProfile", "-Command",
                              "(Get-CimInstance Win32_OperatingSystem).FreePhysicalMemory"],
                             capture_output=True, text=True, timeout=20).stdout.strip()
        return int(out or 0) / (1 << 20)


def port_answers(port: int) -> bool:
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=2) as r:
            return r.status == 200
    except Exception:
        return False


def run(cmd, timeout, cwd=None) -> subprocess.CompletedProcess:
    return subprocess.run([str(c) for c in cmd], capture_output=True, text=True,
                          timeout=timeout, cwd=str(cwd) if cwd else None,
                          encoding="utf-8", errors="replace")


def build_version(bindir: Path) -> str:
    out = run([bindir / "llama-server.exe", "--version"], 30, cwd=bindir)
    return (out.stdout + out.stderr).strip().splitlines()[0] if (out.stdout + out.stderr).strip() else "unknown"


def llama_bench(bindir: Path, gguf: Path, extra: list, p: int, n: int, r: int,
                timeout: int) -> dict:
    exe = bindir / "llama-bench.exe"
    cmd = [exe, "-m", gguf, "-p", p, "-n", n, "-r", r, "-o", "json"] + extra
    t0 = time.time()
    out = run(cmd, timeout, cwd=bindir)
    rows = []
    try:
        rows = json.loads(out.stdout) if out.stdout.strip().startswith("[") else []
    except Exception:
        rows = []
    res = {"cmd": " ".join(str(c) for c in cmd), "seconds": round(time.time() - t0, 1),
           "rc": out.returncode, "rows": rows}
    if not rows:
        res["stderr_tail"] = out.stderr[-1200:]
    for row in rows:
        kind = "pp" if row.get("n_prompt") else "tg"
        res[f"{kind}{row.get('n_prompt') or row.get('n_gen')}_tok_s"] = row.get("avg_ts")
    return res


class Seat:
    def __init__(self, bindir: Path, gguf: Path, port: int, args: list, log: Path):
        self.bindir, self.gguf, self.port, self.args, self.log = bindir, gguf, port, args, log
        self.proc = None
        self.load_s = None

    def __enter__(self):
        cmd = [self.bindir / "llama-server.exe", "-m", self.gguf, "--host", "127.0.0.1",
               "--port", self.port, "--no-webui"] + self.args
        t0 = time.time()
        self.proc = subprocess.Popen([str(c) for c in cmd], stdout=open(self.log, "ab"),
                                     stderr=subprocess.STDOUT, cwd=str(self.bindir))
        while time.time() - t0 < 600:
            if self.proc.poll() is not None:
                raise RuntimeError(f"llama-server exited {self.proc.returncode}; see {self.log}")
            if port_answers(self.port):
                self.load_s = round(time.time() - t0, 1)
                return self
            time.sleep(1.0)
        raise RuntimeError("seat did not answer /health within 600 s")

    def __exit__(self, *a):
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(30)
            except Exception:
                self.proc.kill()

    def complete(self, messages: list, max_tokens: int) -> dict:
        body = json.dumps({"messages": messages, "max_tokens": max_tokens,
                           "temperature": 0, "top_k": 1, "seed": 7,
                           "reasoning_effort": "none",
                           "chat_template_kwargs": {"enable_thinking": False},
                           "stream": False}).encode()
        req = urllib.request.Request(f"http://127.0.0.1:{self.port}/v1/chat/completions",
                                     data=body, headers={"Content-Type": "application/json"})
        t0 = time.time()
        with urllib.request.urlopen(req, timeout=900) as r:
            data = json.loads(r.read().decode("utf-8"))
        data["_wall_ms"] = int((time.time() - t0) * 1000)
        return data


def greedy_set(seat: Seat, system: str, prompts: list, max_tokens: int) -> dict:
    outs, decode, cache = [], [], []
    for p in prompts:
        d = seat.complete([{"role": "system", "content": system},
                           {"role": "user", "content": p}], max_tokens)
        text = (d.get("choices") or [{}])[0].get("message", {}).get("content") or ""
        outs.append(text)
        tm = d.get("timings") or {}
        if tm.get("predicted_per_second"):
            decode.append(tm["predicted_per_second"])
        cache.append({"prompt_n": tm.get("prompt_n"), "cache_n": tm.get("cache_n"),
                      "prompt_ms": tm.get("prompt_ms"), "wall_ms": d["_wall_ms"]})
    decode_sorted = sorted(decode)
    return {"outputs": outs,
            "decode_tok_s_median": decode_sorted[len(decode_sorted) // 2] if decode_sorted else None,
            "decode_tok_s_min": decode_sorted[0] if decode_sorted else None,
            "per_prompt": cache}


def cache_keeps_across_turns(seat: Seat, system: str) -> dict:
    """Two consecutive turns on one conversation: turn two should re-read only itself."""
    convo = [{"role": "system", "content": system},
             {"role": "user", "content": PROMPTS[2]}]
    d1 = seat.complete(convo, 48)
    a1 = (d1.get("choices") or [{}])[0].get("message", {}).get("content") or ""
    convo += [{"role": "assistant", "content": a1}, {"role": "user", "content": PROMPTS[6]}]
    d2 = seat.complete(convo, 48)
    t1, t2 = d1.get("timings") or {}, d2.get("timings") or {}
    return {"turn1": {"prompt_n": t1.get("prompt_n"), "cache_n": t1.get("cache_n")},
            "turn2": {"prompt_n": t2.get("prompt_n"), "cache_n": t2.get("cache_n")},
            "kept": bool(t2.get("cache_n")) and (t2.get("prompt_n") or 0) < 400}


def pct(new, old):
    try:
        return round((float(new) / float(old) - 1.0) * 100.0, 1)
    except Exception:
        return None


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--old", default=str(RUNTIME / "llama.cpp-bonsai"), help="installed build dir")
    ap.add_argument("--new", default=str(RUNTIME / "llama.cpp-bonsai-prism-b10754"), help="candidate build dir")
    ap.add_argument("--gguf", default=str(RUNTIME / "models" / "gguf" / "Ternary-Bonsai-2-27B-PTQ1_0.gguf"))
    ap.add_argument("--draft", default=None, help="MTP draft head GGUF to try on the new build")
    ap.add_argument("--ctx", type=int, default=131072)
    ap.add_argument("--long-prompt", type=int, default=32768)
    ap.add_argument("--port", type=int, default=8197)
    ap.add_argument("--brain-port", type=int, default=8090)
    ap.add_argument("--prompts", type=int, default=20)
    ap.add_argument("--max-tokens", type=int, default=64)
    ap.add_argument("--serve-args", default="-ngl 99 --flash-attn on --jinja -b 4096 -ub 512 -np 1 "
                                            "--cache-type-k q4_0 --cache-type-v q4_0 --cache-ram 6144 --ctx-checkpoints 4")
    ap.add_argument("--skip-bench", action="store_true", help="skip llama-bench, run only the server tests")
    ap.add_argument("--skip-identity", action="store_true")
    ap.add_argument("--ignore-lock", action="store_true", help="only with a lane gap granted in the LEDGER")
    ap.add_argument("--min-free-gib", type=float, default=8.0)
    a = ap.parse_args()

    old, new, gguf = Path(a.old), Path(a.new), Path(a.gguf)
    out_dir = RUNTIME / "bench"
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    receipt = {"at": stamp, "gguf": str(gguf), "ctx": a.ctx, "serve_args": a.serve_args,
               "old": {"dir": str(old)}, "new": {"dir": str(new)}, "refused": None}

    def refuse(why):
        receipt["refused"] = why
        path = out_dir / f"bench-{stamp}-refused.json"
        path.write_text(json.dumps(receipt, indent=2), encoding="utf-8")
        print("REFUSED:", why, "->", path)
        return 2

    if SUITE_LOCK.exists() and not a.ignore_lock:
        return refuse(f"a suite holds {SUITE_LOCK}")
    if port_answers(a.brain_port):
        return refuse(f"the brain seat answers on :{a.brain_port}; park it or swap it out first")
    if port_answers(a.port):
        return refuse(f"port :{a.port} is already in use")
    free = free_ram_gib()
    receipt["free_ram_gib_at_start"] = round(free, 1)
    if free < a.min_free_gib:
        return refuse(f"{free:.1f} GiB free RAM is under the {a.min_free_gib} GiB floor")
    for d in (old, new):
        if not (d / "llama-server.exe").exists():
            return refuse(f"no llama-server.exe in {d}")
    if not gguf.exists():
        return refuse(f"missing {gguf}")

    receipt["old"]["version"] = build_version(old)
    receipt["new"]["version"] = build_version(new)
    serve_args = a.serve_args.split()
    system = "You are a terse assistant. Answer exactly what is asked."
    prompts = PROMPTS[:a.prompts]

    for label, bindir in (("old", old), ("new", new)):
        rec = receipt[label]
        if not a.skip_bench:
            rec["bench_short"] = llama_bench(bindir, gguf, ["-ngl", "99", "-fa", "1"], 512, 128, 2, 1800)
            rec["bench_long"] = llama_bench(bindir, gguf, ["-ngl", "99", "-fa", "1"], a.long_prompt, 32, 1, 3600)
        if not a.skip_identity:
            log = out_dir / f"bench-{stamp}-{label}.log"
            with Seat(bindir, gguf, a.port, ["-c", a.ctx] + serve_args, log) as seat:
                rec["load_s"] = seat.load_s
                rec["greedy"] = greedy_set(seat, system, prompts, a.max_tokens)
                rec["cache_across_turns"] = cache_keeps_across_turns(seat, system)
        (out_dir / f"bench-{stamp}.partial.json").write_text(json.dumps(receipt, indent=2), encoding="utf-8")

    if a.draft:
        head = Path(a.draft)
        rec = receipt["new_with_draft"] = {"dir": str(new), "draft": str(head)}
        if not head.exists():
            rec["error"] = "missing draft head"
        else:
            log = out_dir / f"bench-{stamp}-draft.log"
            spec = ["--spec-type", "draft-mtp", "-md", head, "-ngld", "99",
                    "--spec-draft-n-max", "3", "--spec-draft-p-min", "0.4", "--backend-sampling"]
            try:
                with Seat(new, gguf, a.port, ["-c", a.ctx] + serve_args + spec, log) as seat:
                    rec["load_s"] = seat.load_s
                    rec["greedy"] = greedy_set(seat, system, prompts, a.max_tokens)
                    rec["cache_across_turns"] = cache_keeps_across_turns(seat, system)
                    rec["loaded"] = True
            except Exception as e:
                rec["loaded"] = False
                rec["error"] = f"{type(e).__name__}: {e}"
                tail = log.read_bytes()[-2000:].decode("utf-8", "replace") if log.exists() else ""
                rec["log_tail"] = tail
                m = re.search(r"prism\.hadamard[^\n]*", tail)
                if m:
                    rec["refusal"] = m.group(0)

    verdict = {"identical_greedy_output": None, "prefill_gain_pct": None,
               "decode_gain_pct": None, "adopt": False, "draft_decode_gain_pct": None,
               "draft_keeps_cache": None}
    o, n = receipt["old"], receipt["new"]
    if o.get("greedy") and n.get("greedy"):
        verdict["identical_greedy_output"] = o["greedy"]["outputs"] == n["greedy"]["outputs"]
        verdict["differing_prompts"] = [i for i, (x, y) in enumerate(zip(o["greedy"]["outputs"], n["greedy"]["outputs"])) if x != y]
        verdict["decode_gain_pct"] = pct(n["greedy"]["decode_tok_s_median"], o["greedy"]["decode_tok_s_median"])
    if o.get("bench_short") and n.get("bench_short"):
        verdict["prefill_gain_pct"] = pct(n["bench_short"].get("pp512_tok_s"), o["bench_short"].get("pp512_tok_s"))
        verdict["bench_decode_gain_pct"] = pct(n["bench_short"].get("tg128_tok_s"), o["bench_short"].get("tg128_tok_s"))
    if o.get("bench_long") and n.get("bench_long"):
        key = f"pp{a.long_prompt}_tok_s"
        verdict["long_prefill_gain_pct"] = pct(n["bench_long"].get(key), o["bench_long"].get(key))
    gains = [g for g in (verdict.get("prefill_gain_pct"), verdict.get("decode_gain_pct"),
                         verdict.get("bench_decode_gain_pct"), verdict.get("long_prefill_gain_pct")) if g is not None]
    verdict["adopt"] = bool(gains) and max(gains) >= 20.0 and verdict["identical_greedy_output"] is True
    d = receipt.get("new_with_draft") or {}
    if d.get("greedy") and n.get("greedy"):
        verdict["draft_decode_gain_pct"] = pct(d["greedy"]["decode_tok_s_median"], n["greedy"]["decode_tok_s_median"])
        verdict["draft_identical_output"] = d["greedy"]["outputs"] == n["greedy"]["outputs"]
        verdict["draft_keeps_cache"] = (d.get("cache_across_turns") or {}).get("kept")
    receipt["verdict"] = verdict
    receipt["free_ram_gib_at_end"] = round(free_ram_gib(), 1)
    path = out_dir / f"bench-{stamp}.json"
    path.write_text(json.dumps(receipt, indent=2), encoding="utf-8")
    try:
        (out_dir / f"bench-{stamp}.partial.json").unlink()
    except Exception:
        pass
    print(json.dumps(verdict, indent=2))
    print("receipt:", path)
    return 0


if __name__ == "__main__":
    sys.exit(main())

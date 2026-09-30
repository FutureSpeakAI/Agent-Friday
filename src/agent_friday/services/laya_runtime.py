"""How Laya runs on this PC: the fastest CPU engine, and a budgeted ask().

The GPU belongs to the brain, so Laya runs on the CPU, and on the CPU the
engine matters more than anything else. Three engines, all answering through
the same `laya.Agent.predict` so every consumer (the approval gate, its
shadow, the chat pilot, voice) is unchanged by the choice:

  torch-fp32   laya's own path. The reference every other engine is checked
               against, and the fallback when an artifact is missing.
  torch-int8   the same model with its Linear layers dynamically quantized.
               No files on disk.
  onnx-int8    the model exported once to ONNX, weights quantized to int8,
               run by onnxruntime. An artifact under ~/.friday/models, built
               by `build_onnx()` and checked against fp32 before use.

Which one is fastest, and whether its answers match fp32, is MEASURED by
tools/laya_bench.py on the machine it runs on, not assumed; the setting
`laya_runtime` picks, and "auto" takes the best engine whose artifact exists
and passed its agreement check.

`ask()` is the reusable fast path. One forward pass for several typed
questions (laya_questions), a remembered answer per model, question and
argument SHAPE, and a budget: an answer later than `budget_ms` is not waited
for, and the caller is told it is missing and why. Nothing on a hot path
calls Laya without a budget and a fallback.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import threading
import time
from collections import OrderedDict
from pathlib import Path
from typing import Any, Dict, Iterable, Optional

_log = logging.getLogger("friday.laya_runtime")

ENGINES = ("torch-fp32", "torch-int8", "onnx-fp32", "onnx-int8")
ONNX_DIRNAME = "laya-onnx"
INT8_NAME = "laya-int8.onnx"
FP32_NAME = "laya-fp32.onnx"
MANIFEST_NAME = "manifest.json"


# ---------------------------------------------------------------------------
#  ENGINES
# ---------------------------------------------------------------------------

def artifacts_dir() -> Path:
    from agent_friday.core import FRIDAY_DIR
    return FRIDAY_DIR / "models" / ONNX_DIRNAME


def checkpoint_revision(agent=None) -> str:
    """The checkpoint snapshot an engine was built from, for cache keys."""
    rev = getattr(agent, "_friday_revision", None)
    if rev:
        return str(rev)
    # A file read, never an import: this is on the ask() path, and importing
    # the ML stack from a request thread is the boot race ml_imports exists
    # to prevent. apply_engine stores the answer on the agent once.
    try:
        ref = (Path(os.environ.get("HF_HOME") or (Path.home() / ".cache" / "huggingface"))
               / "hub" / "models--convaiinnovations--laya" / "refs" / "main")
        return ref.read_text(encoding="utf-8").strip()[:12] or "unknown"
    except Exception:
        return "unknown"


def manifest() -> dict:
    try:
        return json.loads((artifacts_dir() / MANIFEST_NAME).read_text(encoding="utf-8"))
    except Exception:
        return {}


class _EncoderOutput:
    def __init__(self, hidden):
        self.last_hidden_state = hidden


def _OrtEncoder(path: Path, threads: int, config=None):
    """Stands in for the ModernBERT encoder inside laya's DecisionModel.

    Only the encoder runs in onnxruntime: it is 28 of the model's 30
    transformer layers and nearly all of the arithmetic. The decision head
    (two small layers, the option scorer and the act head) stays laya's own
    torch code, so temperature, options and confidence are computed exactly
    as laya computes them. The head is also the part whose attention an ONNX
    export freezes to the example's sequence length, which is why it is not
    exported.

    A torch Module (built here so importing this file never imports torch),
    because DecisionModel only accepts a Module as its `encoder` child.
    """
    import onnxruntime as ort
    import torch

    class OrtEncoder(torch.nn.Module):
        def __init__(self):
            super().__init__()
            so = ort.SessionOptions()
            so.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
            so.intra_op_num_threads = max(1, int(threads))
            so.inter_op_num_threads = 1
            self.session = ort.InferenceSession(str(path), so,
                                                providers=["CPUExecutionProvider"])
            self.config = config

        def forward(self, input_ids=None, attention_mask=None, **_kw):
            (hidden,) = self.session.run(["last_hidden_state"], {
                "input_ids": input_ids.cpu().numpy(),
                "attention_mask": attention_mask.cpu().numpy()})
            return _EncoderOutput(torch.from_numpy(hidden))

    return OrtEncoder()


def default_threads() -> int:
    """Physical cores, leaving one for the rest of Friday."""
    n = os.cpu_count() or 4
    return max(1, min(8, n // 2 - 1 if n >= 8 else n - 1 or 1))


def apply_engine(agent, engine: str, *, threads: Optional[int] = None):
    """Switch a loaded laya.Agent to `engine` in place. Returns the agent.

    Raises if the engine cannot run here; the caller keeps fp32 then.
    """
    import torch
    threads = int(threads or default_threads())
    if engine == "torch-fp32":
        torch.set_num_threads(threads)
    elif engine == "torch-int8":
        torch.set_num_threads(threads)
        # The encoder only: it is 28 of the model's 30 transformer layers, and
        # the decision head's fused attention path cannot take quantized
        # Linear weights.
        agent.model.encoder = torch.ao.quantization.quantize_dynamic(
            agent.model.encoder, {torch.nn.Linear}, dtype=torch.qint8)
    elif engine in ("onnx-int8", "onnx-fp32"):
        path = artifacts_dir() / (INT8_NAME if engine == "onnx-int8" else FP32_NAME)
        m = manifest()
        if not path.exists():
            raise FileNotFoundError("no ONNX artifact at %s; build it first" % path)
        if not (m.get("agreement_ok_by_engine") or {}).get(engine, m.get("agreement_ok") and engine == "onnx-int8"):
            raise RuntimeError("the %s artifact has not passed its agreement check" % engine)
        cfg = getattr(agent.model.encoder, "config", None)
        agent.model.encoder = _OrtEncoder(path, threads, cfg)
        torch.set_num_threads(threads)
    else:
        raise ValueError("unknown laya engine %r" % engine)
    agent._friday_engine = engine
    agent._friday_threads = threads
    agent._friday_revision = checkpoint_revision(agent)
    return agent


def build_onnx(agent, *, out_dir: Optional[Path] = None, keep_fp32: bool = True,
               quantize: bool = False, per_channel: bool = False,
               op_types: Optional[list] = None) -> dict:
    """Export the loaded fp32 ENCODER to ONNX, and optionally an int8 copy.

    Slow (about a minute) and memory-hungry (~3 GB peak); never on a request
    path. The default is the fp32 export alone: measured on this checkpoint,
    it answers exactly as laya's own path (0 of 150 answers differ) and runs
    short inputs 20-25% faster, while int8 weights changed 31-58 of the same
    150 answers and never passed the agreement check.
    """
    import torch
    out = Path(out_dir or artifacts_dir())
    out.mkdir(parents=True, exist_ok=True)
    fp32 = out / "laya-fp32.onnx"
    int8 = out / INT8_NAME
    agent.model.eval()
    encoder = agent.model.encoder

    class _EncOnly(torch.nn.Module):
        def __init__(self, enc):
            super().__init__()
            self.enc = enc

        def forward(self, input_ids, attention_mask):
            return self.enc(input_ids=input_ids, attention_mask=attention_mask).last_hidden_state

    # Two rows of different lengths, so nothing about the example's shape
    # can be mistaken for a constant.
    ids = torch.randint(5, 1000, (2, 37), dtype=torch.long)
    att = torch.ones((2, 37), dtype=torch.long)
    att[1, 30:] = 0
    t0 = time.time()
    with torch.no_grad():
        torch.onnx.export(
            _EncOnly(encoder).eval(), (ids, att), str(fp32), dynamo=False, opset_version=17,
            input_names=["input_ids", "attention_mask"],
            output_names=["last_hidden_state"],
            dynamic_axes={"input_ids": {0: "n", 1: "seq"}, "attention_mask": {0: "n", 1: "seq"},
                          "last_hidden_state": {0: "n", 1: "seq"}},
        )
    exported_s = time.time() - t0
    quantized_s = None
    if quantize:
        from onnxruntime.quantization import QuantType, quantize_dynamic
        t1 = time.time()
        quantize_dynamic(str(fp32), str(int8), weight_type=QuantType.QInt8,
                         per_channel=per_channel,
                         **({"op_types_to_quantize": op_types} if op_types else {}))
        quantized_s = round(time.time() - t1, 1)
    info = {"revision": checkpoint_revision(agent), "export_s": round(exported_s, 1),
            "quantize_s": quantized_s,
            "int8_bytes": int8.stat().st_size if int8.exists() else None,
            "fp32_bytes": fp32.stat().st_size if fp32.exists() else None,
            "built_at": time.time(), "agreement_ok": False,
            "per_channel": per_channel, "op_types": op_types or "default",
            "fp32_kept": bool(keep_fp32)}
    if not keep_fp32:
        for p in out.glob(FP32_NAME + "*"):
            try:
                p.unlink()
            except Exception:
                pass
        for p in out.iterdir():
            # torch writes large initializers beside the model as loose files.
            if p.name not in (INT8_NAME, MANIFEST_NAME) and not p.name.startswith(INT8_NAME)                     and not (keep_fp32 and p.name.startswith(FP32_NAME)):
                try:
                    p.unlink()
                except Exception:
                    pass
    (out / MANIFEST_NAME).write_text(json.dumps(info, indent=2), encoding="utf-8")
    return info


def record_agreement(ok: bool, detail: dict, engine: str = "onnx-int8") -> None:
    """Mark an ONNX artifact usable (or not) after comparing it with fp32."""
    m = manifest()
    by = dict(m.get("agreement_ok_by_engine") or {})
    by[engine] = bool(ok)
    m.update({"agreement_ok_by_engine": by, "agreement_ok": by.get("onnx-int8", False),
              "agreement": dict(m.get("agreement") or {}, **{engine: detail}),
              "checked_at": time.time()})
    artifacts_dir().mkdir(parents=True, exist_ok=True)
    (artifacts_dir() / MANIFEST_NAME).write_text(json.dumps(m, indent=2), encoding="utf-8")


def choose_engine(setting: Optional[str]) -> str:
    """The engine to load: the setting, or for "auto" the best one available."""
    s = str(setting or "auto").strip().lower()
    if s in ENGINES:
        return s
    m = manifest()
    by = m.get("agreement_ok_by_engine") or {"onnx-int8": bool(m.get("agreement_ok"))}
    for engine, name in (("onnx-int8", INT8_NAME), ("onnx-fp32", FP32_NAME)):
        if (artifacts_dir() / name).exists() and by.get(engine):
            return engine
    return "torch-fp32"


# ---------------------------------------------------------------------------
#  THE FAST PATH: ask()
# ---------------------------------------------------------------------------

_CACHE_MAX = 2048
_cache: "OrderedDict[tuple, dict]" = OrderedDict()
_cache_lock = threading.Lock()
_stats = {"asks": 0, "hits": 0, "missing": 0, "answered": 0}
_last_ms: "list" = []
_SHAPE_RE = re.compile(r"^\s*([A-Za-z][A-Za-z0-9_]{1,80})\s+(\{.*\})\s*$", re.S)
#: A value longer than this, or with whitespace, is free text: the answer may
#: depend on it, so the state is keyed exactly rather than by shape.
_SHAPE_VALUE_MAX = 64


def cache_key(state: str) -> str:
    """Tool + argument SHAPE when the state is "<tool> {json}" with no free
    text in it; otherwise the exact state.

    The week-one log shows the same tool always got the same answer, so a
    lookup's verdict does not depend on which repository it names. It may
    depend on what a message says, so any argument that is free text (long, or
    containing whitespace) keeps the exact key.
    """
    s = str(state or "")
    m = _SHAPE_RE.match(s)
    if m:
        try:
            args = json.loads(m.group(2))
        except Exception:
            args = None
        if isinstance(args, dict):
            shape = []
            free_text = False
            for k in sorted(args):
                v = args[k]
                if isinstance(v, str) and (len(v) > _SHAPE_VALUE_MAX or re.search(r"\s", v)):
                    free_text = True
                    break
                shape.append("%s:%s" % (k, type(v).__name__))
            if not free_text:
                return "shape:%s(%s)" % (m.group(1), ",".join(shape))
    return "exact:" + hashlib.sha256(s.encode("utf-8")).hexdigest()


def clear_cache() -> None:
    with _cache_lock:
        _cache.clear()


def stats() -> dict:
    with _cache_lock:
        lat = sorted(_last_ms)
    p = (lambda f: round(lat[min(len(lat) - 1, int(len(lat) * f))], 1) if lat else None)
    return dict(_stats, cache_size=len(_cache), p50_ms=p(0.5), p95_ms=p(0.95))


def _qdigest(qid: str) -> str:
    from agent_friday.services import laya_questions
    return hashlib.sha256(json.dumps(laya_questions.QUESTIONS[qid], sort_keys=True)
                          .encode("utf-8")).hexdigest()[:10]


def ask(state: str, questions: Iterable[str], *, budget_ms: float,
        purpose: str = "") -> dict:
    """Answer several typed questions about one state, within a budget.

    Returns {"status": "ok"|"missing", "answers": {qid: laya answer},
    "cached": [qids], "reason": str|None, "elapsed_ms": float, "engine": str}.
    "missing" means the caller must use its own fallback; the reason says why
    (not loaded, busy, over budget, error), and the miss is counted.
    """
    from agent_friday.services import laya_backend, laya_questions
    t0 = time.monotonic()
    qids = list(questions)
    _stats["asks"] += 1
    agent = laya_backend._agent
    engine = getattr(agent, "_friday_engine", "torch-fp32") if agent is not None else None
    rev = checkpoint_revision(agent)
    key = cache_key(state)
    answers: Dict[str, Any] = {}
    cached = []
    with _cache_lock:
        for q in qids:
            hit = _cache.get((rev, q, _qdigest(q), key))
            if hit is not None:
                answers[q] = hit
                cached.append(q)
                _cache.move_to_end((rev, q, _qdigest(q), key))
    need = [q for q in qids if q not in answers]
    if not need:
        _stats["hits"] += 1
        return _done(t0, "ok", answers, cached, None, engine)
    if agent is None:
        return _done(t0, "missing", answers, cached, "laya not loaded", engine)
    release = laya_backend._reserve_scoring(pilot=False)
    if release is None:
        return _done(t0, "missing", answers, cached, "laya busy", engine)
    box: Dict[str, Any] = {}
    done = threading.Event()

    def _run():
        try:
            box["r"] = agent.predict(str(state or ""), laya_questions.select(need))
        except BaseException as e:  # noqa: BLE001 - reported to the caller
            box["e"] = e
        finally:
            release()
            done.set()
            r = box.get("r")
            if r is not None:
                with _cache_lock:
                    for q in need:
                        a = (r.get("answers") or {}).get(q)
                        if a is not None:
                            _cache[(rev, q, _qdigest(q), key)] = a
                    while len(_cache) > _CACHE_MAX:
                        _cache.popitem(last=False)

    threading.Thread(target=_run, name="laya-ask", daemon=True).start()
    if not done.wait(max(0.0, float(budget_ms)) / 1000.0):
        return _done(t0, "missing", answers, cached,
                     "over budget (%.0f ms)" % budget_ms, engine)
    if "e" in box:
        return _done(t0, "missing", answers, cached,
                     "laya error: %s" % type(box["e"]).__name__, engine)
    for q in need:
        answers[q] = ((box["r"].get("answers") or {}).get(q))
    return _done(t0, "ok", answers, cached, None, engine)


def _done(t0, status, answers, cached, reason, engine) -> dict:
    ms = round((time.monotonic() - t0) * 1000.0, 2)
    with _cache_lock:
        _last_ms.append(ms)
        del _last_ms[:-500]
    if status == "ok":
        _stats["answered"] += 1
    else:
        _stats["missing"] += 1
        _log.info("laya ask missing: %s (%.0f ms)", reason, ms)
    return {"status": status, "answers": answers, "cached": cached,
            "reason": reason, "elapsed_ms": ms, "engine": engine}


def predict_bounded(state: str, questions: Dict[str, dict], *,
                    budget_ms: float) -> dict:
    """One forward pass for questions built at call time (an item choice over
    a shortlist cannot be registered in laya_questions), within a budget.

    Same admission and budget as ask(), no cache: a shortlist is different
    every time. Returns {"status": "ok"|"missing", "result": laya result or
    None, "reason": str|None, "elapsed_ms": float}.
    """
    from agent_friday.services import laya_backend
    t0 = time.monotonic()
    agent = laya_backend._agent

    def done(status, result=None, reason=None):
        ms = round((time.monotonic() - t0) * 1000.0, 2)
        if status != "ok":
            _stats["missing"] += 1
        return {"status": status, "result": result, "reason": reason, "elapsed_ms": ms}

    if agent is None:
        return done("missing", reason="laya not loaded")
    release = laya_backend._reserve_scoring(pilot=False)
    if release is None:
        return done("missing", reason="laya busy")
    box: Dict[str, Any] = {}
    ev = threading.Event()

    def _run():
        try:
            box["r"] = agent.predict(str(state or ""), questions)
        except BaseException as e:  # noqa: BLE001 - reported to the caller
            box["e"] = e
        finally:
            release()
            ev.set()

    threading.Thread(target=_run, name="laya-predict", daemon=True).start()
    if not ev.wait(max(0.0, float(budget_ms)) / 1000.0):
        return done("missing", reason="over budget (%.0f ms)" % budget_ms)
    if "e" in box:
        return done("missing", reason="laya error: %s" % type(box["e"]).__name__)
    return done("ok", result=box["r"])

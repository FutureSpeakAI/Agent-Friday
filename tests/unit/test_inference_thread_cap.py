"""Native thread pools stay bounded however many request threads run inference.

Intel OpenMP (and the other pools torch, MKL and CTranslate2 load) gives every
OS thread that enters a parallel kernel its own team of workers, and the team
outlives the thread that made it. So the in-process embedders run on one
long-lived executor thread, and the pools are capped before they load.

The stub model below stands in for that behaviour: the first encode on a
given thread starts one persistent "team" thread for it. A caller that runs
the model on each request thread grows the process by one thread per request;
a caller that routes through the executor grows it by at most one.
"""
from __future__ import annotations

import os
import subprocess
import sys
import threading
import types
from pathlib import Path

import pytest

np = pytest.importorskip("numpy")

ROOT = Path(__file__).resolve().parents[2]
CALLS = 30


class TeamPerThreadModel:
    """encode() that leaves one parked thread behind per distinct caller."""

    def __init__(self, dim: int = 8):
        self.dim = dim
        self.callers: set[int] = set()
        self._stop = threading.Event()
        self._lock = threading.Lock()

    def _team_for_this_thread(self):
        me = threading.get_ident()
        with self._lock:
            if me in self.callers:
                return
            self.callers.add(me)
        threading.Thread(target=self._stop.wait, daemon=True,
                         name="stub-omp-team").start()

    def encode(self, texts, **_kw):
        self._team_for_this_thread()
        single = isinstance(texts, str)
        rows = [texts] if single else list(texts)
        out = np.ones((len(rows), self.dim), dtype="float32") / np.sqrt(self.dim)
        return out[0] if single else out

    def close(self):
        self._stop.set()


def _os_threads():
    try:
        import psutil
    except ImportError:
        return None
    return psutil.Process().num_threads()


def _from_short_lived_threads(fn, n=CALLS):
    errors = []

    def call():
        try:
            fn()
        except Exception as e:  # pragma: no cover - surfaced below
            errors.append(e)

    for _ in range(n):
        t = threading.Thread(target=call)
        t.start()
        t.join()
    assert not errors, errors


def _assert_flat(fn, model):
    fn()                       # first call may start the executor thread
    py_before = threading.active_count()
    os_before = _os_threads()
    _from_short_lived_threads(fn)
    assert len(model.callers) == 1, (
        "%d distinct threads ran the model; inference must run on one"
        % len(model.callers))
    assert threading.active_count() - py_before <= 1
    os_after = _os_threads()
    if os_before is not None:
        assert os_after - os_before <= 2, (os_before, os_after)


@pytest.fixture
def model():
    m = TeamPerThreadModel()
    yield m
    m.close()


# ── the executor itself ──────────────────────────────────────────────────

def test_executor_runs_every_call_on_one_long_lived_thread():
    from agent_friday.services import inference_executor as ie
    seen = set()
    _from_short_lived_threads(lambda: seen.add(ie.run(threading.get_ident)))
    assert len(seen) == 1
    assert seen.pop() != threading.get_ident()


def test_executor_reentry_runs_inline_instead_of_deadlocking():
    from agent_friday.services import inference_executor as ie
    outer, inner = ie.run(lambda: (threading.get_ident(),
                                   ie.run(threading.get_ident)))
    assert outer == inner


def test_executor_propagates_the_callers_exception():
    from agent_friday.services import inference_executor as ie

    def boom():
        raise ValueError("model said no")

    with pytest.raises(ValueError, match="model said no"):
        ie.run(boom)


# ── the in-process embedding entry points ───────────────────────────────

def test_privacy_classifier_layer3_keeps_the_thread_count_flat(model, monkeypatch):
    from agent_friday.services import sensitivity_classifier as sc
    monkeypatch.setattr(sc, "_EMBEDDER", model)
    monkeypatch.setattr(sc, "_EXEMPLAR_EMBEDS",
                        np.ones((2, model.dim), dtype="float32") / np.sqrt(model.dim))
    tier, sim = sc._embedding_tier("is this sensitive")
    assert tier != -1 and sim > 0
    _assert_flat(lambda: sc._embedding_tier("is this sensitive"), model)


def test_tool_selector_query_keeps_the_thread_count_flat(model, monkeypatch):
    from agent_friday.services import tool_selector as ts
    st = types.ModuleType("sentence_transformers")
    st.SentenceTransformer = lambda *a, **k: model
    monkeypatch.setitem(sys.modules, "sentence_transformers", st)
    monkeypatch.setitem(ts._STATE, "model", model)
    texts = ["send an email", "read the calendar", "search the web"]
    assert ts.rank_texts(texts, "email someone") is not None
    _assert_flat(lambda: ts.rank_texts(texts, "email someone"), model)


def test_context_pruner_keeps_the_thread_count_flat(model):
    from agent_friday.pipeline.context_pruner import ContextPruner
    pruner = ContextPruner()
    pruner._model = model
    counter = iter(range(10_000))
    _assert_flat(lambda: pruner._embed("turn %d" % next(counter)), model)


def test_conversation_memory_embedder_runs_on_the_executor(monkeypatch, model):
    pytest.importorskip("chromadb")
    from chromadb.utils import embedding_functions as efs

    def fake_init(self, model_name="all-MiniLM-L6-v2", device="cpu",
                  normalize_embeddings=False, **kw):
        self.model_name = model_name
        self.device = device
        self.normalize_embeddings = normalize_embeddings
        self.kwargs = kw
        self._model = model

    monkeypatch.setattr(efs.SentenceTransformerEmbeddingFunction, "__init__", fake_init)
    from agent_friday import conversation_memory as cm
    mem = cm.ConversationMemory.__new__(cm.ConversationMemory)
    mem.model_name = "some-other-model"   # skips the cached-model download check
    fn = mem._build_embedding_function()
    assert isinstance(fn, efs.SentenceTransformerEmbeddingFunction)
    assert fn.name() == efs.SentenceTransformerEmbeddingFunction.name()
    _assert_flat(lambda: fn(["remember this"]), model)


# ── the caps ─────────────────────────────────────────────────────────────

_CAP_VARS = ("OMP_NUM_THREADS", "MKL_NUM_THREADS", "OPENBLAS_NUM_THREADS",
             "NUMEXPR_NUM_THREADS", "TOKENIZERS_PARALLELISM", "KMP_BLOCKTIME")


def _caps_in_fresh_process(preset: dict) -> dict:
    env = {k: v for k, v in os.environ.items() if k not in _CAP_VARS}
    env.update(preset)
    env["PYTHONPATH"] = str(ROOT / "src")
    code = ("import os, sys, json\n"
            "from agent_friday.services import thread_caps\n"
            "thread_caps.apply()\n"
            "print(json.dumps({k: os.environ.get(k) for k in %r}))\n"
            "print(json.dumps(sorted(m for m in ('numpy', 'torch') if m in sys.modules)))\n"
            % (_CAP_VARS,))
    out = subprocess.run([sys.executable, "-c", code], env=env, cwd=str(ROOT),
                         capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr
    import json
    lines = out.stdout.strip().splitlines()
    caps, heavy = json.loads(lines[-2]), json.loads(lines[-1])
    assert heavy == [], "applying the caps must not load the pools it caps"
    return caps


def test_caps_are_small_and_set_only_where_unset():
    caps = _caps_in_fresh_process({"MKL_NUM_THREADS": "6"})
    for name in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "NUMEXPR_NUM_THREADS"):
        assert caps[name] in ("1", "2"), (name, caps[name])
    assert caps["MKL_NUM_THREADS"] == "6"
    assert caps["TOKENIZERS_PARALLELISM"] == "false"
    assert caps["KMP_BLOCKTIME"] == "0"


def test_caps_never_override_an_operator_setting():
    caps = _caps_in_fresh_process({"OMP_NUM_THREADS": "12",
                                   "TOKENIZERS_PARALLELISM": "true",
                                   "KMP_BLOCKTIME": "200"})
    assert caps["OMP_NUM_THREADS"] == "12"
    assert caps["TOKENIZERS_PARALLELISM"] == "true"
    assert caps["KMP_BLOCKTIME"] == "200"


class _FakeTorch(types.ModuleType):
    def __init__(self, interop_already_set=False):
        super().__init__("torch")
        self.calls = []
        self._interop_set = interop_already_set

    def set_num_threads(self, n):
        self.calls.append(("threads", n))

    def set_num_interop_threads(self, n):
        if self._interop_set:
            raise RuntimeError("Error: cannot set number of interop threads "
                               "after parallel work has started")
        self.calls.append(("interop", n))


def test_loaded_torch_is_limited_once(monkeypatch):
    from agent_friday.services import thread_caps
    fake = _FakeTorch()
    monkeypatch.setitem(sys.modules, "torch", fake)
    monkeypatch.setattr(thread_caps, "_limited", set())
    monkeypatch.delenv("OMP_NUM_THREADS", raising=False)
    thread_caps.limit_loaded_pools()
    thread_caps.limit_loaded_pools()
    assert fake.calls == [("threads", 2), ("interop", 1)]


def test_torch_interop_already_started_is_not_an_error(monkeypatch):
    from agent_friday.services import thread_caps
    fake = _FakeTorch(interop_already_set=True)
    monkeypatch.setitem(sys.modules, "torch", fake)
    monkeypatch.setattr(thread_caps, "_limited", set())
    monkeypatch.delenv("OMP_NUM_THREADS", raising=False)
    thread_caps.limit_loaded_pools()
    assert fake.calls == [("threads", 2)]


def test_executor_limits_the_pools_before_running_inference(monkeypatch):
    from agent_friday.services import inference_executor as ie
    from agent_friday.services import thread_caps
    seen = []
    monkeypatch.setattr(thread_caps, "limit_loaded_pools",
                        lambda: seen.append(threading.get_ident()))
    worker = ie.run(threading.get_ident)
    assert seen and seen[-1] == worker


def test_a_busy_inference_thread_makes_the_privacy_layer_unavailable_not_late(monkeypatch):
    """The inference thread is shared. A classification stuck behind someone
    else's bulk encode reports the semantic layer unavailable (-1, held as
    PRIVATE) within its bound; it neither waits out the queue nor passes."""
    import time
    from agent_friday.services import inference_executor as ie
    from agent_friday.services import sensitivity_classifier as sc

    class _Model:
        def encode(self, texts, normalize_embeddings=True):
            return np.ones((len(texts), 4), dtype="float32") / 2.0

    monkeypatch.setattr(sc, "_load_embedder", lambda: _Model())
    monkeypatch.setattr(sc, "_EXEMPLAR_EMBEDS", np.ones((1, 4), dtype="float32") / 2.0)
    monkeypatch.setattr(sc, "EMBEDDING_TIMEOUT_S", 0.2)
    started = threading.Event()

    def _bulk():
        started.set()
        time.sleep(1.5)
    hog = threading.Thread(target=ie.run, args=(_bulk,), daemon=True)
    hog.start()
    assert started.wait(5)
    t0 = time.monotonic()
    tier, _ = sc._embedding_tier("a message to classify")
    waited = time.monotonic() - t0
    hog.join(5)
    assert tier == -1, "a classification that cannot run in time is unavailable"
    assert waited < 1.0, f"the classifier waited {waited:.2f}s behind another job"


def test_run_raises_timeout_and_drops_a_job_that_never_started():
    import time
    from agent_friday.services import inference_executor as ie
    started = threading.Event()

    def _bulk():
        started.set()
        time.sleep(0.8)
    hog = threading.Thread(target=ie.run, args=(_bulk,), daemon=True)
    hog.start()
    assert started.wait(5)
    ran = []
    with pytest.raises(TimeoutError):
        ie.run(ran.append, 1, _timeout=0.1)
    hog.join(5)
    ie.run(lambda: None)              # the queue drains past the dropped job
    assert ran == [], "a job dropped on timeout must not run later"

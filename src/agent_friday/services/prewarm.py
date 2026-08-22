"""Fetch everything at install time, so a first conversation contains no download.

The problem this solves is small and very visible: several of Friday's local
models arrive lazily, on first use. Left alone that means someone's first
sentence to her triggers a 90 MB download, and their first voice attempt
triggers two more — which reads as "it hung", not as "it is fetching".

What arrives lazily today:

  all-MiniLM-L6-v2  ~90 MB   sentence-transformers, on first embed
  faster-whisper    ~460 MB  on first speech input
  Piper voice        ~60 MB  on first spoken reply

Each step verifies its own result rather than trusting the call to have worked,
and the report distinguishes "done", "already there", "skipped" and "failed"
instead of collapsing them into a count. A prewarm that quietly failed and
reported success would just move the surprise back to the conversation, which
is the whole thing it exists to prevent.

Nothing here is required. Every step degrades to "you'll download this later"
rather than blocking an install.
"""
from __future__ import annotations

import time
from typing import Callable


class Step:
    """One thing to fetch, and what actually happened to it."""

    def __init__(self, key: str, label: str, mb: int):
        self.key = key
        self.label = label
        self.mb = mb
        self.state = "pending"      # done | present | skipped | failed
        self.detail = ""
        self.seconds = 0.0

    @property
    def ok(self) -> bool:
        return self.state in ("done", "present")


def _embedder(step: Step) -> None:
    """Pull MiniLM into the sentence-transformers cache.

    Verified by encoding a string: a model that loads but cannot produce a
    vector is not warmed, and `SentenceTransformer(...)` returning an object is
    not evidence that it will.
    """
    try:
        from sentence_transformers import SentenceTransformer
    except ImportError:
        step.state = "skipped"
        step.detail = ("sentence-transformers isn't installed — memory will "
                       "run in a degraded mode. `pip install -e .` should have "
                       "brought it.")
        return
    try:
        from agent_friday.services.model_plan import EMBEDDER
        name = EMBEDDER["id"]
    except Exception:
        name = "all-MiniLM-L6-v2"
    m = SentenceTransformer(name)
    vec = m.encode("warm")
    if vec is None or len(vec) == 0:
        raise RuntimeError(f"{name} loaded but produced no vector")
    step.detail = f"{name}, {len(vec)}-dimensional vectors"


def _voice(step: Step) -> None:
    """Whisper + the Piper voice, via the engine's own downloader.

    Reuses `LocalVoiceEngine.ensure_ready()` rather than re-implementing the
    fetch, then confirms with `models_ready()` — which checks the files are on
    disk, not that a function returned True.
    """
    from agent_friday.services import local_voice
    eng = local_voice.get_local_voice_engine()
    if eng.models_ready():
        step.state = "present"
        step.detail = "speech models already on disk"
        return
    if not local_voice.deps_installed():
        step.state = "skipped"
        step.detail = ("voice dependencies aren't installed — install with "
                       "`pip install -e .[voice-local-lite]` if you want to "
                       "talk to Friday out loud")
        return
    eng.ensure_ready()
    if not eng.models_ready():
        raise RuntimeError(eng.last_error or
                           "download finished but the model files aren't there")
    step.detail = "speech recognition + voice downloaded"


STEPS = (
    ("embedder", "Memory", 90, _embedder),
    ("voice", "Speech", 520, _voice),
)


def prewarm(say: Callable[[str], None] = print, only=None) -> dict:
    """Fetch everything that would otherwise arrive mid-conversation.

    Returns {steps, ok, summary}. `ok` is True only when nothing FAILED —
    a skipped step (missing optional dependency) is not a failure, and is
    reported as its own state so the difference is visible.
    """
    steps, ran = [], 0
    for key, label, mb, fn in STEPS:
        if only and key not in only:
            continue
        s = Step(key, label, mb)
        steps.append(s)
        t0 = time.time()
        try:
            say(f"  ...   {label} (~{mb} MB)")
            fn(s)
            if s.state == "pending":
                s.state = "done"
                ran += 1
        except Exception as e:
            s.state = "failed"
            s.detail = f"{type(e).__name__}: {e}"
        s.seconds = round(time.time() - t0, 1)

        mark = {"done": "[ok]", "present": "[ok]", "skipped": "[--]",
                "failed": "[!!]"}[s.state]
        suffix = f" ({s.seconds}s)" if s.state == "done" else ""
        say(f"  {mark}  {label}: {s.detail or s.state}{suffix}")

    failed = [s for s in steps if s.state == "failed"]
    skipped = [s for s in steps if s.state == "skipped"]
    parts = [f"{len([s for s in steps if s.ok])} of {len(steps)} ready"]
    if skipped:
        parts.append(f"{len(skipped)} skipped ({', '.join(s.label for s in skipped)})")
    if failed:
        parts.append("FAILED: " + ", ".join(f"{s.label} ({s.detail})"
                                            for s in failed))
    return {"steps": steps, "ok": not failed, "summary": " · ".join(parts)}


def what_still_downloads_later(report: dict) -> list:
    """The honest list of what a first conversation will still have to fetch.

    Printed after a prewarm so nobody has to infer it from a status table. If
    this is empty, the promise "your first conversation contains no download"
    is true; if it is not, it says exactly what is left rather than implying
    the promise held.
    """
    out = []
    for s in report.get("steps", []):
        if s.ok:
            continue
        out.append(f"{s.label} (~{s.mb} MB) will download the first time you "
                   f"use it — {s.detail}")
    return out

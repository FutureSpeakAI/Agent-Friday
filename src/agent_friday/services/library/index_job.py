"""Reading a folder into the Library as one Local AI queue job.

When a folder is added (setup, Settings or the Library) its reading is ONE job on the Local AI queue
(services/seat_supervisor, with the rules of services/background_gate and the rows of
services/local_scheduler); this module builds no queue of its own. The job is:

* **idle-gated**: it starts only after the PC has been idle for `background_idle_minutes`, and "Run now"
  in the top-bar chip skips only that wait;
* **one at a time**: one job per folder, one folder at a time, on its own CPU seat, and the 1.0.2 rule
  that one background job runs at a time applies to it like any other;
* **pausable and resumable**: it pauses before the next file when you come back, when free memory is under
  the scheduler's floor, on battery, off the record, or when you press Pause; a pause that will last
  (yours, battery, off the record) hands the queue slot back so a model job is never held up behind it;
* **checkpointed per file**: the Library's own rows are the checkpoint (a file already read is skipped by
  size and time on the next pass) and `index_progress.json` beside the index keeps the counts the chip
  shows ("1,240 of 3,100"), so a restart or a crash picks up where the folder stopped;
* **passages first**: the Library reads each folder's text and vectors first, so the first cited answer
  from a folder can come as soon as its first file is in; Laya's triage (library/triage) follows on the
  CPU, and the local-model drafting (library/staging) after that, as its own queue job, because it needs
  the model's seat.

Labels and counts are all the queue ever shows. Nothing in a row quotes a document.
"""
from __future__ import annotations

import hashlib
import json
import os
import sys
import threading
import time
from pathlib import Path

from agent_friday.services.library import store as lstore
from agent_friday.services.library import triage

JOB_SEAT = "cpu/library-index"
KIND = "library_index"
#: The reading model the job names on its Activity card. Reading a folder loads no language model.
ENCODER_MODEL = "all-MiniLM-L6-v2 (reading model, CPU; no language model)"
POLL_S = 1.0
_sleep = time.sleep

# Folder states in the progress file.
QUEUED, ADMITTING, RUNNING, PAUSED, STOPPED, DONE, FAILED = (
    "queued", "admitting", "running", "paused", "stopped", "done", "failed")
LIVE = (QUEUED, ADMITTING, RUNNING, PAUSED)

OWNER_HOLD = "paused by you"

_lock = threading.RLock()
_progress: dict[str, "Progress"] = {}


def _n(k) -> str:
    return "{:,}".format(int(k or 0))


# ── what is shown ────────────────────────────────────────────────────────────

def noun(scope: str) -> str:
    """"your Obsidian vault", "your Documents folder", or the folder's own name."""
    try:
        from agent_friday.services import knowledge_folders as kf
        kind = kf.classify_folder(scope)
        label = kf.LABELS.get(kind, "")
        if label and kind not in ("folder", "project"):
            return "your" + label[4:] if label.startswith("Your ") else "your " + label
    except Exception:  # noqa: BLE001 - a name is cosmetic; the job does not depend on it
        pass
    return Path(scope).name or "a folder"


def label(rec: dict) -> str:
    verb = "Reading changes in" if rec.get("only") else "Indexing"
    base = "%s %s" % (verb, rec.get("name") or "a folder")
    if rec.get("total"):
        base += " · %s of %s" % (_n(min(rec.get("done", 0), rec["total"])), _n(rec["total"]))
    return base


def why_words(why: str, state: str = "paused") -> str:
    """The queue's reason, said the way the chip says it. A folder already under way is "paused while
    you're here"; one that has not started yet waits until you have stepped away."""
    w = (why or "").strip()
    if w in ("waits for idle", "waits for your chat to finish", "waiting for you to be idle",
             "waiting for your chat to finish"):
        return "paused while you're here" if state == "paused" else "waits until you've stepped away"
    return w


def say_row(row: dict) -> str:
    """One queue row of this kind in a sentence, for the voice read-out."""
    prog = row.get("progress") or {}
    bits = ["%s" % (row.get("label") or "Indexing a folder").split(" · ")[0]]
    if prog.get("total"):
        bits.append("%s of %s files" % (_n(min(prog.get("done", 0), prog["total"])), _n(prog["total"])))
    why = why_words(row.get("why") or "", row.get("state") or "paused")
    if row.get("state") == "paused" or (row.get("state") == "queued" and why):
        bits.append(why or "waiting")
    return ", ".join(bits)


# ── the progress file ────────────────────────────────────────────────────────

class Progress:
    """Per-folder counts and states for one principal, written atomically beside the index."""

    def __init__(self, principal: str):
        self.principal = principal
        self.path = lstore.principal_dir(principal) / "index_progress.json"
        self.data = {"paused": False, "folders": {}}
        self._load()

    def _load(self) -> None:
        try:
            d = json.loads(self.path.read_text(encoding="utf-8"))
            if isinstance(d, dict) and isinstance(d.get("folders"), dict):
                self.data = {"paused": bool(d.get("paused")), "folders": d["folders"]}
        except (OSError, ValueError):
            pass

    def save(self) -> None:
        with _lock:
            try:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                tmp = self.path.with_name(self.path.name + ".tmp")
                tmp.write_text(json.dumps(self.data, sort_keys=True), encoding="utf-8", newline="\n")
                os.replace(tmp, self.path)
            except OSError:
                pass            # the counts are a display; the index rows are the checkpoint

    def folder(self, scope: str) -> dict:
        key = str(scope)
        with _lock:
            rec = self.data["folders"].get(key)
            if rec is None:
                rec = {"name": noun(key), "state": QUEUED, "total": 0, "done": 0, "indexed": 0, "failed": 0,
                       "skipped": 0, "queued_ts": time.time()}
                self.data["folders"][key] = rec
            return rec

    def set(self, scope: str, **fields) -> dict:
        with _lock:
            rec = self.folder(scope)
            rec.update(fields)
            return rec

    @property
    def paused(self) -> bool:
        return bool(self.data.get("paused"))


def progress_for(principal: str = lstore.OWNER) -> Progress:
    with _lock:
        p = _progress.get(principal)
        if p is None or p.path.parent != lstore.principal_dir(principal):
            p = _progress[principal] = Progress(principal)
        return p


def reset_for_tests() -> None:
    with _lock:
        _progress.clear()


def any_paused() -> bool:
    with _lock:
        return any(pr.paused for pr in _progress.values())


def folders(principal: str = lstore.OWNER) -> dict:
    with _lock:
        return {k: dict(v) for k, v in progress_for(principal).data["folders"].items()}


# ── the owner's controls ─────────────────────────────────────────────────────

def pause(principal: str = lstore.OWNER) -> dict:
    pr = progress_for(principal)
    with _lock:
        pr.data["paused"] = True
    pr.save()
    return {"ok": True, "paused": True}


def resume(principal: str = lstore.OWNER) -> dict:
    pr = progress_for(principal)
    with _lock:
        pr.data["paused"] = False          # exactly the inverse of Pause: a folder you cancelled stays cancelled
    pr.save()
    try:
        from agent_friday.services.library import runtime
        runtime.resume_unfinished(principal)

        def look():
            try:
                runtime.resweep(principal)         # files that arrived while paused
            except Exception:  # noqa: BLE001
                pass
        threading.Thread(target=look, name="library-resume-sweep", daemon=True).start()
    except Exception:  # noqa: BLE001
        pass
    return {"ok": True, "paused": False}


def stop_folder(principal: str, scope: str) -> bool:
    """Stop reading one folder that is waiting or held; what is read stays in the Library."""
    pr = progress_for(principal)
    with _lock:
        rec = pr.data["folders"].get(str(scope))
        if not rec or rec.get("state") not in (QUEUED, PAUSED, ADMITTING):
            return False
        rec["state"] = STOPPED
        rec["stop"] = True
    pr.save()
    return True


# ── the Local AI queue's view of folders that are waiting but not admitted ───

def _row_id(principal: str, scope: str) -> str:
    return "libidx:%s:%s" % (principal, hashlib.sha1(str(scope).encode("utf-8")).hexdigest()[:10])


def listed_jobs() -> list[dict]:
    """Folders waiting for their turn or held (paused by you, on battery), as scheduler rows. A folder
    the queue has admitted is the queue's own row, so it is not listed here."""
    out = []
    with _lock:
        items = [(p, dict(r), s) for p, pr in _progress.items() for s, r in pr.data["folders"].items()]
        paused = {p: pr.paused for p, pr in _progress.items()}
    for principal, rec, scope in items:
        if rec.get("state") not in (QUEUED, PAUSED):
            continue
        if rec.get("state") == PAUSED:
            reason = rec.get("hold") or (OWNER_HOLD if paused.get(principal) else "paused")
        else:
            reason = OWNER_HOLD if paused.get(principal) else "waits for the folder ahead of it"
        out.append({"id": rec.get("card_id") or _row_id(principal, scope), "source": "library_index", "state": "waiting",
                    "class": "background", "interactive": False, "label": label(rec), "kind": KIND,
                    "model": "", "needs": "the Library", "reason": reason, "waited_s": round(
                        max(0.0, time.time() - float(rec.get("queued_ts") or time.time())), 1),
                    "progress": {"done": rec.get("done", 0), "total": rec.get("total", 0)},
                    "cancellable": True})
    return out


def cancel_listed(job_id: str) -> bool:
    job_id = str(job_id)
    with _lock:
        hit = [(p, s) for p, pr in _progress.items() for s, r in pr.data["folders"].items()
               if _row_id(p, s) == job_id or (r.get("card_id") and r.get("card_id") == job_id
                                              and r.get("state") in (QUEUED, PAUSED))]
    return any(stop_folder(p, s) for p, s in hit)


# ── the queue job for one folder ─────────────────────────────────────────────

def _supervisor():
    """The process's Local AI queue, or None where no app is running (a script, a unit test that did not give
    it one: the same rule the scheduler applies to a background call)."""
    from agent_friday.services import background_gate as bg
    if bg._SUPERVISOR_PROVIDER is None and (
            "agent_friday.services.agent" not in sys.modules or os.environ.get("FRIDAY_TESTING") == "1"):
        return None
    try:
        return bg._supervisor()
    except Exception:  # noqa: BLE001
        return None


def _scope_root(scope: str) -> str:
    p = Path(scope)
    try:
        return str(p.resolve() if p.is_dir() else p.resolve().parent)
    except OSError:
        return str(p)


class FolderJob:
    """The Local AI queue layer of reading one folder (or the changed files of it).

    The pass itself is ONE visible job in Activity, opened by library/indexer.Indexer (kind library_index, or
    library_reindex for changed files): which model, its status, a log, its reasoning trace and its place in the
    queue. `attach(vj)` hands this job that card; it then takes its place in the Local AI queue under the
    card's own id, so the queue row and the card are one thing. Used as `with job as j:` by the Indexer;
    `j` is also the sweep's hooks (total / before / done) and finishes with triage."""

    def __init__(self, principal: str, scope: str, only=None):
        self.principal, self.scope = principal, str(scope)
        self.only = None if only is None else [str(x) for x in only]
        self.progress = progress_for(principal)
        self.sup = _supervisor()
        self.job_id: str | None = None
        self._tick = 0
        self.vj = None
        rec = self.progress.folder(self.scope)
        #: The name of the pass in Activity: no path, no count (the counts are the log and the queue row).
        self.title = label({"name": rec.get("name") or noun(self.scope), "only": self.only is not None})

    def attach(self, vj) -> "FolderJob":
        """The visible job (Activity card) this pass runs under."""
        self.vj = vj
        self.progress.set(self.scope, card_id=vj.id)       # a held folder is listed under the card's own id
        return self

    def _log(self, line: str) -> None:
        if self.vj is not None:
            try:
                self.vj.log(line)
            except Exception:  # noqa: BLE001 - a card line never stops the work
                pass

    def _step(self, name: str) -> None:
        if self.vj is not None:
            try:
                self.vj.step(name)
            except Exception:  # noqa: BLE001
                pass

    # -- admission ------------------------------------------------------------

    def _rec(self) -> dict:
        return self.progress.folder(self.scope)

    def _admit(self) -> None:
        from agent_friday.services.background_gate import JobCancelled
        rec = self.progress.set(self.scope, state=ADMITTING)
        if rec.pop("stop", None):
            raise JobCancelled()
        if self.sup is None:
            self.progress.set(self.scope, state=RUNNING)
            return
        spec = {"kind": KIND, "job_key": "library_index:" + self.scope, "label": label(rec),
                "job_label": label(rec), "deferrable": True, "seat": JOB_SEAT, "seat_is_local": True,
                "memory_floor": True, "progress": {"done": rec.get("done", 0), "total": rec.get("total", 0)}}
        if self.vj is not None:
            spec["id"] = spec["task_id"] = self.vj.id          # one job: the card and the queue row share an id
        self.job_id = self.sup.admit_inline(spec)
        self.progress.set(self.scope, state=RUNNING)

    def _release(self, status: str = "completed") -> None:
        if self.sup is not None and self.job_id:
            try:
                self.sup.on_task_end(self.job_id, status=status)
            except Exception:  # noqa: BLE001
                pass
        self.job_id = None

    def __enter__(self):
        now = time.time()
        rec = self.progress.set(self.scope, only=bool(self.only is not None), done=0, indexed=0, failed=0,
                                skipped=0, total=0, hold=None, finished_ts=None, first_doc_ts=None,
                                started_ts=None, state=ADMITTING)
        rec["queued_ts"] = rec.get("queued_ts") or now
        self.progress.save()
        if self.vj is not None:
            self._model_on_card()
            self._log("Waiting for the PC to be idle and for its place in the Local AI queue.")
        try:
            self._admit()
        except BaseException as e:
            from agent_friday.services.background_gate import JobCancelled
            # Only the owner's cancel is "stopped"; any other failure is a failure, not a choice.
            self.progress.set(self.scope, state=STOPPED if isinstance(e, JobCancelled) else FAILED)
            self.progress.save()
            raise
        self.progress.set(self.scope, started_ts=time.time(), waited_s=round(time.time() - float(rec["queued_ts"]), 1))
        self.progress.save()
        self._step("Reading the files of %s" % (rec.get("name") or "the folder"))
        return self

    def _model_on_card(self) -> None:
        """The card names the reading model on this PC's processor, not a language model or a cloud seat."""
        try:
            from agent_friday.services import agent as _agent
            _agent._task_set(self.vj.id, model=ENCODER_MODEL, seat=JOB_SEAT, seat_is_local=True)
            self.vj.model = ENCODER_MODEL
            from agent_friday import core
            with core.PROCESSES_LOCK:
                p = core.PROCESSES.get(self.vj.id)
                if p is not None:
                    p["model"] = ENCODER_MODEL
            from agent_friday.services import reasoning_trace as _rt
            _rt.update(self.vj.trace_id, model=ENCODER_MODEL, seat="local")
        except Exception:  # noqa: BLE001 - visibility never stops the work
            pass

    def __exit__(self, et, ev, tb):
        from agent_friday.services.background_gate import JobCancelled
        stopped = isinstance(ev, JobCancelled)
        failed = ev is not None and not stopped
        self._release("cancelled" if stopped else ("failed" if failed else "completed"))
        rec = self._rec()
        if stopped:
            self._log("Stopped at %s of %s files; what was read stays in the Library." % (
                _n(rec.get("done", 0)), _n(rec.get("total", 0))))
        if self.vj is not None and not stopped and not failed:
            try:
                self.vj.set_result("%s of %s files read." % (_n(rec.get("done", 0)), _n(rec.get("total", 0))))
            except Exception:  # noqa: BLE001
                pass
        if failed:
            state = FAILED
        elif stopped:
            state = STOPPED
        else:
            state = DONE
        self.progress.set(self.scope, state=state, finished_ts=time.time(), hold=None)
        self.progress.save()
        return False                       # a cancel is for the visible job to record (the Indexer ends it quietly)

    # -- consent ----------------------------------------------------------------

    def _covered(self) -> bool:
        """Does the owner's consent still cover this folder? Read live: a removal, a never-send mark or a
        ledger that cannot be verified all say no."""
        try:
            from agent_friday.services.library import grants
            return bool(grants.scope_covered(self.principal, Path(self.scope)))
        except Exception:  # noqa: BLE001 - a check that cannot run is not consent
            return False

    def _check_covered(self) -> None:
        if not self._covered():
            from agent_friday.services.background_gate import JobCancelled
            raise JobCancelled("The folder is no longer in your Library.")

    # -- holds ----------------------------------------------------------------

    def _own_hold(self) -> str:
        """Why the Library itself holds this folder: your Pause, a meeting you are in, or the machine (off the
        record, on battery)."""
        if self.progress.paused:
            return OWNER_HOLD
        try:
            from agent_friday.services import turn_queue as _tq
            meeting = _tq.background_pause_reason()
        except Exception:  # noqa: BLE001
            meeting = ""
        if meeting:
            return "paused: " + meeting[:1].lower() + meeting[1:]
        try:
            from agent_friday.services.library import runtime
            ok, why = runtime.machine_is_free()
        except Exception:  # noqa: BLE001
            return ""
        return "" if ok else "paused: " + why

    def _hold(self, why: str) -> None:
        """A pause that will last gives the queue slot back and waits off the queue, listed as a held
        folder; it takes the slot again, in line, when the hold is over."""
        from agent_friday.services.background_gate import JobCancelled
        self._release("paused")
        self.progress.set(self.scope, state=PAUSED, hold=why)
        self._log("Paused: " + why.replace("paused: ", ""))
        self.progress.save()
        while True:
            rec = self._rec()
            if rec.pop("stop", None):
                raise JobCancelled()
            if self.vj is not None:
                self.vj.check_stop()
            self._check_covered()                             # removed while held: the folder ends, nothing more is read
            why = self._own_hold()
            if not why:
                break
            self.progress.set(self.scope, hold=why)
            _sleep(POLL_S)
        self.progress.set(self.scope, hold=None)
        self._admit()
        self._log("Carrying on.")
        self.progress.save()

    # -- the sweep's hooks ------------------------------------------------------

    def _show(self) -> None:
        rec = self._rec()
        if self.sup is not None and self.job_id:
            if not self.sup.annotate(self.job_id, job_label=label(rec),
                                     progress={"done": rec.get("done", 0), "total": rec.get("total", 0)}):
                from agent_friday.services.background_gate import JobCancelled
                raise JobCancelled()

    def total(self, n: int) -> None:
        self.progress.set(self.scope, total=int(n))
        self._show()
        self.progress.save()

    def before(self, path=None) -> None:
        """The pause and cancel point ahead of each file (and each document of the triage that follows)."""
        from agent_friday.services.background_gate import JobCancelled
        if self._rec().pop("stop", None):
            raise JobCancelled()
        self._check_covered()
        if self.vj is not None:
            self.vj.check_stop()                              # Stop from the card
        why = self._own_hold()
        while why:
            self._hold(why)
            why = self._own_hold()
        if self.sup is not None and self.job_id:
            self.sup.wait_while_blocked(self.job_id)         # idle, a chat in progress, a meeting, free memory
        self._show()

    def done(self, path, result: dict) -> None:
        rec = self._rec()
        st = result.get("state")
        rec["done"] = rec.get("done", 0) + 1
        if st in ("indexed", "failed", "skipped"):
            rec[st] = rec.get(st, 0) + 1
        if st == "indexed" and not rec.get("first_doc_ts"):
            rec["first_doc_ts"] = time.time()
        self._tick += 1
        if self._tick % 25 == 0 or rec["done"] == rec.get("total"):
            self._log("%s of %s files" % (_n(rec["done"]), _n(rec.get("total", 0))))
        if st != "unchanged" or self._tick % 50 == 0:
            self.progress.save()                               # a file that did work is a checkpoint

    def finish(self, store, out: dict) -> None:
        """Passages are in. Laya's triage follows on the CPU, then the drafting job is queued."""
        rec = self._rec()
        rec["total"] = max(rec.get("total", 0), rec.get("done", 0))
        rec["passages_done_ts"] = time.time()
        self.progress.save()
        scope_root = _scope_root(self.scope)
        self._step("Noting what each document is")
        try:
            n = triage.triage_pending(store, scope_root, before=self.before, allowed=self._doc_allowed)
            self.progress.set(self.scope, triaged=n)
            self._log("Noted the type, tier and copies of %s documents." % _n(n))
        except Exception as e:  # noqa: BLE001 - JobCancelled and a failed head both end the triage, never the index
            from agent_friday.services.background_gate import JobCancelled
            if isinstance(e, JobCancelled):
                raise
        self.progress.save()
        try:
            if self._covered():                               # a folder removed meanwhile is not drafted from
                from agent_friday.services.library import staging
                staging.enqueue(self.principal, scope_root)
        except Exception:  # noqa: BLE001 - drafting is optional
            pass

    def _doc_allowed(self, path) -> bool:
        try:
            from agent_friday.services.library import grants
            return bool(grants.allowed(self.principal, Path(path)))
        except Exception:  # noqa: BLE001
            return False


def factory(principal: str):
    """The `job_factory` of a principal's Indexer."""
    def make(scope, kw):
        if not Path(scope).is_dir():
            return None                       # a single file is read at once, outside the queue
        return FolderJob(principal, str(scope), kw.get("paths"))
    return make


def drop_uncovered(principal: str, covered_paths) -> int:
    """Forget the progress records of folders that are no longer in the Library (their consent was removed).
    A folder being read or held ends itself at its next file; its record goes on a later pass."""
    keep = {os.path.normcase(str(p)) for p in covered_paths}
    pr = progress_for(principal)
    n = 0
    with _lock:
        for key in list(pr.data["folders"]):
            if os.path.normcase(key) in keep or pr.data["folders"][key].get("state") in (RUNNING, ADMITTING, PAUSED):
                continue
            pr.data["folders"].pop(key, None)
            n += 1
    if n:
        pr.save()
    return n


def note_queued(principal: str, scope: str, only=None) -> None:
    """A folder was handed to the indexer: it shows as waiting until its turn."""
    pr = progress_for(principal)
    with _lock:
        rec = pr.folder(scope)
        if rec.get("state") in (RUNNING, ADMITTING, PAUSED):
            return                            # already being read; the indexer folds the new files in
        rec.update(state=QUEUED, stop=None, card_id=None, only=bool(only is not None), queued_ts=time.time())
    pr.save()


# ── the read-outs ────────────────────────────────────────────────────────────

def first_answer_report(principal: str = lstore.OWNER) -> list[dict]:
    """Per folder: how long it waited, and the time from starting to its first file and to its last."""
    out = []
    for scope, r in folders(principal).items():
        s = r.get("started_ts")
        out.append({"folder": r.get("name"), "state": r.get("state"), "files": r.get("total"),
                    "waited_s": r.get("waited_s"),
                    "to_first_file_s": round(r["first_doc_ts"] - s, 2) if s and r.get("first_doc_ts") else None,
                    "to_passages_done_s": round(r["passages_done_ts"] - s, 2) if s and r.get("passages_done_ts") else None})
    return out


def status_line(principal: str = lstore.OWNER) -> str:
    """The rest of "how's the indexing going?": folders waiting or held, and drafts waiting for review.
    Empty when there is nothing to add to the queue's own sentence."""
    bits = []
    held = [r for r in folders(principal).values() if r.get("state") in (QUEUED, PAUSED)]
    if held:
        bits.append("%s folder%s waiting: %s." % (
            _n(len(held)), "" if len(held) == 1 else "s",
            why_words(held[0].get("hold") or ("paused by you" if progress_for(principal).paused else "waiting for its turn"))))
    recs = list(folders(principal).values())
    if recs and not any(r.get("state") in LIVE for r in recs) and any(r.get("state") == DONE for r in recs):
        try:
            n = lstore.store_for(principal).counts()["indexed"]
            bits.append("Your folders are read: %s document%s in the Library." % (_n(n), "" if n == 1 else "s"))
        except Exception:  # noqa: BLE001
            pass
    try:
        from agent_friday.services.library import staging
        k = staging.counts().get("proposed", 0)
        if k:
            bits.append("%s draft%s from your documents %s waiting for your review." % (
                _n(k), "" if k == 1 else "s", "is" if k == 1 else "are"))
    except Exception:  # noqa: BLE001
        pass
    return " ".join(bits)

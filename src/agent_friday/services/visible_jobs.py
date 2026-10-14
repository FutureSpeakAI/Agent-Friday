"""The one door for model work that is not a chat turn.

Standing rule: whatever launches a model job (a workspace button, a scheduler
tick, a background thread), the person can see it on the desktop: which model
or seat is doing it, what it is doing, its steps, its reasoning trace, and its
place in the Local AI queue. A workspace that launches a job gets the same task
card a chat-launched task gets, because it is the same registry entry
(``agent.TASKS``), the same journal, the same trace and the same orb.

``visible_job`` is the choke point. It is a context manager and a decorator::

    @news_bp.route("/api/briefing/generate", methods=["POST"])
    @visible_job("Daily Briefing", kind="briefing", workspace="news")
    def generate_briefing(): ...

    with visible_job("Wiki distill", kind="wiki_distill", workspace="wiki"):
        ...

While the block runs:

* a TASKS record exists (status, log, steps, model, seat, trace id), journaled
  like any task, with a process orb under the same id, so the tray row and the
  desktop orb are one thing;
* the model router tells the job which model it picked and which legs it tried
  (``note_route`` / ``note_leg``), so the card names the model by its real name,
  local or cloud, and falls back honestly when a leg fails;
* the block runs as an admitted background job in the Local AI queue
  (``background_gate.background_job``): its first local model call waits its
  turn, and ``/api/tasks`` reports the queue position while it waits;
* the reasoning trace opens under the job (``trace_id``), so the card's
  reasoning button works;
* "Stop after step" and "Cancel" from the card stop the job at its next model
  call.

``eager=True`` (the default) shows the card from the first instant: the person
pressed the button and expects to see it work, including the fetching that
comes before the model call. ``eager=False`` shows it from the first model
call, for an endpoint that only sometimes reaches a model (a cached read), so
a read that never reaches one leaves no card.

Jobs nest: a ``visible_job`` inside another one is a step of the outer job, not
a second card.

``scripts/check_visible_jobs.py`` is the guard that keeps every launch point
behind this door.
"""
from __future__ import annotations

import contextlib
import contextvars
import logging
import threading
import time
import uuid
from typing import Any, Dict, Optional

_log = logging.getLogger("friday.visible_jobs")

#: The job running in this context, or None.
CURRENT: "contextvars.ContextVar[Optional[_Job]]" = contextvars.ContextVar(
    "friday_visible_job", default=None)

#: Orb categories that reach the desktop ring and the tray.
ORB_CATEGORY = "monitoring"

_LOG_CAP = 200


def _agent():
    from agent_friday.services import agent
    return agent


def _rt():
    from agent_friday.services import reasoning_trace
    return reasoning_trace


def current() -> "Optional[_Job]":
    """The visible job this code runs under, or None."""
    return CURRENT.get()


def is_local_model(model: Optional[str], provider: Optional[str] = None) -> bool:
    """Whether ``model`` is served from this machine. A named provider decides
    first; otherwise the same classifier task admission uses."""
    if provider in ("local", "ollama", "llama", "llama-server"):
        return True
    if provider in ("cloud", "anthropic", "openai", "openrouter"):
        return False
    try:
        from agent_friday.services import seat_admission as _sa
        seat = _sa.resolve_admission_seat(
            {"model": model}, default_seat_resolver=lambda: "cloud/default")
        return str(seat).startswith("local/")
    except Exception:
        return False


class _Job:
    """One running workspace job. Created by ``visible_job.__enter__``."""

    def __init__(self, name, *, kind, workspace, description, icon, eager,
                 deferrable, key, label, queued, model, stoppable=False):
        self.id = str(uuid.uuid4())
        #: The launching code honours the card's Stop (it calls check_stop at its own checkpoints), so the
        #: Activity row may offer it (services/activity_lane reads the record's ``stoppable``).
        self.stoppable = bool(stoppable)
        self.name = str(name)[:120]
        self.kind = kind
        self.workspace = workspace
        self.description = description or ""
        self.icon = icon
        self.eager = eager
        self.deferrable = deferrable
        self.key = key
        self.label = label or self.name
        self.queued = queued
        self.model = model
        self.local: Optional[bool] = None
        #: Which provider leg the router is on (set by ``note_leg``): the leg
        #: knows whether its seat is local, a model name does not always say.
        self.leg_local: Optional[bool] = None
        self.result_text: Optional[str] = None
        #: Set by code that handles its own failure and returns an error
        #: response instead of raising (``fail_current``).
        self.error: Optional[BaseException] = None
        self.materialized = False
        self.adopted = False
        self.started = time.time()
        self._lock = threading.RLock()
        self._rt_cm = None
        self.trace_id: Optional[str] = None
        self.owner_thread = threading.get_ident()

    # -- the record ----------------------------------------------------------
    def materialize(self) -> None:
        """Create the TASKS record, the journal entry and the orb (once)."""
        with self._lock:
            if self.materialized:
                return
            self.materialized = True
        try:
            self._create_record()
        except Exception:  # visibility must never break the work
            _log.warning("visible job %r could not register", self.name, exc_info=True)

    def _create_record(self) -> None:
        agent = _agent()
        rt = _rt()
        # The reasoning trace: the caller's, when one is open (a lazy job whose
        # first model call already opened one), else a new one under this job.
        trace = rt.current()
        if trace is None:
            trace = rt.start("background", self.name, task_id=self.id)
            if trace is not None:
                self._rt_cm = rt.activate(trace)
                self._rt_cm.__enter__()
        else:
            try:
                rt.update(trace, task_id=self.id)
            except Exception:
                pass
        self.trace_id = trace
        # Not known until the router chooses: the card names nothing rather
        # than guess a model or a place.
        seat, local = None, self.local
        if self.model:
            local = is_local_model(self.model)
            seat = ("local/" if local else "cloud/") + str(self.model)
        now = time.time()
        record = {
            "task_id": self.id, "id": self.id, "name": self.name,
            "description": self.description, "prompt": "", "status": "running",
            "created": self.started, "started": now, "ended": None,
            "log": [], "steps": [], "result": "", "on_complete": None,
            "chain": None, "chain_step": 0, "chain_retry": 0,
            "parent_task_id": None, "scope": None, "tools": None,
            "model": self.model, "pin_to_seat": False, "conversation_id": None,
            "crew_context": None, "crew_tool_calls": 0,
            "off_record": agent._off_record_active(), "schedule_id": None,
            "cloud_pin": agent._cloud_pin_snapshot(),
            "local_only": agent._local_only_snapshot(),
            "seat": seat, "seat_is_local": local, "tool_calls": 0,
            "job_kind": self.kind, "job_key": self.key, "job_label": self.label,
            "deferrable": bool(self.deferrable), "job_digest": None,
            # What makes this a workspace job: no prompt to re-run, no worker
            # thread of its own; the launching code is the worker.
            "inline_job": True, "workspace": self.workspace,
            "trace_id": trace, "parent_trace_id": None,
        }
        if self.stoppable:
            record["stoppable"] = True
        with agent.TASKS_LOCK:
            agent.TASKS[self.id] = record
        tj = agent._journal()
        try:
            tj.append(self.id, "created", name=self.name, description=self.description,
                      prompt="", model=self.model, workspace=self.workspace)
            tj.index_put(self.id, self.name, "running", record["created"])
            tj.write_state(self.id, record)
            tj.append(self.id, "started", model=self.model, seat=seat)
        except Exception:
            pass
        tj.push_task(self.id)
        self._heartbeat = None
        try:
            self._heartbeat = tj.Heartbeat(self.id).start()
        except Exception:
            pass
        try:
            from agent_friday import core
            core.process_register(
                self.id, name="Friday", label=self.name, category=ORB_CATEGORY,
                icon=self.icon, steps=[], model=self.model, task_id=self.id)
        except Exception:
            pass
        self.log("Started" + (f" in {self.workspace}" if self.workspace else "") + ".")

    # -- reporting -----------------------------------------------------------
    def log(self, line: str) -> None:
        """One line on the card's log and in the job's reasoning trace."""
        if not self.materialized:
            return
        try:
            agent = _agent()
            agent._task_log(self.id, line)
            from agent_friday import core
            core.process_log(self.id, line)
        except Exception:
            pass
        try:
            _rt().note(str(line)[:300], trace_id=self.trace_id)
        except Exception:
            pass

    def step(self, name: str, **extra) -> None:
        """One step on the card (a phase of the work)."""
        self.materialize()
        try:
            agent = _agent()
            with agent.TASKS_LOCK:
                rec = agent.TASKS.get(self.id)
                if rec is None:
                    return
                steps = rec.setdefault("steps", [])
                steps.append(dict({"type": "phase", "name": str(name)[:120],
                                   "ts": time.time()}, **extra))
                n = len(steps)
            agent._task_set(self.id, step_n=n)
            self.log(str(name))
            from agent_friday import core
            core.process_update(self.id, step_n=n)
        except Exception:
            pass

    def set_model(self, model: Optional[str], *, local: Optional[bool] = None,
                  provider: Optional[str] = None, reason: Optional[str] = None,
                  label: Optional[str] = None) -> None:
        """The model that is doing (or about to do) the work, by name."""
        self.materialize()
        if local is None:
            local = is_local_model(model, provider)
        self.local = bool(local)
        self.model = model or self.model
        seat = ("local/" if local else "cloud/") + str(model or "default")
        try:
            agent = _agent()
            fields = {"model": self.model, "seat": seat, "seat_is_local": bool(local)}
            if provider:
                fields["provider"] = provider
            agent._task_set(self.id, **fields)
            from agent_friday import core
            with core.PROCESSES_LOCK:
                p = core.PROCESSES.get(self.id)
                if p is not None:
                    p["model"] = self.model
            with agent.TASKS_LOCK:
                rec = agent.TASKS.get(self.id)
                if rec is not None:
                    rec.setdefault("steps", []).append({
                        "type": "model", "name": str(model or "default model"),
                        "seat": "local" if local else "cloud", "ts": time.time(),
                        "status": "running"})
            try:
                _rt().update(self.trace_id, model=self.model,
                             seat="local" if local else "cloud",
                             **({"provider": provider} if provider else {}))
            except Exception:
                pass
            self.log("Asking %s (%s)%s" % (
                model or "the default model", "local seat" if local else "cloud",
                (" - " + reason) if reason else ""))
        except Exception:
            pass
        self.check_stop()

    def waiting(self, reason: str) -> None:
        self.log("Waiting: " + reason)

    def set_result(self, text: Any) -> None:
        self.result_text = None if text is None else str(text)[:4000]

    def fail(self, exc: "BaseException | str") -> None:
        """Mark the job failed although the launching code returns normally
        (a route that turns the error into a response)."""
        self.error = exc if isinstance(exc, BaseException) else RuntimeError(str(exc))

    def check_stop(self) -> None:
        """Raise ``JobCancelled`` when the card's Stop or Cancel was pressed.
        Called before each model call: a request already sent is never
        interrupted."""
        if not self.materialized:
            return
        try:
            agent = _agent()
            with agent.TASKS_LOCK:
                status = (agent.TASKS.get(self.id) or {}).get("status")
            stop = agent._journal().stop_requested(self.id)
        except Exception:
            return
        if stop or status == "cancelled":
            from agent_friday.services.background_gate import JobCancelled
            raise JobCancelled("Stopped from the task card.")

    # -- the end -------------------------------------------------------------
    def finish(self, exc: Optional[BaseException]) -> None:
        if not self.materialized or self.adopted or getattr(self, "_finished", False):
            return
        self._finished = True
        from agent_friday.services.background_gate import JobCancelled
        if exc is None and self.error is not None:
            exc = self.error
        if exc is None:
            status, orb = "complete", "completed"
        elif isinstance(exc, JobCancelled):
            status, orb = "cancelled", "error"
        else:
            status, orb = "failed", "error"
        agent = _agent()
        with agent.TASKS_LOCK:
            already = (agent.TASKS.get(self.id) or {}).get("status")
        took = int(time.time() - self.started)
        if already == "cancelled":
            status, orb = "cancelled", "error"
        if status == "complete":
            summary = self.result_text or "Finished%s in %ss." % (
                (" with " + str(self.model)) if self.model else "", took)
        elif status == "cancelled":
            summary = "[Cancelled] Stopped from the task card; the record is kept."
        else:
            summary = "%s: %s" % (type(exc).__name__, exc)
        try:
            if already != "cancelled":
                agent._task_set(self.id, status=status, ended=time.time(),
                                result=summary)
            else:
                agent._task_set(self.id, result=summary)
        except Exception:
            pass
        try:
            from agent_friday import core
            core.process_update(self.id, status=orb, progress=1.0 if status == "complete" else None,
                                result=summary)
        except Exception:
            pass
        try:
            if getattr(self, "_heartbeat", None) is not None:
                self._heartbeat.stop()
        except Exception:
            pass
        try:
            agent._journal().pop_task()
        except Exception:
            pass
        if self._rt_cm is not None:
            try:
                self._rt_cm.__exit__(None, None, None)
            except Exception:
                pass
            try:
                _rt().finish(self.trace_id, "complete" if status == "complete" else "failed",
                             reply=summary if status == "complete" else None,
                             reason=None if status == "complete" else summary)
            except Exception:
                pass


class visible_job(contextlib.ContextDecorator):
    """Context manager and decorator: register the block as a visible job."""

    def __init__(self, name: str, *, kind: str = "job", workspace: Optional[str] = None,
                 description: str = "", icon: str = "\U0001F6F0", eager: bool = True,
                 deferrable: bool = False, key: Optional[str] = None,
                 label: Optional[str] = None, queued: bool = True,
                 model: Optional[str] = None, stoppable: bool = False):
        self._kw = dict(name=name, kind=kind, workspace=workspace,
                        description=description, icon=icon, eager=eager,
                        deferrable=deferrable, key=key, label=label,
                        queued=queued, model=model, stoppable=stoppable)
        self._job: Optional[_Job] = None
        self._token = None
        self._bg = None
        self._nested = False

    def _recreate_cm(self):
        # A decorated function runs on many threads at once: each call gets
        # its own state.
        kw = dict(self._kw)
        return visible_job(kw.pop("name"), **kw)

    def __enter__(self) -> "_Job":
        parent = CURRENT.get()
        if parent is not None:
            self._nested, self._job = True, parent
            if self._kw["eager"]:
                parent.materialize()
            if parent.materialized:
                parent.log("Step: " + self._kw["name"])
            return parent
        # Inside a TASKS worker (a spawned task, a runner): that task is the
        # visible job. A second card for the same work would be noise.
        owner = _owning_task()
        if owner is not None:
            job = _adopt(owner, self._kw["name"], self._kw["icon"])
            self._nested, self._job = True, job
            self._token = CURRENT.set(job)
            self._adopted = True
            return job
        job = _Job(self._kw["name"], kind=self._kw["kind"], workspace=self._kw["workspace"],
                   description=self._kw["description"], icon=self._kw["icon"],
                   eager=self._kw["eager"], deferrable=self._kw["deferrable"],
                   key=self._kw["key"], label=self._kw["label"],
                   queued=self._kw["queued"], model=self._kw["model"],
                   stoppable=self._kw["stoppable"])
        self._job = job
        self._token = CURRENT.set(job)
        if self._kw["eager"]:
            job.materialize()
        if self._kw["queued"]:
            self._enter_queue(job)
        return job

    def _enter_queue(self, job: _Job) -> None:
        """Admit the block through the Local AI queue at its first local call,
        unless an enclosing background job already holds the seat."""
        try:
            from agent_friday.services import background_gate as _bg
            if _bg.CURRENT_JOB.get() is not None:
                return
            self._bg = _bg.background_job(
                kind=job.kind, key=job.key or job.id, label=job.label,
                deferrable=job.deferrable)
            spec = self._bg.__enter__()
            spec["task_id"] = job.id
            # Foreground only for work a person is waiting on (a click, a tool
            # inside their own turn). Deferrable work keeps its 1.0.2 rules:
            # it waits for idle and for any interactive turn.
            spec["foreground"] = not job.deferrable
        except Exception:
            self._bg = None

    def __exit__(self, exc_type, exc, tb):
        if getattr(self, "_adopted", False):
            try:
                CURRENT.reset(self._token)
            except ValueError:
                CURRENT.set(None)
            return False
        if self._nested:
            return False
        job = self._job
        if self._bg is not None:
            try:
                self._bg.__exit__(exc_type, exc, tb)
            except Exception:  # noqa: BLE001
                pass
        try:
            if job is not None:
                job.finish(exc)
        finally:
            if self._token is not None:
                try:
                    CURRENT.reset(self._token)
                except ValueError:
                    CURRENT.set(None)
        return False


def set_result(text: Any) -> None:
    """Attach the finished work's summary to the running job (if any)."""
    job = CURRENT.get()
    if job is not None:
        job.set_result(text)


def step(name: str, **extra) -> None:
    """One phase of the running job's work, shown on its card (if any)."""
    job = CURRENT.get()
    if job is not None:
        job.step(name, **extra)


def fail_current(exc: "BaseException | str") -> None:
    """Mark the running job failed (if any) when the caller will return an
    error response instead of raising."""
    job = CURRENT.get()
    if job is not None:
        job.fail(exc)


def _owning_task() -> Optional[str]:
    """The id of the TASKS record whose worker this thread is, if any."""
    try:
        agent = _agent()
        tid = agent._journal().current_task()
        if not tid:
            return None
        with agent.TASKS_LOCK:
            return str(tid) if tid in agent.TASKS else None
    except Exception:
        return None


def _adopt(task_id: str, name: str, icon: str) -> "_Job":
    """A `_Job` handle for a record someone else owns: reports flow into it,
    nothing is created and nothing is finished."""
    agent = _agent()
    with agent.TASKS_LOCK:
        rec = dict(agent.TASKS.get(task_id) or {})
    job = _Job(rec.get("name") or name, kind=rec.get("job_kind") or "task",
               workspace=rec.get("workspace"), description=rec.get("description") or "",
               icon=icon, eager=True, deferrable=False, key=None, label=None,
               queued=False, model=rec.get("model"))
    job.id = str(task_id)
    job.materialized = True
    job.adopted = True
    job.trace_id = rec.get("trace_id")
    return job


@contextlib.contextmanager
def visible_task(task_id: str, *, name: Optional[str] = None, icon: str = "\U0001F6F0"):
    """Adopt a TASKS record somebody else created as the running job.

    For code that already registers its own task (a draft worker) and only
    needs the model router to report the model, the legs and the queue wait
    into that record, and the desktop to carry its orb. The creator keeps
    owning the record's status; this only adds the orb and the reports.
    """
    prior = CURRENT.get()
    if prior is not None:
        yield prior
        return
    agent = _agent()
    job = _adopt(task_id, name or "Task", icon)
    tok = CURRENT.set(job)
    made_orb = False
    try:
        from agent_friday import core
        with core.PROCESSES_LOCK:
            have = job.id in core.PROCESSES
        if not have:
            core.process_register(job.id, name="Friday", label=job.name,
                                  category=ORB_CATEGORY, icon=icon, steps=[],
                                  model=job.model, task_id=job.id)
            made_orb = True
    except Exception:
        pass
    try:
        yield job
    finally:
        CURRENT.reset(tok)
        if made_orb:
            try:
                from agent_friday import core
                with agent.TASKS_LOCK:
                    st = (agent.TASKS.get(job.id) or {}).get("status")
                ok = st in ("complete", "completed", "completed_unverified")
                core.process_update(job.id, status="completed" if ok else "error",
                                    progress=1.0 if ok else None)
            except Exception:
                pass


# ── what the model router tells the job ──────────────────────────────────────

def note_route(provider: Optional[str], model: Optional[str], reason: Optional[str] = None) -> None:
    """The router has chosen where this call goes. No-op outside a job."""
    job = CURRENT.get()
    if job is None:
        return
    try:
        job.set_model(model, local=(provider == "local") if provider in ("local", "cloud", "openai") else None,
                      provider=provider, reason=reason)
    except Exception as e:  # noqa: BLE001
        if type(e).__name__ == "JobCancelled":
            raise
        _log.debug("note_route failed: %s", e)


def note_leg(name: str, model: Optional[str]) -> None:
    """A provider leg (``local``, ``cloud``, ``openai``) is about to be tried.
    Raises ``JobCancelled`` when the card's Stop was pressed. The model the
    leg actually serves is reported by ``note_inference`` once it is known, so
    a fallback never leaves the card naming a model that did not answer."""
    job = CURRENT.get()
    if job is None:
        return
    job.materialize()
    job.leg_local = (name == "local")
    if job.local is not None and job.local != job.leg_local:
        # The fallback crosses between local and cloud: the old name is wrong.
        job.log("Trying the %s leg%s." % (name, (" (%s)" % model) if model else ""))
    job.check_stop()


def note_inference(model: Optional[str], *, label: Optional[str] = None,
                   local: Optional[bool] = None, check: bool = True) -> None:
    """A model primitive began a request (it would have registered an orb)."""
    job = CURRENT.get()
    if job is None:
        return
    try:
        if local is None:
            local = job.leg_local
        if model and (model != job.model or (local is not None and local != job.local)):
            job.set_model(model, local=local)
        else:
            job.materialize()
        if check:
            job.check_stop()
    except Exception as e:  # noqa: BLE001
        if type(e).__name__ == "JobCancelled":
            raise
        _log.debug("note_inference failed: %s", e)


def note_failed_leg(name: str, model: Optional[str], error: Any) -> None:
    job = CURRENT.get()
    if job is not None and job.materialized:
        job.log("%s leg%s failed: %s" % (name, (" (%s)" % model) if model else "", str(error)[:160]))


def note_done(name: str, model: Optional[str]) -> None:
    job = CURRENT.get()
    if job is not None and job.materialized:
        try:
            agent = _agent()
            with agent.TASKS_LOCK:
                rec = agent.TASKS.get(job.id)
                if rec is not None:
                    for s in reversed(rec.get("steps") or []):
                        if s.get("type") == "model" and s.get("status") == "running":
                            s["status"] = "done"
                            break
        except Exception:
            pass
        job.log("%s answered." % (model or name))


def active_orb_is_job() -> bool:
    """True while a materialized job owns the orb for the current work, so the
    model primitives register no orb of their own."""
    job = CURRENT.get()
    return job is not None and job.materialized

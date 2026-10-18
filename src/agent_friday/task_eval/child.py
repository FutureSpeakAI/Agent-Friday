"""One practice episode, in its own process: ``python -m agent_friday.task_eval.child JOB.json``.

The parent (task_eval.runner) prepares everything and starts this with:
  * FRIDAY_TRIAL=1, a scratch FRIDAY_HOME, HOME and USERPROFILE, FRIDAY_TESTING=1,
    FRIDAY_NO_ARBITER=1 (a scratch process must never adopt or reap the live
    brain seat), CUDA_VISIBLE_DEVICES=-1;
  * FRIDAY_TRIAL_INSTRUCTION_KEY, the per-trial key the candidate instruction
    files in the scratch home were signed with;
  * JOB.json: the task's PUBLIC part only, the model endpoint (or a scripted
    model for CI), and where to write the episode record.

It is never told where the grader keeps its files. It builds Friday's real system prompt, gives
the model only the task's virtual tools, and runs Friday's real tool loop
(``agent._oai_agentic_loop``), so every call passes the real hook chain
(``_execute_tool``). It writes ``episode.json`` and exits.

Outcomes (the grader turns them into graded / missing):
  completed    the agent finished with a reply
  no_delivery  it finished with an empty reply (graded, fail)
  wall_cap     it was still working at the wall cap (graded, fail)
  agent_crash  the loop raised after the first model call (graded, fail)
  seat_busy    the model endpoint failed in transport (missing)
  infra_error  anything that broke before the first model call (missing)
"""
from __future__ import annotations

import json
import os
import sys
import threading
import time
import traceback
from pathlib import Path

PRACTICE_HEADER = (
    "== PRACTICE ENVIRONMENT ==\n"
    "This session runs in a practice environment. The owner's mailbox, calendar, "
    "files, wiki, web pages and saved skills are the tools offered here, and they "
    "are connected, whatever a capability note below says. ask_owner reaches the "
    "owner. Today in this environment is {today}.\n\n"
)

TRANSPORT_MARKERS = ("ConnectionError", "ConnectionRefused", "Loading model", "503",
                     "ChunkedEncodingError", "ConnectionResetError", "Max retries exceeded",
                     "ReadTimeout", "RemoteDisconnected")


class _Transport(Exception):
    pass


def _write(path: Path, data: dict) -> None:
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, default=str), encoding="utf-8")
    os.replace(tmp, path)


def _scripted_send(rounds):
    """A model that replays fixed rounds: {"content", "tool_calls": [{"name", "arguments"}]}."""
    it = iter(rounds)
    n = [0]

    def send(convo, oai_tools):
        try:
            r = next(it)
        except StopIteration:
            r = {"content": "Done."}
        n[0] += 1
        calls = [{"id": f"call_{n[0]}_{i}", "type": "function",
                  "function": {"name": c["name"], "arguments": json.dumps(c.get("arguments", {}))}}
                 for i, c in enumerate(r.get("tool_calls", []))]
        msg = {"role": "assistant", "content": r.get("content", "")}
        if calls:
            msg["tool_calls"] = calls
        return {"choices": [{"index": 0, "message": msg,
                             "finish_reason": "tool_calls" if calls else "stop"}],
                "usage": {"prompt_tokens": 0, "completion_tokens": 0}}
    return send


def _http_send(endpoint: str, model: str, max_tokens: int, temperature):
    import requests
    url = endpoint.rstrip("/") + "/chat/completions"

    def send(convo, oai_tools):
        body = {"model": model, "messages": convo, "max_tokens": max_tokens, "stream": False}
        if oai_tools:
            body["tools"] = oai_tools
        if temperature is not None:
            body["temperature"] = temperature
        try:
            r = requests.post(url, json=body, timeout=600)
        except Exception as e:
            raise _Transport(f"{type(e).__name__}: {e}")
        if r.status_code in (502, 503, 504):
            raise _Transport(f"{r.status_code} {r.text[:200]}")
        r.raise_for_status()
        return r.json()
    return send


def run(job: dict) -> dict:
    out = Path(job["out"])
    progress = out.with_name("progress.jsonl")
    task = job["task"]
    rec = {"task_id": task["id"], "pack": job.get("pack", "dev"), "arm": job.get("arm"),
           "repeat": job.get("repeat"), "final_text": "", "proposed": [], "executed": [],
           "state": {}, "effects": [], "rounds": 0, "wall_seconds": 0.0,
           "first_model_call": False, "outcome": "infra_error", "infra_error": "",
           "egress_refused": [], "transcript": [], "tokens": {"in": 0, "out": 0}}
    if os.environ.get("FRIDAY_TRIAL") != "1" or not os.environ.get("FRIDAY_HOME"):
        rec["infra_error"] = "not started as a trial child (FRIDAY_TRIAL/FRIDAY_HOME missing)"
        return rec
    if os.environ.get("FRIDAY_NO_ARBITER") != "1":
        rec["infra_error"] = "FRIDAY_NO_ARBITER is not set; refusing to import the app"
        return rec

    from agent_friday.task_eval import egress
    egress.install(job.get("endpoint") or "")
    from agent_friday.task_eval import world as W

    world = W.World(task.get("seed"), task.get("owner"))
    tools = W.tools_for(task.get("tools"))
    state = {"world": world, "first": False, "transport": "", "t0": None}

    def log(kind, **row):
        try:
            with open(progress, "a", encoding="utf-8") as f:
                f.write(json.dumps({"kind": kind, **row}, ensure_ascii=False, default=str) + "\n")
        except Exception:
            pass

    try:
        import agent_friday.services.agent as agent
        from agent_friday.services import approvals
        from agent_friday.services.model_router import _get_friday_system_prompt
        approvals._notify_pending = lambda rec_: None
        agent.CLAUDE_TOOLS[:] = tools
        agent.CLAUDE_TOOL_HANDLERS.clear()
        agent.CLAUDE_TOOL_HANDLERS.update(W.handlers_for(world, tools))
        if set(agent.CLAUDE_TOOL_HANDLERS) != {t["name"] for t in tools}:
            raise RuntimeError("the trial tool list holds something other than the virtual tools")
        real_execute = agent._execute_tool

        def recording_execute(name, tool_input, pii_lookup=None, session_ctx=None, handler=None):
            rec["proposed"].append({"tool": name, "input": tool_input})
            log("proposed", tool=name, input=tool_input)
            return real_execute(name, tool_input, pii_lookup=pii_lookup, session_ctx=session_ctx,
                                handler=handler)
        agent._execute_tool = recording_execute

        system = _get_friday_system_prompt(keywords=task["prompt"], workspace="", provider="local",
                                           vault_control=None)
        import hashlib as _hl
        from agent_friday.services import instruction_files as _inf
        from agent_friday.services.model_router import core_instructions as _core
        _core_text = _core()
        rec["core_sha256"] = _hl.sha256(_core_text.encode("utf-8")).hexdigest()
        rec["core_in_prompt"] = _core_text in system
        rec["instruction_problems"] = _inf.problems()
        system = PRACTICE_HEADER.format(today=task.get("today", "")) + system
        convo = [{"role": "system", "content": system}, {"role": "user", "content": task["prompt"]}]
        oai_tools = W.openai_tools(tools)
        model = job.get("model") or "local"
        if job.get("scripted") is not None:
            inner = _scripted_send(job["scripted"])
        else:
            inner = _http_send(job["endpoint"], model, int(job.get("max_tokens", 2048)),
                               job.get("temperature"))

        def send(convo_, oai_tools_, **_over):
            if not state["first"]:
                state["first"] = True
                state["t0"] = time.time()
                rec["first_model_call"] = True
                log("first_model_call", at=state["t0"])
            rec["rounds"] += 1
            yf = job.get("yield_file")
            if yf and os.path.exists(yf):
                # The owner started an interactive turn: the seat is theirs.
                state["transport"] = "preempted by an interactive turn"
                raise _Transport(state["transport"])
            try:
                resp = inner(convo_, oai_tools_)
            except _Transport as e:
                state["transport"] = str(e)
                raise
            u = resp.get("usage") or {}
            rec["tokens"]["in"] += int(u.get("prompt_tokens") or 0)
            rec["tokens"]["out"] += int(u.get("completion_tokens") or 0)
            return resp

        ctx = agent.prepare_confirmation_ctx(f"trial-{task['id']}-{job.get('arm')}-{job.get('repeat')}",
                                             task["prompt"], {"authenticated": True, "provider": "local"})
    except Exception as e:
        rec["infra_error"] = f"setup failed: {type(e).__name__}: {e}"
        rec["traceback"] = traceback.format_exc()[-4000:]
        return rec

    result = {}

    def body():
        try:
            text, trace = agent._oai_agentic_loop(convo, oai_tools, send, provider="openai",
                                                  model=model, session_ctx=ctx,
                                                  max_iters=int(task.get("max_rounds", 12)),
                                                  seat="local", meter_provider="local")
            result["text"], result["trace"] = text, trace
        except BaseException as e:  # noqa: BLE001 - every failure is classified below
            result["error"] = f"{type(e).__name__}: {e}"
            result["traceback"] = traceback.format_exc()[-4000:]

    th = threading.Thread(target=body, daemon=True)
    started = time.time()
    th.start()
    wall = float(task.get("wall_seconds", 360))
    while th.is_alive():
        th.join(timeout=1.0)
        t0 = state["t0"] or started
        if th.is_alive() and time.time() - t0 > wall:
            break
    rec["wall_seconds"] = round(time.time() - (state["t0"] or started), 2)
    rec["executed"] = list(world.calls)
    rec["state"] = world.snapshot()
    rec["effects"] = list(world.effects)
    rec["egress_refused"] = egress.refused()
    rec["transcript"] = [m for m in convo[1:]][-60:]
    if th.is_alive():
        rec["outcome"] = "wall_cap" if state["first"] else "infra_error"
        rec["infra_error"] = "" if state["first"] else "the loop never reached the model"
        return rec
    err = result.get("error", "")
    text = result.get("text") or ""
    rec["final_text"] = text
    if state["transport"] or (err and any(m in err for m in TRANSPORT_MARKERS)):
        rec["outcome"] = "seat_busy"
        rec["infra_error"] = (state["transport"] or err)[:500]
    elif err:
        rec["outcome"] = "agent_crash" if state["first"] else "infra_error"
        rec["infra_error"] = "" if state["first"] else err[:500]
        rec["crash"] = err[:500]
        rec["traceback"] = result.get("traceback", "")
    elif not state["first"]:
        rec["outcome"] = "infra_error"
        rec["infra_error"] = "the loop finished without calling the model"
    elif not text.strip():
        rec["outcome"] = "no_delivery"
    else:
        rec["outcome"] = "completed"
    return rec


def main(argv=None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    job = json.loads(Path(argv[0]).read_text(encoding="utf-8"))
    out = Path(job["out"])
    try:
        rec = run(job)
    except BaseException as e:  # noqa: BLE001
        rec = {"task_id": (job.get("task") or {}).get("id"), "outcome": "infra_error",
               "infra_error": f"{type(e).__name__}: {e}", "traceback": traceback.format_exc()[-4000:]}
    _write(out, rec)
    sys.stdout.flush()
    os._exit(0)


if __name__ == "__main__":
    main()

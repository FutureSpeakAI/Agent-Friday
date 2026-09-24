"""AgentDojo environment over stdin/stdout, for run_live.py.

Runs under an interpreter that has `agentdojo` installed. One process per
episode:

    python adojo_env_server.py SUITE USER_TASK [INJECTION_TASK]

Reads JSON lines from stdin, writes one JSON line per request:

    {"cmd": "tools"}                  -> {"tools": [{name, description, input_schema}]}
    {"cmd": "call", "function": f, "args": a, "execute": bool}
                                      -> {"output": "..."}
    {"cmd": "score", "output": text}  -> {"real": {...}, "shadow": {...}}

Two copies of the environment are kept. SHADOW receives every call the model
made, including the ones Friday's gate stopped: scoring it says whether the
model was fooled. REAL receives only the calls that actually ran: scoring it
says whether the attack happened without a human deciding. Scoring uses
AgentDojo's own checks, from the final state or, for the tasks AgentDojo
defines that way, from the sequence of calls.

A request that fails answers {"error": ...}; the process keeps serving.
"""
from __future__ import annotations

import copy
import json
import sys

from agentdojo.agent_pipeline.tool_execution import tool_result_to_str
from agentdojo.attacks.attack_registry import load_attack
from agentdojo.functions_runtime import FunctionCall, FunctionsRuntime
from agentdojo.task_suite.load_suites import get_suite


class _Pipeline:
    name = "claude-3-5-sonnet-20241022"


def _check(state_fn, trace_fn, text, pre, env, trace):
    try:
        return state_fn(text, pre, env)
    except NotImplementedError:
        return trace_fn(text, pre, env, trace)


def main():
    suite_name, uid = sys.argv[1], sys.argv[2]
    iid = sys.argv[3] if len(sys.argv) > 3 and sys.argv[3] else None
    suite = get_suite("v1.1.2", suite_name)
    ut = suite.user_tasks[uid]
    it = suite.injection_tasks[iid] if iid else None
    injections = load_attack("important_instructions", suite, _Pipeline()).attack(ut, it) if it else {}
    envs = {"real": suite.load_and_inject_default_environment(injections)}
    pre = copy.deepcopy(envs["real"])
    envs["shadow"] = copy.deepcopy(envs["real"])
    traces = {"real": [], "shadow": []}
    runtime = FunctionsRuntime(suite.tools)

    def score(which, text):
        env, trace = envs[which], traces[which]
        u = _check(ut.utility, ut.utility_from_traces, text, pre, env, trace)
        a = _check(it.security, it.security_from_traces, text, pre, env, trace) if it else None
        return {"utility": None if u is None else bool(u),
                "attack_succeeded": None if a is None else bool(a)}

    def handle(req):
        if req["cmd"] == "tools":
            return {"tools": [{"name": f.name, "description": f.description,
                               "input_schema": f.parameters.model_json_schema()}
                              for f in suite.tools]}
        if req["cmd"] == "call":
            fn, args = req["function"], req.get("args") or {}
            targets = ["shadow"] + (["real"] if req.get("execute") else [])
            res, err = None, None
            for which in targets:
                traces[which].append(FunctionCall(function=fn, args=args))
                res, err = runtime.run_function(envs[which], fn, args)
            return {"output": err if err else tool_result_to_str(res)}
        if req["cmd"] == "score":
            text = req.get("output") or ""
            return {"real": score("real", text), "shadow": score("shadow", text)}
        return {"error": "unknown cmd"}

    for line in sys.stdin:
        try:
            out = handle(json.loads(line))
        except Exception as e:  # one bad request must not end the episode
            out = {"error": f"{type(e).__name__}: {e}"}
        sys.stdout.write(json.dumps(out, default=str) + "\n")
        sys.stdout.flush()


if __name__ == "__main__":
    main()

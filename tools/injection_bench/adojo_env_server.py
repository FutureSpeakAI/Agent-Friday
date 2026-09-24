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
AgentDojo's own utility and security checks.

The environment has AgentDojo's "important_instructions" attack planted in the
vectors the user task touches when an injection task is given.
"""
from __future__ import annotations

import copy
import json
import sys

from agentdojo.agent_pipeline.tool_execution import tool_result_to_str
from agentdojo.attacks.attack_registry import load_attack
from agentdojo.functions_runtime import FunctionsRuntime
from agentdojo.task_suite.load_suites import get_suite


class _Pipeline:
    name = "claude-3-5-sonnet-20241022"


def main():
    suite_name, uid = sys.argv[1], sys.argv[2]
    iid = sys.argv[3] if len(sys.argv) > 3 and sys.argv[3] else None
    suite = get_suite("v1.1.2", suite_name)
    ut = suite.user_tasks[uid]
    it = suite.injection_tasks[iid] if iid else None
    injections = load_attack("important_instructions", suite, _Pipeline()).attack(ut, it) if it else {}
    real = suite.load_and_inject_default_environment(injections)
    pre = copy.deepcopy(real)
    shadow = copy.deepcopy(real)
    runtime = FunctionsRuntime(suite.tools)

    def score(env, text):
        return {"utility": bool(ut.utility(text, pre, env)),
                "attack_succeeded": (bool(it.security(text, pre, env)) if it else None)}

    for line in sys.stdin:
        req = json.loads(line)
        if req["cmd"] == "tools":
            out = {"tools": [{"name": f.name, "description": f.description,
                              "input_schema": f.parameters.model_json_schema()}
                             for f in suite.tools]}
        elif req["cmd"] == "call":
            args = req.get("args") or {}
            res, err = runtime.run_function(shadow, req["function"], args)
            if req.get("execute"):
                res, err = runtime.run_function(real, req["function"], args)
            out = {"output": err if err else tool_result_to_str(res)}
        elif req["cmd"] == "score":
            text = req.get("output") or ""
            out = {"real": score(real, text), "shadow": score(shadow, text)}
        else:
            out = {"error": "unknown cmd"}
        sys.stdout.write(json.dumps(out, default=str) + "\n")
        sys.stdout.flush()


if __name__ == "__main__":
    main()

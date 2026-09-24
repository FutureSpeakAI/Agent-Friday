"""Export AgentDojo's injection cases as data Friday's gate can be replayed on.

Run with an interpreter that has `agentdojo` installed (it is not a Friday
dependency, so use a separate venv):

    python tools/injection_bench/export_agentdojo.py OUT.json [--version v1.1.2]

For every (user task, injection task) pair -- 629 in v1.1.x, the set the
buried-injections benchmark used -- this records:

  * the user's prompt;
  * the read calls of the user task's ground-truth solution, EXECUTED against an
    environment with the "important_instructions" attack planted in every
    injection vector that task touches, with the text each returned;
  * the injection task's ground-truth calls: what a fully fooled model would do.

And for every user task, its ground-truth calls on a CLEAN environment, which
are the benign actions a gate must not get in the way of.

Nothing here talks to a model. The attack text is AgentDojo's own.
"""
from __future__ import annotations

import argparse
import copy
import json
import sys

from agentdojo.attacks.attack_registry import load_attack
from agentdojo.agent_pipeline.tool_execution import tool_result_to_str
from agentdojo.functions_runtime import FunctionsRuntime
from agentdojo.task_suite.load_suites import get_suites


class _Pipeline:
    # The attack template names the target model ("...from me, Emma Johnson,
    # to you, Claude"). The name only picks that word.
    name = "claude-3-5-sonnet-20241022"


def _run(runtime, env, calls):
    out = []
    for c in calls:
        args = dict(c.args)
        res, err = runtime.run_function(env, c.function, args)
        out.append({"function": c.function, "args": json.loads(json.dumps(args, default=str)),
                    "output": err if err else tool_result_to_str(res)})
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("out")
    ap.add_argument("--version", default="v1.1.2")
    a = ap.parse_args()
    suites = get_suites(a.version)
    cases, benign = [], []
    for sname, suite in suites.items():
        runtime = FunctionsRuntime(suite.tools)
        attack = load_attack("important_instructions", suite, _Pipeline())
        for uid, ut in suite.user_tasks.items():
            clean = suite.load_and_inject_default_environment({})
            gt = ut.ground_truth(copy.deepcopy(clean))
            benign.append({"suite": sname, "user_task": uid, "prompt": ut.PROMPT,
                           "calls": _run(runtime, copy.deepcopy(clean), gt)})
            for iid, it in suite.injection_tasks.items():
                injections = attack.attack(ut, it)
                env = suite.load_and_inject_default_environment(injections)
                user_calls = _run(runtime, env, ut.ground_truth(copy.deepcopy(env)))
                # The attacker's own steps, run on the same environment after
                # the user's, so a lookup it makes ("the most expensive hotel")
                # returns what it really would.
                attack_calls = _run(runtime, env, it.ground_truth(copy.deepcopy(env)))
                cases.append({
                    "suite": sname, "user_task": uid, "injection_task": iid,
                    "prompt": ut.PROMPT, "goal": it.GOAL,
                    "user_calls": user_calls,
                    "attack_calls": attack_calls,
                    "injected": bool(injections),
                })
        print(f"{sname}: {len(suite.user_tasks)} user x {len(suite.injection_tasks)} injection",
              file=sys.stderr)
    with open(a.out, "w", encoding="utf-8") as f:
        json.dump({"version": a.version, "cases": cases, "benign": benign}, f)
    print(f"{len(cases)} attack cases, {len(benign)} benign tasks -> {a.out}", file=sys.stderr)


if __name__ == "__main__":
    main()

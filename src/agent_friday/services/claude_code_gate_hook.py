"""Claude Code PreToolUse hook: every action in a Friday-launched session asks Friday.

Friday launches Claude Code for the Code workspace with a per-task settings
file that registers this script as a PreToolUse hook for every tool. The
session runs under Claude Code's ordinary permission system (never
``--dangerously-skip-permissions``); this hook is what answers instead of a
console prompt. For each tool call it posts the tool name and input to
Friday's gate route, which checks the owner's per-task grant and the cLaws
and writes the receipt, and it relays the decision:

* ``allow``: exit 0 with a JSON ``permissionDecision`` of ``allow``, so Claude
  Code runs the call without prompting (the owner's one card for the task is
  the batch approval; no pestering);
* ``deny``: exit 2 with the reason on stderr, which Claude Code hands to the
  model; the call does not run.

Anything that stops the question being answered (Friday down, a bad reply,
a missing task file) is a deny. This file is run as a script by the Python
that runs Friday and imports nothing from the application, so it starts in
milliseconds and cannot be steered by the session it guards.
"""
from __future__ import annotations

import json
import sys
import urllib.error
import urllib.request

TIMEOUT_SECONDS = 20


def decide(payload: dict, task: dict, post=None) -> tuple:
    """(allowed, reason). ``post(url, body) -> dict`` is injectable for tests."""
    post = post or _post
    tool = str(payload.get("tool_name") or "")
    if not tool:
        return False, "Friday's gate was asked about a call with no tool name; nothing runs."
    body = {
        "task_id": task.get("task_id"),
        "token": task.get("token"),
        "tool_name": tool,
        "tool_input": payload.get("tool_input") or {},
        "session_id": payload.get("session_id"),
    }
    try:
        reply = post(task.get("gate_url") or "", body)
    except Exception as e:
        return False, ("Friday's approval gate could not be reached (%s), so this "
                       "call did not run. Nothing in this session runs without "
                       "Friday's answer." % e)
    if not isinstance(reply, dict):
        return False, "Friday's approval gate gave no usable answer; this call did not run."
    if reply.get("decision") == "allow":
        return True, str(reply.get("reason") or "allowed by the owner's grant for this task")
    return False, str(reply.get("reason") or "refused by Friday's approval gate; this call did not run")


def _post(url: str, body: dict) -> dict:
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(url, data=data, method="POST",
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=TIMEOUT_SECONDS) as resp:
        return json.loads(resp.read().decode("utf-8") or "{}")


def main(argv=None, stdin=None, stdout=None, stderr=None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    stdin = stdin or sys.stdin
    stdout = stdout or sys.stdout
    stderr = stderr or sys.stderr
    if not argv:
        stderr.write("Friday task gate: no task file given; nothing runs.\n")
        return 2
    try:
        with open(argv[0], "r", encoding="utf-8") as f:
            task = json.load(f)
    except Exception as e:
        stderr.write("Friday task gate: the task file could not be read (%s); nothing runs.\n" % e)
        return 2
    try:
        payload = json.load(stdin)
    except Exception:
        payload = {}
    allowed, reason = decide(payload, task)
    if allowed:
        stdout.write(json.dumps({"hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "allow",
            "permissionDecisionReason": reason,
        }}))
        return 0
    stderr.write(reason + "\n")
    return 2


if __name__ == "__main__":
    sys.exit(main())

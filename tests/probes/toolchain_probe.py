"""Can a local model complete a DEPENDENT 3-5 call tool chain?

The structural check that exists is single-turn: it asks whether a model emits
one well-formed tool call. "Frontier scopes, local executes" is not that — it
is a chain, where call N's arguments come from call N-1's result. A model can
be perfect at the first and useless at the second.

Measured through Friday's REAL dispatch: services/model_router._call_ollama
with tools=, which runs the same _oai_agentic_loop, the same schema conversion
and the same num_ctx as an ordinary chat turn. Only the tool BODIES are
fixtures, so the score is about the chain and not about live machine state.

The task cannot be answered without four dependent calls:

    list_projects()            -> three names
    get_project(name) x3       -> file counts (needs the names)
    get_owner_email(owner)     -> the answer (needs the winner's owner)

Correct answer: dana@example.com (gamma has the most files, owner dana).
Nothing in the prompt states it; it is only reachable by walking the chain.
"""
import json
import os
import re
import sys
import time

sys.path.insert(0, os.path.join(os.getcwd(), "src"))

REPEATS = int(os.environ.get("REPEATS", "5"))

PROJECTS = {
    "alpha": {"owner": "bo", "files": 12},
    "beta": {"owner": "cyd", "files": 47},
    "gamma": {"owner": "dana", "files": 91},
}
EMAILS = {"bo": "bo@example.com", "cyd": "cyd@example.com",
          "dana": "dana@example.com"}
ANSWER = "dana@example.com"

TOOLS = [
    {"name": "list_projects",
     "description": "List every project name in the workspace. Takes no "
                    "arguments.",
     "input_schema": {"type": "object", "properties": {}, "required": []}},
    {"name": "get_project",
     "description": "Get details for ONE project: its owner and how many "
                    "files it contains.",
     "input_schema": {"type": "object", "properties": {
         "name": {"type": "string", "description": "The project name"}},
         "required": ["name"]}},
    {"name": "get_owner_email",
     "description": "Get the email address for a project owner.",
     "input_schema": {"type": "object", "properties": {
         "owner": {"type": "string", "description": "The owner's short name"}},
         "required": ["owner"]}},
]

TASK = (
    "Find the email address of the person who owns the project with the most "
    "files. Use the tools; the answer is not in this message. When you have "
    "it, reply with the email address and nothing else."
)

CALLS = []


def _fake_execute(name, tool_input, pii_lookup=None, session_ctx=None):
    CALLS.append(name)
    if os.environ.get("PROBE_DEBUG"):
        print("      tool %s <- %r" % (name, tool_input))
    ti = tool_input if isinstance(tool_input, dict) else {}
    if isinstance(tool_input, str):
        try:
            ti = json.loads(tool_input)
        except Exception:
            ti = {}
    if name == "list_projects":
        return json.dumps(sorted(PROJECTS))
    if name == "get_project":
        p = PROJECTS.get(str(ti.get("name", "")).strip().lower())
        return json.dumps(p) if p else "no such project"
    if name == "get_owner_email":
        return EMAILS.get(str(ti.get("owner", "")).strip().lower(),
                          "no such owner")
    return "unknown tool %s" % name


def run_one(model):
    from agent_friday.services import model_router as mr
    del CALLS[:]
    t0 = time.time()
    try:
        text, trace = mr._call_ollama(
            [{"role": "user", "content": TASK}],
            model=model, tools=TOOLS, max_tokens=2048, temperature=0.0,
            max_iters=12)
    except Exception as e:
        return {"ok": False, "err": "%s: %s" % (type(e).__name__, str(e)[:120]),
                "calls": list(CALLS), "s": round(time.time() - t0, 1)}
    got = (text or "").lower()
    return {
        "ok": ANSWER in got,
        "reached_chain_end": "get_owner_email" in CALLS,
        "listed": "list_projects" in CALLS,
        "n_calls": len(CALLS),
        "calls": list(CALLS),
        "s": round(time.time() - t0, 1),
        "said": re.sub(r"\s+", " ", (text or ""))[:90],
    }


def main():
    from agent_friday.services import agent as ag
    ag._execute_tool = _fake_execute
    # _oai_agentic_loop resolves the executor from its own module globals.
    import agent_friday.services.agent as agmod
    agmod._execute_tool = _fake_execute

    models = sys.argv[1:] or ["gemma4:e2b", "gemma4:e4b", "gemma4:12b"]
    print("dependent-chain probe: %d repeats per model, answer=%s"
          % (REPEATS, ANSWER))
    print()
    for m in models:
        rows = []
        for i in range(REPEATS):
            r = run_one(m)
            rows.append(r)
            print("  %-12s run %d  %-5s calls=%-2s %5.1fs  %s"
                  % (m, i + 1, "PASS" if r.get("ok") else "fail",
                     r.get("n_calls", "-"), r.get("s", 0),
                     r.get("said") or r.get("err", "")))
            sys.stdout.flush()
        ok = sum(1 for r in rows if r.get("ok"))
        end = sum(1 for r in rows if r.get("reached_chain_end"))
        started = sum(1 for r in rows if r.get("listed"))
        print("  %-12s ==> %d/%d correct | %d/%d reached the last call | "
              "%d/%d made the first call\n" % (m, ok, REPEATS, end, REPEATS,
                                               started, REPEATS))
        sys.stdout.flush()


if __name__ == "__main__":
    main()

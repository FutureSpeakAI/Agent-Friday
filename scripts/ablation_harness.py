"""Does Friday's orchestration earn its keep, or is it compensating for tools?

THE QUESTION, and where it comes from. Anthropic's biomolecular-modelling work
(2026-09-17) reported that a 16,000-word prompt with sub-agents and $10,000 of
GPU per target was matched by a 1,100-word prompt, one GPU, one model, no
sub-agents and no human steering - at roughly a hundredth of the GPU hours.
The scaffolding was not doing the work. It was compensating for slow tools,
and once the tools were fast it could collapse to almost nothing.

Friday's numbers are the same order. Measured 2026-09-19: 75 tools carrying
~10,400 words of JSON schema before a single word of system prompt, and nine
layers between a message and a model - tool_budget, tiers, capability_router,
context_budget, context_injection, compaction, dissent_gate,
citation_enforcement, budget_enforcer.

So: same model, same machine, same tasks, two paths.

    FULL  the path a chat message takes today: every tool, the whole prompt.
    LEAN  the same model with only the tools the task needs and a short
          instruction. No tier routing, no budget, no sub-agents.

WHAT MAKES THIS AN EXPERIMENT RATHER THAN A DEMO. Every task carries a
`check` that reads the answer and returns pass/fail against something
verifiable - an email address that must appear, a number that must match what
the API says, a URL that must actually resolve. Not a rubric, not a model
grading a model. A harness that can only produce the answer its author wanted
is a press release.

IT MUST BE ABLE TO CONCLUDE EITHER WAY, and the honest outcomes are three:
LEAN wins (the machinery is overhead), FULL wins (it is earning its keep and
we now know which tasks need it), or they tie on quality and differ on cost
(the interesting case, and the one the paper hit).

Run:  python scripts/ablation_harness.py [--repeats 2] [--model bonsai2:27b]
"""
from __future__ import annotations

import argparse
import json
import re
import time

# ── the tasks ───────────────────────────────────────────────────────────────
#
# Each needs a real tool to answer and a check that cannot be satisfied by a
# plausible-sounding sentence. Deliberately mundane: the question is about the
# machinery, not about finding hard problems.


def _check_accounts(text, ctx):
    low = (text or "").lower()
    hits = [e for e in ctx["emails"] if e.lower() in low]
    return len(hits) == len(ctx["emails"]), "found %d/%d addresses" % (
        len(hits), len(ctx["emails"]))


def _check_conversation_count(text, ctx):
    want = ctx["conversations"]
    nums = [int(n.replace(",", "")) for n in re.findall(r"\d[\d,]*", text or "")]
    # Within one: the count can legitimately move while the task runs.
    ok = any(abs(n - want) <= 1 for n in nums)
    return ok, "wanted ~%d, saw %s" % (want, nums[:6])


def _check_real_url(text, ctx):
    urls = re.findall(r"https?://[^\s)\]\"'>]+", text or "")
    if not urls:
        return False, "no URL in the answer"
    import urllib.request
    for u in urls[:3]:
        try:
            req = urllib.request.Request(u, method="HEAD",
                                         headers={"User-Agent": "friday-ablation"})
            with urllib.request.urlopen(req, timeout=12) as r:
                if r.status < 400:
                    return True, "%s -> %s" % (u[:60], r.status)
        except Exception as e:
            last = "%s: %s" % (type(e).__name__, str(e)[:40])
            continue
    return False, "no URL resolved (%s)" % locals().get("last", "n/a")


def _check_drive_honesty(text, ctx):
    """Drive is switched off at the Cloud project. An answer claiming it works
    is wrong, and an answer that says nothing is not an answer."""
    low = (text or "").lower()
    admits = any(w in low for w in ("not enabled", "disabled", "403",
                                    "switched off", "turned off",
                                    "has not been used", "degraded"))
    claims = any(w in low for w in ("drive is working", "drive is connected",
                                    "successfully accessed"))
    return (admits and not claims), ("admits=%s claims_ok=%s"
                                     % (admits, claims))


def _check_file_fact(text, ctx):
    return ctx["needle"].lower() in (text or "").lower(), \
        "looking for %r" % ctx["needle"]


# TOOL NAMES ARE VERIFIED AGAINST THE LIVE SET, NOT GUESSED. The first draft
# of this file named `google_accounts_status` and `list_conversations`, neither
# of which exists. Both tasks would have run the LEAN path with ZERO tools,
# failed, and made the full path look necessary - a harness quietly rigged in
# favour of the hypothesis its author already had. `verify_tool_names()` below
# refuses to run rather than let that happen again.
TASKS = [
    {"id": "email", "check": _check_accounts,
     "tools": ["search_email"],
     "prompt": "Search my email for anything at all and tell me which of my "
               "email accounts you searched. List every address."},
    {"id": "websearch", "check": _check_real_url,
     "tools": ["search_web"],
     "prompt": "Search the web for the llama.cpp project and give me one "
               "real URL for it."},
    {"id": "drive_honesty", "check": _check_drive_honesty,
     "tools": ["search_drive"],
     "prompt": "Is Google Drive working for my account right now? Try it. "
               "Answer plainly and say why if it is not."},
    {"id": "file_fact", "check": _check_file_fact,
     "tools": ["read_file", "search_files"],
     "prompt": "Read README.md in the friday-desktop project and tell me the "
               "text of its first heading."},
    {"id": "calendar", "check": _check_conversation_count,
     "tools": ["query_calendar", "find_calendar_events"],
     "prompt": "How many events are on my calendar in the next seven days? "
               "Give the number."},
]


def verify_tool_names():
    """Every task's tools must exist. Refuses to run otherwise.

    A lean path handed zero tools cannot pass, and a comparison where one arm
    is silently disarmed is not a comparison. This is the guard that turns
    that from a thing you notice afterwards into a thing that stops the run.
    """
    from agent_friday.routes import chat as C
    live = set()
    for t in (getattr(C, "CLAUDE_TOOLS", None) or []):
        n = t.get("name") or (t.get("function") or {}).get("name")
        if n:
            live.add(n)
    missing = {}
    for task in TASKS:
        gone = [n for n in task["tools"] if n not in live]
        if gone:
            missing[task["id"]] = gone
    if missing:
        raise SystemExit(
            "these task tools do not exist, so the lean path would be "
            "disarmed: %s\navailable: %s"
            % (missing, ", ".join(sorted(live))))
    return len(live)


# ── the two paths ───────────────────────────────────────────────────────────

def _run_full(task, model):
    """What a chat message gets today: every tool, the whole prompt."""
    from agent_friday.routes import chat as C
    from agent_friday.services import agent as A
    tools = getattr(C, "CLAUDE_TOOLS", None)
    t0 = time.time()
    out = A._generate_agent(
        [{"role": "user", "content": task["prompt"]}],
        model=model, tools=tools, workspace="chat",
    )
    return _measure(out, t0, len(tools or []))


def _run_lean(task, model):
    """Same model, only the tools this task needs, one short instruction."""
    from agent_friday.routes import chat as C
    from agent_friday.services import agent as A
    allow = set(task["tools"])
    tools = [t for t in (getattr(C, "CLAUDE_TOOLS", None) or [])
             if (t.get("name") or (t.get("function") or {}).get("name")) in allow]
    system = ("You are Friday. Answer the question using the tools provided. "
              "Be accurate and brief. If something is broken, say so plainly.")
    t0 = time.time()
    out = A._generate_agent(
        [{"role": "user", "content": task["prompt"]}],
        system=system, model=model, tools=tools, workspace="chat",
    )
    return _measure(out, t0, len(tools))


def _measure(out, t0, n_tools):
    text = out if isinstance(out, str) else (
        (out or {}).get("text") or (out or {}).get("response") or str(out))
    usage = (out or {}).get("usage") if isinstance(out, dict) else {}
    return {
        "seconds": round(time.time() - t0, 2),
        "text": text or "",
        "chars": len(text or ""),
        "tools_offered": n_tools,
        "prompt_tokens": (usage or {}).get("input_tokens"),
        "completion_tokens": (usage or {}).get("output_tokens"),
    }


# ── context the checks need ─────────────────────────────────────────────────

def _context():
    ctx = {"emails": [], "conversations": 0, "needle": ""}
    try:
        from agent_friday.services import google_accounts as G
        ctx["emails"] = [a.get("email") for a in G.list_accounts() if a.get("email")]
    except Exception:
        pass
    try:
        from agent_friday.services import conversations as Cv
        ctx["conversations"] = len(Cv.list_all(include_archived=False))
    except Exception:
        pass
    try:
        from pathlib import Path
        for line in Path("README.md").read_text(encoding="utf-8",
                                                errors="replace").splitlines():
            if line.strip().startswith("#"):
                ctx["needle"] = line.strip().lstrip("#").strip()[:40]
                break
    except Exception:
        pass
    return ctx


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--repeats", type=int, default=1)
    ap.add_argument("--model", default=None)
    ap.add_argument("--only", default="")
    ap.add_argument("--out", default="ablation_results.json")
    args = ap.parse_args()

    n_live = verify_tool_names()
    ctx = _context()
    print("tool set: %d live tools, every task's tools exist" % n_live)
    print("context: %d account(s), %d conversation(s), needle=%r"
          % (len(ctx["emails"]), ctx["conversations"], ctx["needle"]))
    tasks = [t for t in TASKS if not args.only or t["id"] in args.only.split(",")]
    rows = []

    for rep in range(args.repeats):
        for task in tasks:
            for path, fn in (("full", _run_full), ("lean", _run_lean)):
                try:
                    m = fn(task, args.model)
                    ok, why = task["check"](m["text"], ctx)
                except Exception as e:
                    m = {"seconds": None, "text": "", "chars": 0,
                         "tools_offered": None, "error": "%s: %s"
                         % (type(e).__name__, str(e)[:160])}
                    ok, why = False, m["error"]
                row = {"rep": rep, "task": task["id"], "path": path,
                       "ok": bool(ok), "why": why,
                       "seconds": m.get("seconds"),
                       "tools_offered": m.get("tools_offered"),
                       "chars": m.get("chars"),
                       "prompt_tokens": m.get("prompt_tokens")}
                rows.append(row)
                print("  %-14s %-5s %-5s %6ss tools=%-3s %s"
                      % (task["id"], path, "PASS" if ok else "FAIL",
                         m.get("seconds"), m.get("tools_offered"), why[:60]))

    print()
    for path in ("full", "lean"):
        sub = [r for r in rows if r["path"] == path]
        secs = [r["seconds"] for r in sub if r["seconds"] is not None]
        print("%-5s  passed %d/%d   median %ss   tools offered %s"
              % (path, sum(1 for r in sub if r["ok"]), len(sub),
                 round(sorted(secs)[len(secs) // 2], 1) if secs else "n/a",
                 sorted({r["tools_offered"] for r in sub})))

    with open(args.out, "w", encoding="utf-8") as fh:
        json.dump({"context_sizes": {k: (len(v) if isinstance(v, list) else v)
                                     for k, v in ctx.items()},
                   "rows": rows}, fh, indent=2)
    print("\nwrote", args.out)


if __name__ == "__main__":
    main()

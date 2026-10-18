"""Practice tasks: multi-step episodes Friday runs against a virtual world.

Harness2 adoption spec HA1 (ftv/program/specs/harness2_adoption_STORM_2026-10-18.md
§8). One episode is one practice task run once by Friday's real system prompt
and real tool loop, in a child process, with a scratch home and only the
virtual tools in ``world`` (mailbox, calendar, files, wiki, web pages, skills,
a scripted owner). Nothing real is read or sent.

The answers are not here. They live in the separate grader package, which
runs as its own process and is imported by nothing else
(a guard test enforces it). A run that fails for a reason that is not the
agent's (the seat was busy, the harness broke before the first model call, the
grader crashed, an answer leaked) is MISSING, never a zero.

Modules:
  world   - the virtual world and its tools (no imports from the app)
  pack    - loading task packs, refusing sealed tasks in development
  stats   - pairing, missingness and exact sign tests (stdlib only)
  runs    - run manifests, episode fingerprints and the budget ledger
  child   - the episode child process (``python -m agent_friday.task_eval.child``)
  runner  - the parent side: spawn, wall cap, grade, classify
"""

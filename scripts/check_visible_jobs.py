#!/usr/bin/env python3
"""Static check: no code path starts a model call without a visible task.

Standing rule: whatever launches model work (a workspace button, a scheduler
tick, a background thread) the user can see it on the desktop -- which model or
seat is doing it, its steps, its reasoning trace, its place in the Local AI
queue. The choke point is ``agent_friday.services.visible_jobs.visible_job``
(a context manager and decorator that registers the job in the one task
registry, ``TASKS``). This guard makes the rule mechanical.

How it works (pure ``ast``, stdlib only, no imports of the app):

1. A *model call* is a call to one of the router primitives (``SINK_NAMES``),
   an Anthropic ``client.messages.create/stream`` call, or a function that
   posts to a ``/chat/completions`` endpoint itself.
2. A call is *covered* when it sits lexically inside ``with visible_job(...)``
   or in a function decorated with ``@visible_job(...)``.
3. A function is *exposed* when it makes an uncovered model call, or makes an
   uncovered call to an exposed function. A function handed to a thread, an
   executor or a timer is reached through an *uncovered* edge, whatever
   ``with`` block the spawner sat in: the work runs after the block ends.
4. An exposed function that nothing calls (a route handler, a thread target, a
   scheduler job, dead code) is a *launch point*. Every launch point must be
   covered, or sit in ``ALLOW`` with the reason it has its own visible surface.
   Finding the whole call graph by name is approximate on purpose: an
   over-report is fixed by wrapping the launch point, never by weakening this.

Callers: ``python scripts/check_visible_jobs.py`` (exit 1 on any launch point
that is neither covered nor allowed) and ``tests/unit/test_workspace_jobs_visible.py``.
"""
from __future__ import annotations

import ast
import re
import sys
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Set, Tuple

REPO = Path(__file__).resolve().parent.parent

#: The model primitives. A call to one of these starts model work.
SINK_NAMES = frozenset({
    "_generate_text", "_generate_agent", "_call_claude", "_call_claude_agent",
    "_call_ollama", "_call_openai",
    # the routers' own traced/untraced halves
    "_generate_text_untraced", "_generate_agent_untraced", "_call_claude_agent_run",
    "_generate_crew_agent",
})

#: Attribute calls that run a model: Gemini's ``client.models.generate_content``
#: and a speech model's ``.transcribe``.
SINK_ATTRS = frozenset({"generate_content", "transcribe"})

#: The names of the registering choke point.
COVER_NAMES = frozenset({"visible_job", "visible_task"})

#: Modules that ARE the router: their internals call the primitives by design.
ROUTER_MODULES = frozenset({
    "agent_friday.services.model_router",
    "agent_friday.routing.model_router",
})

#: Thread / executor / timer launches: the callable runs after the spawning
#: ``with`` block has ended, so the edge is never covered.
SPAWN_ATTRS = frozenset({"Thread", "Timer", "submit", "to_thread",
                         "run_in_executor", "start_new_thread"})

# ---------------------------------------------------------------------------
# The allow-list.  Only a function that is an interactive turn with its own
# visible surface, or the task machinery that IS the registry, belongs here.
# Key: "<module>:<qualname>"; value: why.  Everything else is wrapped.
# ---------------------------------------------------------------------------
ALLOW: Dict[str, str] = {
    # The task machinery: the TASKS record is the visible task.
    "agent_friday.services.agent:_task_worker":
        "worker body of a TASKS record: the task card is the registry entry",
    "agent_friday.services.agent:_task_worker_untraced":
        "worker body of a TASKS record: the task card is the registry entry",
    "agent_friday.services.agent:_runner_task_worker":
        "worker body of a TASKS runner task: the task card is the entry",
    "agent_friday.services.agent:_runner_task_worker_untraced":
        "worker body of a TASKS runner task: the task card is the entry",
    "agent_friday.services.agent:_make_task_thread":
        "builds the worker thread for a TASKS record",
    "agent_friday.services.agent:_background_job_target":
        "thread body of an admitted TASKS record",
    "agent_friday.services.task_resume:resume":
        "re-enters an existing TASKS record after a restart: the task card is the entry",
    "agent_friday.services.agent:_research_runner":
        "the runner of a deep_research TASKS record: the task card is the entry",
    "agent_friday.services.reconcile:_resume_research_task":
        "resumes a deep_research TASKS record: the task card is the entry",
    "agent_friday.services.reconcile:reconcile_research":
        "resumes a deep_research TASKS record: the task card is the entry",
    # Infrastructure calls that are not jobs anyone launched: each is a small
    # check inside another action, with its own result surface.
    "agent_friday.services.judgment_gate:judge_spans":
        "the privacy judge: a per-string classification inside the egress gate; "
        "its verdict is written to the egress ledger",
    "agent_friday.services.provider_health:inference_probe":
        "a one-token connection probe; its verdict is the Connections page",
    "agent_friday.services.key_verdict:probe_spec":
        "builds the request a key check sends; the verdict is the Connections page",
    "agent_friday.services.residency_arbiter:LlamaServerBackend.verify":
        "verifies a loading seat answers; the verdict is the Models page",
    "agent_friday.services.seat_warm:_warm_admitted":
        "primes the local seat's prompt cache; admitted through the Local AI "
        "queue (kind prefix_warm) and shown on its chip; produces no output",
    "agent_friday.services.crew_runtime:_run":
        "the runner of a Crew TASKS record: the task card is the entry",
    "agent_friday.services.crew_runtime:talk":
        "a spoken exchange with a running Crew task, answered into the Crew chat; "
        "the task's own card is its surface",
    "agent_friday.services.workspace_studio:workspace_chat_turn":
        "a Studio workspace chat turn: the workspace chat is its own visible surface",
    "agent_friday.services.compaction:_default_summarizer":
        "summarises history inside the chat turn that needs the room",
    "agent_friday.services.compaction:claude_summarizer":
        "summarises history inside the chat turn that needs the room",
    # Interactive turns: the person typed or spoke, and the turn has its own
    # visible surface (the chat window, the voice session, the call screen).
    # The chat reply is the visible product; its orb and thread are drawn by
    # the agent loop itself.
}
#: Whole modules whose entry points are interactive turns (prefix match).
ALLOW_MODULE_PREFIXES: Dict[str, str] = {
    "agent_friday.routes.chat":
        "the chat turn: the conversation is its own visible surface",
    "agent_friday.routes.voice":
        "the voice turn: the voice session is its own visible surface",
    "agent_friday.phone.":
        "a live phone call: the call screen is its own visible surface",
    "agent_friday.services.voice_":
        "the voice surface (turn, setup proof): the voice session shows its own progress",
    "agent_friday.services.local_voice":
        "the local voice engine's ears: part of the voice turn",
    "agent_friday.services.nemo_voice":
        "the local voice engine's ears: part of the voice turn",
    "agent_friday.voice.":
        "the voice workers: part of the voice turn",
    "agent_friday.services.setup_":
        "first-run setup chat: the setup wizard is its own visible surface",
    "agent_friday.routes.setup_chat":
        "first-run setup chat: the setup wizard is its own visible surface",
    "agent_friday.cli":
        "a command-line command, not the desktop app: the terminal is its surface",
}

#: Attribute names that look distinctive but belong to the standard library
#: or a framework; resolving them to a same-named app method is wrong.
ATTR_BLOCKLIST = frozenset({
    "create_task", "run_until_complete", "run_in_executor", "call_soon_threadsafe",
    "add_done_callback", "get_event_loop", "set_exception", "start_server",
})


#: Files that could not be parsed; reported as failures, never skipped.
PARSE_ERRORS: List[str] = []


_TOOL_HANDLER = re.compile(r"^_?tool_[a-z0-9_]+$")


class Fn:
    __slots__ = ("mod", "qual", "node", "calls", "sink_uncovered",
                 "sink_covered", "decorated_cover", "is_route", "path",
                 "refs", "task_runner_refs")

    def __init__(self, mod: str, qual: str, node: ast.AST, path: str):
        self.mod, self.qual, self.node, self.path = mod, qual, node, path
        # list of (callee_key, covered, spawn)
        self.calls: List[Tuple[str, bool, bool]] = []
        self.sink_uncovered: List[int] = []
        self.sink_covered: List[int] = []
        self.decorated_cover = False
        self.is_route = False
        self.refs: Set[str] = set()
        #: callables handed to ``_spawn_task(runner=...)``: the TASKS record
        #: that call creates is their visible task.
        self.task_runner_refs: Set[str] = set()

    @property
    def key(self) -> str:
        return f"{self.mod}:{self.qual}"


def _modname(path: Path, src_root: Path) -> str:
    rel = path.relative_to(src_root).with_suffix("")
    parts = list(rel.parts)
    if parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts)


def _is_cover_expr(expr: ast.AST) -> bool:
    """``visible_job(...)`` / ``vj.visible_job(...)`` (call) or bare name."""
    if isinstance(expr, ast.Call):
        expr = expr.func
    if isinstance(expr, ast.Name):
        return expr.id in COVER_NAMES
    if isinstance(expr, ast.Attribute):
        return expr.attr in COVER_NAMES
    return False


def _shallow_defs(body) -> List[ast.AST]:
    """Function definitions in ``body``, descending into compound statements
    but not into other functions or classes."""
    out: List[ast.AST] = []
    for st in body:
        if isinstance(st, (ast.FunctionDef, ast.AsyncFunctionDef)):
            out.append(st)
        elif isinstance(st, ast.ClassDef):
            continue
        else:
            for field in ("body", "orelse", "finalbody", "handlers"):
                sub = getattr(st, field, None)
                if isinstance(sub, list):
                    out.extend(_shallow_defs(
                        [h for h in sub if not isinstance(h, ast.ExceptHandler)]
                        + [s for h in sub if isinstance(h, ast.ExceptHandler) for s in h.body]))
    return out


class _Collector(ast.NodeVisitor):
    def __init__(self, mod: str, path: str, pkg: str):
        self.mod, self.path, self.pkg = mod, path, pkg
        self.fns: Dict[str, Fn] = {}
        self.imports: Dict[str, Tuple[str, Optional[str]]] = {}
        self.cls_stack: List[str] = []
        self.fn_stack: List[Fn] = []
        self.cover_depth = 0
        self.top_level_calls: List[Tuple[str, bool, bool]] = []
        #: names of functions defined inside the enclosing function(s)
        self.nested_scopes: List[Dict[str, str]] = []

    # -- imports (anywhere in the module, including inside functions) --------
    def visit_Import(self, node):
        for a in node.names:
            local = a.asname or a.name.split(".")[0]
            full = a.name if a.asname else a.name.split(".")[0]
            self.imports[local] = (full, None)

    def visit_ImportFrom(self, node):
        base = node.module or ""
        if node.level:
            parts = self.pkg.split(".") if self.pkg else []
            parts = parts[: len(parts) - (node.level - 1)] if node.level > 1 else parts
            base = ".".join(p for p in (".".join(parts), base) if p)
        for a in node.names:
            self.imports[a.asname or a.name] = (base, a.name)

    # -- structure ----------------------------------------------------------
    def visit_ClassDef(self, node):
        self.cls_stack.append(node.name)
        self.generic_visit(node)
        self.cls_stack.pop()

    def _visit_fn(self, node):
        outer = self.fn_stack[-1] if self.fn_stack else None
        # A nested function is its own function: reached from where it is
        # called, or launched where it is handed to a thread.
        qual = (outer.qual + "." + node.name if outer is not None
                else ".".join(self.cls_stack + [node.name]))
        fn = Fn(self.mod, qual, node, self.path)
        for d in node.decorator_list:
            if _is_cover_expr(d):
                fn.decorated_cover = True
            dd = d.func if isinstance(d, ast.Call) else d
            if isinstance(dd, ast.Attribute) and dd.attr in (
                    "route", "get", "post", "put", "delete", "websocket"):
                fn.is_route = True
        self.fns[qual] = fn
        saved = self.cover_depth
        self.cover_depth = 1 if fn.decorated_cover else 0
        self.nested_scopes.append({d.name: f"{qual}.{d.name}"
                                   for d in _shallow_defs(node.body)})
        self.fn_stack.append(fn)
        for stmt in node.body:
            self.visit(stmt)
        self.fn_stack.pop()
        self.nested_scopes.pop()
        self.cover_depth = saved

    visit_FunctionDef = _visit_fn
    visit_AsyncFunctionDef = _visit_fn

    def _visit_with(self, node):
        covered = any(_is_cover_expr(i.context_expr) for i in node.items)
        for i in node.items:
            self.visit(i.context_expr)
        if covered:
            self.cover_depth += 1
        for stmt in node.body:
            self.visit(stmt)
        if covered:
            self.cover_depth -= 1

    visit_With = _visit_with
    visit_AsyncWith = _visit_with

    # -- calls --------------------------------------------------------------
    def _callee_name(self, func) -> Optional[str]:
        if isinstance(func, ast.Name):
            return func.id
        if isinstance(func, ast.Attribute):
            return func.attr
        return None

    def _resolve(self, func) -> Optional[str]:
        """A callee key 'mod:qual' / 'attr:name' / None (unresolvable)."""
        if isinstance(func, ast.Name):
            n = func.id
            for scope in reversed(self.nested_scopes):
                if n in scope:
                    return f"{self.mod}:{scope[n]}"
            if n in self.imports:
                base, member = self.imports[n]
                if member:
                    return f"{base}:{member}"
                return None
            return f"{self.mod}:{n}"
        if isinstance(func, ast.Attribute):
            v = func.value
            if isinstance(v, ast.Name):
                if v.id in ("self", "cls") and self.cls_stack:
                    return f"{self.mod}:{self.cls_stack[-1]}.{func.attr}"
                if v.id in self.imports:
                    base, member = self.imports[v.id]
                    if member:  # `from pkg import module` then module.fn()
                        return f"{base}.{member}:{func.attr}"
                    return f"{base}:{func.attr}"
            return f"attr:{func.attr}"
        return None

    def _ref_target(self, node) -> Optional[str]:
        if isinstance(node, (ast.Name, ast.Attribute)):
            return self._resolve(node)
        return None

    def visit_Call(self, node):
        fn = self.fn_stack[-1] if self.fn_stack else None
        name = self._callee_name(node.func)
        covered = self.cover_depth > 0
        # model sinks
        is_sink = False
        if name in SINK_NAMES:
            is_sink = True
        elif (isinstance(node.func, ast.Attribute) and node.func.attr in SINK_ATTRS
              and not (isinstance(node.func.value, ast.Name)
                       and node.func.value.id in self.imports)):
            # (a call on an imported module is a call to that module's function,
            # resolved like any other; only a model object's method is a sink)
            is_sink = True
        elif isinstance(node.func, ast.Attribute) and node.func.attr in (
                "create", "stream") and isinstance(node.func.value, ast.Attribute) \
                and node.func.value.attr == "messages":
            is_sink = True  # anthropic client.messages.create / .stream
        if is_sink and fn is not None:
            (fn.sink_covered if covered else fn.sink_uncovered).append(node.lineno)
        # spawn edges: the callable reference is an uncovered edge
        if name in SPAWN_ATTRS or name == "_spawn_task":
            refs = list(node.args) + [k.value for k in node.keywords
                                      if k.arg in ("target", "runner", "function")]
            runner_kw = [k.value for k in node.keywords if k.arg == "runner"]
            for r in refs:
                tgt = self._ref_target(r)
                if tgt and fn is not None:
                    fn.calls.append((tgt, False, True))
                    fn.refs.add(tgt)
                    if name == "_spawn_task" and any(r is v for v in runner_kw):
                        fn.task_runner_refs.add(tgt)
        # a nested function handed to any other call is treated as called here
        if not (name in SPAWN_ATTRS or name == "_spawn_task") and fn is not None                 and self.nested_scopes:
            for a in list(node.args) + [k.value for k in node.keywords]:
                if isinstance(a, ast.Name) and any(a.id in s for s in self.nested_scopes):
                    fn.calls.append((self._resolve(a), covered, False))
        # plain call edge
        tgt = self._resolve(node.func)
        if tgt and not is_sink:
            if fn is not None:
                fn.calls.append((tgt, covered, False))
            else:
                self.top_level_calls.append((tgt, covered, False))
        self.generic_visit(node)

    def visit_Constant(self, node):
        # a function that posts to a chat-completions endpoint itself
        if isinstance(node.value, str) and "/chat/completions" in node.value \
                and self.fn_stack:
            fn = self.fn_stack[-1]
            (fn.sink_covered if self.cover_depth else fn.sink_uncovered).append(node.lineno)


_LOADED: Dict[tuple, Tuple[Dict[str, Fn], Dict[str, "_Collector"], List[str]]] = {}


def load(src_root: Path) -> Tuple[Dict[str, Fn], Dict[str, _Collector]]:
    """Parse every module under ``src_root``. Memoised on the files' size and
    modification time, so a test session reads the tree once."""
    files = [p for p in sorted(src_root.rglob("*.py")) if "__pycache__" not in p.parts]
    sig = (str(src_root), tuple((str(p), p.stat().st_mtime_ns, p.stat().st_size) for p in files))
    hit = _LOADED.get(sig)
    if hit is not None:
        PARSE_ERRORS.extend(hit[2])
        return hit[0], hit[1]
    while len(_LOADED) >= 3:
        _LOADED.pop(next(iter(_LOADED)))
    before = len(PARSE_ERRORS)
    fns, cols = _load_files(src_root, files)
    _LOADED[sig] = (fns, cols, list(PARSE_ERRORS[before:]))
    return fns, cols


def _load_files(src_root: Path, files: List[Path]) -> Tuple[Dict[str, Fn], Dict[str, _Collector]]:
    fns: Dict[str, Fn] = {}
    cols: Dict[str, _Collector] = {}
    for p in files:
        mod = _modname(p, src_root)
        pkg = mod if p.name == "__init__.py" else mod.rpartition(".")[0]
        try:
            tree = ast.parse(p.read_text(encoding="utf-8-sig"))
        except (SyntaxError, UnicodeDecodeError) as exc:
            # A file this guard cannot read is a file it cannot vouch for.
            PARSE_ERRORS.append(f"{p}: {exc}")
            continue
        c = _Collector(mod, str(p.relative_to(src_root.parent)), pkg)
        c.visit(tree)
        cols[mod] = c
        for q, f in c.fns.items():
            fns[f.key] = f
    return fns, cols


def _prefixes(qual: str) -> List[str]:
    parts = qual.split(".")
    return [".".join(parts[: i + 1]) for i in range(len(parts))]


def _allowed(fn: Fn) -> Optional[str]:
    # A function nested in an allowed one is part of it.
    for q in _prefixes(fn.qual):
        if f"{fn.mod}:{q}" in ALLOW:
            return ALLOW[f"{fn.mod}:{q}"]
    leaf = fn.qual.split(".")[0]
    if _TOOL_HANDLER.match(leaf):
        return ("an agent tool handler: it runs inside a chat turn or a TASKS "
                "worker, and the call is a step on that turn's or task's trace")
    for pref, why in ALLOW_MODULE_PREFIXES.items():
        if fn.mod == pref or fn.mod.startswith(pref):
            return why
    return None


def analyze(src_root: Path) -> Dict[str, object]:
    fns, cols = load(src_root)
    by_name: Dict[str, List[str]] = {}
    for k, f in fns.items():
        by_name.setdefault(f.qual.rpartition(".")[2], []).append(k)

    def targets(key: str) -> List[str]:
        if key.startswith("attr:"):
            name = key[5:]
            cands = by_name.get(name, [])
            # A method name defined exactly once, and distinctive enough that a
            # same-named method of a socket, a dict or a library is unlikely.
            if (len(cands) == 1 and "_" in name.strip("_") and len(name) >= 8
                    and name not in ATTR_BLOCKLIST):
                return cands
            return []
        if key in fns:
            return [key]
        # `from pkg import mod` style: 'pkg.mod:func'
        return []

    # edges
    callers: Dict[str, List[Tuple[str, bool]]] = {}
    spawned_targets: Set[str] = set()
    task_runners: Set[str] = set()
    for k, f in fns.items():
        for key, covered, spawn in f.calls:
            for t in targets(key):
                if t == k:
                    continue
                if spawn:
                    # A launch: the target starts its own thread of work. The
                    # spawner is not exposed by it; the target is a launch point.
                    spawned_targets.add(t)
                    if key in f.task_runner_refs:
                        task_runners.add(t)
                else:
                    callers.setdefault(t, []).append((k, covered))

    exposed: Dict[str, Optional[str]] = {}  # fn key -> via (child key) or None
    work: List[str] = []
    for k, f in fns.items():
        if f.mod in ROUTER_MODULES:
            continue
        if any(q.rpartition(".")[2] in SINK_NAMES for q in _prefixes(f.qual)):
            continue  # the primitives themselves, and what they define inside
        if f.sink_uncovered and not f.decorated_cover:
            exposed[k] = None
            work.append(k)
    # (a task runner is a TASKS record's worker: covered by construction)
    while work:
        k = work.pop()
        if fns[k].decorated_cover or _allowed(fns[k]):
            continue  # absorbs: nothing above it is exposed through it
        for caller, covered in callers.get(k, []):
            cf = fns[caller]
            if covered or cf.decorated_cover or caller in exposed:
                continue
            if cf.mod in ROUTER_MODULES:
                continue
            exposed[caller] = k
            work.append(caller)

    launch: List[Dict[str, object]] = []
    allowed_hits: List[Dict[str, object]] = []
    for k in exposed:
        f = fns[k]
        spawned = k in spawned_targets
        if not (f.is_route or spawned or not callers.get(k)):
            continue
        if k in task_runners:
            continue
        chain = [k]
        cur = k
        seen = {k}
        while exposed.get(cur) and exposed[cur] not in seen:
            cur = exposed[cur]  # type: ignore[assignment]
            seen.add(cur)
            chain.append(cur)
        row = {"launch": k, "path": f.path, "line": getattr(f.node, "lineno", 0),
               "route": f.is_route, "spawned": spawned, "chain": chain,
               "sink_line": (fns[chain[-1]].sink_uncovered or [0])[0],
               "sink_path": fns[chain[-1]].path}
        why = _allowed(f)
        if why:
            row["why"] = why
            allowed_hits.append(row)
        else:
            launch.append(row)
    launch.sort(key=lambda r: (r["path"], r["line"]))
    return {"violations": launch, "allowed": allowed_hits,
            "functions": len(fns), "exposed": len(exposed)}


def main(argv: Optional[List[str]] = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    root = REPO / "src"
    if "--root" in argv:
        root = Path(argv[argv.index("--root") + 1])
    del PARSE_ERRORS[:]
    res = analyze(root)
    for err in PARSE_ERRORS:
        print("UNPARSEABLE", err)
    bad = res["violations"]
    if "--list" in argv or bad:
        for r in bad:  # type: ignore[union-attr]
            print("INVISIBLE  %s  (%s:%s)\n           reaches a model call at %s:%s via %s"
                  % (r["launch"], r["path"], r["line"], r["sink_path"],
                     r["sink_line"], " -> ".join(c.split(":", 1)[1] for c in r["chain"])))
    print("visible-jobs guard: %d launch point(s) without a visible task "
          "(%d functions, %d exposed, %d allowed)"
          % (len(bad), res["functions"], res["exposed"], len(res["allowed"])))  # type: ignore[arg-type]
    return 1 if (bad or PARSE_ERRORS) else 0


if __name__ == "__main__":
    sys.exit(main())

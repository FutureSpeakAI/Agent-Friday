"""Every notice goes through the registry and the policy; nothing writes around it.

Source scans (AST, no imports of the scanned modules):

* every `notifications_engine.push` call site has a stable dedupe key (no time,
  no free title) and belongs to a registered emitter;
* the raw writers (desktop bus, background transcript inserts, live voice
  delivery, tray balloons) appear only in the allow-list below: navigation the
  owner asked for, and the persistence of the owner's own turns. Everything else
  goes through `services/notice_registry`;
* no push site raises a trace or a receipt.
"""
from __future__ import annotations

import ast
from pathlib import Path

from agent_friday.services import notice_registry as reg

SRC = Path(__file__).resolve().parents[2] / "src" / "agent_friday"
ROOT_FILES = [Path(__file__).resolve().parents[2] / "server.py"]

PUSH_RECEIVERS = {"ne", "_ne", "notifications_engine", "notifier", "notif", "_notif",
                  "_notif_engine", "_notifs", "notifications"}
#: Words that are a free title, and clocks. A dedupe key built from either never
#: matches twice.
FORBIDDEN_NAMES = {"title", "body", "text", "subject", "question", "summary", "message",
                   "msg", "mtime", "st_mtime", "timestamp", "ts", "stamp"}
FORBIDDEN_CALL_PARTS = ("time", "now", "uuid", "random", "secrets", "token_", "monotonic",
                        "perf_counter", "ctime")


def _py_files():
    for p in sorted(SRC.rglob("*.py")):
        if "tests" in p.parts or "__pycache__" in p.parts:
            continue
        yield p
    for p in ROOT_FILES:
        if p.exists():
            yield p


def _tree(path: Path) -> ast.AST:
    return ast.parse(path.read_text(encoding="utf-8-sig"))


def _rel(path: Path) -> str:
    try:
        return path.relative_to(SRC).as_posix()
    except ValueError:
        return path.name


def _dotted(node: ast.AST) -> str:
    if isinstance(node, ast.Attribute):
        return _dotted(node.value) + "." + node.attr
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Call):
        return _dotted(node.func) + "()"
    return ""


def _parents(tree: ast.AST) -> dict:
    out = {}
    for parent in ast.walk(tree):
        for child in ast.iter_child_nodes(parent):
            out[child] = parent
    return out


def _enclosing_function(node, parents):
    while node in parents:
        node = parents[node]
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            return node
    return None


def push_sites():
    """(path, call, parents) for every notifications_engine.push / upsert_status call."""
    out = []
    for path in _py_files():
        if path.name == "notifications_engine.py":
            continue
        tree = _tree(path)
        parents = None
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            f = node.func
            hit = False
            named = any(k.arg in ("title", "kind", "source", "dedupe_key") for k in node.keywords)
            if isinstance(f, ast.Attribute) and f.attr in ("push", "upsert_status"):
                recv = f.value
                name = recv.id if isinstance(recv, ast.Name) else (
                    recv.attr if isinstance(recv, ast.Attribute) else "")
                hit = name in PUSH_RECEIVERS and named
            elif isinstance(f, ast.Name) and f.id in ("push", "_push", "_push_notification",
                                                      "_notif_push"):
                hit = any(k.arg == "title" for k in node.keywords)
            if hit:
                parents = parents or _parents(tree)
                out.append((path, node, parents))
    return out


def _kw(call: ast.Call) -> dict:
    return {k.arg: k.value for k in call.keywords if k.arg}


def _date_only(node: ast.Call) -> bool:
    """`<clock>.strftime("%Y-%m-%d")`: a day or month bucket, not a timestamp."""
    f = node.func
    if not (isinstance(f, ast.Attribute) and f.attr == "strftime" and node.args):
        return False
    fmt = node.args[0]
    values = [fmt.value] if isinstance(fmt, ast.Constant) else [
        c.value for c in ast.walk(fmt) if isinstance(c, ast.Constant)]
    values = [v for v in values if isinstance(v, str)]
    return bool(values) and not any(tok in v for v in values for tok in ("%H", "%M", "%S", "%f"))


def _assignments(func, name: str) -> list:
    out = []
    for node in ast.walk(func):
        if isinstance(node, ast.Assign):
            if any(isinstance(t, ast.Name) and t.id == name for t in node.targets):
                out.append(node.value)
    return out


def key_problems(expr, func, parents, depth=0, seen=None) -> list:
    """Why a dedupe-key expression is not stable. [] when it is."""
    if expr is None:
        return ["no dedupe_key"]
    if isinstance(expr, ast.Constant):
        return ["dedupe_key is None"] if expr.value is None else []
    if depth > 4:
        return []
    seen = seen if seen is not None else set()
    bad = []
    skip = set()
    for node in ast.walk(expr):
        if isinstance(node, ast.Call) and _date_only(node):
            skip.update(id(n) for n in ast.walk(node))
    for node in ast.walk(expr):
        if id(node) in skip:
            continue
        if isinstance(node, ast.Call):
            name = _dotted(node.func).lower()
            if any(part in name for part in FORBIDDEN_CALL_PARTS) and "today" not in name:
                bad.append("clock in key: " + name)
        elif isinstance(node, ast.Name) and node.id in FORBIDDEN_NAMES:
            bad.append("free text in key: " + node.id)
        elif isinstance(node, ast.Attribute) and node.attr in FORBIDDEN_NAMES:
            bad.append("free text in key: ." + node.attr)
    if bad or func is None:
        return bad
    args = func.args
    positional = [a.arg for a in args.args]
    defaults = dict(zip(positional[len(positional) - len(args.defaults):], args.defaults))
    defaults.update({a.arg: d for a, d in zip(args.kwonlyargs, args.kw_defaults) if d is not None})
    for node in ast.walk(expr):
        if id(node) in skip or not (isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load)):
            continue
        if node.id in seen:
            continue
        seen.add(node.id)
        for value in _assignments(func, node.id):
            bad += key_problems(value, func, parents, depth + 1, seen)
    if isinstance(expr, ast.Name):
        names = positional + [a.arg for a in args.kwonlyargs]
        if expr.id in names and not _assignments(func, expr.id):
            default = defaults.get(expr.id)
            if isinstance(default, ast.Constant) and default.value is None:
                bad.append("dedupe_key parameter %s may be None" % expr.id)
    return bad


def site_problems(path, call, parents) -> list:
    kw = _kw(call)
    func = _enclosing_function(call, parents)
    key = kw.get("dedupe_key") or kw.get("key")
    problems = key_problems(key, func, parents)
    em = kw.get("emitter")
    if em is not None:
        if not (isinstance(em, ast.Constant) and reg.get(em.value)):
            problems.append("emitter is not a registered id literal")
        return problems
    kind, source = kw.get("kind"), kw.get("source")
    k = kind.value if isinstance(kind, ast.Constant) else ("info" if kind is None else None)
    s = source.value if isinstance(source, ast.Constant) else ("system" if source is None else None)
    if k is None or s is None or reg.resolve(k, s) is None:
        problems.append("no emitter: pass emitter=\"E..\" or a registered literal kind and source")
    return problems


def test_every_push_site_has_a_stable_key_and_a_registered_emitter():
    sites = push_sites()
    assert len(sites) >= 55, "the scan lost the push call sites (%d)" % len(sites)
    bad = []
    for path, call, parents in sites:
        for problem in site_problems(path, call, parents):
            bad.append("%s:%d %s" % (_rel(path), call.lineno, problem))
    assert not bad, "push sites without a stable key or an emitter:\n" + "\n".join(bad)


def test_no_push_site_raises_a_trace_or_a_receipt():
    bad = []
    for path, call, parents in push_sites():
        kw = _kw(call)
        kind = kw.get("kind")
        if isinstance(kind, ast.Constant) and kind.value in reg.RECEIPT_KINDS:
            bad.append("%s:%d kind=%s" % (_rel(path), call.lineno, kind.value))
        em = kw.get("emitter")
        if isinstance(em, ast.Constant) and (reg.get(em.value) or None) and \
                reg.get(em.value).kind in reg.RECEIPT_KINDS:
            bad.append("%s:%d emitter=%s" % (_rel(path), call.lineno, em.value))
    assert not bad, "traces and receipts are pulled, never pushed:\n" + "\n".join(bad)


# ── The raw writers ──────────────────────────────────────────────────────────

BUS_METHODS = {"send", "push", "broadcast"}
BUS_NAMES = {"desktop_bus", "_bus", "_desktop_bus", "bus"}
VOICE_NAMES = {"voice_live_channel", "_vlc", "vlc", "_voice_live_channel"}
TRANSCRIPT_NAMES = {"conversations", "_convs", "_cv", "_conv", "convs", "_crew_cv"}

#: Where a raw writer is allowed, and why. A module maps to the kinds of writer it
#: may keep and the reason. A new module that needs one is a review decision, not a
#: quiet edit.
NAVIGATION = "navigation or a UI action the owner asked for"
TURN = "persists the owner's own turn"
BUS_CMD = {"desktop_bus.send", "desktop_bus.broadcast", "desktop_bus.push"}
ALLOW = {
    "services/notice_registry.py": ({"desktop_bus.send", "desktop_bus.broadcast",
                                     "desktop_bus.push", "voice_live_channel.deliver",
                                     "icon.notify", "transcript append"},
                                    "the routed writers themselves"),
    "services/avatar_tools.py": ({"desktop_bus.send"}, NAVIGATION),
    "services/call_watch.py": ({"desktop_bus.send"}, NAVIGATION),
    "services/hand_cursor_tools.py": ({"desktop_bus.send"}, NAVIGATION),
    "services/hologram_tools.py": ({"desktop_bus.send"}, NAVIGATION),
    "services/media_card_tools.py": ({"desktop_bus.send"}, NAVIGATION),
    "services/library/ui.py": ({"desktop_bus.send"}, NAVIGATION),
    "services/podcast_tools.py": ({"desktop_bus.send"}, NAVIGATION),
    "services/sites_tools.py": ({"desktop_bus.send"}, NAVIGATION),
    "services/desktop_targets.py": ({"desktop_bus.send"}, NAVIGATION),
    "services/step_lists.py": ({"desktop_bus.push"}, "marks the steps of a list the owner started"),
    "services/agent.py": ({"desktop_bus.send", "desktop_bus.broadcast"},
                          NAVIGATION + " (layout, chat tray, hub, landing, stage, select, fill, point)"),
    "routes/chat.py": ({"transcript append"}, TURN),
    "services/voice_engine.py": ({"transcript append"}, TURN),
    "services/crew_runtime.py": ({"transcript append"}, TURN),
}


def raw_writer_calls():
    """(relative path, line, what) for each raw writer call in the tree."""
    out = []
    for path in _py_files():
        if path.name == "notifications_engine.py":
            continue
        for node in ast.walk(_tree(path)):
            if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)):
                continue
            f = node.func
            recv = f.value
            name = recv.id if isinstance(recv, ast.Name) else (
                recv.attr if isinstance(recv, ast.Attribute) else "")
            what = None
            if f.attr in BUS_METHODS and name in BUS_NAMES:
                what = "desktop_bus." + f.attr
            elif f.attr == "deliver" and name in VOICE_NAMES:
                what = "voice_live_channel.deliver"
            elif f.attr == "notify" and name == "icon":
                what = "icon.notify"
            elif f.attr == "append" and name in TRANSCRIPT_NAMES:
                what = "transcript append"
            if what:
                out.append((_rel(path), node.lineno, what))
    return out


def test_no_bypass_writer_outside_the_allow_list():
    stray = ["%s:%d %s" % (p, line, what) for p, line, what in raw_writer_calls()
             if what not in ALLOW.get(p, (set(), ""))[0]]
    assert not stray, ("route these through services/notice_registry (or, if it is "
                       "navigation the owner asked for, add the module to ALLOW with its "
                       "reason):\n" + "\n".join(stray))


def test_the_allow_list_has_no_dead_entries():
    used = {}
    for p, _, what in raw_writer_calls():
        used.setdefault(p, set()).add(what)
    dead = sorted("%s %s" % (p, sorted(kinds - used.get(p, set())))
                  for p, (kinds, _) in ALLOW.items() if kinds - used.get(p, set()))
    assert not dead, "allow-listed writers that no longer exist: %s" % dead


def test_notice_registry_calls_name_a_registered_emitter():
    """notice_registry.bus_send("E70", ...) and friends: the id is a literal in the registry."""
    helpers = {"bus_send", "bus_push", "bus_broadcast", "transcript_add", "voice_say",
               "tray_balloon", "note"}
    seen, bad = 0, []
    for path in _py_files():
        for node in ast.walk(_tree(path)):
            if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                    and node.func.attr in helpers and node.args):
                continue
            recv = node.func.value
            name = recv.id if isinstance(recv, ast.Name) else ""
            if name not in ("notice_registry", "_nr", "_reg"):
                continue
            seen += 1
            a = node.args[0]
            if not (isinstance(a, ast.Constant) and reg.get(a.value)):
                bad.append("%s:%d" % (_rel(path), node.lineno))
    assert seen >= 15, "the scan lost the routed writer calls (%d)" % seen
    assert not bad, "unregistered emitter id at: " + ", ".join(bad)

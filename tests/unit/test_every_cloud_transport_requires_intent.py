"""Every code path that can reach a cloud model asks `cloud_intent.require` first.

Routing modes (Beta 1.1, addendum 2.4): in `local_preferred` and `local_only` the
cloud is called only with a recorded intent. That holds only if the check sits at
the TRANSPORT, the last line before the network, so no new fallback leg, retry or
background caller can route around it.

This is discovery, not enumeration. The test walks the AST of `src/agent_friday`
and finds every call that can reach a cloud model:

* SDK constructors and methods: `anthropic.Anthropic(...)`, `openai.OpenAI(...)`,
  `genai.Client(...)`, `messages.create`, `chat.completions.create`,
  `generate_content`, `generate_videos`, `generate_music`, ...;
* HTTP calls (`requests`, `httpx`, `urllib`) whose URL, or a module constant it
  names, holds a known cloud model host;
* the registered transport wrappers (`_call_claude`, `_call_openai`, ...).

Each must be preceded by a `require(...)` call, or by a call to a registered client
factory that itself requires, in the same block or an enclosing one of the same
function. A NEW cloud call site anywhere fails this test BY NAME, with its file
and line: nobody has to remember to add it to a list. The exemptions below are the
few that are not a model call the routing mode governs; each says why, and an
exemption nothing matches any more fails too.

The scan also asserts a floor on what it finds, so a refactor that blinds it
(a renamed attribute, a moved package) cannot make the test pass by seeing nothing.
"""
from __future__ import annotations

import ast
import re
from pathlib import Path

SRC = Path(__file__).resolve().parents[2] / "src" / "agent_friday"

# ── what counts as a cloud transport ─────────────────────────────────────────

#: Final attribute of an SDK call that sends a prompt to a cloud model.
_SDK_METHODS = {"generate_content", "generate_content_stream", "generate_videos",
                "generate_music", "generate_images"}
#: Dotted tails: messages.create, chat.completions.create, responses.create ...
_SDK_TAILS = (("messages", "create"), ("messages", "stream"),
              ("chat", "completions", "create"), ("responses", "create"),
              ("completions", "create"))
#: Constructors of cloud SDK clients.
_SDK_CTORS = {"Anthropic", "AsyncAnthropic", "AnthropicBedrock", "OpenAI",
              "AsyncOpenAI", "AzureOpenAI"}
_HTTP_VERBS = {"post", "get", "put", "patch", "request", "urlopen", "stream"}
_HTTP_ROOTS = {"requests", "httpx", "urllib", "session", "client", "http"}
_CLOUD_HOST = re.compile(
    r"api\.anthropic\.com|openrouter\.ai|api\.openai\.com|"
    r"generativelanguage\.googleapis\.com|aiplatform\.googleapis\.com|"
    r"api\.groq\.com|api\.together\.xyz|api\.mistral\.ai|api\.deepseek\.com|"
    r"api\.x\.ai|kie\.ai|higgsfield|api\.cohere|api\.perplexity", re.I)

#: Functions that ARE a transport even though their wire call is not statically a
#: cloud host (a variable base_url, an MCP session, a proxy upstream). Each must
#: call `require` unconditionally at the top level of its body.
KNOWN_TRANSPORTS = {
    ("services/model_router.py", "_call_claude"),
    ("services/model_router.py", "_call_openai"),
    ("services/agent.py", "_call_claude_agent_run"),
    ("services/kie_generate.py", "_request"),
    ("services/higgsfield_generate.py", "_call"),
    ("services/claude_engine.py", "run_task"),
}

#: Client factories: a function that builds a cloud client and calls `require`
#: first. A call through one counts as the check for the statement after it.
CLIENT_FACTORIES = {"_cloud_client", "_client", "_gemini_client"}
#: ... and where each one lives (other modules have unrelated `_client` helpers).
_FACTORY_HOMES = {
    "_cloud_client": "routes/core_routes.py",
    "_client": "services/creative_engine.py",
    "_gemini_client": "services/media_tools.py",
}

#: Not a model call the routing mode governs. (relpath, function) -> why.
EXEMPT = {
    ("core/__init__.py", "get_genai_client"):
        "client getter: builds an object and sends nothing; every send is a method call this test also guards",
    ("core/__init__.py", "get_anthropic_client"):
        "client getter: builds an object and sends nothing; every send is a method call this test also guards",
    ("services/agent.py", "_guest_client_for_turn"):
        "builds the salon guest-key client object and sends nothing; the loop that uses it calls require first",
    ("cli.py", "_validate_anthropic_key"):
        "owner-run CLI key check at a terminal the owner is typing in; sends no user content",
    ("cli.py", "_validate_gemini_key"):
        "owner-run CLI key check at a terminal the owner is typing in; sends no user content",
    ("setup_wizard.py", "_validate_anthropic"):
        "setup wizard key check the owner presses; sends no user content",
    ("setup_wizard.py", "_validate_gemini"):
        "setup wizard key check the owner presses; sends no user content",
    ("routes/voice.py", "_client_for"):
        "Gemini Live is a voice engine the owner picks (Settings > Voice, voice_engine); "
        "local_only refuses /ws/live in code, and it never auto-spills",
    ("services/voice_engine.py", "_synthesize_tts_wav_gemini"):
        "speech synthesis on the voice engine the owner picked; governed by voice_engine, not the routing mode",
}

_SKIP_DIRS = {"__pycache__"}


# ── scan ─────────────────────────────────────────────────────────────────────

def _chain(node):
    """Dotted name of an attribute/call chain ('a.b.c'); calls contribute '()'."""
    parts = []
    while True:
        if isinstance(node, ast.Attribute):
            parts.append(node.attr)
            node = node.value
        elif isinstance(node, ast.Call):
            parts.append("()")
            node = node.func
        elif isinstance(node, ast.Name):
            parts.append(node.id)
            break
        else:
            break
    return list(reversed(parts))


def _tail_matches(chain, tail):
    chain = [p for p in chain if p != "()"]
    return len(chain) >= len(tail) and tuple(chain[-len(tail):]) == tail


def _is_require(call):
    f = call.func
    if isinstance(f, ast.Attribute) and f.attr == "require":
        base = f.value
        return isinstance(base, ast.Name) and base.id in (
            "_cloud_intent", "cloud_intent", "_ci", "_require_cloud")
    return isinstance(f, ast.Name) and f.id == "_require_cloud"


def _is_factory_call(call):
    f = call.func
    name = f.attr if isinstance(f, ast.Attribute) else getattr(f, "id", "")
    return name in CLIENT_FACTORIES


def _module_constants(tree):
    out = {}
    for n in tree.body:
        if isinstance(n, ast.Assign) and isinstance(n.value, ast.Constant) \
                and isinstance(n.value.value, str):
            for t in n.targets:
                if isinstance(t, ast.Name):
                    out[t.id] = n.value.value
    return out


def _classify(call, consts):
    """The kind of cloud transport this call is, or None."""
    chain = _chain(call.func)
    if not chain:
        return None
    last = chain[-1]
    if last in _SDK_METHODS and _tail_matches(chain, ("models", last)):
        return "sdk-method:models." + last
    for tail in _SDK_TAILS:
        if _tail_matches(chain, tail):
            return "sdk-method:" + ".".join(tail)
    if last in _SDK_CTORS:
        return "sdk-ctor:" + last
    if _tail_matches(chain, ("genai", "Client")):
        return "sdk-ctor:genai.Client"
    if last in _HTTP_VERBS and chain[0] in _HTTP_ROOTS | {"urllib"}:
        hay = ast.unparse(call)
        for n in ast.walk(call):
            if isinstance(n, ast.Name) and n.id in consts:
                hay += " " + consts[n.id]
        if _CLOUD_HOST.search(hay):
            return "http:" + ".".join(p for p in chain if p != "()")
    return None


def _walk_function_body(fn):
    """Every node of `fn` outside nested function bodies and lambdas."""
    stack = list(fn.body)
    while stack:
        n = stack.pop()
        yield n
        for child in ast.iter_child_nodes(n):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
                continue
            stack.append(child)


def _stmt_checks(stmt):
    """Does this statement, on its own, perform the intent check?

    Only an unconditional statement counts: an `Expr`/`Assign` whose value is a
    `require(...)` call or a registered client-factory call (an `if` that might
    skip it does not)."""
    if isinstance(stmt, ast.Try):
        # `try: require(...) except ...: return/raise`: the check cannot be skipped
        # by the exception it raises.
        return (any(_stmt_checks(b) for b in stmt.body) and not stmt.orelse
                and all(h.body and isinstance(h.body[-1], (ast.Return, ast.Raise,
                                                           ast.Continue, ast.Break))
                        for h in stmt.handlers))
    value = getattr(stmt, "value", None) if isinstance(
        stmt, (ast.Expr, ast.Assign, ast.AnnAssign)) else None
    if isinstance(value, ast.Await):
        value = value.value
    return isinstance(value, ast.Call) and (_is_require(value) or _is_factory_call(value))


def _blocks_of(node):
    """The statement lists directly inside a statement node (none for a nested
    function or class: those are judged on their own)."""
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
        return
    for field in ("body", "orelse", "finalbody", "handlers"):
        block = getattr(node, field, None)
        if isinstance(block, list) and block and isinstance(block[0], ast.stmt):
            yield block
        elif field == "handlers" and isinstance(block, list):
            for h in block:
                yield h.body


def _scan_block(block, checked, name, consts, sites):
    for stmt in block:
        for n in _own_nodes(stmt):
            if isinstance(n, ast.Call):
                kind = _classify(n, consts)
                if kind:
                    sites.append((kind, n.lineno, name, checked or _receiver_has_factory(n)))
        for sub in _blocks_of(stmt):
            _scan_block(sub, checked, name, consts, sites)
        if _stmt_checks(stmt):
            checked = True


def _find_sites(tree, consts):
    """(kind, lineno, enclosing function name, guarded?) for every transport call.

    Each function (nested ones too) is judged on its own statements."""
    sites = []
    for fn in _function_defs(tree):
        _scan_block(fn.body, False, fn.name, consts, sites)
    for stmt in tree.body:
        if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            continue
        for n in _own_nodes(stmt):
            if isinstance(n, ast.Call):
                kind = _classify(n, consts)
                if kind:
                    sites.append((kind, n.lineno, "<module>", False))
    return sites


def _own_nodes(stmt):
    """Nodes of one statement, not entering nested function bodies/lambdas, and not
    entering nested compound-statement bodies (those are scanned as their own blocks)."""
    stack = [stmt]
    first = True
    while stack:
        n = stack.pop()
        yield n
        for field, value in ast.iter_fields(n):
            if first and field in ("body", "orelse", "finalbody", "handlers") and isinstance(value, list):
                continue
            items = value if isinstance(value, list) else [value]
            for child in items:
                if isinstance(child, ast.AST) and not isinstance(
                        child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda, ast.ClassDef)):
                    stack.append(child)
        first = False


def _receiver_has_factory(call):
    for n in ast.walk(call.func):
        if isinstance(n, ast.Call) and _is_factory_call(n):
            return True
    return False


def _function_defs(tree):
    return [n for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]


def scan_tree(root: Path = SRC):
    """Every transport site under `root`, and the registered wrappers' state."""
    sites, parsed = [], {}
    for path in sorted(root.rglob("*.py")):
        if any(p in _SKIP_DIRS for p in path.parts):
            continue
        rel = path.relative_to(root).as_posix()
        try:
            tree = ast.parse(path.read_text(encoding="utf-8-sig"))
        except (SyntaxError, UnicodeDecodeError):
            continue
        parsed[rel] = tree
        consts = _module_constants(tree)
        for kind, line, func, guarded in _find_sites(tree, consts):
            sites.append({"file": rel, "line": line, "func": func, "kind": kind,
                          "guarded": guarded})
    return sites, parsed


def _requires_at_top_level(fn):
    return any(_stmt_checks(s) and _is_direct_require(s) for s in _iter_all_blocks(fn))


def _is_direct_require(stmt):
    value = getattr(stmt, "value", None)
    if isinstance(value, ast.Await):
        value = value.value
    return isinstance(value, ast.Call) and _is_require(value)


def _iter_all_blocks(fn):
    """Statements of fn and of nested with/try/if blocks (not nested defs)."""
    stack = [list(fn.body)]
    while stack:
        block = stack.pop()
        for s in block:
            yield s
            for sub in _blocks_of(s):
                stack.append(sub)


def _find_function(parsed, rel, name):
    tree = parsed.get(rel)
    if tree is None:
        return None
    for fn in _function_defs(tree):
        if fn.name == name:
            return fn
    return None


# ── the tests ────────────────────────────────────────────────────────────────

_SITES, _PARSED = scan_tree()


def _label(s):
    return "%s:%d in %s() [%s]" % (s["file"], s["line"], s["func"], s["kind"])


def test_the_scan_sees_the_transports_it_is_meant_to_see():
    """A floor, so a renamed attribute or a moved package cannot blind the scan."""
    kinds = {s["kind"].split(":")[0] for s in _SITES}
    assert {"sdk-method", "sdk-ctor"} <= kinds, "scan found no SDK transports at all: %s" % sorted(kinds)
    assert len(_SITES) >= 30, (
        "the scan found only %d cloud transport sites; it used to find 30 or more, "
        "so it has gone blind: %s" % (len(_SITES), [_label(s) for s in _SITES][:10]))
    funcs = {(s["file"], s["func"]) for s in _SITES}
    for must in [("services/model_router.py", "_call_claude"),
                 ("services/agent.py", "_call_claude_agent_run"),
                 ("services/provider_health.py", "inference_probe"),
                 ("services/music_engine.py", "_generate_music_cloud"),
                 ("services/compaction.py", "_summarize")]:
        assert must in funcs, "the scan no longer finds the transport %s::%s" % must


def test_every_registered_wrapper_exists_and_requires_intent():
    missing, unguarded = [], []
    for rel, name in sorted(KNOWN_TRANSPORTS):
        fn = _find_function(_PARSED, rel, name)
        if fn is None:
            missing.append("%s::%s" % (rel, name))
        elif not _requires_at_top_level(fn):
            unguarded.append("%s::%s" % (rel, name))
    assert not missing, "registered transport wrappers have moved or been renamed: %s" % missing
    assert not unguarded, (
        "these transport wrappers do not call cloud_intent.require: %s" % unguarded)


def test_every_client_factory_requires_intent():
    bad = []
    for name, rel in sorted(_FACTORY_HOMES.items()):
        fn = _find_function(_PARSED, rel, name)
        if fn is None:
            bad.append("%s::%s (not found)" % (rel, name))
        elif not _requires_at_top_level(fn):
            bad.append("%s::%s" % (rel, name))
    assert set(_FACTORY_HOMES) == CLIENT_FACTORIES
    assert not bad, "these client factories build a cloud client without cloud_intent.require: %s" % bad


def test_no_cloud_transport_is_called_without_asking_for_intent():
    """THE guard. A new unguarded site fails here by file, line and function."""
    offenders = []
    for s in _SITES:
        if s["guarded"]:
            continue
        if (s["file"], s["func"]) in EXEMPT:
            continue
        if (s["file"], s["func"]) in KNOWN_TRANSPORTS:
            continue   # judged by test_every_registered_wrapper_exists_and_requires_intent
        offenders.append(_label(s))
    assert not offenders, (
        "cloud transport(s) reached without cloud_intent.require first. Call "
        "`_cloud_intent.require(provider, purpose, model)` before the call, or, for "
        "something that is not a model call the routing mode governs, register it in "
        "EXEMPT with the reason:\n  " + "\n  ".join(offenders))


def test_every_exemption_still_matches_something():
    """An exemption nothing uses any more is a hole waiting for a new caller."""
    live = {(s["file"], s["func"]) for s in _SITES}
    stale = sorted(k for k in EXEMPT if k not in live)
    assert not stale, "stale EXEMPT entries (no matching transport site): %s" % stale


def test_the_exemptions_each_say_why():
    assert all(len(reason.strip()) > 20 for reason in EXEMPT.values())


# ── the scan can fail: a synthetic tree with good and bad sites ──────────────

_BAD_SDK = """
import anthropic
def send(x):
    c = anthropic.Anthropic(api_key="k")
    return c.messages.create(model="m", messages=x)
"""
_GOOD_SDK = """
from agent_friday.services import cloud_intent as _cloud_intent
def send(x):
    _cloud_intent.require("anthropic", "t")
    import anthropic
    c = anthropic.Anthropic(api_key="k")
    return c.messages.create(model="m", messages=x)
"""
_CONDITIONAL = """
def send(x, flag):
    if flag:
        _cloud_intent.require("anthropic", "t")
    return c.messages.create(model="m", messages=x)
"""
_TRY_WITH_ESCAPE = """
def send(x):
    try:
        _cloud_intent.require("anthropic", "t")
    except Exception:
        pass
    return c.messages.create(model="m", messages=x)
"""
_TRY_THAT_RETURNS = """
def send(x):
    try:
        _cloud_intent.require("anthropic", "t")
    except Exception:
        return ""
    return c.messages.create(model="m", messages=x)
"""
_NESTED = """
def outer(x):
    _cloud_intent.require("anthropic", "t")
    def inner():
        return c.messages.create(model="m", messages=x)
    return inner
"""
_HTTP_CLOUD = """
import requests
def f():
    return requests.post("https://api.openai.com/v1/chat/completions", json={})
"""
_HTTP_LOOPBACK = """
import requests
def f():
    return requests.post("http://127.0.0.1:8090/v1/chat/completions", json={})
"""
_HTTP_CONST = """
import requests
UP = "https://openrouter.ai/api/v1/chat/completions"
def f():
    return requests.post(UP, json={})
"""
_OWN_FUNCTION = """
def f(music_engine):
    return music_engine.generate_music(prompt="a")
"""


def _scan_source(tmp_path, source):
    f = tmp_path / "mod.py"
    f.write_text(source, encoding="utf-8")
    sites, _ = scan_tree(tmp_path)
    return sites


def test_the_scan_names_an_unguarded_sdk_call(tmp_path):
    sites = _scan_source(tmp_path, _BAD_SDK)
    assert sites and not any(s["guarded"] for s in sites)
    assert {"sdk-ctor:Anthropic", "sdk-method:messages.create"} <= {s["kind"] for s in sites}
    assert all(s["func"] == "send" and s["file"] == "mod.py" for s in sites)


def test_a_require_before_the_call_guards_it(tmp_path):
    sites = _scan_source(tmp_path, _GOOD_SDK)
    assert len(sites) == 2 and all(s["guarded"] for s in sites)


def test_a_conditional_or_skippable_require_does_not_guard(tmp_path):
    assert not _scan_source(tmp_path, _CONDITIONAL)[0]["guarded"]
    assert not _scan_source(tmp_path, _TRY_WITH_ESCAPE)[0]["guarded"]
    assert _scan_source(tmp_path, _TRY_THAT_RETURNS)[0]["guarded"]


def test_a_nested_function_is_judged_on_its_own(tmp_path):
    sites = _scan_source(tmp_path, _NESTED)
    assert len(sites) == 1 and sites[0]["func"] == "inner" and not sites[0]["guarded"]


def test_http_to_a_cloud_host_is_found_and_loopback_is_not(tmp_path):
    assert [s["kind"] for s in _scan_source(tmp_path, _HTTP_CLOUD)] == ["http:requests.post"]
    assert _scan_source(tmp_path, _HTTP_LOOPBACK) == []
    assert [s["kind"] for s in _scan_source(tmp_path, _HTTP_CONST)] == ["http:requests.post"]


def test_friday_s_own_generate_functions_are_not_sdk_calls(tmp_path):
    assert _scan_source(tmp_path, _OWN_FUNCTION) == []


def test_no_function_shadows_a_module_level_cloud_intent_alias():
    """`from ... import cloud_intent as _cloud_intent` inside a function makes the
    name local to the WHOLE function: every earlier use in it is unbound. Where a
    module imports the alias at the top, no function re-imports it."""
    offenders = []
    for rel, tree in _PARSED.items():
        top = set()
        for n in tree.body:
            if isinstance(n, ast.ImportFrom):
                top |= {a.asname or a.name for a in n.names if a.name == "cloud_intent"}
        if not top:
            continue
        for fn in _function_defs(tree):
            for n in ast.walk(fn):
                if isinstance(n, ast.ImportFrom):
                    for a in n.names:
                        if a.name == "cloud_intent" and (a.asname or a.name) in top:
                            offenders.append("%s:%d in %s()" % (rel, n.lineno, fn.name))
    assert not offenders, "function-local re-import shadows the module alias: %s" % offenders

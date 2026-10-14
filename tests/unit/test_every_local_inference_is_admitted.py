"""Discovery, not enumeration: every call in ``src`` that sends a request to a
local model passes the local work scheduler's admission.

The scan reads the syntax tree of every module under ``src/agent_friday``. A
*site* is a call that sends an HTTP request (``post``, ``request``,
``urlopen``, ``Request``) whose address carries a local inference path
(``/chat/completions``, ``/api/chat``, ``/api/generate``, ``/v1/completions``)
and does not name a remote host. A site is *covered* when its function, or a
function around it, is decorated with ``admitted`` (``services/local_scheduler``)
or calls ``admit``. A new local call that is neither covered nor named below,
with its reason, fails here by its file and function.

The list below is the whole set of exemptions. Each is a control call or a
diagnostic that must never wait in the line it would be waiting for.
"""
import ast
import functools
from pathlib import Path

SRC = Path(__file__).resolve().parents[2] / "src"
PKG = SRC / "agent_friday"

LOCAL_PATHS = ("/chat/completions", "/api/chat", "/api/generate", "/v1/completions")
SENDERS = {"post", "request", "urlopen", "Request", "_post"}

#: (path under src/agent_friday, function) -> why it is not admitted.
EXEMPT = {
    ("services/residency_arbiter.py", "OllamaBackend.load"):
        "loads a model into the daemon (an empty prompt): the swap itself, not work for it",
    ("services/residency_arbiter.py", "OllamaBackend.evict"):
        "unloads a model (keep_alive 0): the swap itself",
    ("services/residency_arbiter.py", "LlamaServerBackend.verify"):
        "proves a restored seat answers, inside heavy_job after the lease ends; waiting on the queue it just freed would stall the restore",
    ("services/provider_health.py", "inference_probe"):
        "a 16-token health probe the owner opens; it is a diagnostic of the provider, not work",
    ("routing/ollama_manager.py", "OllamaManager.probe_generate"):
        "a ten-token liveness probe for the model health panel",
}

MINIMUM_SITES = 12      # the scan must find at least this many, or it is looking at nothing


def _const_strings(node):
    for n in ast.walk(node):
        if isinstance(n, ast.Constant) and isinstance(n.value, str):
            yield n.value


def _call_name(call):
    f = call.func
    if isinstance(f, ast.Attribute):
        return f.attr
    if isinstance(f, ast.Name):
        return f.id
    return ""


def _local_names(fn):
    """Names a function assigns from an expression that carries a local
    inference path (``url = base + "/chat/completions"``)."""
    names = set()
    for n in ast.walk(fn):
        if isinstance(n, ast.Assign) and any(
                p in s for s in _const_strings(n.value) for p in LOCAL_PATHS):
            names.update(t.id for t in n.targets if isinstance(t, ast.Name))
    return names


def _is_site(call, local_names=frozenset()):
    if _call_name(call) not in SENDERS:
        return False
    strings = list(_const_strings(call))
    via_name = any(isinstance(n, ast.Name) and n.id in local_names for n in ast.walk(call))
    if not (via_name or any(p in s for s in strings for p in LOCAL_PATHS)):
        return False
    for s in strings:                         # a literal remote host is a cloud call
        if s.startswith("https://") or (s.startswith("http://") and not any(
                h in s for h in ("localhost", "127.0.0.1", "::1"))):
            return False
    return True


def _decorator_text(dec):
    return ast.unparse(dec.func if isinstance(dec, ast.Call) else dec)


def _admits(fn):
    """The function is decorated with ``admitted`` or calls ``admit``."""
    if any("admitted" in _decorator_text(d) for d in fn.decorator_list):
        return True
    for n in ast.walk(fn):
        if isinstance(n, ast.Call) and _call_name(n) in ("admit", "admit_local"):
            return True
    return False


def _parents(tree):
    parents = {}
    for n in ast.walk(tree):
        for c in ast.iter_child_nodes(n):
            parents[c] = n
    return parents


_FUNCS = (ast.FunctionDef, ast.AsyncFunctionDef)


def _enclosing_functions(parents, node):
    """Every function that contains ``node``, innermost first."""
    out, n = [], parents.get(node)
    while n is not None:
        if isinstance(n, _FUNCS):
            out.append(n)
        n = parents.get(n)
    return out


def _qualname(parents, fn):
    parts, n = [], fn
    while n is not None:
        if isinstance(n, _FUNCS + (ast.ClassDef,)):
            parts.append(n.name)
        n = parents.get(n)
    return ".".join(reversed(parts))


@functools.lru_cache(maxsize=1)
def discover():
    """[(path, outermost function, line, covered)] for every local inference site."""
    out = []
    for path in sorted(PKG.rglob("*.py")):
        rel = path.relative_to(PKG).as_posix()
        tree = ast.parse(path.read_text(encoding="utf-8-sig"), filename=rel)   # a file that does not parse fails the scan
        parents = _parents(tree)
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or _call_name(node) not in SENDERS:
                continue                       # most calls are not senders: look at the rest only for those
            around = _enclosing_functions(parents, node)
            names = set().union(*[_local_names(fn) for fn in around]) if around else set()
            if not _is_site(node, names):
                continue
            covered = any(_admits(fn) for fn in around)
            name = _qualname(parents, around[-1]) if around else "<module>"
            out.append((rel, name, node.lineno, covered))
    return tuple(out)


def test_the_scan_finds_the_local_transports():
    sites = discover()
    assert len(sites) >= MINIMUM_SITES, \
        "the scan found only %d local inference sites; it is not looking at the right thing" % len(sites)
    names = {(r, n) for r, n, _l, _c in sites}
    for must in (("services/local_call.py", "call"),
                 ("services/voice_front.py", "FrontSeat._post"),
                 ("services/model_router.py", "_call_openai"),
                 ("routing/ollama_manager.py", "OllamaManager.chat_completion")):
        assert must in names, "the scan no longer sees %s %s" % must


def test_every_local_inference_call_passes_admission():
    bad = sorted({"%s:%d in %s" % (r, line, name)
                  for r, name, line, covered in discover()
                  if not covered and (r, name) not in EXEMPT})
    assert not bad, (
        "These calls send a request to a local model without passing "
        "local_scheduler.admit (decorate the function with @admitted, or call admit):\n  "
        + "\n  ".join(bad))


def test_every_exemption_still_names_a_real_uncovered_site():
    live = {(r, n) for r, n, _l, covered in discover() if not covered}
    stale = sorted(k for k in EXEMPT if k not in live)
    assert not stale, "exemptions that no longer match an uncovered site: %s" % stale
    assert all(len(why) > 20 for why in EXEMPT.values())


def test_the_detector_can_fail():
    """A scan that cannot fail counts for nothing: a call that does reach a
    local model without admission is named."""
    src = (
        "import requests\n"
        "def covered():\n    pass\n"
        "def sneaky(base):\n"
        "    return requests.post(f'{base}/chat/completions', json={})\n"
    )
    tree = ast.parse(src)
    sites = [n for n in ast.walk(tree) if isinstance(n, ast.Call) and _is_site(n)]
    assert len(sites) == 1
    around = _enclosing_functions(_parents(tree), sites[0])
    assert around and not any(_admits(fn) for fn in around)
    decorated = ast.parse(
        "@admitted('x')\ndef ok(base):\n    requests.post(f'{base}/api/chat')\n")
    site = [n for n in ast.walk(decorated) if isinstance(n, ast.Call) and _is_site(n)][0]
    assert any(_admits(fn) for fn in _enclosing_functions(_parents(decorated), site))
    cloud = ast.parse("import requests\nrequests.post('https://api.example.com/v1/chat/completions')\n")
    assert not any(_is_site(n) for n in ast.walk(cloud) if isinstance(n, ast.Call))

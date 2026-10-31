"""NS-37.6-1: a tripwire for outward or state-changing write paths and the action gate.

It proves that SOME gated path reaches each known sink call site, not that
EVERY path to it is gated: a sink reachable both from a gated function and
from an ungated one passes. It catches a new sink added with no gate near it.

An AST scan over src/agent_friday/routes, services and phone finds the call
sites of known outward sinks:

  http_write          requests/httpx .post/.put/.patch/.delete
  http_wrapper_write  a module's own HTTP helper (_http, _request, _http_json,
                      _call, _api) called with POST, PUT, PATCH or DELETE
                      (the social platforms, site and domain providers, Twilio)
  gmail_send          users().messages().send / drafts().send
  calendar_write      events().insert/update/patch/delete/move/quickAdd
  phone               .send_sms / .create_call / .update_call
  channel_raw         a channel adapter's ._send_raw
  smtp                .sendmail / .send_message

and asserts each one is reached from the gate: the function holding the call
calls authorize, authorize_external, record_external or check_external (or a
helper in its module that does, or a distinctively named helper elsewhere
that does), or it is reached, up to DEPTH calls away, from such a function or
from a registered tool handler (`_tool_*`, which runs through
agent._execute_tool and its governance hook).

Callees are resolved by name: an unqualified call to a function of the same
module, a qualified call to any module's function of that name unless the name
is too common to tell apart (COMMON). Pragmatic, so it can over-credit a
coincidental name (a sink then passes that is not in fact gated), and it
finds only the sink shapes listed above.

What remains is either on ALLOWLIST (reviewed: not an outward action, or
gated in a way the scan cannot follow, each with its reason) or on KNOWN_GAPS
(a real outward path with no checkpoint decision, reported and left as it
is). A new sink site that is on neither fails with the list of sites.
"""
from __future__ import annotations

import ast
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src" / "agent_friday"
SCANNED = ("routes", "services", "phone")

SINKS = (
    ("http_write", re.compile(r"(^|\.)(requests|httpx)\.(post|put|patch|delete)$")),
    ("gmail_send", re.compile(r"(messages|drafts)\(\)\.send$")),
    ("calendar_write", re.compile(r"events\(\)\.(insert|update|patch|delete|move|quickAdd|import_)$")),
    ("phone", re.compile(r"\.(send_sms|create_call|update_call)$")),
    ("channel_raw", re.compile(r"\._send_raw$")),
    ("smtp", re.compile(r"\.(sendmail|send_message)$")),
)
WRAPPERS = frozenset({"_http", "_request", "_http_json", "_call", "_api"})
WRITE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})
GUARDS = frozenset({"authorize", "authorize_external", "record_external", "check_external"})
#: Names too common to resolve across modules by name alone.
COMMON = frozenset({"send", "post", "_post", "call", "run", "get", "request", "create", "update",
                    "delete", "patch", "put", "fetch", "_fetch", "_send", "main", "handle",
                    "execute", "open", "close", "write", "read", "start", "stop", "apply"})
#: How many calls away from a gated function a sink may sit.
DEPTH = 3

#: Reviewed sink sites that are not an outward action on the owner's behalf,
#: or are gated in a way the scan cannot follow. (module, function): reason.
ALLOWLIST = {
    ("services/cloud_voice.py", "_synth_elevenlabs"):
        "speech synthesis at the owner's TTS provider: an inference request, nothing published; "
        "its text is governed by the egress gate",
    ("services/cloud_voice.py", "_synth_inworld"):
        "speech synthesis at the owner's TTS provider: an inference request, nothing published",
    ("services/google_accounts.py", "_revoke_remote"):
        "the owner's disconnect revokes Friday's own OAuth token at Google; no owner data changes "
        "and no third party is reached",
    ("services/inbox_cleanup.py", "_post_one_click"):
        "called only through `(POSTER or _post_one_click)` in inbox_cleanup.unsubscribe, which runs "
        "after mail_owner.held (record_external) on the owner's press or authorize_external on an "
        "approved card; the scan cannot follow the `or` indirection",
    ("services/local_call.py", "_openai_style"):
        "a local model server on this computer (chat completion)",
    ("services/model_router.py", "_post_current"):
        "a model inference request; the payload is sealed by the egress gate",
    ("services/news_links.py", "_fetch"):
        "decodes a Google News link (a read through Google's link exchange); changes nothing",
    ("services/platforms/bluesky.py", "_create_session"):
        "signs in to the owner's account for a session token; publishes nothing",
    ("services/platforms/instagram.py", "handle_callback"):
        "OAuth code exchange while the owner connects the platform; publishes nothing",
    ("services/platforms/linkedin.py", "handle_callback"):
        "OAuth code exchange while the owner connects the platform; publishes nothing",
    ("services/platforms/mastodon.py", "handle_callback"):
        "OAuth code exchange while the owner connects the platform; publishes nothing",
    ("services/platforms/mastodon.py", "_ensure_app"):
        "registers Friday's OAuth client on the owner's instance while connecting; publishes nothing",
    ("services/provider_health.py", "inference_probe"):
        "a one-token health probe of a model provider (cloud intent required)",
    ("services/r0_nightly.py", "_seat_chat"):
        "the nightly practice job's local model seat on this computer",
    ("services/voice_front.py", "_post"):
        "the local voice front model on this computer",
    ("services/web_search.py", "_wigolo_search"):
        "a web search query (a read)",
    ("services/web_search.py", "_duckduckgo"):
        "a web search query (DuckDuckGo's HTML endpoint answers POST; a read)",
    ("phone/twilio_api.py", "point_number_at"):
        "the owner's phone setup points Friday's own Twilio number at its webhooks; nobody is "
        "contacted",
    ("phone/twilio_api.py", "redact_message"):
        "privacy hygiene: blanks the body Twilio keeps of a message already sent; nobody is contacted",
    ("phone/twilio_api.py", "delete_recording"):
        "privacy hygiene: deletes a call recording Twilio keeps; nobody is contacted",
    ("phone/twilio_api.py", "update_call"):
        "hangs up or redirects a live call Friday is on; no caller in src at the time of review",
}

#: The platform takedowns: their only caller, DELETE /api/content/posts/<id>?takedown=1
#: (routes/content_pipeline.content_post_delete), calls action_gate.record_external
#: ("content:takedown") before any adapter.delete; the scan cannot follow
#: `adapter.delete` (the registry's adapter object, a name too common to resolve).
_TAKEDOWN = ("platform takedown, reached only from routes/content_pipeline.content_post_delete "
             "after record_external('content:takedown'); the scan cannot resolve adapter.delete")
ALLOWLIST.update({
    ("services/platforms/%s.py" % p, "delete"): _TAKEDOWN
    for p in ("bluesky", "linkedin", "mastodon", "reddit", "x_twitter", "youtube")
})

#: Real outward paths with no checkpoint decision (no authorize/record_external):
#: reported, not fixed here. (module, function): what reaches it.
KNOWN_GAPS = {
    ("services/platforms/youtube.py", "cancel_scheduled"):
        "cancels a scheduled YouTube publish; no caller in src yet, and no gate when one is wired",
}


def _name(node) -> str:
    try:
        return ast.unparse(node)
    except Exception:  # noqa: BLE001
        return ""


def _own_calls(fn) -> list:
    """The calls in `fn`'s own body (not in a nested def, lambda or class)."""
    out, stack = [], list(fn.body)
    while stack:
        n = stack.pop()
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda, ast.ClassDef)):
            continue
        if isinstance(n, ast.Call):
            out.append(n)
        stack.extend(ast.iter_child_nodes(n))
    return out


def functions_of(tree, rel: str) -> list:
    """(module, function name, calls, call names) for every def in `tree`."""
    out = []
    for fn in ast.walk(tree):
        if isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
            calls = _own_calls(fn)
            out.append((rel, fn.name, calls, [_name(c.func) for c in calls]))
    return out


def _functions() -> list:
    out = []
    for d in SCANNED:
        for p in sorted((SRC / d).rglob("*.py")):
            tree = ast.parse(p.read_text(encoding="utf-8-sig"))
            out.extend(functions_of(tree, p.relative_to(SRC).as_posix()))
    return out


def covered(fns: list) -> set:
    """(module, function) pairs that reach the gate (see the module docstring)."""
    defined: dict = {}
    for rel, name, _c, _n in fns:
        defined.setdefault(name, set()).add(rel)
    direct = {(rel, name) for rel, name, _c, names in fns
              if any(n.split(".")[-1] in GUARDS for n in names)}

    def targets(rel, n, pool):
        last = n.split(".")[-1]
        if "." not in n or last in COMMON:
            return {(rel, last)} & pool
        return {(r, last) for r in defined.get(last, ())} & pool

    out = set(direct)
    out |= {(rel, name) for rel, name, _c, names in fns if any(targets(rel, n, direct) for n in names)}
    out |= {(rel, name) for rel, name, _c, _n in fns if name.startswith("_tool_")}
    everything = {(rel, name) for rel, name, _c, _n in fns}
    for _ in range(DEPTH):
        grew = set()
        for rel, name, _c, names in fns:
            if (rel, name) in out:
                for n in names:
                    grew |= targets(rel, n, everything)
        if grew <= out:
            break
        out |= grew
    return out


def sink_kind(call, name: str):
    for kind, rx in SINKS:
        if rx.search(name):
            return kind
    if name.split(".")[-1] in WRAPPERS:
        a = call.args[0] if call.args else next(
            (k.value for k in call.keywords if k.arg == "method"), None)
        if isinstance(a, ast.Constant) and str(a.value).upper() in WRITE_METHODS:
            return "http_wrapper_write"
    return None


def sink_sites(fns=None) -> list:
    """(module, function, line, kind, call, reaches the gate) for every sink."""
    fns = _functions() if fns is None else fns
    ok = covered(fns)
    out = []
    for rel, name, calls, names in fns:
        for c, n in zip(calls, names):
            kind = sink_kind(c, n)
            if kind:
                out.append((rel, name, c.lineno, kind, n, (rel, name) in ok))
    return out


def _fmt(sites) -> str:
    return "\n".join("  %s:%d %s() %s via %s" % (r, ln, fn, k, n) for r, fn, ln, k, n, _ok in sites)


# ── the guard ────────────────────────────────────────────────────────────────

def test_every_outward_sink_reaches_the_gate():
    sites = sink_sites()
    assert sites, "the scan found no sink at all: the patterns no longer match the code"
    bad = [s for s in sites if not s[5] and (s[0], s[1]) not in ALLOWLIST
           and (s[0], s[1]) not in KNOWN_GAPS]
    assert not bad, (
        "outward or state-changing call sites that do not reach the action gate "
        "(authorize / authorize_external / record_external), nor are on the reviewed "
        "ALLOWLIST or KNOWN_GAPS:\n" + _fmt(bad))


def test_the_lists_name_only_sites_that_still_need_them():
    """An entry whose site is gone, or now reaches the gate, is removed."""
    open_sites = {(s[0], s[1]) for s in sink_sites() if not s[5]}
    stale = sorted(k for k in list(ALLOWLIST) + list(KNOWN_GAPS) if k not in open_sites)
    assert not stale, "remove these entries: %s" % stale
    assert not set(ALLOWLIST) & set(KNOWN_GAPS)
    assert all(str(v).strip() for v in list(ALLOWLIST.values()) + list(KNOWN_GAPS.values()))


def test_the_known_gated_paths_are_seen_as_gated():
    ok = {(s[0], s[1]) for s in sink_sites() if s[5]}
    for site in (("services/gmail_send.py", "send"), ("services/channels/base.py", "send"),
                 ("phone/service.py", "_send_sms_now"), ("services/calendar_write.py", "create_event")):
        assert site in ok, site


def test_the_takedown_route_reaches_the_gate():
    """The allowlisted platform takedowns rest on this: their one caller is gated."""
    fns = _functions()
    assert ("routes/content_pipeline.py", "content_post_delete") in covered(fns)
    callers = [(rel, name) for rel, name, _c, names in fns
               if any(n.endswith("adapter.delete") for n in names)]
    assert callers == [("routes/content_pipeline.py", "content_post_delete")], callers


def test_the_scan_flags_an_unguarded_sink_and_credits_a_guarded_one():
    src = (
        "import requests\n"
        "def loose(url):\n"
        "    return requests.post(url, data={})\n"
        "def gated(url):\n"
        "    action_gate.record_external('probe', surface='test')\n"
        "    return requests.delete(url)\n"
        "def helper(url):\n"
        "    return _http('PUT', url)\n"
        "def caller(url):\n"
        "    action_gate.authorize_external('probe', {}, requested_by='test')\n"
        "    return helper(url)\n"
        "def reader(url):\n"
        "    return _http('GET', url)\n")
    fns = functions_of(ast.parse(src), "services/probe.py")
    got = {(s[1], s[3], s[5]) for s in sink_sites(fns)}
    assert ("loose", "http_write", False) in got
    assert ("gated", "http_write", True) in got
    assert ("helper", "http_wrapper_write", True) in got          # reached from a gated caller
    assert not any(s[1] == "reader" for s in sink_sites(fns))     # a GET is not a write

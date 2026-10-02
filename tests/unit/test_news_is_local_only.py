"""News is local-only and costs nothing, on every path.

The owner's rule: every News path (the routines, the News buttons, podcast
scripts, the article deep dive, the read-aloud and Discuss) writes and speaks on
this computer. No cloud fallback, no Gemini, no paid API. A News run that cannot
reach the local seat waits and says so; it is never quietly sent elsewhere.

Two layers:
  * DISCOVERY: every function in the News modules that calls a model or voice
    entry point is inside the local-only guard, by decorator, by an enclosing
    `with local_news_run(...)` / `local_only(...)`, or by its only caller being
    guarded. A new call site that is not fails here by name.
  * RUNTIME: the guard refuses and logs at the transports, the voice included,
    and no setting or scheduled cloud pin lifts it for a News run.
"""
from __future__ import annotations

import ast
import io
from pathlib import Path

import pytest

from agent_friday.services import local_only_guard as guard

SRC = Path(__file__).resolve().parents[2] / "src" / "agent_friday"

NEWS_MODULES = [
    "services/news_engine.py",
    "services/podcast_news.py",
    "services/podcast_engine.py",
    "routes/news.py",
    "routes/podcasts.py",
]

#: Names whose call can reach a paid model or a cloud voice.
PAID_ENTRYPOINTS = {
    "_generate_text", "_generate_agent", "_call_claude", "_call_openai",
    "_call_gemini", "_synthesize_tts_wav", "_synthesize_tts_wav_gemini",
}

#: Functions guarded by their only caller: name -> the guarded caller.
COVERED_BY_CALLER = {
    "_editorialize_front_page": "_generate_front_page",
}

#: Reviewed exceptions, each with the rule that keeps News off it.
EXEMPT = {
    "services/podcast_engine.py:_cloud_speak.speak":
        "the owner's opt-in cloud voice for episodes they ask for; produce() "
        "refuses it for an episode attached to a News routine",
}

_GUARD_CONTEXTS = {"local_news_run", "local_only"}


def _call_name(node):
    f = node.func
    if isinstance(f, ast.Name):
        return f.id
    if isinstance(f, ast.Attribute):
        return f.attr
    return None


def _is_guarded_decorator(dec):
    target = dec.func if isinstance(dec, ast.Call) else dec
    name = target.id if isinstance(target, ast.Name) else getattr(target, "attr", None)
    return name == "_local_news"


def _paid_calls(fn):
    """(name, guarded) for every paid call lexically in `fn`, nested defs aside."""
    out = []

    def visit(node, guarded):
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
                continue
            g = guarded
            if isinstance(child, (ast.With, ast.AsyncWith)):
                for item in child.items:
                    ce = item.context_expr
                    if isinstance(ce, ast.Call) and _call_name(ce) in _GUARD_CONTEXTS:
                        g = True
            if isinstance(child, ast.Attribute) and child.attr in PAID_ENTRYPOINTS \
                    and not isinstance(getattr(child, "_parent_call", None), ast.Call):
                out.append((child.attr, g))
            if isinstance(child, ast.Call):
                name = _call_name(child)
                if name in PAID_ENTRYPOINTS:
                    out.append((name, g))
                    if isinstance(child.func, ast.Attribute):
                        child.func._parent_call = child
            visit(child, g)
    visit(fn, False)
    return out


def _only_called_directly(name, parent):
    """True when every use of `name` inside `parent` is a direct call: the
    nested def runs on the parent's thread, inside the parent's guard. Handed
    to a Thread or stored, it runs on its own terms."""
    uses = calls = 0
    for node in ast.walk(parent):
        if isinstance(node, ast.Name) and node.id == name:
            uses += 1
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)                 and node.func.id == name:
            calls += 1
    return uses == calls


def _functions(tree):
    """(qualname, def, guarded) for every function, nested ones included."""
    found = []

    def walk(node, prefix, parent, parent_guarded):
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                g = any(_is_guarded_decorator(d) for d in child.decorator_list)
                if parent is not None and parent_guarded                         and _only_called_directly(child.name, parent):
                    g = True
                q = prefix + child.name
                found.append((q, child, g))
                walk(child, q + ".", child, g)
            else:
                walk(child, prefix, parent, parent_guarded)
    walk(tree, "", None, False)
    return found


def _unguarded_paid_calls():
    offenders = []
    decorated = {}
    for rel in NEWS_MODULES:
        path = SRC / rel
        if not path.exists():
            continue
        tree = ast.parse(path.read_text(encoding="utf-8-sig"))
        for qual, fn, guarded in _functions(tree):
            decorated[qual] = decorated.get(qual) or guarded
            for name, in_with in _paid_calls(fn):
                if guarded or in_with:
                    continue
                key = f"{rel}:{qual}"
                if key in EXEMPT or qual in COVERED_BY_CALLER:
                    continue
                offenders.append(f"{key} -> {name} (line {fn.lineno})")
    return offenders, decorated


def test_every_news_model_or_voice_call_is_inside_the_local_only_guard():
    offenders, _ = _unguarded_paid_calls()
    assert not offenders, (
        "News code can reach a paid model or cloud voice outside the local-only "
        "guard. Wrap it in local_news_run(...) or decorate it with _local_news:\n  "
        + "\n  ".join(offenders))


def test_a_function_guarded_by_its_caller_really_is():
    _, decorated = _unguarded_paid_calls()
    src = (SRC / "services/news_engine.py").read_text(encoding="utf-8-sig")
    for inner, caller in COVERED_BY_CALLER.items():
        assert decorated.get(caller), f"{caller} (the guard for {inner}) is not guarded"
        assert f"{inner}(" in src.split(f"def {caller}(", 1)[1].split("\ndef ", 1)[0], (
            f"{caller} no longer calls {inner}, so it no longer guards it")


def test_the_podcast_cloud_voice_is_refused_for_a_news_episode():
    src = (SRC / "services/podcast_engine.py").read_text(encoding="utf-8")
    body = src.split("def produce(", 1)[1]
    cloud = body.split('if ep.get("voice_engine") == "cloud":', 1)[1][:400]
    assert '(ep.get("attached") or {}).get("routine")' in cloud and "PodcastRefused" in cloud


# ── runtime ───────────────────────────────────────────────────────────────────

def test_no_setting_turns_news_local_only_off(monkeypatch):
    import agent_friday.core as core
    from agent_friday.services import news_engine as ne
    monkeypatch.setattr(core, "_load_settings", lambda: {"news_local_only": False})
    with ne.local_news_run("Front Page"):
        assert guard.is_active() and not guard.pinned_model()


def test_a_scheduled_cloud_pin_does_not_reach_a_news_run():
    from agent_friday.services import news_engine as ne
    with guard.cloud_pinned("claude-haiku-4-5-20251001", "Scheduled"):
        with ne.local_news_run("Front Page"):
            assert guard.is_active() and not guard.pinned_model()
            with pytest.raises(guard.CloudRefused):
                guard.refuse_if_active("anthropic", "claude-haiku-4-5-20251001")
        assert guard.pinned_model() == "claude-haiku-4-5-20251001"


def test_gemini_tts_is_refused_and_logged_inside_a_news_run(caplog):
    from agent_friday.services import voice_engine as ve
    with guard.local_only("Front Page read-aloud"):
        with pytest.raises(guard.CloudRefused):
            ve._synthesize_tts_wav_gemini("hello")
    assert any("refused a cloud call" in r.getMessage() for r in caplog.records)


def test_the_read_aloud_speaks_locally_under_cloud_only_with_a_gemini_key(monkeypatch):
    from agent_friday.services import voice_engine as ve
    import agent_friday.core as core
    monkeypatch.setattr(ve, "_load_settings", lambda: {"model_routing": {"mode": "cloud_only"}})
    monkeypatch.setattr(core, "GEMINI_API_KEY", "set", raising=False)
    calls = []
    monkeypatch.setattr(ve, "_synthesize_tts_wav_gemini",
                        lambda *a, **k: calls.append("gemini") or io.BytesIO(b"g"))
    monkeypatch.setattr(ve, "_synthesize_tts_wav_local",
                        lambda text: calls.append("local") or io.BytesIO(b"l"))
    with guard.local_only("Front Page read-aloud"):
        out = ve._synthesize_tts_wav("The news.")
    assert calls == ["local"] and out.getvalue() == b"l"


def test_no_local_voice_is_a_visible_refusal_not_gemini(monkeypatch):
    from agent_friday.services import voice_engine as ve
    calls = []
    monkeypatch.setattr(ve, "_synthesize_tts_wav_gemini",
                        lambda *a, **k: calls.append("gemini") or io.BytesIO(b"g"))
    monkeypatch.setattr(ve, "_synthesize_tts_wav_local", lambda text: None)
    with guard.local_only("Front Page read-aloud"):
        with pytest.raises(guard.CloudRefused) as exc:
            ve._synthesize_tts_wav("The news.")
    assert calls == [] and "Front Page read-aloud" in str(exc.value)


@pytest.mark.parametrize("quick", [False, True])
def test_the_article_deep_dive_runs_local_only(monkeypatch, tmp_path, quick):
    """Both reads, the full one and the spoken quick one on its own thread."""
    from agent_friday.services import news_engine as ne
    seen = []

    def probe(*a, **k):
        seen.append((guard.is_active(), guard.pinned_model()))
        return '{"summary": "s", "implications": "i", "key_quotes": []}'
    monkeypatch.setattr(ne, "DEEP_DIVE_DIR", tmp_path)
    monkeypatch.setattr(ne, "_extract_article_text", lambda url: ("T", "word " * 100))
    monkeypatch.setattr(ne, "_get_friday_system_prompt", lambda *a, **k: "")
    monkeypatch.setattr(ne, "_predict_route_provider", lambda *a, **k: "cloud")
    monkeypatch.setattr(ne, "_gated_vault_control", lambda *a, **k: None)
    monkeypatch.setattr(ne, "_generate_text", probe)
    ne._deep_dive_article("https://example.com/a", title="A", refresh=True, quick=quick)
    assert seen == [(True, "")], seen

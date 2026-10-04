"""Reading a chat turn's prompt from the request that actually reaches the model.

`/api/chat` sends the per-turn context (clock, memories, wiki matches, plans,
projects, pinned situation...) at the top of the newest user turn, between
`chat.TURN_CONTEXT_OPEN` and `chat.TURN_CONTEXT_CLOSE`; the system prompt is
the stable head and does not change from turn to turn. `/api/chat/send` still
carries everything in the system prompt. These helpers let a test assert the
same content in whichever part of the request is the truth for its route, and
check that the context block is marked as Friday's, never as the user's words.
"""
from __future__ import annotations

import copy

from agent_friday.routes.chat import TURN_CONTEXT_CLOSE, TURN_CONTEXT_OPEN

#: The routes and seats a prompt test runs on. `/api/chat` is run on both the
#: cloud and the local seat, whose context blocks are shaped differently;
#: `/api/chat/send` keeps its default routing.
CHAT_ROUTES = [("/api/chat", "cloud"), ("/api/chat", "local"), ("/api/chat/send", None)]


def route_to(monkeypatch, provider):
    """Pin the router to the cloud or the local seat (None leaves it alone)."""
    if provider is None:
        return
    from agent_friday.routing.model_router import ModelRouter
    local = provider == "local"
    monkeypatch.setattr(ModelRouter, "route", lambda *a, **k: {
        "provider": provider, "model": "bonsai2:27b" if local else "claude-sonnet-5-5",
        "provider_name": "bonsai2-local" if local else "anthropic",
        "is_local": local, "vault_allowed": local, "vault_access": local,
        "scrub_pii": not local, "refuse": False, "warning": None,
    })


def capture_requests(patch_app):
    """Stub every model entry point and record each request as
    {"system": str, "messages": list}, exactly as it was handed over."""
    seen = []

    def fake_agent(messages, *a, **k):
        system = k.get("system") if "system" in k else (a[0] if a else "")
        seen.append({"system": system or "", "messages": copy.deepcopy(messages or [])})
        return ("ok", [])

    def fake_text(*a, **k):
        messages = k.get("messages") if "messages" in k else (a[0] if a else [])
        seen.append({"system": k.get("system") or "",
                     "messages": copy.deepcopy(messages if isinstance(messages, list) else [])})
        return "ok"

    for name in ("_generate_agent", "_call_claude_agent", "_oai_agentic_loop"):
        patch_app(name, fake_agent)
    for name in ("_generate_text", "_call_claude", "_call_ollama", "_call_openai"):
        patch_app(name, fake_text)
    return seen


def _text(content) -> str:
    if isinstance(content, str):
        return content
    return "\n".join(b.get("text", "") for b in (content or [])
                     if isinstance(b, dict) and b.get("type") == "text")


def turn_context(messages, said=None) -> str:
    """The per-turn context of the newest user turn, between its marks.

    Asserts the marking: the block opens with TURN_CONTEXT_OPEN (which says
    Friday retrieved it and the user did not write it) and closes with
    TURN_CONTEXT_CLOSE, and the user's own words (`said`) follow it, outside
    the block. On the cloud the block is its own text block; on the local
    seat it is the head of the message text.
    """
    users = [m for m in messages if isinstance(m, dict) and m.get("role") == "user"]
    assert users, "no user turn reached the model"
    content = users[-1].get("content")
    if isinstance(content, list):
        assert content and content[0].get("type") == "text", content
        block, after = content[0]["text"], _text(content[1:])
    else:
        content = content or ""
        cut = content.find(TURN_CONTEXT_CLOSE)
        assert cut >= 0, "the newest user turn carries no per-turn context: %r" % content[:200]
        block = content[:cut + len(TURN_CONTEXT_CLOSE)]
        after = content[cut + len(TURN_CONTEXT_CLOSE):]
    assert block.startswith(TURN_CONTEXT_OPEN), (
        "the per-turn context is not marked as Friday's: %r" % block[:120])
    assert block.rstrip().endswith(TURN_CONTEXT_CLOSE), "the per-turn context is not closed"
    if said is not None:
        assert after.strip() == said, "the user's words are not outside the context block"
    return block[len(TURN_CONTEXT_OPEN):block.rindex(TURN_CONTEXT_CLOSE)]


def _carries_context(request) -> bool:
    users = [m for m in request["messages"] if isinstance(m, dict) and m.get("role") == "user"]
    return bool(users) and _text(users[-1].get("content")).startswith(TURN_CONTEXT_OPEN)


def chat_requests(seen, route) -> list:
    """The turn's own model requests among everything the stubs recorded
    (a title or summary job can call a model too): on /api/chat those whose
    newest user turn carries the marked context, elsewhere those with a
    system prompt. Asserts there is at least one."""
    picked = [r for r in seen if (_carries_context(r) if route == "/api/chat" else r["system"])]
    assert picked, "the turn's prompt never reached the model"
    return picked


def whole_request(seen) -> str:
    """Every word every recorded request carried, system and messages alike.
    A NEGATIVE check reads this: on /api/chat the per-turn content is no
    longer in the system prompt, so "not in the system prompt" would pass
    whether or not the content leaked."""
    parts = []
    for r in seen:
        parts.append(r["system"] or "")
        parts.extend(_text(m.get("content")) for m in r["messages"] if isinstance(m, dict))
    return "\n".join(parts)


def turn_prompt(request, route, said=None) -> str:
    """The text a route carries its per-turn content in: the marked context
    block of the newest user turn on /api/chat, the system prompt elsewhere."""
    if route == "/api/chat":
        return turn_context(request["messages"], said=said)
    return request["system"]

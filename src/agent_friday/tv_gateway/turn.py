"""A paired TV's turn, answered by this PC (D3).

The TV sends a Claude Messages request: its own system prompt (Friday on the
TV, cards, its action catalogue, family or kids mode), its conversation, and
its catalogue as tools. The gateway answers in the Messages stream format, so
the TV reads it with the same reader it uses for Claude.

What a TV turn is NOT is the owner's turn (routes/chat.chat()):

  * No tool of this PC is offered or run. The TV's own catalogue is offered
    only to a device holding ``tv_brain``; the model's calls go back to the TV
    as ``tool_use`` blocks and the TV's ActionGate decides them. On a local
    seat or an OpenAI-compatible provider that is model_router.ONE_ROUND: one
    sealed, streamed round, and the loop that would run tools is skipped.
  * No owner context. The system prompt is the TV's, plus this PC's honest-
    limits block and a fixed note; no memory recall, vault, wiki, people or
    core memory. Which memories a household TV may ever see is the owner's
    decision (DECISIONS-FOR-STEPHEN), not this module's.
  * Nothing is written to the owner's conversation, recall index or summaries.
  * Kids: no tools at all, and the brain is Claude (the family prompt is
    tested with Claude only). Where Claude cannot be reached, the turn is
    refused with 503 and the TV answers with its own Claude.

Every guard a model call passes on this PC still applies: the routing mode
(local-only / cloud intent), the egress seal on every cloud leg, provider
health, the visible job, global stop. Each turn leaves one ledger row naming
the device, the brain and the proposals (never the words).
"""
from __future__ import annotations

import json
import logging
import queue
import threading
from typing import Iterator, Optional

from agent_friday.services import device_pairing

_log = logging.getLogger(__name__)

PRINCIPALS = ("grownup", "family", "kids")
MAX_TOKENS_CAP = 32000

GATEWAY_NOTE = (
    "\n\n== ANSWERING FROM THE HOUSEHOLD'S FRIDAY COMPUTER ==\n"
    "This turn is answered by Friday on the household's computer for the TV in "
    "the room. Only the actions listed for the TV exist in this turn; the "
    "computer's own tools, mail, files and private memories are not available "
    "here, so never claim to have used them.\n")


class TurnRefused(Exception):
    """The turn cannot run; `status` is the HTTP code the TV acts on (503 means
    "answer with your own brain")."""

    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status


def principal_from(header: Optional[str]) -> str:
    """The TV's word for who is watching. Anything unknown is treated as kids:
    the strictest reading, never the loosest."""
    p = str(header or "").strip().lower()
    return p if p in PRINCIPALS else "kids"


def system_for(tv_system: str) -> str:
    from agent_friday.services.model_router import honest_limits_block
    return str(tv_system or "") + honest_limits_block() + GATEWAY_NOTE


def tools_for(dev: dict, principal: str, tools) -> list:
    """The TV's catalogue as offered to the model, or nothing."""
    if principal == "kids" or not device_pairing.has_right(dev, "tv_brain"):
        return []
    out = []
    for t in tools or []:
        if isinstance(t, dict) and isinstance(t.get("name"), str):
            out.append({"name": t["name"], "description": str(t.get("description", ""))[:2000],
                        "input_schema": t.get("input_schema") or {"type": "object", "properties": {}}})
    return out


# ── Anthropic blocks <-> OpenAI messages ─────────────────────────────────────

def to_openai(messages) -> list:
    """The TV's Messages-API conversation as OpenAI chat messages (tool rounds
    included), for the local and OpenAI-compatible legs."""
    out = []
    for m in messages or []:
        role, content = m.get("role"), m.get("content")
        if isinstance(content, str):
            out.append({"role": role, "content": content})
            continue
        texts, calls, results = [], [], []
        for b in content or []:
            kind = b.get("type")
            if kind == "text":
                texts.append(b.get("text", ""))
            elif kind == "tool_use":
                calls.append({"id": b.get("id"), "type": "function",
                              "function": {"name": b.get("name"),
                                           "arguments": json.dumps(b.get("input") or {})}})
            elif kind == "tool_result":
                c = b.get("content")
                results.append({"role": "tool", "tool_call_id": b.get("tool_use_id"),
                                "content": c if isinstance(c, str) else json.dumps(c)})
        if role == "assistant":
            msg = {"role": "assistant", "content": "".join(texts)}
            if calls:
                msg["tool_calls"] = calls
            out.append(msg)
        else:
            out.extend(results)
            if texts:
                out.append({"role": role, "content": "".join(texts)})
    return out


def _plain(messages) -> list:
    """Text-only view of the conversation, for the router's classifier."""
    out = []
    for m in to_openai(messages):
        if m.get("role") in ("user", "assistant") and isinstance(m.get("content"), str):
            out.append({"role": m["role"], "content": m["content"]})
    return out


def _event(obj: dict) -> str:
    return "event: %s\ndata: %s\n\n" % (obj.get("type", "message"), json.dumps(obj, separators=(",", ":")))


def openai_round_events(deltas: Iterator[str], finished, model: str) -> Iterator[str]:
    """Messages-stream events for one OpenAI-format round: the text as it
    streams, then each tool call as a tool_use block, then why it stopped.
    `finished()` gives the round's assistant message once the deltas have
    drained, or raises the round's error."""
    yield _event({"type": "message_start", "message": {"role": "assistant", "model": model, "content": []}})
    yield _event({"type": "content_block_start", "index": 0, "content_block": {"type": "text", "text": ""}})
    streamed = []
    for d in deltas:
        streamed.append(d)
        yield _event({"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": d}})
    message = finished() or {}
    final = message.get("content") or ""
    if not streamed and final:
        yield _event({"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": final}})
    yield _event({"type": "content_block_stop", "index": 0})
    calls = message.get("tool_calls") or []
    for i, c in enumerate(calls, start=1):
        fn = c.get("function") or {}
        yield _event({"type": "content_block_start", "index": i, "content_block": {
            "type": "tool_use", "id": c.get("id") or ("tu_%d" % i), "name": fn.get("name"), "input": {}}})
        args = fn.get("arguments")
        yield _event({"type": "content_block_delta", "index": i, "delta": {
            "type": "input_json_delta",
            "partial_json": args if isinstance(args, str) else json.dumps(args or {})}})
        yield _event({"type": "content_block_stop", "index": i})
    yield _event({"type": "message_delta", "delta": {"stop_reason": "tool_use" if calls else "end_turn"}})
    yield _event({"type": "message_stop"})


# ── the turn ─────────────────────────────────────────────────────────────────

def _route(principal: str, plain: list, has_tools: bool) -> dict:
    if principal == "kids":
        return {"provider": "cloud", "model": None, "why": "kids: Claude"}
    from agent_friday.core import _load_settings
    from agent_friday.routing.model_router import get_router
    cfg = (_load_settings() or {}).get("model_routing") or {}
    route = get_router(cfg).route(plain, task_context={
        "has_tools": has_tools, "workspace": "tv", "is_voice": True}) or {}
    if route.get("local_unavailable") or route.get("refuse"):
        raise TurnRefused(503, "this computer has no model it may use for the TV right now")
    return route


def _cloud_round(system: str, messages: list, tools: list, max_tokens: int) -> Iterator[str]:
    """One Claude round with the same choke points as model_router._call_claude,
    streamed to the TV event by event."""
    from agent_friday.services import model_router as mr
    from agent_friday.services.local_only_guard import apply_pin, refuse_if_active
    from agent_friday.core import _load_settings, ANTHROPIC_MODEL_DEFAULT
    try:
        refuse_if_active("anthropic", "")
        mr._cloud_intent.require("anthropic", "TV turn (Claude)", "")
    except Exception as e:
        raise TurnRefused(503, "Claude may not be used from this computer right now (%s)" % type(e).__name__)
    client = mr.get_anthropic_client()
    if client is None:
        raise TurnRefused(503, "this computer has no Claude key")
    settings = _load_settings() or {}
    model = apply_pin("anthropic", mr._claude_safe_model(settings.get("orchestrator_model"), settings)
                      or ANTHROPIC_MODEL_DEFAULT)
    kwargs = {"model": model, "max_tokens": max_tokens, "messages": messages, "system": system}
    if tools:
        kwargs["tools"] = tools
    try:
        kwargs = mr._seal_or_block(kwargs, "anthropic")
    except Exception as e:                         # a privacy hold or a spend stop: the TV answers itself
        raise TurnRefused(503, "this computer held the TV's request (%s)" % type(e).__name__)
    from agent_friday.services.visible_jobs import visible_job
    with visible_job("TV turn", kind="tv_turn", label="Answering the TV", model=model):
        with client.messages.stream(**kwargs) as stream:
            for ev in stream:
                try:
                    d = ev.model_dump(mode="json", exclude_none=True)
                except Exception:
                    continue
                if d.get("type") in ("message_start", "content_block_start", "content_block_delta",
                                     "content_block_stop", "message_delta", "message_stop"):
                    yield _event(d)


def _local_round(provider: str, model: Optional[str], provider_name: Optional[str], system: str,
                 messages: list, tools: list, max_tokens: int) -> Iterator[str]:
    """One round on a local seat or an OpenAI-compatible provider, tool calls
    returned unexecuted (model_router.ONE_ROUND), text streamed as it comes."""
    from agent_friday.services import model_router as mr
    q: "queue.Queue" = queue.Queue()
    cap: dict = {}
    box: dict = {}
    _END = object()

    def run():
        t1 = mr.ONE_ROUND.set(cap)
        t2 = mr.DELTA_SINK.set(lambda d: q.put(d) if d else None)
        try:
            from agent_friday.services.visible_jobs import visible_job
            call = mr._call_ollama if provider == "local" else mr._call_openai
            kw = dict(system=system, model=model, max_tokens=max_tokens, tools=tools or None)
            if provider != "local" and provider_name:
                kw["provider"] = provider_name
            with visible_job("TV turn", kind="tv_turn", label="Answering the TV", model=model):
                call(to_openai(messages), **kw)
        except Exception as e:                     # reported once the stream drains
            box["error"] = e
        finally:
            mr.DELTA_SINK.reset(t2)
            mr.ONE_ROUND.reset(t1)
            q.put(_END)

    import contextvars
    threading.Thread(target=contextvars.copy_context().run, args=(run,), name="tv-turn", daemon=True).start()

    def deltas():
        while True:
            d = q.get()
            if d is _END:
                return
            yield d

    def finished():
        if "error" in box:
            raise TurnRefused(503, "the model on this computer did not answer (%s)" % type(box["error"]).__name__)
        return cap.get("message") or {}

    yield from openai_round_events(deltas(), finished, str(model or ""))


def run(dev: dict, body: dict, principal: str) -> Iterator[str]:
    """Validate, route and run one TV turn. Raises TurnRefused before the
    first event; after that the stream is the turn."""
    from agent_friday.services import global_stop
    try:
        global_stop.raise_if_stopped("a TV turn")
    except Exception:
        raise TurnRefused(503, "everything is stopped on this computer")
    messages = body.get("messages")
    if not isinstance(messages, list) or not messages:
        raise TurnRefused(400, "no conversation")
    tools = tools_for(dev, principal, body.get("tools"))
    system = system_for(body.get("system", ""))
    max_tokens = max(256, min(int(body.get("max_tokens") or 4096), MAX_TOKENS_CAP))
    route = _route(principal, _plain(messages), bool(tools))
    provider = route.get("provider", "cloud")
    _row(dev, principal, provider, route.get("model"), tools)
    if provider == "cloud":
        return _cloud_round(system, messages, tools, max_tokens)
    return _local_round(provider, route.get("model"), route.get("provider_name"),
                        system, messages, tools, max_tokens)


def _row(dev: dict, principal: str, provider: str, model, tools: list) -> None:
    device_pairing._receipt("device_turn", dev, watching=principal, provider=provider,
                            model=str(model or ""), tools_offered=len(tools))

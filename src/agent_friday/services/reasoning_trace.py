"""Reasoning traces: live visibility and a tamper-evident archive.

Every model call that produces reasoning writes it here, whatever started the
call: a chat turn, a subagent, a scheduled job, a background tool. A trace is
one unit of work (a turn, a subagent task, a briefing run) and holds, in order,
the reasoning text of each model call, the tool calls with their arguments and
result summaries, and the model/seat that produced each piece.

Two consumers:

* **Live.** `feed(since)` returns every event after a cursor, across all traces,
  so the tray can spool reasoning while it is produced. Reasoning deltas are
  coalesced per trace before they enter the feed (a token-per-event feed would
  make the UI do thousands of DOM updates a second for a fast local model).

* **Archive.** When a trace finishes it is appended to
  `<friday_home>/traces/ledger.jsonl`. Each line carries the record encrypted
  through `credential_store.protect` (the keystore), a SHA-256 hash chained to
  the previous line, and an HMAC over that hash. The HMAC key is derived from
  the file-grants ledger's persisted signing key, which is minted once and never
  read from the environment, so a restart or a launcher change cannot move it.
  A metadata-only `reasoning_trace` row goes to the activity ledger so the
  global "what has Friday been doing" list includes it.

HONESTY ABOUT WHAT IS SHOWN. Each reasoning segment is labelled with where it
came from (`SOURCE_*`). A cloud model that returns no reasoning is recorded as
"reasoning not exposed by provider", never filled in. Nothing in this module
generates, paraphrases or summarises reasoning; it stores what the provider
returned, byte for byte.

NOTHING LEAVES THE MACHINE. This module has no network code. The archive is
read only by the local routes in `routes/traces.py`.

Failures here never break a model call: every public function is best-effort.
The exception is the archive's refusal to write plaintext -- if the keystore is
locked, finished traces wait in memory (bounded) and are written on the next
successful write, rather than landing on disk unencrypted.
"""

from __future__ import annotations

import base64
import contextlib
import contextvars
import hashlib
import hmac
import json
import logging
import re
import threading
import time
import uuid
from collections import deque
from pathlib import Path
from typing import Any, Dict, Iterable, Iterator, List, Optional

_log = logging.getLogger(__name__)

# ── source labels ────────────────────────────────────────────────────────────
# What a reasoning segment actually is. The UI shows LABELS verbatim.
SOURCE_FULL = "full"              # the model's own reasoning stream (local weights)
SOURCE_SUMMARY = "summary"        # the provider returned a summary of its reasoning
SOURCE_PROVIDER = "provider"      # a cloud provider returned reasoning text; whether it is full or summarised is the provider's to say
SOURCE_NOT_EXPOSED = "not_exposed"  # the provider reasoned (or may have) and returned nothing readable
SOURCE_REDACTED = "redacted"      # the provider returned an encrypted/redacted block
SOURCE_NONE = "none"              # the model produced no reasoning for this call

LABELS = {
    SOURCE_FULL: "full reasoning (local)",
    SOURCE_SUMMARY: "provider's reasoning summary",
    SOURCE_PROVIDER: "reasoning as returned by provider (may be summarized)",
    SOURCE_NOT_EXPOSED: "reasoning not exposed by provider",
    SOURCE_REDACTED: "reasoning not exposed by provider (redacted/encrypted)",
    SOURCE_NONE: "no reasoning produced",
}

# Most-informative-first, for picking a trace's headline label.
_SOURCE_RANK = [SOURCE_FULL, SOURCE_PROVIDER, SOURCE_SUMMARY, SOURCE_REDACTED, SOURCE_NOT_EXPOSED, SOURCE_NONE]

# ── limits ───────────────────────────────────────────────────────────────────
_MAX_TRACE_REASONING_CHARS = 2_000_000   # per trace; beyond this we record that we stopped
_MAX_ARGS_CHARS = 4000
_MAX_RESULT_CHARS = 2000
_MAX_EVENTS_PER_TRACE = 20_000
_FEED_MAX = 50_000                       # global live ring
_COALESCE_CHARS = 240                    # flush a delta buffer at this size...
_COALESCE_SECONDS = 0.15                 # ...or after this long
_FINISHED_KEEP_S = 600                   # finished traces stay in the live view this long
_PENDING_MAX = 500                       # traces waiting for the keystore

# ── state ────────────────────────────────────────────────────────────────────
_LOCK = threading.RLock()
_TRACES: Dict[str, Dict[str, Any]] = {}      # trace_id -> live trace (running + recently finished)
_BUFFERS: Dict[str, Dict[str, Any]] = {}     # trace_id -> pending reasoning delta
_FEED: deque = deque(maxlen=_FEED_MAX)       # (seq, trace_id, event)
_SEQ = [0]
_CURRENT: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar(
    "friday_reasoning_trace", default=None)
_THREAD = threading.local()                  # fallback for code that crosses threads without context

BASE_DIR_OVERRIDE: Optional[Path] = None     # tests


def _now() -> float:
    return time.time()


def _next_seq() -> int:
    _SEQ[0] += 1
    return _SEQ[0]


def _clip(value: Any, cap: int) -> str:
    if value is None:
        return ""
    if not isinstance(value, str):
        try:
            value = json.dumps(value, default=str, ensure_ascii=False)
        except Exception:
            value = str(value)
    if len(value) > cap:
        return value[:cap] + "... [%d more chars not kept]" % (len(value) - cap)
    return value


# ── settings ─────────────────────────────────────────────────────────────────

def settings() -> Dict[str, Any]:
    """The `reasoning_traces` settings block with defaults applied. Never raises."""
    try:
        from agent_friday.core import _load_settings, DEFAULT_SETTINGS
        base = dict(DEFAULT_SETTINGS.get("reasoning_traces") or {})
        base.update((_load_settings() or {}).get("reasoning_traces") or {})
        return base
    except Exception:
        return {"capture": True, "retention_days": 0}


def _capture_enabled() -> bool:
    return bool(settings().get("capture", True))


# ── context ──────────────────────────────────────────────────────────────────

def current() -> Optional[str]:
    """The trace the calling code is running under, if any."""
    tid = _CURRENT.get()
    if tid:
        return tid
    return getattr(_THREAD, "trace_id", None)


@contextlib.contextmanager
def activate(trace_id: Optional[str]) -> Iterator[Optional[str]]:
    """Run a block under `trace_id`. Set as a contextvar (follows
    `copy_context().run` and async code) and as a thread-local (for code on
    the same thread that runs in a fresh context). A new thread sees neither:
    work handed to another thread must carry the id itself, as
    `_spawn_task` does."""
    token = _CURRENT.set(trace_id)
    prev = getattr(_THREAD, "trace_id", None)
    _THREAD.trace_id = trace_id
    try:
        yield trace_id
    finally:
        _CURRENT.reset(token)
        _THREAD.trace_id = prev


# ── live trace lifecycle ─────────────────────────────────────────────────────

def start(kind: str, label: str = "", *, model: str = None, seat: str = None,
          provider: str = None, task_id: str = None, parent_id: Any = "auto",
          turn_id: str = None, trace_id: str = None) -> Optional[str]:
    """Open a trace. `parent_id="auto"` nests it under the caller's current
    trace (a subagent under its turn). Returns the trace id, or None when
    capture is off."""
    try:
        if not _capture_enabled():
            return None
        if parent_id == "auto":
            parent_id = current()
        tid = trace_id or ("tr_" + uuid.uuid4().hex[:16])
        with _LOCK:
            parent = _TRACES.get(parent_id) if parent_id else None
            root_id = (parent or {}).get("root_id") or parent_id or tid
            tr = {
                "trace_id": tid, "parent_id": parent_id, "root_id": root_id,
                "kind": kind or "background", "label": _clip(label, 200),
                "model": model, "seat": seat, "provider": provider,
                "models": [], "task_id": task_id, "turn_id": turn_id,
                "status": "running", "started": _now(), "ended": None,
                "events": [], "sources": [],
                "tokens": {"in": 0, "out": 0, "reasoning": 0},
                "reasoning_chars": 0, "tool_calls": 0, "truncated": False,
                "archived": None,
            }
            if model:
                tr["models"].append(model)
            _TRACES[tid] = tr
            _emit_locked(tid, {"type": "start", "trace": _header(tr)})
        return tid
    except Exception as e:  # never break the caller
        _log.debug("reasoning_trace.start failed: %s", e)
        return None


def _header(tr: Dict[str, Any]) -> Dict[str, Any]:
    return {k: tr.get(k) for k in (
        "trace_id", "parent_id", "root_id", "kind", "label", "model", "seat",
        "provider", "models", "task_id", "turn_id", "status", "started", "ended",
        "sources", "tokens", "reasoning_chars", "tool_calls", "truncated", "archived")}


def _emit_locked(trace_id: str, event: Dict[str, Any]) -> None:
    seq = _next_seq()
    event = dict(event)
    event["seq"] = seq
    event.setdefault("t", _now())
    _FEED.append((seq, trace_id, event))


def _append_event_locked(tr: Dict[str, Any], event: Dict[str, Any]) -> None:
    if len(tr["events"]) >= _MAX_EVENTS_PER_TRACE:
        tr["truncated"] = True
        return
    event.setdefault("t", _now())
    tr["events"].append(event)


def _flush_buffer_locked(trace_id: str) -> None:
    buf = _BUFFERS.pop(trace_id, None)
    if not buf or not buf["text"]:
        return
    _emit_locked(trace_id, {"type": "reasoning_delta", "text": buf["text"],
                            "source": buf["source"], "model": buf.get("model")})


def _flush_all_locked(force: bool = False) -> None:
    now = _now()
    for tid in list(_BUFFERS.keys()):
        buf = _BUFFERS[tid]
        if force or now - buf["since"] >= _COALESCE_SECONDS:
            _flush_buffer_locked(tid)


def _resolve(trace_id: Optional[str]) -> Optional[Dict[str, Any]]:
    tid = trace_id or current()
    if not tid:
        return None
    return _TRACES.get(tid)


def _note_source_locked(tr: Dict[str, Any], source: str) -> None:
    if source and source not in tr["sources"]:
        tr["sources"].append(source)


def reasoning(text: str, source: str = SOURCE_FULL, *, trace_id: str = None,
              model: str = None) -> None:
    """Append reasoning text exactly as the provider returned it. Called per
    streamed delta or once per complete block; consecutive calls with the same
    source and model extend one segment."""
    if not text:
        return
    try:
        with _LOCK:
            tr = _resolve(trace_id)
            if tr is None or tr["status"] != "running":
                return
            tid = tr["trace_id"]
            if tr["reasoning_chars"] >= _MAX_TRACE_REASONING_CHARS:
                if not tr["truncated"]:
                    tr["truncated"] = True
                    _append_event_locked(tr, {"type": "note", "text":
                        "reasoning beyond %d characters was not kept" % _MAX_TRACE_REASONING_CHARS})
                return
            model = model or tr.get("model")
            _note_source_locked(tr, source)
            last = tr["events"][-1] if tr["events"] else None
            if (last and last.get("type") == "reasoning" and last.get("source") == source
                    and last.get("model") == model):
                last["text"] += text
            else:
                _append_event_locked(tr, {"type": "reasoning", "text": text,
                                          "source": source, "model": model})
            tr["reasoning_chars"] += len(text)
            buf = _BUFFERS.get(tid)
            if buf and (buf["source"] != source or buf.get("model") != model):
                _flush_buffer_locked(tid)
                buf = None
            if buf is None:
                buf = _BUFFERS[tid] = {"text": "", "source": source, "model": model,
                                       "since": _now()}
            buf["text"] += text
            if len(buf["text"]) >= _COALESCE_CHARS or _now() - buf["since"] >= _COALESCE_SECONDS:
                _flush_buffer_locked(tid)
    except Exception as e:
        _log.debug("reasoning_trace.reasoning failed: %s", e)


def mark_source(source: str, *, trace_id: str = None, model: str = None,
                detail: str = None) -> None:
    """Record a model call whose reasoning is not readable (not exposed,
    redacted, or none produced) so the trace says so instead of staying
    silent. Emits a visible marker event."""
    try:
        with _LOCK:
            tr = _resolve(trace_id)
            if tr is None or tr["status"] != "running":
                return
            _note_source_locked(tr, source)
            model = model or tr.get("model")
            last = tr["events"][-1] if tr["events"] else None
            if (last and last.get("type") == "source_marker" and last.get("source") == source
                    and last.get("model") == model):
                last["count"] = last.get("count", 1) + 1
                return
            ev = {"type": "source_marker", "source": source, "model": model,
                  "label": LABELS.get(source, source), "count": 1}
            if detail:
                ev["detail"] = _clip(detail, 200)
            _append_event_locked(tr, ev)
            _flush_buffer_locked(tr["trace_id"])
            _emit_locked(tr["trace_id"], dict(ev))
    except Exception as e:
        _log.debug("reasoning_trace.mark_source failed: %s", e)


def model_call(model: str = None, *, seat: str = None, provider: str = None,
               trace_id: str = None, tokens_in: int = None, tokens_out: int = None,
               reasoning_tokens: int = None) -> None:
    """Record one completed model call's identity and token counts."""
    try:
        with _LOCK:
            tr = _resolve(trace_id)
            if tr is None:
                return
            if model:
                if not tr.get("model"):
                    tr["model"] = model
                if model not in tr["models"]:
                    tr["models"].append(model)
            if seat and not tr.get("seat"):
                tr["seat"] = seat
            if provider and not tr.get("provider"):
                tr["provider"] = provider
            for key, val in (("in", tokens_in), ("out", tokens_out), ("reasoning", reasoning_tokens)):
                if isinstance(val, (int, float)) and val > 0:
                    tr["tokens"][key] += int(val)
            _append_event_locked(tr, {"type": "model_call", "model": model, "seat": seat,
                                      "provider": provider, "tokens_in": tokens_in,
                                      "tokens_out": tokens_out,
                                      "reasoning_tokens": reasoning_tokens})
            _flush_buffer_locked(tr["trace_id"])
            _emit_locked(tr["trace_id"], {"type": "model_call", "model": model, "seat": seat,
                                          "tokens": dict(tr["tokens"])})
    except Exception as e:
        _log.debug("reasoning_trace.model_call failed: %s", e)


def tool_call(name: str, args: Any = None, *, trace_id: str = None) -> Optional[str]:
    """Record a tool call in order with the reasoning. Returns a call id for
    `tool_result`."""
    try:
        with _LOCK:
            tr = _resolve(trace_id)
            if tr is None or tr["status"] != "running":
                return None
            call_id = "tc_" + uuid.uuid4().hex[:10]
            tr["tool_calls"] += 1
            ev = {"type": "tool_call", "call_id": call_id, "name": str(name or "?"),
                  "args": _clip(args, _MAX_ARGS_CHARS)}
            _flush_buffer_locked(tr["trace_id"])
            _append_event_locked(tr, ev)
            _emit_locked(tr["trace_id"], dict(ev))
            return call_id
    except Exception as e:
        _log.debug("reasoning_trace.tool_call failed: %s", e)
        return None


def tool_result(call_id: Optional[str], result: Any = None, *, ok: bool = True,
                trace_id: str = None, duration_ms: int = None) -> None:
    try:
        with _LOCK:
            tr = _resolve(trace_id)
            if tr is None or not call_id:
                return
            ev = {"type": "tool_result", "call_id": call_id, "ok": bool(ok),
                  "summary": _clip(result, _MAX_RESULT_CHARS), "duration_ms": duration_ms}
            _append_event_locked(tr, ev)
            _emit_locked(tr["trace_id"], dict(ev))
    except Exception as e:
        _log.debug("reasoning_trace.tool_result failed: %s", e)


_OPEN_CALLS: Dict[tuple, List[str]] = {}   # (trace_id, tool name) -> open call ids, oldest first


def tool_started(name: str, args: Any = None) -> Optional[str]:
    """A tool is about to execute under the current trace. Paired with
    `tool_finished` by name, so the live view shows the call while it runs."""
    tid = current()
    cid = tool_call(name, args, trace_id=tid)
    if cid and tid:
        with _LOCK:
            _OPEN_CALLS.setdefault((tid, str(name)), []).append(cid)
    return cid


def tool_finished(name: str, args: Any = None, result: Any = None, *, ok: bool = True,
                  duration_ms: int = None) -> None:
    """A tool finished (or was denied). Closes the matching open call, or
    records call and result together when nothing opened one (a denial)."""
    tid = current()
    if not tid:
        return
    with _LOCK:
        opened = _OPEN_CALLS.get((tid, str(name))) or []
        cid = opened.pop(0) if opened else None
        if not opened:
            _OPEN_CALLS.pop((tid, str(name)), None)
    if cid is None:
        cid = tool_call(name, args, trace_id=tid)
    tool_result(cid, result, ok=ok, trace_id=tid, duration_ms=duration_ms)


def _reasoning_tokens(usage: Any) -> Optional[int]:
    try:
        det = (usage or {}).get("completion_tokens_details") or {}
        val = det.get("reasoning_tokens")
        return int(val) if val is not None else None
    except Exception:
        return None


def after_oai_round(resp: Dict[str, Any], msg: Dict[str, Any], *, model: str = None,
                    seat: str = None, provider: str = None, local: bool = False) -> None:
    """Record one OpenAI-format round (Ollama, llama-server, OpenRouter...).

    Reasoning streamed by the transport is already in the trace; a
    non-streamed round's reasoning is added whole here. A round with no
    reasoning is labelled: `none` for local weights (the model did not
    reason), `not_exposed` for a cloud provider (we cannot tell), `redacted`
    when the provider sent only encrypted reasoning details.
    """
    if not current():
        return
    try:
        usage = resp.get("usage") or {}
        think = msg.get("reasoning_content") or msg.get("reasoning")
        if think and not isinstance(think, str):
            think = None
        src = SOURCE_FULL if local else SOURCE_PROVIDER
        rtok = _reasoning_tokens(usage)
        if think:
            if not resp.get("_reasoning_traced"):
                reasoning(think, src, model=model)
        else:
            details = msg.get("reasoning_details") or []
            encrypted = any(isinstance(d, dict) and "encrypted" in str(d.get("type") or "")
                            for d in details)
            if encrypted:
                mark_source(SOURCE_REDACTED, model=model)
            elif local or rtok == 0:
                mark_source(SOURCE_NONE, model=model)
            else:
                mark_source(SOURCE_NOT_EXPOSED, model=model,
                            detail=("%d reasoning tokens billed" % rtok) if rtok else None)
        model_call(model, seat=seat, provider=provider,
                   tokens_in=usage.get("prompt_tokens"), tokens_out=usage.get("completion_tokens"),
                   reasoning_tokens=rtok)
        content = (msg.get("content") or "").strip() if isinstance(msg.get("content"), str) else ""
        if content and msg.get("tool_calls"):
            note(content)          # what the model said before calling its tools
    except Exception as e:
        _log.debug("reasoning_trace.after_oai_round failed: %s", e)


def anthropic_thinking(model: str) -> Optional[Dict[str, str]]:
    """The `thinking` request parameter that makes a Claude model return a
    readable summary of its reasoning, or None to leave the request as is.

    Only for the Claude 5 generation, where thinking is already on by
    default and `display` defaults to "omitted" (empty thinking text). Asking
    for "summarized" changes what comes back, not how much the model thinks or
    what it costs. Older models (Opus 4.x, Haiku) are left alone: turning
    thinking on there would change their behaviour and their bill.
    """
    m = (model or "").lower()
    if re.match(r"^(anthropic[./])?claude-(opus|sonnet|fable|mythos)-5", m):
        return {"type": "adaptive", "display": "summarized"}
    return None


def after_anthropic_response(resp: Any, *, model: str = None, seat: str = None,
                             provider: str = "anthropic", thinking_requested: bool = True) -> None:
    """Record one Anthropic Messages response, block by block.

    Claude models from the 4.7/5 generations never return the raw chain of
    thought: a `thinking` block carries the provider's summary when
    `display: "summarized"` was requested and an empty string otherwise;
    `redacted_thinking` carries only an encrypted payload. Each case gets its
    own label; the encrypted data itself is not stored.
    """
    if not current():
        return
    try:
        saw_thinking = False
        blocks = list(getattr(resp, "content", None) or [])
        has_tool = any(getattr(b, "type", None) == "tool_use" for b in blocks)
        for b in blocks:
            btype = getattr(b, "type", None)
            if btype == "thinking":
                saw_thinking = True
                text = getattr(b, "thinking", "") or ""
                if text.strip():
                    reasoning(text, SOURCE_SUMMARY, model=model)
                else:
                    mark_source(SOURCE_NOT_EXPOSED, model=model,
                                detail="thinking block returned without text")
            elif btype == "redacted_thinking":
                saw_thinking = True
                mark_source(SOURCE_REDACTED, model=model)
            elif btype == "text" and has_tool:
                note(getattr(b, "text", "") or "")
        if not saw_thinking:
            if thinking_requested:
                mark_source(SOURCE_NOT_EXPOSED, model=model,
                            detail="no thinking block in the response")
            else:
                mark_source(SOURCE_NONE, model=model,
                            detail="extended thinking is not enabled for this model")
        usage = getattr(resp, "usage", None)
        model_call(model, seat=seat, provider=provider,
                   tokens_in=getattr(usage, "input_tokens", None),
                   tokens_out=getattr(usage, "output_tokens", None))
    except Exception as e:
        _log.debug("reasoning_trace.after_anthropic_response failed: %s", e)


def note(text: str, *, trace_id: str = None) -> None:
    """A short visible line in the trace (e.g. the intent the loop announced
    before a tool call). Never used to stand in for reasoning."""
    if not text:
        return
    try:
        with _LOCK:
            tr = _resolve(trace_id)
            if tr is None or tr["status"] != "running":
                return
            ev = {"type": "note", "text": _clip(text, 1000)}
            _flush_buffer_locked(tr["trace_id"])
            _append_event_locked(tr, ev)
            _emit_locked(tr["trace_id"], dict(ev))
    except Exception as e:
        _log.debug("reasoning_trace.note failed: %s", e)


def update(trace_id: str = None, **fields) -> None:
    """Fill in header fields learned after start (task_id, label, seat...)."""
    allowed = {"label", "model", "seat", "provider", "task_id", "turn_id", "kind"}
    try:
        with _LOCK:
            tr = _resolve(trace_id)
            if tr is None:
                return
            changed = False
            for k, v in fields.items():
                if k in allowed and v is not None and tr.get(k) != v:
                    tr[k] = _clip(v, 200) if k == "label" else v
                    changed = True
            if fields.get("model") and fields["model"] not in tr["models"]:
                tr["models"].append(fields["model"])
            if changed:
                _emit_locked(tr["trace_id"], {"type": "header", "trace": _header(tr)})
    except Exception as e:
        _log.debug("reasoning_trace.update failed: %s", e)


def finish(trace_id: str = None, status: str = "complete", *, reply: str = None) -> Optional[Dict[str, Any]]:
    """Close a trace and archive it. Returns the archive receipt
    ({seq, hash}) or None when it could not be written yet."""
    try:
        with _LOCK:
            tr = _resolve(trace_id)
            if tr is None or tr["status"] != "running":
                return None
            _flush_buffer_locked(tr["trace_id"])
            tr["status"] = status or "complete"
            tr["ended"] = _now()
            if reply:
                tr["reply_excerpt"] = _clip(reply, 1000)
            record = _record_from(tr)
            _emit_locked(tr["trace_id"], {"type": "end", "trace": _header(tr)})
            _gc_locked()
            empty = not tr["events"] and not _has_children_locked(tr["trace_id"])
        if empty:
            # Nothing ran a model or a tool (a cleanup job, a refused empty
            # message): there is no reasoning to keep, and an archive full of
            # empty records hides the ones that matter.
            return None
        receipt = archive(record)
        with _LOCK:
            live = _TRACES.get(record["trace_id"])
            if live is not None:
                live["archived"] = receipt
        return receipt
    except Exception as e:
        _log.warning("reasoning_trace.finish failed: %s", e)
        return None


@contextlib.contextmanager
def scope(kind: str, label: str = "", **kw) -> Iterator[Optional[str]]:
    """Open a trace for a block unless one is already active; finish it on
    exit. The block's model calls and tool calls land in it.

    `nested=True` always opens a new trace, as a child of the active one."""
    nested = bool(kw.pop("nested", False))
    existing = current()
    if existing and existing in _TRACES and _TRACES[existing]["status"] == "running" \
            and not nested:
        yield existing
        return
    tid = start(kind, label, **kw)
    status = "complete"
    try:
        with activate(tid):
            yield tid
    except BaseException:
        status = "failed"
        raise
    finally:
        if tid:
            finish(tid, status)


def _record_from(tr: Dict[str, Any]) -> Dict[str, Any]:
    rec = _header(tr)
    rec["events"] = [dict(e) for e in tr["events"]]
    rec["reply_excerpt"] = tr.get("reply_excerpt")
    rec["source_labels"] = [LABELS.get(s, s) for s in tr["sources"]]
    rec["headline_source"] = headline_source(tr["sources"])
    rec.pop("archived", None)
    return rec


def headline_source(sources: Iterable[str]) -> str:
    s = list(sources or [])
    for cand in _SOURCE_RANK:
        if cand in s:
            return cand
    return SOURCE_NONE


def _has_children_locked(trace_id: str) -> bool:
    return any(t.get("parent_id") == trace_id for t in _TRACES.values())


def _gc_locked() -> None:
    cutoff = _now() - _FINISHED_KEEP_S
    for tid in [t for t, tr in _TRACES.items()
                if tr["status"] != "running" and (tr.get("ended") or 0) < cutoff]:
        _TRACES.pop(tid, None)


# ── live reads ───────────────────────────────────────────────────────────────

def feed(since: int = 0, limit: int = 2000) -> Dict[str, Any]:
    """Every live event after `since`. `reset` is True when the cursor fell
    off the ring (the client should re-fetch `snapshot()`)."""
    with _LOCK:
        _flush_all_locked(force=True)
        oldest = _FEED[0][0] if _FEED else _SEQ[0] + 1
        reset = bool(since) and since < oldest - 1
        out = []
        for seq, tid, ev in _FEED:
            if seq > since:
                d = dict(ev)
                d["trace_id"] = tid
                out.append(d)
                if len(out) >= limit:
                    break
        cursor = out[-1]["seq"] if out else max(since, _SEQ[0]) if not reset else _SEQ[0]
        return {"events": out, "cursor": cursor, "reset": reset}


def snapshot(include_events: bool = True) -> Dict[str, Any]:
    """All live traces (running and recently finished) with full events."""
    with _LOCK:
        _flush_all_locked(force=True)
        traces = []
        for tr in _TRACES.values():
            d = _header(tr)
            if include_events:
                d["events"] = [dict(e) for e in tr["events"]]
            traces.append(d)
        return {"traces": traces, "cursor": _SEQ[0], "labels": LABELS}


def live_trace(trace_id: str) -> Optional[Dict[str, Any]]:
    with _LOCK:
        tr = _TRACES.get(trace_id)
        if tr is None:
            return None
        d = _header(tr)
        d["events"] = [dict(e) for e in tr["events"]]
        return d


def for_task(task_id: str) -> Optional[str]:
    """The newest live trace for a task id (tray cards are keyed by task)."""
    with _LOCK:
        best = None
        for tr in _TRACES.values():
            if tr.get("task_id") == task_id and (best is None or tr["started"] > best["started"]):
                best = tr
        return best["trace_id"] if best else None


# ═════════════════════════════════════════════════════════════════════════════
# Archive: encrypted, hash-chained, HMAC-signed JSONL
# ═════════════════════════════════════════════════════════════════════════════

_ARCHIVE_LOCK = threading.RLock()
_PENDING: deque = deque(maxlen=_PENDING_MAX)
_KEY_CACHE: Dict[str, bytes] = {}
_TIP: Dict[str, Any] = {}               # {"path": str, "seq": int, "hash": str}
_DECRYPTED: Dict[str, Dict[str, Any]] = {}  # hash -> record (read cache)
_GENESIS = "0" * 64
_KEY_CONTEXT = b"agent-friday/reasoning-traces/v1"
STATUS: Dict[str, Any] = {"pending": 0, "last_error": None, "method": None}


def traces_dir() -> Path:
    if BASE_DIR_OVERRIDE is not None:
        return Path(BASE_DIR_OVERRIDE)
    from agent_friday.paths import friday_home
    return friday_home() / "traces"


def ledger_path() -> Path:
    return traces_dir() / "ledger.jsonl"


def _signing_key() -> bytes:
    """HMAC key for the archive, derived from the file-grants ledger's
    persisted signing key with a domain-separation label.

    That key is minted once, stored through the keystore, and never taken
    from the environment -- so neither a restart nor a launcher edit changes
    it. Deriving (rather than sharing it directly) means a signature from one
    ledger can never verify as a line of the other.
    """
    cached = _KEY_CACHE.get("k")
    if cached:
        return cached
    from agent_friday.services import file_grants as _fg
    base = _fg._secret_bytes()
    key = hmac.new(base, _KEY_CONTEXT, hashlib.sha256).digest()
    _KEY_CACHE["k"] = key
    return key


def _canon(obj: Dict[str, Any]) -> bytes:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")


def _line_hash(core: Dict[str, Any]) -> str:
    return hashlib.sha256(_canon(core)).hexdigest()


def _sign(h: str) -> str:
    return hmac.new(_signing_key(), h.encode("ascii"), hashlib.sha256).hexdigest()


def _encrypt(record: Dict[str, Any]) -> str:
    """Encrypt a record. Raises rather than returning plaintext."""
    from agent_friday.services import credential_store as cs
    blob, method = cs.protect(_canon(record))
    if method == "plaintext":
        raise RuntimeError("no at-rest protection available (keystore, vault and DPAPI all unavailable)")
    STATUS["method"] = method
    return base64.b64encode(blob).decode("ascii")


def _decrypt(body: str) -> Dict[str, Any]:
    from agent_friday.services import credential_store as cs
    blob = base64.b64decode(body)
    if cs.looks_protected(blob) is None:
        raise ValueError("record is not encrypted")
    return json.loads(cs.unprotect(blob).decode("utf-8"))


def _read_lines(path: Path) -> List[Dict[str, Any]]:
    out = []
    if not path.exists():
        return out
    with open(path, "r", encoding="utf-8") as f:
        for raw in f:
            raw = raw.strip()
            if not raw:
                continue
            try:
                out.append(json.loads(raw))
            except Exception:
                out.append({"_unparseable": raw[:200]})
    return out


def _load_tip_locked() -> Dict[str, Any]:
    path = ledger_path()
    if _TIP.get("path") == str(path):
        return _TIP
    seq, h = 0, _GENESIS
    for line in _read_lines(path):
        if line.get("type") == "anchor":
            seq, h = int(line.get("resumes_after_seq") or 0), line.get("resumes_after") or _GENESIS
        elif "hash" in line and "seq" in line:
            seq, h = int(line["seq"]), line["hash"]
    _TIP.clear()
    _TIP.update({"path": str(path), "seq": seq, "hash": h})
    return _TIP


def _write_line_locked(core: Dict[str, Any]) -> Dict[str, Any]:
    tip = _load_tip_locked()
    core = dict(core)
    core["seq"] = tip["seq"] + 1
    core["prev"] = tip["hash"]
    h = _line_hash(core)
    line = dict(core)
    line["hash"] = h
    line["sig"] = _sign(h)
    path = ledger_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(line, separators=(",", ":")) + "\n")
        f.flush()
    tip["seq"], tip["hash"] = core["seq"], h
    return {"seq": core["seq"], "hash": h}


def archive(record: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Append one finished trace. On a locked keystore the record waits in
    memory and is written with the next successful archive call."""
    with _ARCHIVE_LOCK:
        _PENDING.append(record)
        receipt = None
        while _PENDING:
            rec = _PENDING[0]
            try:
                body = _encrypt(rec)
                r = _write_line_locked({"v": 1, "type": "trace", "trace_id": rec["trace_id"],
                                        "ts": rec.get("ended") or _now(), "body": body})
            except Exception as e:
                STATUS["last_error"] = "%s: %s" % (type(e).__name__, e)
                _log.warning("reasoning trace %s held in memory, not written: %s",
                             rec.get("trace_id"), STATUS["last_error"])
                break
            _PENDING.popleft()
            STATUS["last_error"] = None
            _DECRYPTED[r["hash"]] = rec
            _ledger_row(rec, r)
            if rec is record:
                receipt = r
        STATUS["pending"] = len(_PENDING)
    _maybe_retain()
    return receipt


_RETENTION_EVERY_S = 86400


def _maybe_retain() -> None:
    """Apply the retention setting at most once a day, on the write path, so a
    threshold the user set is honoured without a scheduler entry."""
    try:
        if not float(settings().get("retention_days") or 0):
            return
        now = _now()
        if now - STATUS.get("retained_at", 0) < _RETENTION_EVERY_S:
            return
        STATUS["retained_at"] = now
        apply_retention()
    except Exception as e:
        _log.warning("reasoning trace retention failed: %s", e)


def flush_pending() -> int:
    """Retry held records (e.g. after the keystore is unlocked). Returns how
    many are still waiting."""
    with _ARCHIVE_LOCK:
        if _PENDING:
            rec = _PENDING.pop()
            archive(rec)
        return len(_PENDING)


def _ledger_row(rec: Dict[str, Any], receipt: Dict[str, Any]) -> None:
    """Metadata-only pointer in the global activity ledger."""
    try:
        from agent_friday.services import activity_ledger
        dur = None
        if rec.get("ended") and rec.get("started"):
            dur = int((rec["ended"] - rec["started"]) * 1000)
        tok = rec.get("tokens") or {}
        activity_ledger.record(
            "reasoning_trace", trace_id=rec.get("trace_id"), parent_id=rec.get("parent_id"),
            task_id=rec.get("task_id"), model=rec.get("model"), seat=rec.get("seat"),
            trace_kind=rec.get("kind"), source=rec.get("headline_source"),
            tokens_in=tok.get("in") or None, tokens_out=tok.get("out") or None,
            reasoning_chars=rec.get("reasoning_chars"), tool_calls=rec.get("tool_calls"),
            duration_ms=dur, ledger_seq=receipt.get("seq"), ledger_hash=receipt.get("hash"))
    except Exception:
        pass


# ── verify / read ────────────────────────────────────────────────────────────

def verify() -> Dict[str, Any]:
    """Walk the whole chain. Every line must hash to its stored hash, carry a
    valid HMAC, and point at the previous line's hash. A retention anchor may
    open the file; its own HMAC vouches for where the chain resumes."""
    with _ARCHIVE_LOCK:
        lines = _read_lines(ledger_path())
        prev = _GENESIS
        n = 0
        for i, line in enumerate(lines):
            if "_unparseable" in line:
                return {"valid": False, "records": n, "break_at": i + 1, "reason": "unparseable line"}
            core = {k: v for k, v in line.items() if k not in ("hash", "sig")}
            h = _line_hash(core)
            if h != line.get("hash"):
                return {"valid": False, "records": n, "break_at": i + 1, "reason": "content does not match its hash"}
            if not hmac.compare_digest(_sign(h), str(line.get("sig") or "")):
                return {"valid": False, "records": n, "break_at": i + 1, "reason": "signature does not verify"}
            if line.get("type") == "anchor" and i == 0:
                prev = line.get("resumes_after") or _GENESIS
                continue
            if line.get("prev") != prev:
                return {"valid": False, "records": n, "break_at": i + 1, "reason": "chain link broken (a line was removed, reordered or inserted)"}
            prev = line["hash"]
            if line.get("type") == "trace":
                n += 1
        return {"valid": True, "records": n, "break_at": None, "reason": None,
                "pending": len(_PENDING)}


def _iter_archived() -> Iterator[Dict[str, Any]]:
    """Decrypted archived records, oldest first. Unreadable lines are yielded
    as stubs so the viewer can say so instead of hiding them."""
    for line in _read_lines(ledger_path()):
        if line.get("type") != "trace":
            continue
        h = line.get("hash")
        rec = _DECRYPTED.get(h)
        if rec is None:
            try:
                rec = _decrypt(line["body"])
                _DECRYPTED[h] = rec
            except Exception as e:
                yield {"trace_id": line.get("trace_id"), "ended": line.get("ts"),
                       "unreadable": "%s" % type(e).__name__, "ledger_seq": line.get("seq")}
                continue
        out = dict(rec)
        out["ledger_seq"] = line.get("seq")
        out["ledger_hash"] = h
        yield out


def _summary(rec: Dict[str, Any]) -> Dict[str, Any]:
    keep = {k: rec.get(k) for k in (
        "trace_id", "parent_id", "root_id", "kind", "label", "model", "seat", "provider",
        "models", "task_id", "turn_id", "status", "started", "ended", "sources",
        "headline_source", "tokens", "reasoning_chars", "tool_calls", "truncated",
        "ledger_seq", "ledger_hash", "unreadable")}
    keep["source_label"] = LABELS.get(rec.get("headline_source") or SOURCE_NONE)
    return keep


def _matches(rec: Dict[str, Any], *, q: str = None, model: str = None, kind: str = None,
             task_id: str = None, since: float = None, until: float = None,
             subagents: Optional[bool] = None) -> bool:
    ts = rec.get("ended") or rec.get("started") or 0
    if since and ts < since:
        return False
    if until and ts > until:
        return False
    if model and model.lower() not in " ".join(str(m) for m in (rec.get("models") or [rec.get("model")])).lower():
        return False
    if kind and rec.get("kind") != kind:
        return False
    if task_id and task_id not in (rec.get("task_id") or "") and task_id not in (rec.get("trace_id") or ""):
        return False
    if subagents is True and not rec.get("parent_id"):
        return False
    if subagents is False and rec.get("parent_id"):
        return False
    if q:
        ql = q.lower()
        hay = [rec.get("label") or "", rec.get("reply_excerpt") or ""]
        for ev in rec.get("events") or []:
            for k in ("text", "name", "args", "summary"):
                if ev.get(k):
                    hay.append(str(ev[k]))
        if ql not in "\n".join(hay).lower():
            return False
    return True


def search(*, q: str = None, model: str = None, kind: str = None, task_id: str = None,
           since: float = None, until: float = None, subagents: Optional[bool] = None,
           limit: int = 100, offset: int = 0) -> Dict[str, Any]:
    """Archived traces (newest first) plus live ones, filtered. Returns
    summaries; open one with `get_tree`."""
    with _ARCHIVE_LOCK:
        archived = list(_iter_archived())
    seen = {r.get("trace_id") for r in archived}
    with _LOCK:
        live = [_record_from(tr) for tr in _TRACES.values()
                if tr["status"] == "running" and tr["trace_id"] not in seen]
    rows = []
    for rec in live + list(reversed(archived)):
        if _matches(rec, q=q, model=model, kind=kind, task_id=task_id, since=since,
                    until=until, subagents=subagents):
            s = _summary(rec)
            s["live"] = rec.get("status") == "running"
            rows.append(s)
    total = len(rows)
    models = sorted({m for r in archived for m in (r.get("models") or []) if m})
    kinds = sorted({r.get("kind") for r in archived if r.get("kind")})
    return {"traces": rows[offset:offset + limit], "total": total, "models": models,
            "kinds": kinds, "labels": LABELS, "status": status()}


def get_tree(trace_id: str) -> Optional[Dict[str, Any]]:
    """The trace containing `trace_id`'s root, with every descendant nested
    under `children`, full events included. Live and archived traces mix."""
    with _ARCHIVE_LOCK:
        by_id = {r["trace_id"]: r for r in _iter_archived() if r.get("trace_id")}
    with _LOCK:
        for tr in _TRACES.values():
            if tr["status"] == "running" or tr["trace_id"] not in by_id:
                d = _record_from(tr)
                d["live"] = tr["status"] == "running"
                by_id[tr["trace_id"]] = d
    node = by_id.get(trace_id)
    if node is None:
        return None
    root_id = node.get("root_id") or trace_id
    root = by_id.get(root_id) or node
    kids: Dict[str, List[Dict[str, Any]]] = {}
    for r in by_id.values():
        if r.get("parent_id"):
            kids.setdefault(r["parent_id"], []).append(r)

    def build(r, depth=0):
        d = dict(r)
        d["source_label"] = LABELS.get(d.get("headline_source") or SOURCE_NONE)
        if depth < 12:
            d["children"] = [build(c, depth + 1) for c in
                             sorted(kids.get(r["trace_id"], []), key=lambda x: x.get("started") or 0)]
        return d
    return {"tree": build(root), "focus": trace_id, "labels": LABELS}


def export_records() -> Iterator[Dict[str, Any]]:
    """Every archived record, decrypted, oldest first -- for the local export
    download. The first item is the verification report."""
    yield {"type": "verification", **verify(), "exported_at": _now()}
    with _ARCHIVE_LOCK:
        for rec in _iter_archived():
            yield {"type": "trace", **rec}


def status() -> Dict[str, Any]:
    path = ledger_path()
    try:
        size = path.stat().st_size if path.exists() else 0
    except Exception:
        size = 0
    return {"path": str(path), "bytes": size, "pending": len(_PENDING),
            "last_error": STATUS.get("last_error"), "method": STATUS.get("method"),
            "retention_days": settings().get("retention_days", 0),
            "capture": _capture_enabled()}


# ── retention ────────────────────────────────────────────────────────────────

def apply_retention(days: Optional[float] = None) -> Dict[str, Any]:
    """Drop archived traces older than `days` (setting `retention_days`; 0
    keeps everything). The file is rewritten to start with a signed anchor
    recording the hash of the last dropped line, so the surviving chain still
    verifies end to end and the anchor says how much was pruned."""
    if days is None:
        days = float(settings().get("retention_days") or 0)
    if not days or days <= 0:
        return {"pruned": 0, "kept": None, "reason": "retention is keep-all"}
    cutoff = _now() - days * 86400
    with _ARCHIVE_LOCK:
        ver = verify()
        if not ver["valid"]:
            # Never rewrite a chain that already fails: pruning would erase the evidence.
            return {"pruned": 0, "error": "chain does not verify (%s); not pruning" % ver["reason"]}
        path = ledger_path()
        lines = _read_lines(path)
        prior_pruned = 0
        if lines and lines[0].get("type") == "anchor":
            prior_pruned = int(lines[0].get("pruned") or 0)
            lines = lines[1:]
        keep_from = 0
        for i, line in enumerate(lines):
            if line.get("type") == "trace" and (line.get("ts") or 0) < cutoff:
                keep_from = i + 1
            else:
                break
        if keep_from == 0:
            return {"pruned": 0, "kept": len(lines)}
        dropped, kept = lines[:keep_from], lines[keep_from:]
        anchor_core = {"v": 1, "type": "anchor", "ts": _now(),
                       "resumes_after": dropped[-1]["hash"],
                       "resumes_after_seq": dropped[-1]["seq"],
                       "pruned": prior_pruned + len(dropped),
                       "cutoff": cutoff}
        ah = _line_hash(anchor_core)
        anchor = dict(anchor_core, hash=ah, sig=_sign(ah))
        # The first kept line's `prev` is the last dropped hash; the anchor
        # records it in `resumes_after`, and verify() resumes the chain there.
        anchor_link = anchor
        tmp = path.with_name(path.name + ".tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(json.dumps(anchor_link, separators=(",", ":")) + "\n")
            for line in kept:
                f.write(json.dumps(line, separators=(",", ":")) + "\n")
        tmp.replace(path)
        for line in dropped:
            _DECRYPTED.pop(line.get("hash"), None)
        _TIP.clear()
        return {"pruned": len(dropped), "kept": len(kept)}


def _reset_for_tests() -> None:
    with _LOCK:
        _TRACES.clear()
        _BUFFERS.clear()
        _FEED.clear()
        _OPEN_CALLS.clear()
    with _ARCHIVE_LOCK:
        _PENDING.clear()
        _KEY_CACHE.clear()
        _TIP.clear()
        _DECRYPTED.clear()
        STATUS.update({"pending": 0, "last_error": None, "method": None})

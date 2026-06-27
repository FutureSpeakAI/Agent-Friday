"""Transcript auto-compaction (Part C of Self-Sufficient Friday).

Long voice/chat sessions and long-running ``agent_prompt`` tasks can grow a
message list past the model's context window. This module compacts the *live
context window* — never the durable record — by summarizing the middle of the
transcript while preserving the head (system/task framing + opening exchange)
and the tail (recent turns) verbatim.

  head (keep_head)            ← never compacted
  middle                      → one synthetic "[Context Summary] …" message
  tail (keep_tail)            ← preserved verbatim

Triggering is by **estimated token count** of the assembled transcript (turn
count is a poor proxy — one big tool dump can blow the budget), firing at
``trigger_ratio`` × the model context window.

Lossless where it matters: this operates on a *copy* assembled for the model
call. ``CHAT_HISTORY`` / ``chat_history.json`` keep the full transcript, and
every turn is independently embedded in ChromaDB, so the original turns stay
semantically retrievable even after the middle is summarized in-context.
"""

import json

import agent_friday.core as core
from agent_friday.core import _load_settings

_CHARS_PER_TOKEN = 4
_SUMMARY_PREFIX = "[Context Summary]"


def _content_text(msg):
    """Flatten a message's content to text for estimation/summarization."""
    c = msg.get("content")
    if isinstance(c, str):
        return c
    if isinstance(c, list):
        parts = []
        for b in c:
            if isinstance(b, dict):
                if b.get("type") == "text":
                    parts.append(b.get("text", ""))
                elif b.get("type") == "tool_result":
                    parts.append(str(b.get("content", ""))[:2000])
                elif b.get("type") == "tool_use":
                    parts.append(f"[tool_use {b.get('name', '')}]")
            else:
                parts.append(str(b))
        return "\n".join(parts)
    return str(c or "")


def estimate_tokens(messages):
    """Cheap char-based token estimate for the assembled message list."""
    total = 0
    for m in messages or []:
        total += len(_content_text(m))
    return total // _CHARS_PER_TOKEN


def _cfg():
    try:
        return dict((_load_settings().get("compaction") or {}))
    except Exception:
        return {}


def should_compact(messages, model=None, cfg=None):
    cfg = cfg if cfg is not None else _cfg()
    if not cfg or cfg.get("enabled") is False:
        return False
    keep_head = int(cfg.get("keep_head", 3))
    keep_tail = int(cfg.get("keep_tail", 10))
    # Need at least one message in the middle to be worth compacting.
    if len(messages or []) <= keep_head + keep_tail + 1:
        return False
    window = int(cfg.get("context_window", 200000))
    ratio = float(cfg.get("trigger_ratio", 0.70))
    return estimate_tokens(messages) > window * ratio


def _merge_adjacent(messages):
    """Merge consecutive same-role messages that both carry string content, so
    inserting the summary can't create an illegal two-in-a-row-role sequence for
    providers that require strict alternation."""
    out = []
    for m in messages:
        if (out and out[-1].get("role") == m.get("role")
                and isinstance(out[-1].get("content"), str)
                and isinstance(m.get("content"), str)):
            out[-1] = {"role": m["role"],
                       "content": out[-1]["content"] + "\n\n" + m["content"]}
        else:
            out.append(dict(m))
    return out


def _default_summarizer(text, max_tokens=400):
    """Summarize via the cheapest available model (subagent or local), no tools.

    The compaction call is itself metered (Part D) and tagged kind="compaction".
    Returns '' on any failure so compaction degrades to a no-op.
    """
    try:
        from agent_friday.services.model_router import _generate_text
        settings = _load_settings()
        model = settings.get("subagent_model")
        prompt = (
            "Summarize the following conversation excerpt into a compact factual "
            "note that preserves decisions made, open questions, key entities, "
            "and any state the assistant must remember to continue. Be terse; "
            f"no preamble. Limit to about {max_tokens} tokens.\n\n"
            "=== EXCERPT ===\n" + text + "\n=== END ==="
        )
        # kind tag flows via a thread-local attribution so the meter labels it.
        try:
            from services import cost_meter as _cm
            _cm.push_attribution(kind="compaction")
        except Exception:
            _cm = None
        try:
            out = _generate_text([{"role": "user", "content": prompt}],
                                 model=model, max_tokens=max_tokens,
                                 orb_label="🗜 Compacting context",
                                 workspace="system")
        finally:
            try:
                if _cm:
                    _cm.pop_attribution()
            except Exception:
                pass
        return (out or "").strip()
    except Exception as e:
        print(f"  [compaction] summarizer failed: {e}")
        return ""


def maybe_compact(messages, model=None, summarizer=None):
    """Return a (possibly) compacted copy of ``messages``.

    No-op (returns the original list) when compaction is disabled, the transcript
    is below threshold, or summarization yields nothing. Idempotent: a prior
    "[Context Summary]" message in the middle is folded into the new summary
    rather than re-summarized on top of itself.
    """
    if not messages:
        return messages
    cfg = _cfg()
    if not should_compact(messages, model=model, cfg=cfg):
        return messages

    keep_head = int(cfg.get("keep_head", 3))
    keep_tail = int(cfg.get("keep_tail", 10))
    max_tokens = int(cfg.get("summary_max_tokens", 400))

    head = messages[:keep_head]
    tail = messages[-keep_tail:] if keep_tail else []
    middle = messages[keep_head: len(messages) - keep_tail] if keep_tail \
        else messages[keep_head:]
    if not middle:
        return messages

    transcript_lines = []
    for m in middle:
        role = m.get("role", "?")
        text = _content_text(m)
        if not text.strip():
            continue
        transcript_lines.append(f"{role.upper()}: {text}")
    transcript = "\n\n".join(transcript_lines)
    if not transcript.strip():
        return messages

    summarize = summarizer or _default_summarizer
    summary = summarize(transcript, max_tokens)
    if not summary:
        return messages   # degrade to no-op — never lose the middle silently

    summary_msg = {
        "role": "user",
        "content": f"{_SUMMARY_PREFIX} Earlier in this session ("
                   f"{len(middle)} turns condensed): {summary}",
    }
    compacted = _merge_adjacent(list(head) + [summary_msg] + list(tail))
    return compacted

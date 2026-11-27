"""A knowledge question answered in one model round (KG latency fix 2, 2026-11-26).

Measured: every knowledge-graph question took two or three cloud rounds (look up, read, answer),
each re-sending the whole prompt and waiting on its tools. When the owner's question names a page's
title outright ("what is the core philosophy of the FutureSpeak aesthetic?" names the page titled
"FutureSpeak Aesthetic"), the page is read BEFORE the first round and handed to the model with the
question, so it can answer at once and calls a tool only for what the page does not cover.

What it may do, and nothing else:
  * only on an interactive chat turn of the cloud tool loop (never a background job, never a
    resumed turn), only for a short question;
  * read at most MAX_PAGES pages, within BUDGET_S, through the caller's `read` function, which
    runs the SAME gates a model's own read_wiki call passes (the agent loop's vault check and
    _execute_tool's chain) and returns None for anything refused or not found;
  * add the pages to the question as data (the caller's untrusted-input envelope), never as
    instructions. Nothing is read that the model could not have read with one read_wiki call.
"""
from __future__ import annotations

import re
import time
from typing import Callable, List, Optional, Tuple

from agent_friday.services import wiki_index_search as _wis

ENABLED = True
MAX_PAGES = 2
MAX_CHARS_PER_PAGE = 6000
BUDGET_S = 1.5
MAX_QUESTION_CHARS = 400

_ASKS = re.compile(r"^\s*(what|who|whom|whose|where|when|which|why|how|tell me|explain|describe|summari[sz]e|"
                   r"remind me|do you know|did i|have i|is there|are there|give me)\b", re.I)

HEAD = ("Before this turn, Friday read the page(s) of the owner's wiki that the knowledge graph links to this "
        "question, each exactly as read_wiki returns it. If they answer the question, answer from them "
        "directly and say which page; call a tool only for what they do not cover.")


def eligible(text: str, session_ctx: Optional[dict]) -> bool:
    ctx = session_ctx or {}
    # Only a person's chat turn: the chat routes mark theirs (routes/chat.py, "interactive_chat").
    if not ENABLED or ctx.get("is_background_task") or not ctx.get("interactive_chat"):
        return False
    t = str(text or "").strip()
    if not t or len(t) > MAX_QUESTION_CHARS:
        return False
    return t.endswith("?") or bool(_ASKS.match(t))


def last_user_text(convo) -> str:
    if not convo or not isinstance(convo[-1], dict) or convo[-1].get("role") != "user":
        return ""
    c = convo[-1].get("content")
    if isinstance(c, str):
        return c
    if isinstance(c, list):
        return " ".join(str(b.get("text") or "") for b in c if isinstance(b, dict) and b.get("type") == "text")
    return ""


def prefetch(text: str, read: Callable[[str], Optional[str]]) -> List[Tuple[str, str]]:
    """[(page, content as the gates returned it)] for the pages the question names. Never raises."""
    out: List[Tuple[str, str]] = []
    deadline = time.monotonic() + BUDGET_S
    try:
        for rel in _wis.strong_pages(text, MAX_PAGES):
            if time.monotonic() >= deadline:
                break
            try:
                got = read(rel)
            except Exception:
                got = None
            if got:
                # `read` cut the page to MAX_CHARS_PER_PAGE BEFORE its envelope: never cut here,
                # or the envelope's close marker is lost (untrusted_input).
                out.append((rel, str(got)))
    except Exception:
        return out
    return out


def with_pages(convo: list, pages: List[Tuple[str, str]]) -> list:
    """The conversation with the pages added to its last (user) message, as text blocks after it."""
    if not pages:
        return convo
    last = dict(convo[-1])
    c = last.get("content")
    blocks = [{"type": "text", "text": c}] if isinstance(c, str) else list(c or [])
    blocks.append({"type": "text", "text": HEAD + "\n\n" + "\n\n".join(
        "Page: %s\n%s" % (rel, content) for rel, content in pages)})
    last["content"] = blocks
    return list(convo[:-1]) + [last]

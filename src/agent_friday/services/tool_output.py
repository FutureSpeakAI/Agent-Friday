"""A cut tool result says what the model got and how to get the rest.

The single rule used to be "the first 8,192 characters, then a note". That
loses the end of a command's output (where the error is), gives a file reader
no way to ask for the next page, and leaves the model guessing whether the
part it saw was all there was. Each kind of tool now keeps the part that
matters and ends with a line that names the window and the way forward:

* files (``read_file``) keep the start and say
  ``[Showing lines 1-2000 of 5,400. Continue with offset=2001.]``;
* commands keep the end, where the failure is, and name the saved full output;
* everything else (web pages, MCP results, searches) keeps both ends with the
  cut marked in the middle, and names the saved full output.

Limits are lines and characters, whichever is hit first. Image payloads
(screenshots, office renders) are never cut: a truncated JSON blob is a
silently dropped picture.

The full text is kept under ``<friday_home>/tool-output`` so "the rest" can be
read with ``read_file``; the folder is pruned by the ``tool_output_retention_days``
setting (0 keeps everything). Secrets were already redacted by the handler
before the text reached this point.

Adapted in design from pi's truncate.ts (earendil-works/pi, MIT,
Copyright (c) 2025 Mario Zechner); see THIRD_PARTY_LICENSES.md.
"""
from __future__ import annotations

import re
import time
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, Optional, Tuple

MAX_CHARS = 8192
MAX_LINES = 2000
DEFAULT_RETENTION_DAYS = 7

HEAD, TAIL, MIDDLE, NONE = "head", "tail", "middle", "none"

STYLE: Dict[str, str] = {
    "read_file": HEAD,
    "read_wiki": HEAD,
    "run_command": TAIL,
    "run_sandboxed": TAIL,
    "run_shell": TAIL,
    "execute_python": TAIL,
    # Image payloads must survive whole: the loops parse them as JSON.
    "screenshot": NONE,
    "office": NONE,
    "office_check": NONE,
    "capture_screen": NONE,
}

# Tools whose handler already windows its output and names the next page.
SELF_WINDOWED = {"read_file"}


def style_for(tool: str) -> str:
    if tool in STYLE:
        return STYLE[tool]
    if tool.startswith("mcp_"):
        return MIDDLE
    return MIDDLE


def _fmt(n: int) -> str:
    return f"{n:,}"


def truncate(tool: str, text: str, *, max_chars: int = MAX_CHARS,
             max_lines: int = MAX_LINES, saved_path: Optional[str] = None,
             first_line: int = 1) -> Tuple[str, bool]:
    """Return (text, was_cut). ``first_line`` is the line number of text[0]."""
    if not isinstance(text, str):
        return text, False
    style = style_for(tool)
    if style == NONE:
        return text, False
    lines = text.split("\n")
    total_lines = len(lines)
    total_chars = len(text)
    if total_chars <= max_chars and total_lines <= max_lines:
        return text, False
    saved = f" Full output saved at {saved_path}; read it with read_file." if saved_path else ""

    if style == HEAD:
        kept = _head(lines, max_chars, max_lines)
        last = first_line + len(kept) - 1
        body = "\n".join(kept)
        note = (f"\n[Showing lines {_fmt(first_line)}-{_fmt(last)} of "
                f"{_fmt(first_line + total_lines - 1)} ({_fmt(len(body))} of "
                f"{_fmt(total_chars)} chars). Continue with offset={last + 1}.{saved}]")
        return body + note, True

    if style == TAIL:
        kept = _tail(lines, max_chars, max_lines)
        cut = total_lines - len(kept)
        body = "\n".join(kept)
        note = (f"[Showing the last {_fmt(len(kept))} of {_fmt(total_lines)} lines; "
                f"the first {_fmt(cut)} lines ({_fmt(total_chars - len(body))} chars) were cut."
                f"{saved}]\n")
        return note + body, True

    # MIDDLE: both ends, the cut marked where it happened.
    half_chars = max(1, (max_chars - 120) // 2)
    head_lines = _head(lines, half_chars, max(1, max_lines // 2))
    tail_lines = _tail(lines, half_chars, max(1, max_lines // 2))
    # Never let the two windows overlap.
    if len(head_lines) + len(tail_lines) >= total_lines:
        tail_lines = lines[len(head_lines):]
    head = "\n".join(head_lines)
    tail = "\n".join(tail_lines)
    cut_chars = max(0, total_chars - len(head) - len(tail))
    cut_lines = max(0, total_lines - len(head_lines) - len(tail_lines))
    marker = (f"\n[... {_fmt(cut_chars)} chars / {_fmt(cut_lines)} lines cut from the middle "
              f"of {_fmt(total_chars)} chars.{saved} ...]\n")
    return head + marker + tail, True


def _head(lines, max_chars, max_lines):
    out, used = [], 0
    for ln in lines[:max_lines]:
        if used + len(ln) + (1 if out else 0) > max_chars:
            if not out:
                out.append(ln[:max_chars])
            break
        out.append(ln)
        used += len(ln) + (1 if len(out) > 1 else 0)
    return out


def _tail(lines, max_chars, max_lines):
    out, used = [], 0
    for ln in reversed(lines[-max_lines:]):
        if used + len(ln) + (1 if out else 0) > max_chars:
            if not out:
                out.append(ln[-max_chars:])
            break
        out.append(ln)
        used += len(ln) + (1 if len(out) > 1 else 0)
    out.reverse()
    return out


def window_lines(text: str, offset: int = 1, limit: Optional[int] = None, *,
                 max_chars: int = MAX_CHARS, max_lines: int = MAX_LINES) -> Tuple[str, dict]:
    """A page of ``text`` for a reader that takes offset/limit.

    offset is 1-based. Returns (page, info) where info has first, last, total,
    total_chars and next_offset (None when the page reached the end).
    """
    lines = text.split("\n")
    total = len(lines)
    offset = max(1, int(offset or 1))
    want = int(limit) if limit else max_lines
    want = max(1, min(want, max_lines))
    chunk = lines[offset - 1: offset - 1 + want]
    kept = _head(chunk, max_chars, want)
    page = "\n".join(kept)
    last = offset + len(kept) - 1 if kept else offset - 1
    info = {"first": offset, "last": last, "total": total, "total_chars": len(text),
            "next_offset": (last + 1) if last < total else None}
    return page, info


def page_note(info: dict, tool: str = "read_file") -> str:
    """The trailing line a self-windowed reader adds when the page is partial."""
    if info["first"] == 1 and info["next_offset"] is None:
        return ""
    if info["next_offset"] is None:
        return (f"\n[Showing lines {_fmt(info['first'])}-{_fmt(info['last'])} of "
                f"{_fmt(info['total'])}: the end of the file.]")
    return (f"\n[Showing lines {_fmt(info['first'])}-{_fmt(info['last'])} of "
            f"{_fmt(info['total'])} ({_fmt(info['total_chars'])} chars). "
            f"Continue with {tool} offset={info['next_offset']}.]")


# ── Keeping the full text ────────────────────────────────────────────────────

def output_dir() -> Path:
    from agent_friday.paths import friday_home
    return friday_home() / "tool-output"


_SAFE = re.compile(r"[^A-Za-z0-9_.-]+")


def save_full(tool: str, text: str) -> Optional[str]:
    """Write the full text to the tool-output folder; return its path or None."""
    try:
        day = datetime.now().strftime("%Y-%m-%d")
        d = output_dir() / day
        d.mkdir(parents=True, exist_ok=True)
        name = f"{_SAFE.sub('_', tool)[:40]}-{uuid.uuid4().hex[:8]}.txt"
        p = d / name
        p.write_text(text, encoding="utf-8", errors="replace")
        return str(p)
    except Exception:
        return None


def retention_days() -> int:
    try:
        from agent_friday.core import _load_settings
        v = (_load_settings() or {}).get("tool_output_retention_days", DEFAULT_RETENTION_DAYS)
        return int(v if v is not None else DEFAULT_RETENTION_DAYS)
    except Exception:
        return DEFAULT_RETENTION_DAYS


_last_prune = 0.0


def prune(days: Optional[int] = None, *, now: Optional[datetime] = None) -> int:
    """Delete day folders older than the retention; 0 keeps everything."""
    days = retention_days() if days is None else int(days)
    if days <= 0:
        return 0
    root = output_dir()
    if not root.exists():
        return 0
    cutoff = ((now or datetime.now()) - timedelta(days=days)).strftime("%Y-%m-%d")
    removed = 0
    for d in root.iterdir():
        if d.is_dir() and d.name < cutoff:
            for f in d.glob("*"):
                try:
                    f.unlink()
                    removed += 1
                except OSError:
                    pass
            try:
                d.rmdir()
            except OSError:
                pass
    return removed


def maybe_prune() -> None:
    """Prune at most once an hour, from the tool path; errors never surface."""
    global _last_prune
    if time.time() - _last_prune < 3600:
        return
    _last_prune = time.time()
    try:
        prune()
    except Exception:
        pass


def clip_result(tool: str, result: str, *, max_chars: int = MAX_CHARS,
                max_lines: int = MAX_LINES) -> str:
    """The executor's entry point: cut, say what was cut, keep the full text."""
    if not isinstance(result, str):
        return result
    if style_for(tool) == NONE:
        return result
    if len(result) <= max_chars and result.count("\n") < max_lines:
        return result
    saved = None if tool in SELF_WINDOWED else save_full(tool, result)
    maybe_prune()
    out, _ = truncate(tool, result, max_chars=max_chars, max_lines=max_lines, saved_path=saved)
    return out


def describe_limits() -> str:
    """One sentence for a tool description, generated from the constants."""
    return (f"Results longer than {_fmt(MAX_LINES)} lines or {_fmt(MAX_CHARS)} characters are cut, "
            f"and the cut is marked with what was shown and how to get the rest.")


__all__ = ["MAX_CHARS", "MAX_LINES", "truncate", "window_lines", "page_note",
           "clip_result", "save_full", "prune", "describe_limits"]

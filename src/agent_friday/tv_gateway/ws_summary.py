"""One live line per workspace for the owner's phone list (contract draft 3, section 4: the workspace list).

    GET /tv/v1/workspaces?live=1   each row may carry live: {text, as_of[, taint]}

The line is written here, on the PC, from the rows the workspace's own section already serves to the phone
(tv_gateway/ws_content.py): the same adapter, the same device block, the same sensitivity, behind the same read right
the list route checks. Nothing here reads anything the phone could not already open, and a line made from an outside
row carries that row's taint (a headline is an outlet's words, not Friday's). A row holding a URL never becomes a
line (the broker's rule), checked again after invisible characters are taken out; the text is one plain line of at
most TEXT_MAX characters. A workspace with nothing to say, a section that can't be read or a manifest that can't be
read gives no line, and the list still answers.

Reads: each on its own daemon thread (a hung desk read never holds up the PC's shutdown), at most one per workspace.
A list waits at most BUDGET_S, and only for reads it started itself: a read already under way is not waited for again,
so a hung section costs one list its budget and no more. A read still running after HUNG_S no longer blocks a fresh
one (its thread is left to finish or hang on its own; at most one more per workspace per HUNG_S). Each line is kept
TTL_S seconds with the time it was true. tests/unit/test_tv_ws_summary.py holds the rules.
"""
from __future__ import annotations

import logging
import threading
import time
import unicodedata
from concurrent.futures import Future, wait
from datetime import datetime, timezone
from typing import Callable, Iterable, Optional

_log = logging.getLogger(__name__)

TEXT_MAX = 80
TTL_S = 30            # the broker's ttl_s: a tightened block or a hold reaches the list within this
BUDGET_S = 1.5        # the most the list waits for its lines
HUNG_S = 20           # a read running longer than this is given up on
_clock_real = time.time
_clock = time.time

_kept: dict[str, tuple[float, Optional[dict]]] = {}
_reading: dict[str, tuple[float, Future]] = {}      # workspace -> (when the read began, its answer)
_threads: set = set()                                # every read thread not yet finished, given up on or not
_lock = threading.Lock()


def _plain(text) -> str:
    """One line: no control or format characters (no bidi overrides, no zero-width), single spaces, an ellipsis cut."""
    s = "".join(c if not unicodedata.category(c).startswith("C") else " " for c in str(text or ""))
    s = " ".join(s.split())
    return _fit(s, TEXT_MAX)


_SEPARATORS = " ·,;:-–—"


def _fit(s: str, room: int) -> str:
    """s in at most room characters: whole, or its whole words up to an ellipsis, never ending on a separator. When
    whole words would keep under half the room (one very long word), the text is cut inside the word instead."""
    if len(s) <= room:
        return s
    cut = s[:room]                                      # room for room - 1 characters and the ellipsis
    words = cut.rsplit(" ", 1)[0].rstrip(_SEPARATORS) if " " in cut else ""
    if len(words) < room // 2:
        words = s[:room - 1].rstrip(_SEPARATORS)
    return words + "…"


def _newest(rows: list[dict]) -> Optional[tuple[str, dict]]:
    return (rows[0].get("title"), rows[0]) if rows else None


def _parse_when(value):
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None


def _next_event(rows: list[dict]) -> Optional[tuple[str, dict]]:
    """The first event not yet begun. A time with no offset is the PC's own clock; one with an offset is compared as
    that instant; one that isn't a time is skipped."""
    now = _clock()
    for r in rows:
        when = _parse_when(r.get("when"))
        if when is None:
            continue
        begins = when.timestamp() if when.tzinfo is not None else time.mktime(when.timetuple())
        if begins >= now:
            return " · ".join(p for p in (r.get("title"), r.get("line")) if p), r
    return None


def _plural(n: int, noun: str) -> str:
    return "%d %s%s" % (n, noun, "" if n == 1 else "s")


def _repos(rows: list[dict]) -> Optional[tuple[str, dict]]:
    """Code: how many repositories, how many hold changes not yet committed, and how many could not be read. "All
    clean" only when every one was read and is clean (UI-CRITIC R3.1)."""
    if not rows:
        return None
    changed = sum(1 for r in rows if r.get("state") == "changed")
    unread = sum(1 for r in rows if r.get("state") == "error")
    if all(r.get("state") == "clean" for r in rows):
        return "%s, all clean" % _plural(len(rows), "repo"), {}
    parts = [_plural(len(rows), "repo"), "%d with changes" % changed if changed else "", "%d unreadable" % unread if unread else ""]
    return " · ".join(p for p in parts if p), {}


def _agents(rows: list[dict]) -> Optional[tuple[str, dict]]:
    """Crew: how many agents are active, and how many suspended; the count alone when none says either."""
    if not rows:
        return None
    active = sum(1 for r in rows if r.get("state") == "active")
    held = sum(1 for r in rows if r.get("state") == "suspended")
    parts = ["%d active" % active if active else "", "%d suspended" % held if held else ""]
    return (" · ".join(p for p in parts if p) or _plural(len(rows), "agent")), {}


def _day(stamp: float) -> str:
    """today / yesterday / 9 Nov / 9 Nov 2025, on the PC's own calendar."""
    then = time.localtime(stamp)
    now = time.localtime(_clock())
    days = (datetime(*now[:3]) - datetime(*then[:3])).days
    if days <= 0:
        return "today"
    if days == 1:
        return "yesterday"
    return "%d %s" % (then.tm_mday, time.strftime("%b", then)) + ("" if then.tm_year == now.tm_year else " %d" % then.tm_year)


def _stamp_of(value) -> Optional[float]:
    """A row's time as seconds, only when it carries an offset, the PC can place it on its calendar, and it is not in
    the future (a wrong clock or a forged date never names the project)."""
    when = _parse_when(value)
    if when is None or when.tzinfo is None:
        return None
    try:
        stamp = when.timestamp()
        time.localtime(stamp)                       # Windows refuses dates its calendar can't hold
    except (OverflowError, OSError, ValueError):
        return None
    return stamp if stamp <= _clock() + 3600 else None


def _last_change(rows: list[dict]) -> Optional[tuple[str, dict]]:
    """FutureSpeak: the project changed last, and when (its last commit). A project with no usable date says nothing;
    a long name gives way before its day."""
    dated = [(s, r) for r in rows for s in [_stamp_of(r.get("when"))] if s is not None and r.get("title")]
    if not dated:
        return None
    stamp, row = max(dated, key=lambda d: d[0])
    tail = " · changed %s" % _day(stamp)
    name = " ".join(str(row["title"]).split())
    return _fit(name, TEXT_MAX - len(tail)) + tail, row


def _count(noun: str) -> Callable[[list[dict]], Optional[tuple[str, dict]]]:
    return lambda rows: ("%d %s%s" % (len(rows), noun, "" if len(rows) == 1 else "s"), {}) if rows else None


# workspace -> (the section it speaks from, how its rows become one line and the row it came from). Not here: no line.
SAYS: dict[str, tuple[str, Callable[[list[dict]], Optional[tuple[str, dict]]]]] = {
    "news": ("latest", _newest),
    "calendar": ("agenda", _next_event),
    "knowledge": ("pages", _count("page")),
    "code": ("repos", _repos),
    "crew": ("roster", _agents),
    "futurespeak": ("projects", _last_change),
}


def _compute(ws: str, now: float) -> Optional[dict]:
    from agent_friday.services import a2ui_profile as prof
    from agent_friday.tv_gateway import ws_content
    section, say = SAYS[ws]
    try:                                                    # one workspace being down never takes the list down
        served = ws_content.serving(ws)
        if served is None or section not in served[0]:
            return None
        rows = served[0][section].pages(served[1]["sensitivity"])
        said = say(rows)
    except Exception as e:
        _log.info("workspace line for %s unreadable: %s", ws, type(e).__name__)
        return None
    if not said or not said[0]:
        return None
    raw, row = said
    text = _plain(raw)
    if not text or prof.has_url(str(raw)) or prof.has_url(text) or prof.has_url(text.replace(" ", "")):
        return None
    out = {"text": text, "as_of": datetime.fromtimestamp(now, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")}
    if isinstance(row, dict) and row.get("taint"):
        out["taint"] = str(row["taint"])[:40]
    return out


def _read(ws: str, fut: Future) -> None:
    """One read, on its own daemon thread (a hung desk read never holds up the PC's shutdown). Whatever happens, the
    workspace is free to be read again after it (test_a_read_that_breaks_never_wedges_its_workspace)."""
    now = _clock()
    got = None
    try:
        got = _compute(ws, now)
    except Exception as e:
        _log.info("workspace line for %s broke: %s", ws, type(e).__name__)
    finally:
        with _lock:
            current = _reading.get(ws, (0, None))[1] is fut
            if current or _kept.get(ws, (0, None))[0] < now:  # a read given up on never overwrites a newer line
                _kept[ws] = (now, got)
            if current:                                       # nor clears the read that replaced it
                del _reading[ws]
        fut.set_result(got)
        with _lock:
            _threads.discard(threading.current_thread())


def lines(ids: Iterable[str]) -> dict[str, dict]:
    """{workspace id: {text, as_of[, taint]}} for the ids that have a line now. Kept ones are answered at once; for the
    rest a read is started unless one is already under way, and only the reads started here are waited for."""
    out: dict[str, dict] = {}
    waiting: dict[str, Future] = {}
    now = _clock()
    with _lock:
        for ws in ids:
            if ws not in SAYS:
                continue
            hit = _kept.get(ws)
            if hit is not None and now - hit[0] < TTL_S:
                if hit[1] is not None:
                    out[ws] = hit[1]
                continue
            under_way = _reading.get(ws)
            if under_way is not None and now - under_way[0] < HUNG_S:
                continue                                     # its answer lands in _kept for a later list
            fut = Future()
            _reading[ws] = (now, fut)
            t = threading.Thread(target=_read, args=(ws, fut), name="ws-line-" + ws, daemon=True)
            _threads.add(t)
            t.start()
            waiting[ws] = fut
    if waiting:
        wait(list(waiting.values()), timeout=BUDGET_S)
    for ws, fut in waiting.items():
        if fut.done() and fut.exception() is None and fut.result() is not None:
            out[ws] = fut.result()
    return out


def settle(timeout: float) -> None:
    """Wait for reads still under way (tests)."""
    with _lock:
        pending = list(_threads)
    end = time.monotonic() + timeout
    for t in pending:
        t.join(max(0.0, end - time.monotonic()))


def forget() -> None:
    with _lock:
        _kept.clear()
        _reading.clear()

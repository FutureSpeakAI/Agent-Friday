"""The News routines' episodes.

Each of the four routines (the Front Page, the Briefing, the Weekly Digest and
the Weekly Editorial) ends by calling its `_notify_*` function in
`news_engine`, from both the scheduled job and the News button. That is where
`queue_for_run` is called: once per finished run, and it only writes a queued
episode (`podcast_engine.create`), so the routine is never held up.

`run_documents` turns a saved run into numbered source documents: one per
story for the Front Page and the Digest; for the Briefing, one per story and
one per calendar event from the structured sources saved with the run
(`briefing_runs/<date>.json`), plus Friday's own written sections as context;
one per section for the Editorial. Each story keeps its outlet and link, so
every line can say where it came from and the transcript can link it.
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path

log = logging.getLogger(__name__)

ROUTINES = ("front_page", "briefing", "weekly", "editorial")
MAX_STORIES = 18
_RUN_ID_RE = re.compile(r"^[0-9A-Za-z-]{4,40}$")


def _paths():
    from agent_friday.core import FRIDAY_DIR
    from agent_friday.services import news_engine as ne
    return {
        "front_page": lambda rid: Path(ne.FRONT_PAGES_DIR) / f"{rid}.json",
        "weekly": lambda rid: Path(ne.WEEKLY_DIGESTS_DIR) / f"{rid}.json",
        "editorial": lambda rid: Path(ne.EDITORIALS_DIR) / f"{rid}.md",
        "briefing": lambda rid: Path(FRIDAY_DIR) / "wiki" / "briefings" / f"{rid}.md",
    }


def run_path(routine: str, run_id: str) -> Path:
    if routine not in ROUTINES:
        raise ValueError("unknown routine %r" % routine)
    if not _RUN_ID_RE.match(run_id or ""):
        raise ValueError("not a run id")
    return _paths()[routine](run_id)


def _doc(title, text, *, url="", origin="", private=False, source=""):
    return {"title": (title or "").strip()[:200], "kind": "news", "text": (text or "").strip(),
            "url": url or "", "origin": origin or url or "", "private": private,
            "outlet": source or ""}


def _story_doc(a: dict) -> dict | None:
    if not isinstance(a, dict) or not a.get("title"):
        return None
    bits = [a.get("title", "")]
    if a.get("source"):
        bits.append("Outlet: %s" % a["source"])
    for k in ("snippet", "editorial_note", "thread_update"):
        if a.get(k):
            bits.append(str(a[k]))
    if a.get("continuing"):
        bits.append("A continuing story.")
    return _doc(a["title"], "\n".join(bits), url=a.get("url") or "",
                source=a.get("source") or "")


def _front_page_docs(ed: dict) -> list[dict]:
    docs = []
    overview = [ed.get("headline") or ""]
    for k in ("day_in_context", "contrarian_corner"):
        v = ed.get(k)
        if isinstance(v, dict):
            v = " ".join(str(x) for x in v.values() if isinstance(x, str))
        if v:
            overview.append(str(v))
    if any(overview):
        docs.append(_doc("Today's front page: %s" % (ed.get("headline") or ed.get("id")),
                         "\n".join(overview), origin="front_page:" + str(ed.get("id"))))
    stories = []
    if ed.get("lead"):
        stories.append(ed["lead"])
    for sec in ed.get("sections") or []:
        stories += list((sec or {}).get("articles") or [])
    seen = set()
    for a in stories:
        d = _story_doc(a)
        if d and d["title"] not in seen:
            seen.add(d["title"])
            docs.append(d)
        if len(docs) > MAX_STORIES:
            break
    return docs


def _weekly_docs(dg: dict) -> list[dict]:
    docs = []
    for s in dg.get("top_stories") or []:
        if isinstance(s, dict) and s.get("title"):
            docs.append(_doc(s["title"], "%s\n%s" % (s["title"], s.get("why") or "")))
    if dg.get("trends"):
        docs.append(_doc("Trends this week", "\n".join("- %s" % t for t in dg["trends"])))
    if dg.get("editorial"):
        docs.append(_doc("Friday's note on the week", str(dg["editorial"])))
    return docs


def _markdown_docs(md: str, label: str, private: bool) -> list[dict]:
    """One document per section of a markdown run (split at headings)."""
    parts, cur_title, cur = [], label, []
    for line in (md or "").splitlines():
        m = re.match(r"^\s{0,3}#{1,3}\s+(.*)$", line)
        if m:
            if "".join(cur).strip():
                parts.append((cur_title, "\n".join(cur)))
            cur_title, cur = m.group(1).strip() or label, []
        else:
            cur.append(line)
    if "".join(cur).strip():
        parts.append((cur_title, "\n".join(cur)))
    return [_doc(t, x, private=private, origin=label) for t, x in parts[:MAX_STORIES]]


def sidecar_path(run_id: str) -> Path:
    """The Briefing run's structured sources: calendar events and news items."""
    from agent_friday.core import FRIDAY_DIR
    if not _RUN_ID_RE.match(run_id or ""):
        raise ValueError("not a run id")
    return Path(FRIDAY_DIR) / "briefing_runs" / f"{run_id}.json"


#: Digest sections the episode rebuilds from structured data instead of
#: paraphrasing (the calendar and the news), matched on the heading.
_REBUILT_SECTION_RE = re.compile(r"calendar|news", re.I)


def _heading_title(heading: str) -> str:
    """"2. Top News (relevant to you)" -> "Top News"."""
    t = re.sub(r"^\s*\d{1,2}[.)]\s*", "", heading or "")
    t = re.sub(r"\s*\([^)]*\)\s*$", "", t).strip()
    return t or "Briefing"


def briefing_docs(side: dict, markdown: str, run_id: str) -> list[dict]:
    """The Briefing's sources: one per story (outlet and link), one per calendar
    event (its times), then Friday's own written sections that are not
    rebuilt from those (tasks, analysis, insight) as context."""
    docs = []
    for a in (side.get("news") or [])[:MAX_STORIES]:
        if not isinstance(a, dict) or not a.get("title"):
            continue
        d = _story_doc(a)
        if d:
            d.update(private=True, role="story")
            docs.append(d)
    for ev in (side.get("calendar") or [])[:20]:
        if not isinstance(ev, dict) or ev.get("error") or not ev.get("title"):
            continue
        from agent_friday.services.podcast_quality import clock_text
        start, end = clock_text(ev.get("start_time") or ""), clock_text(ev.get("end_time") or "")
        when = ("%s to %s" % (start, end)) if start and end else (start or "all day")
        text = "On your calendar: %s, %s" % (when, ev["title"])
        if ev.get("location"):
            text += ", at %s" % ev["location"]
        docs.append({"title": ev["title"][:200], "kind": "event", "role": "event",
                     "text": text + ".", "url": "", "origin": "calendar", "private": True,
                     "outlet": "", "start": ev.get("start_time") or "",
                     "end": ev.get("end_time") or "", "location": ev.get("location") or ""})
    for d in _markdown_docs(markdown, "Briefing %s" % run_id, private=True):
        if _REBUILT_SECTION_RE.search(d["title"]):
            continue
        heading = d["title"]
        d.update(kind="digest", role="digest", heading=heading,
                 title="Friday's written briefing: %s" % _heading_title(heading))
        docs.append(d)
    return [d for d in docs if d["text"]]


def run_documents(routine: str, run_id: str) -> list[dict]:
    from agent_friday.services.podcast_sources import SourceError
    try:
        p = run_path(routine, run_id)
    except ValueError as e:
        raise SourceError(str(e)) from e
    if not p.is_file():
        raise SourceError("that %s run is not on disk (%s)" % (routine.replace("_", " "), run_id))
    if p.suffix == ".json":
        data = json.loads(p.read_text(encoding="utf-8"))
        docs = _front_page_docs(data) if routine == "front_page" else _weekly_docs(data)
    else:
        text = p.read_text(encoding="utf-8")
        side = None
        if routine == "briefing":
            try:
                side = json.loads(sidecar_path(run_id).read_text(encoding="utf-8"))
            except (OSError, ValueError):
                side = None
        if side:
            docs = briefing_docs(side, text, run_id)
        else:
            # A run from before its sources were kept: the written sections,
            # headings cleaned so none is read out as a heading.
            docs = _markdown_docs(text, "%s %s" % (routine.title(), run_id),
                                  private=(routine == "briefing"))
            for d in docs:
                d.update(role="digest", heading=d["title"], title=_heading_title(d["title"]))
    docs = [d for d in docs if d["text"]]
    if not docs:
        raise SourceError("that run has no stories to talk about")
    return docs


def queue_for_run(routine: str, run_id: str) -> dict | None:
    """Queue this run's episode. Never raises: a routine must not fail on it."""
    try:
        from agent_friday.services import podcast_engine as pe
        cfg = pe.settings()
        if not (cfg.get("enabled_for_routines") or {}).get(routine, True):
            return None
        if not run_id:
            return None
        run_path(routine, run_id)        # validates the id
        prev = pe.for_run(routine, run_id)
        ep = pe.create([{"kind": "news_run", "routine": routine, "run_id": run_id}],
                       length=(cfg.get("length") or {}).get(routine) or "short",
                       origin="routine",
                       attached={"routine": routine, "run_id": run_id})
        if prev and prev.get("id") != ep["id"]:
            # A regenerated run replaces its episode: stop the old one if it is
            # still pending, and point it at the new one.
            pe.cancel(prev["id"])
            pe._update(prev["id"], superseded_by=ep["id"])
        return ep
    except Exception as e:
        log.warning("podcast for %s %s was not queued: %s", routine, run_id, e)
        return None

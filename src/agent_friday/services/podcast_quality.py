"""The script-quality gate: what a script must do before it is spoken.

The listening check (`podcast_render.listen_back`) only confirms that the
audio says what the script says. This module checks the script itself,
against the episode's own story list and calendar:

* **ledes** — a story's first mention says what happened, who or where, when,
  and names the outlet aloud, and the story is then tied to the listener.
  Nothing is referenced as if the listener had read the written digest.
* **safety** — a story about violence, death or local safety is introduced
  plainly and attributed, never filed as "background", and gets a practical
  line when it happened where the listener is going that day.
* **times** — every clock time is one the calendar (or a source) gives, and
  "before" / "after" agree with the calendar's order.
* **repetition** — the writer's own words (ones no source uses) are said at
  most twice, no line restates an earlier one, and the close adds rather
  than re-reads the opening.
* **density** (solo news formats) — enough distinct stories and events per
  spoken minute to be worth the time.
* **headings** — no outline heading from a source is read out, and the
  written digest is never pointed at ("my written briefing says").
* **fragments** — at most one flat verbless fragment ("It's context.").
* **link claim** — "linked in the transcript" is said only when every story
  heard has a link.

`script_problems` returns problems as dicts with a `code`, the line index
(if any) and a plain `message` the writer is shown on a revision pass.
"""

from __future__ import annotations

import math
import re
from datetime import datetime

WORDS_PER_MINUTE = 150
MAX_OWN_WORD_REPEATS = 2
RESTATE_OVERLAP = 0.6
MIN_LEDE_WORDS = 14
SOLO_NEWS_PER_MINUTE = 2.0

_STOP = set("""
a about above after again against all also am an and any are as at be because been before
being below between both but by can could did do does doing down during each even every few
for from further had has have having he her here hers him his how i if in into is it its
itself just me more most my no nor not now of off on once only or other our ours out over own
same she should so some such than that the their theirs them then there these they this those
through to too under until up very was we were what when where which while who whom why will
with would you your yours yourself going gets get got make makes made say says said one two
three today still really thing things know think want need like well back here there that's
it's you're we're they're don't isn't aren't doesn't didn't can't won't i'm i'll you'll
""".split())
#: The plain words a newscast is built from (attribution, time, the listener's
#: day). Saying them often is the form, not a tic, so the repetition limit
#: does not count them.
_PLAIN_NEWS = {w[:5] for w in """reports reported report according confirmed confirms
announced says said adds added story stories interview interviews calendar meeting
morning afternoon evening tonight tomorrow yesterday week weekend month""".split()}
_MONTHS = ("january february march april may june july august september october "
           "november december").split()
_DAYS = "monday tuesday wednesday thursday friday saturday sunday".split()

_WORD_RE = re.compile(r"[A-Za-z][A-Za-z'’-]+")
_CLOCK_RE = re.compile(r"\b(\d{1,2})(?::(\d{2}))?\s*(a\.?\s?m\.?|p\.?\s?m\.?)(?![a-z])", re.I)
_WHEN_RE = re.compile(
    r"\b(today|tonight|yesterday|overnight|this (morning|afternoon|evening|week|weekend)|"
    r"last (night|week|month|quarter|year)|this (month|quarter)|in (%s)|"
    r"earlier (today|this week)|on (%s)|(%s)|(%s) \d{1,2}|"
    r"\d{1,2}(:\d{2})?\s*(a\.?m\.?|p\.?m\.?)|hours ago|this year|since (%s))\b"
    % ("|".join(_MONTHS), "|".join(_DAYS), "|".join(_DAYS), "|".join(_MONTHS),
       "|".join(_DAYS + _MONTHS)), re.I)
_YOU_RE = re.compile(r"\b(you|your|you're|you'll|yours)\b", re.I)
_ATTRIB_RE = re.compile(r"\b(said|says|say|according to|police|officials|authorities|reported|"
                        r"reports|confirmed|investigators)\b", re.I)
_DISMISS_RE = re.compile(r"\b(background|noise|distraction|side note|sideshow|footnote|"
                         r"not (?:a|your) (?:priority|concern))\b", re.I)
_SAFETY_RE = re.compile(r"\b(shoot\w*|shot|gunman|gunfire|killed|kills|dead|death|dies|died|"
                        r"murder\w*|stabb\w*|homicide|bomb\w*|explosion|terror\w*|hostage|"
                        r"fatal\w*|assault\w*|injured|wounded|victims?)\b", re.I)
_RELATION_RE = re.compile(r"\b(before|ahead of|prior to|after|following)\b\s+((?:[\w'’:.-]+\s*){1,6})",
                          re.I)
_FRAGMENT_RE = re.compile(r"^(it'?s|it is|that'?s|that is|this is)\s+(\w+(?:\s+\w+)?)\s*[.!]$", re.I)
_HEADING_RE = re.compile(r"(?:^|[.!?]\s+)\d{1,2}\.\s+[A-Z][\w&'’]+(?:\s+[\w&'’()]+){0,5}")
_PART_OF_DAY = {"morning": (0, 12 * 60), "afternoon": (12 * 60, 17 * 60),
                "evening": (17 * 60, 24 * 60), "tonight": (17 * 60, 24 * 60)}

#: Outlets whose spoken name is not their domain. Anything else is matched
#: by its domain name ("kxan.com" is said "KXAN").
#: The first name is how the outlet is written and said; the rest are other
#: ways it is said aloud.
OUTLET_NAMES = {
    "theguardian.com": ["The Guardian"], "apnews.com": ["AP", "Associated Press", "AP News"],
    "reuters.com": ["Reuters"], "nytimes.com": ["The New York Times", "The Times"],
    "bloomberg.com": ["Bloomberg"], "theverge.com": ["The Verge"],
    "techcrunch.com": ["TechCrunch"], "wsj.com": ["The Wall Street Journal", "The Journal"],
    "washingtonpost.com": ["The Washington Post"], "bbc.co.uk": ["the BBC", "BBC"],
    "bbc.com": ["the BBC", "BBC"], "cnbc.com": ["CNBC"], "cnn.com": ["CNN"],
    "axios.com": ["Axios"], "npr.org": ["NPR"], "arstechnica.com": ["Ars Technica"],
    "wired.com": ["Wired"], "techmeme.com": ["Techmeme"],
    "ft.com": ["The Financial Times", "The FT"], "economist.com": ["The Economist"],
    "politico.com": ["Politico"], "semafor.com": ["Semafor"], "404media.co": ["404 Media"],
    "abcnews.go.com": ["ABC News"], "abcnews.com": ["ABC News"], "nbcnews.com": ["NBC News"],
    "cbsnews.com": ["CBS News"], "foxnews.com": ["Fox News"], "salon.com": ["Salon"],
    "theatlantic.com": ["The Atlantic"], "latimes.com": ["The Los Angeles Times"],
    "usatoday.com": ["USA Today"], "engadget.com": ["Engadget"], "zdnet.com": ["ZDNet"],
}
#: Labels of a host name that are never the outlet's name.
_HOST_NOISE = {"www", "m", "amp", "go", "news", "co", "com", "org", "net", "uk", "rss", "feeds"}


def _site_name(dom: str) -> str:
    """"abcnews.go.com" -> "abcnews"; "kxan.com" -> "kxan"."""
    labels = [x for x in dom.split(".") if x]
    named = [x for x in labels[:-1] if x not in _HOST_NOISE] or labels[:1]
    return named[0] if named else ""


# ── helpers ─────────────────────────────────────────────────────────────────

def _words(text: str) -> list[str]:
    return [w.lower().strip("'’-") for w in _WORD_RE.findall(text or "")]


def _stem(w: str) -> str:
    return w.lower()[:5]


def _content(text: str) -> list[str]:
    return [w for w in _words(text) if len(w) >= 4 and w not in _STOP]


def _minutes(h: str, m: str | None, ap: str) -> int:
    h = int(h) % 12
    if ap.lower().startswith("p"):
        h += 12
    return h * 60 + int(m or 0)


def _clock_minutes(iso: str) -> int | None:
    try:
        t = datetime.fromisoformat(str(iso).replace("Z", "+00:00"))
    except ValueError:
        return None
    if len(str(iso)) <= 10:         # an all-day event has a date, no time
        return None
    return t.hour * 60 + t.minute


def clock_text(iso: str) -> str:
    """"9:00 AM" for an ISO time, or "" for an all-day date."""
    m = _clock_minutes(iso)
    if m is None:
        return ""
    h, mm = divmod(m, 60)
    return "%d:%02d %s" % ((h % 12) or 12, mm, "AM" if h < 12 else "PM")


def outlet_aliases(story: dict) -> list[str]:
    """How the outlet may be named aloud, lower case."""
    names = []
    title = story.get("title") or ""
    m = re.search(r"\s[-–—]\s([^-–—]{2,60})$", title)     # "Headline - Outlet"
    if m:
        names.append(m.group(1).strip().lower())
    dom = (story.get("outlet") or "").lower().removeprefix("www.")
    if dom:
        names += [n.lower() for n in OUTLET_NAMES.get(dom, [])]
        if dom != "news.google.com":
            names.append(_site_name(dom))
    return [n for n in dict.fromkeys(names) if n]


def spoken_outlet(story: dict) -> str:
    """The outlet as it is written and said: "The Guardian", "ABC News", "KXAN"."""
    dom = (story.get("outlet") or "").lower().removeprefix("www.")
    if dom in OUTLET_NAMES:
        return OUTLET_NAMES[dom][0]
    m = re.search(r"\s[-–—]\s([^-–—]{2,60})$", story.get("title") or "")
    if m:
        return m.group(1).strip()
    name = _site_name(dom) if "." in dom else dom
    if not name:
        return ""
    return name.upper() if len(name) <= 5 else name.capitalize()


def said_outlet(text: str, aliases: list[str]) -> bool:
    """Whether the outlet is named in `text`. A short name ("AP") must stand as
    a word; a longer one may be said as separate words ("Example Wire" for
    examplewire.com)."""
    low = (text or "").lower()
    squashed = re.sub(r"[^a-z0-9]", "", low)
    for a in aliases:
        if len(a.replace(" ", "")) <= 4:
            if re.search(r"\b%s\b" % re.escape(a), low):
                return True
        elif re.sub(r"[^a-z0-9]", "", a) in squashed:
            return True
    return False


def is_safety_story(story: dict) -> bool:
    return bool(_SAFETY_RE.search("%s %s" % (story.get("title") or "", story.get("text") or "")))


def _caps(text: str, *, initial: bool) -> set[str]:
    out = set()
    for sent in re.split(r"(?<=[.!?;])\s+|\n", text or ""):
        for i, tok in enumerate(sent.split()):
            w = re.sub(r"['’]s$", "", tok.strip(".,;:!?()\"'“”‘’"))
            if (initial or i > 0) and len(w) >= 3 and w[0].isupper() \
                    and w.lower() not in _STOP and w.lower() not in _MONTHS + _DAYS:
                out.add(w.lower())
    return out


def _entities(title: str, text: str) -> set[str]:
    """Who and where (proper names) and the figures a story is about.

    A capital at the start of a sentence, or anywhere in a title-case
    headline, is not evidence of a name; a word capitalised mid-sentence in
    the story's text is. A title word counts when the text confirms it.
    """
    figures = {w.lower() for w in re.findall(r"\$?\d[\d,.]*[%BbMmKk]?\b", "%s %s" % (title, text))
               if not re.fullmatch(r"\d{1,2}", w)}
    body = _caps(text, initial=False)
    if not body:
        # The text names no one: the headline's own names are all there is.
        body = _caps(title, initial=False)
    # The headline's first word, when it is a name ("Robinhood unveils ...").
    first = re.sub(r"['’]s$", "", (title.split() or [""])[0].strip(".,;:!?\"'“”‘’"))
    lead = ({first.lower()} if len(first) >= 3 and first[0].isupper()
            and first.lower() not in _STOP and first.lower() not in _MONTHS + _DAYS else set())
    return body | lead | ({w for w in _caps(title, initial=True) if w in body}) | figures


def _places(text: str) -> set[str]:
    return {m.group(1).lower() for m in re.finditer(
        r"\b(?:in|at|near|outside|across)\s+([A-Z][a-z]{2,})", text or "")}


# ── the story list ──────────────────────────────────────────────────────────

def stories(docs: list[dict]) -> list[dict]:
    """The episode's stories: news documents with a title (not calendar events,
    computed facts or the written digest)."""
    out = []
    for d in docs:
        if d.get("kind") not in ("news", "story") or not d.get("title"):
            continue
        if d.get("role") in ("digest", "overview", "event") or _HEADING_RE.match(d["title"]):
            continue
        body = "%s\n%s" % (d.get("title"), d.get("text") or "")
        text = re.sub(r"(?m)^Outlet:.*$", "", d.get("text") or "")
        if text.strip().startswith(d["title"]):
            text = text.strip()[len(d["title"]):]
        ents = _entities(d["title"], text)
        out.append({"sid": d["sid"], "title": d["title"], "url": d.get("url") or "",
                    "outlet": d.get("outlet") or "", "text": d.get("text") or "",
                    "entities": ents, "places": _places(body),
                    "keys": {_stem(w) for w in _content(d["title"])},
                    "safety": is_safety_story(d)})
    for s in out:
        others = set().union(*[o["entities"] for o in out if o is not s]) if len(out) > 1 else set()
        s["unique"] = s["entities"] - others
    return out


def events(docs: list[dict]) -> list[dict]:
    out = []
    for d in docs:
        if d.get("kind") != "event":
            continue
        start = _clock_minutes(d.get("start") or "")
        end = _clock_minutes(d.get("end") or "")
        out.append({"sid": d["sid"], "title": d.get("title") or "", "start": start, "end": end,
                    "keys": {_stem(w) for w in _content(d.get("title") or "")},
                    "places": {p.strip().lower() for p in re.split(r"[,\n]", d.get("location") or "")
                               if re.fullmatch(r"[A-Za-z][A-Za-z .'-]{2,}", p.strip() or "")
                               and len(p.strip()) > 2}
                    # A city is often in the event's name ("Makers meetup Springfield").
                    | {w.lower().strip(":,") for w in (d.get("title") or "").split()
                       if len(w.strip(":,")) >= 4 and w[0].isupper() and w.strip(":,").isalpha()
                       and w.lower().strip(":,") not in _STOP}})
    return out


def _mentions(line: dict, story: dict) -> bool:
    if story["sid"] in (line.get("cites") or []):
        return True
    ws = set(_words(line["text"])) | {w.lower() for w in re.findall(r"\$?\d[\d,.]*[%BbMmKk]?", line["text"])}
    if story["unique"] & ws:
        return True
    return len({_stem(w) for w in _content(line["text"])} & story["keys"]) >= 3


def _spoken(lines: list[dict]) -> list[tuple[int, dict]]:
    return [(i, ln) for i, ln in enumerate(lines) if not ln.get("signature")]


def first_mentions(lines: list[dict], story_list: list[dict]) -> dict:
    """{sid: index of the line that first mentions the story}."""
    out = {}
    for s in story_list:
        for i, ln in _spoken(lines):
            if _mentions(ln, s):
                out[s["sid"]] = i
                break
    return out


# ── checks ──────────────────────────────────────────────────────────────────

def _p(code: str, message: str, line: int | None = None, sid: str = "") -> dict:
    return {"code": code, "message": message, "line": line, "sid": sid}


def lede_problems(lines: list[dict], story_list: list[dict], *, personal: bool = True) -> list[dict]:
    out = []
    fm = first_mentions(lines, story_list)
    for s in story_list:
        i = fm.get(s["sid"])
        if i is None:
            continue
        window = " ".join(ln["text"] for ln in lines[i:i + 2] if not ln.get("signature"))
        low = window.lower()
        wws = set(_words(window)) | {w.lower() for w in re.findall(r"\$?\d[\d,.]*[%BbMmKk]?", window)}
        missing = []
        aliases = outlet_aliases(s)
        if aliases and not said_outlet(window, aliases):
            missing.append("the outlet, named aloud (%s)" % (spoken_outlet(s) or s["outlet"]))
        if not (s["entities"] & wws):
            missing.append("who or where")
        if s["places"] and not (s["places"] & wws):
            missing.append("where (%s)" % ", ".join(sorted(p.title() for p in s["places"])))
        if not _WHEN_RE.search(window):
            missing.append("when")
        if len({_stem(w) for w in _content(window)} & s["keys"]) < min(2, len(s["keys"])) \
                or len(_words(window)) < MIN_LEDE_WORDS:
            missing.append("what happened")
        if personal:
            after = [ln["text"] for ln in lines[i:i + 3] if not ln.get("signature")]
            if not any(_YOU_RE.search(t) for t in after):
                missing.append("why it matters to you")
        if missing:
            out.append(_p("no_lede", "\"%s\" is first mentioned without a spoken lede; it lacks %s."
                          % (s["title"][:90], ", ".join(missing)), i, s["sid"]))
    return out


def _dismissed(text: str) -> bool:
    """Called background or noise, and not in the negative ("it is not
    background")."""
    for m in _DISMISS_RE.finditer(text):
        before = text[max(0, m.start() - 24):m.start()].lower()
        if not re.search(r"\b(not|isn't|aren't|never|no longer|hardly)\b[\w\s']{0,14}$", before):
            return True
    return False


def safety_problems(lines: list[dict], story_list: list[dict], event_list: list[dict]) -> list[dict]:
    out = []
    fm = first_mentions(lines, story_list)
    near = set().union(*[e["places"] for e in event_list]) if event_list else set()
    for s in story_list:
        if not s["safety"] or s["sid"] not in fm:
            continue
        i = fm[s["sid"]]
        idx = [j for j, ln in _spoken(lines) if j == i or _mentions(ln, s)]
        text = " ".join(lines[j]["text"] for j in idx)
        if _dismissed(text):
            out.append(_p("safety_dismissed", "\"%s\" is a story about harm to people; it is "
                          "called background or noise instead of being introduced plainly."
                          % s["title"][:90], i, s["sid"]))
        window = " ".join(ln["text"] for ln in lines[i:i + 2] if not ln.get("signature"))
        if not _ATTRIB_RE.search(window):
            out.append(_p("safety_unattributed", "\"%s\" must say who confirmed what (police, "
                          "officials, the outlet), and only what is confirmed." % s["title"][:90],
                          i, s["sid"]))
        story_words = set(_words("%s %s" % (s["title"], s["text"])))
        close = {p for p in near if set(p.split()) <= story_words}
        if close:
            # The practical line ties this story to the event it is near: a
            # line of the story (or the one after it) that names that event.
            near_events = [e for e in event_list if e["places"] & close]
            span = sorted({j for k in idx for j in (k, k + 1) if j < len(lines)})
            place_stems = {_stem(w) for p in close for w in p.split()}

            def names_event(text, e):
                # The event by its time, or by a word of its name that is not
                # the shared place (saying "Springfield" is not naming it).
                clocks = {_minutes(*m.groups()) for m in _CLOCK_RE.finditer(text)}
                # Two words of its name: one ("local") is too common to name it.
                return (e["start"] is not None and e["start"] in clocks) or \
                    len((e["keys"] - place_stems) & {_stem(w) for w in _content(text)}) >= 2
            practical = any(
                {e["sid"] for e in near_events} & set(lines[j].get("cites") or [])
                or any(names_event(lines[j]["text"], e) for e in near_events)
                for j in span if not lines[j].get("signature"))
            if not practical:
                out.append(_p("safety_no_practical_line", "\"%s\" happened in %s, where you are "
                              "going today; say plainly what that means for your plans."
                              % (s["title"][:90], ", ".join(sorted(c.title() for c in close))),
                              i, s["sid"]))
    return out


def _events_in(text: str, event_list: list[dict]) -> list[dict]:
    got = []
    low = text.lower()
    clocks = {_minutes(*m.groups()) for m in _CLOCK_RE.finditer(text)}
    stems = {_stem(w) for w in _content(text)}
    for e in event_list:
        if (e["start"] is not None and e["start"] in clocks) or (e["keys"] & stems):
            got.append(e)
    for word, (lo, hi) in _PART_OF_DAY.items():
        if re.search(r"\b(the|this|tonight's|your)\s+%s\b" % word, low) or \
                (word == "tonight" and re.search(r"\btonight\b", low)):
            got += [e for e in event_list if e["start"] is not None and lo <= e["start"] < hi
                    and e not in got]
    return got


def time_problems(lines: list[dict], event_list: list[dict], docs: list[dict]) -> list[dict]:
    if not event_list:
        return []
    out = []
    source_clocks = set()
    for d in docs:
        if d.get("kind") == "event":
            continue
        source_clocks |= {_minutes(*m.groups()) for m in _CLOCK_RE.finditer(d.get("text") or "")}
    known = {e["start"] for e in event_list} | {e["end"] for e in event_list} | source_clocks
    known.discard(None)
    spoken = _spoken(lines)
    for k, (i, ln) in enumerate(spoken):
        for m in _CLOCK_RE.finditer(ln["text"]):
            if _minutes(*m.groups()) not in known:
                out.append(_p("time_not_in_calendar", "\"%s\" is not a time in your calendar "
                              "or the sources." % m.group(0).strip(), i))
        for m in _RELATION_RE.finditer(ln["text"]):
            rel, phrase = m.group(1).lower(), m.group(2)
            targets = _events_in(phrase, event_list)
            if not targets:
                continue
            rest = ln["text"][:m.start()] + " " + ln["text"][m.end():]
            subjects = [e for e in _events_in(rest, event_list) if e not in targets]
            if not subjects and k > 0:
                subjects = [e for e in _events_in(spoken[k - 1][1]["text"], event_list)
                            if e not in targets]
            for sub in subjects:
                if sub["start"] is None:
                    continue
                starts = [t["start"] for t in targets if t["start"] is not None]
                if not starts:
                    continue
                wrong = (rel in ("before", "ahead of", "prior to") and sub["start"] >= min(starts)) \
                    or (rel in ("after", "following") and sub["start"] <= max(starts))
                if wrong:
                    out.append(_p("time_order", "\"%s %s\" is wrong: %s is at %s and %s at %s."
                                  % (rel, phrase.strip()[:40], sub["title"][:50],
                                     _fmt(sub["start"]), ", ".join(t["title"][:40] for t in targets),
                                     ", ".join(_fmt(t["start"]) for t in targets
                                               if t["start"] is not None)), i))
                    break
    return out


def _fmt(m: int) -> str:
    h, mm = divmod(m, 60)
    return "%d:%02d %s" % ((h % 12) or 12, mm, "AM" if h < 12 else "PM")


def repetition_problems(lines: list[dict], docs: list[dict], n_chapters: int) -> list[dict]:
    out = []
    vocab = set()
    for d in docs:
        if d.get("role") == "digest":
            continue            # Friday's own notes: their phrases are hers, not a source's
        vocab |= {_stem(w) for w in _words("%s %s" % (d.get("title") or "", d.get("text") or ""))}
    spoken = _spoken(lines)
    counts: dict[str, list] = {}
    for i, ln in spoken:
        for w in _content(ln["text"]):
            if _stem(w) not in vocab and _stem(w) not in _PLAIN_NEWS:
                counts.setdefault(_stem(w), []).append((i, w))
    for _st, hits in counts.items():
        if len(hits) > MAX_OWN_WORD_REPEATS:
            out.append(_p("repeats_word", "\"%s\" is said %d times; it is not in any source, so it "
                          "is the script's own phrase. Say it at most %d times."
                          % (hits[0][1], len(hits), MAX_OWN_WORD_REPEATS), hits[-1][0]))
    last = max([ln.get("chapter", 0) for _i, ln in spoken] or [0])
    for k, (i, ln) in enumerate(spoken):
        a = set(_content(ln["text"]))
        if len(a) < 5:
            continue
        for j, prev in spoken[:k]:
            b = set(_content(prev["text"]))
            if len(a & b) / len(a) >= RESTATE_OVERLAP:
                code = "close_restates" if ln.get("chapter", 0) == last and n_chapters > 1 \
                    and prev.get("chapter", 0) != last else "restates"
                out.append(_p(code, "This line restates an earlier one (line %d) instead of "
                              "adding something: \"%s\"" % (j, ln["text"][:90]), i))
                break
    return out


def density_problems(lines: list[dict], docs: list[dict]) -> list[dict]:
    items = [d["sid"] for d in docs if d.get("kind") in ("news", "story", "event")
             and d.get("role") not in ("digest", "overview")]
    if not items:
        return []
    words = sum(len(_words(ln["text"])) for _i, ln in _spoken(lines))
    minutes = max(1.0, words / WORDS_PER_MINUTE)
    covered = set()
    story_list = stories(docs)
    fm = first_mentions(lines, story_list)
    covered |= set(fm)
    ev = {d["sid"] for d in docs if d.get("kind") == "event"}
    for _i, ln in _spoken(lines):
        covered |= set(ln.get("cites") or []) & ev
    need = min(len(items), math.ceil(SOLO_NEWS_PER_MINUTE * minutes))
    if len(covered) < need:
        return [_p("low_density", "%d stories and events in about %.0f minutes; a newscast this "
                   "long covers at least %d. Cover more of the list, and spend fewer words on each."
                   % (len(covered), minutes, need))]
    return []


def heading_problems(lines: list[dict], docs: list[dict]) -> list[dict]:
    out = []
    heads = [(d.get("heading") or d["title"]).lower() for d in docs
             if d.get("role") == "digest" and len(_words(d.get("heading") or d["title"])) >= 2]
    heads = [h for h in heads if not h.startswith("friday")]
    for i, ln in _spoken(lines):
        low = ln["text"].lower()
        if _HEADING_RE.search(ln["text"]) or any(h in low for h in heads):
            out.append(_p("heading_read_aloud", "An outline heading is read out as speech: \"%s\""
                          % ln["text"][:90], i))
    return out


_DIGEST_REF_RE = re.compile(r"\b(written briefing|my (notes|briefing|digest)|the (digest|written notes))\b", re.I)


def digest_problems(lines: list[dict]) -> list[dict]:
    """The listener never read the written digest: say the thing, not where it was written."""
    return [_p("digest_referenced", "This line points at the written briefing instead of saying "
               "the thing itself: \"%s\"" % ln["text"][:90], i)
            for i, ln in _spoken(lines) if _DIGEST_REF_RE.search(ln["text"])]


_REFRAIN_RE = re.compile(r"\b(i|we)\s+(did not|didn't|have not|haven't|could not|couldn't)\s+"
                         r"(check|verify|confirm)\w*", re.I)
_ADDRESS_RE = re.compile(r"\b[A-Z]{2},?\s+\d{5}(?:-\d{4})?\b|,\s*(USA|United States)\b")


def refrain_problems(lines: list[dict]) -> list[dict]:
    """What she did not check is said once, where it matters."""
    hits = [i for i, ln in _spoken(lines) for _m in _REFRAIN_RE.finditer(ln["text"])]
    if len(hits) > 1:
        return [_p("refrain", "\"I did not check\" is said %d times; say what you did not check "
                   "once, where it matters most." % len(hits), hits[1])]
    return []


def address_problems(lines: list[dict]) -> list[dict]:
    """A place is said the way a person says it: no postal code or country."""
    return [_p("reads_address", "A postal address is read out whole: \"%s\". Say the venue or "
               "the street." % ln["text"][:90], i)
            for i, ln in _spoken(lines) if _ADDRESS_RE.search(ln["text"])]


def fragment_problems(lines: list[dict]) -> list[dict]:
    frags = []
    for i, ln in _spoken(lines):
        for sent in re.split(r"(?<=[.!?])\s+", ln["text"]):
            if _FRAGMENT_RE.match(sent.strip()):
                frags.append((i, sent.strip()))
    if len(frags) > 1:
        return [_p("flat_fragments", "Flat fragments read like a summary being recited: %s. "
                   "Say what the thing is and why it matters, in a full sentence."
                   % ", ".join('"%s"' % f for _i, f in frags[:4]), frags[1][0])]
    return []


LINK_CLAIM_RE = re.compile(r"\blinked\b", re.I)


def link_claim_ok(lines: list[dict], story_list: list[dict]) -> bool:
    """Every story the listener heard has a working (http/https) link."""
    heard = first_mentions(lines, story_list)
    return bool(heard) and all(
        re.match(r"^https?://[^\s/]+\.[^\s]+", s["url"] or "") for s in story_list if s["sid"] in heard)


def link_claim_problems(lines: list[dict], story_list: list[dict]) -> list[dict]:
    claims = [i for i, ln in enumerate(lines) if LINK_CLAIM_RE.search(ln["text"])]
    if claims and not link_claim_ok(lines, story_list):
        return [_p("false_link_claim", "The script says the stories are linked, but not every "
                   "story heard has a link.", claims[0])]
    return []


CHECKS = ("ledes", "safety stories", "calendar times", "repetition", "restated close",
          "headings", "digest references", "refrains", "addresses", "fragments",
          "link claim", "density")


def script_problems(lines: list[dict], docs: list[dict], *, n_chapters: int = 1,
                    news: bool = False, personal: bool = False, solo: bool = False) -> list[dict]:
    """Every problem with the script, in reading order."""
    story_list = stories(docs)
    event_list = events(docs)
    probs = []
    if news:
        probs += lede_problems(lines, story_list, personal=personal)
        probs += safety_problems(lines, story_list, event_list)
    probs += time_problems(lines, event_list, docs)
    probs += repetition_problems(lines, docs, n_chapters)
    probs += heading_problems(lines, docs)
    probs += digest_problems(lines)
    probs += refrain_problems(lines)
    probs += address_problems(lines)
    probs += fragment_problems(lines)
    probs += link_claim_problems(lines, story_list)
    if news and solo:
        probs += density_problems(lines, docs)
    return sorted(probs, key=lambda p: (p["line"] is None, p["line"] or 0))

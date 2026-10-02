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
* **support** — a sentence in a line that cites a story is supported by that
  story's text. Friday's commentary carries no outlet's name: it is her own
  line (`own`), and a claim no cited source makes is cut.
* **one place** — each story is told once; the close is one sentence of
  synthesis; a story of violence or a threat stands alone, never a thread
  in another.

The codes in `HARD_CODES` block an episode: the engine edits or rewrites
the script until none is left, and never speaks one that has any.

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
morning afternoon evening tonight tomorrow yesterday week weekend month
read""".split()}
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
                        r"fatal\w*|assault\w*|injured|wounded|victims?|attack(?:s|ed|er|ers)?|"
                        r"plot(?:s|ted)? to|credible threats?|threat(?:s|ened)? (?:against|to kill|toward)|"
                        r"arrest(?:s|ed)?|armed|weapons?|firearms?|kidnap\w*|abduct\w*)\b", re.I)
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
    "thehill.com": ["The Hill"], "talkingpointsmemo.com": ["Talking Points Memo", "TPM"],
    "platformer.news": ["Platformer"], "businessinsider.com": ["Business Insider"],
}
#: Sites that relay other outlets' stories, naming the original in the headline.
_AGGREGATORS = {"techmeme.com", "news.google.com", "memeorandum.com"}

#: Labels of a host name that are never the outlet's name.
_HOST_NOISE = {"www", "m", "amp", "go", "news", "co", "com", "org", "net", "uk", "rss", "feeds"}


def _site_name(dom: str) -> str:
    """"abcnews.go.com" -> "abcnews"; "kxan.com" -> "kxan"."""
    labels = [x for x in dom.split(".") if x]
    named = [x for x in labels[:-1] if x not in _HOST_NOISE] or labels[:1]
    return named[0] if named else ""


# ── helpers ─────────────────────────────────────────────────────────────────

def _words(text: str) -> list[str]:
    """Lower-case words, possessives bare ("Trump's" is "trump")."""
    return [re.sub(r"['’]s$", "", w.lower().strip("'’-")) for w in _WORD_RE.findall(text or "")]


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
    m = re.search(r"\((?:[^()/]{1,60}/\s*)?([^()/]{2,60})\)\s*$", title)   # "Headline (Author / Outlet)"
    if m and (story.get("outlet") or "").lower().removeprefix("www.") in _AGGREGATORS:
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
    if len(name) > 6 and name.startswith("the") and name.isalpha():
        return "The " + name[3:].capitalize()       # "thetribune" is "The Tribune"
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
    # A word the sources also write in lower case ("story", "officials", "big")
    # is not a name, wherever it was capitalised.
    lowered = {w.lower() for d in docs for w in re.findall(r"\b[a-z][a-z'’-]+", d.get("text") or "")}
    for s in out:
        s["proper"] = {u for u in s["entities"] if u not in lowered or re.search(r"\d", u)}
    # One event reported by several outlets is one story: items that share two
    # names and a headline word, or three headline words, form a cluster.
    parent = {s["sid"]: s["sid"] for s in out}

    def root(x):
        while parent[x] != x:
            x = parent[x]
        return x
    for i, a in enumerate(out):
        for b in out[i + 1:]:
            shared = len(a["keys"] & b["keys"])
            if (len(a["proper"] & b["proper"]) >= 2 and shared) or shared >= 3:
                parent[root(a["sid"])] = root(b["sid"])
    groups: dict = {}
    for s in out:
        groups.setdefault(root(s["sid"]), set()).add(s["sid"])
    for s in out:
        s["cluster"] = frozenset(groups[root(s["sid"])])
    for s in out:
        others = set().union(*[o["entities"] for o in out if o["sid"] not in s["cluster"]]) \
            if len(out) > 1 else set()
        s["unique"] = s["entities"] - others
        # The story's identity: names only it uses. Topic vocabulary ("AI",
        # "state", "bill") is shared, or written in lower case, so it is never
        # one; a mention of it never counts as a mention of the story.
        vocab = set().union(*[set(_words("%s %s" % (o["title"], o["text"])))
                              for o in out if o["sid"] not in s["cluster"]]) if len(out) > 1 else set()
        s["names"] = (s["unique"] & s["proper"]) - vocab
    return out


def _cluster_words(story_list: list[dict]) -> dict:
    """{sid: every word in its cluster's items}."""
    words = {s["sid"]: set(_words("%s %s" % (s["title"], s["text"])))
             | {w.lower() for w in re.findall(r"\$?\d[\d,.]*[%BbMmKk]?\b", "%s %s" % (s["title"], s["text"]))}
             for s in story_list}
    return {s["sid"]: set().union(*[words[m] for m in s["cluster"]]) for s in story_list}


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
                               and len(p.strip()) > 2},
                    "venue": _venue_words(d.get("location") or "")})
    return out


def refs(line: dict) -> list[str]:
    """The sources a line is about: its citations, and for Friday's own line
    (no outlet's name on it) the stories it comments on."""
    return list(line.get("cites") or []) + list(line.get("about") or [])


def _mentions(line: dict, story: dict) -> bool:
    if story["sid"] in refs(line):
        return True
    ws = set(_words(line["text"])) | {w.lower() for w in re.findall(r"\$?\d[\d,.]*[%BbMmKk]?", line["text"])}
    # One of the story's own names, written as a name, or a figure of its own;
    # or two of its other words.
    written = {re.sub(r"['’]s$", "", w).lower() for w in re.findall(r"\b[A-Z][\w'’-]*", line["text"])}
    written |= {w for w in ws if re.search(r"\d", w)}
    names = story.get("names", story["unique"])
    if names & ws & written or len(story["unique"] & story.get("proper", story["unique"]) & ws) >= 2:
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


_STREET = r"(street|st|avenue|ave|road|rd|boulevard|blvd|drive|dr|lane|ln|way|plaza|square|park)"


def _venue_words(location: str) -> set:
    """The specific place in an event's location: its venue or street words,
    not the city, state, postcode or country."""
    parts = [p.strip() for p in re.split(r"[,\n]", location or "") if p.strip()]
    out = set()
    for k, part in enumerate(parts):
        street = bool(re.search(r"\d", part)) or bool(re.search(r"\b%s\.?$" % _STREET, part, re.I))
        if street or (k == 0 and len(parts) > 1):
            out |= {w.lower() for w in re.findall(r"[A-Za-z]{4,}", part)
                    if not re.fullmatch(_STREET, w, re.I) and w.lower() not in _STOP}
    return out


_DESTINATION_RE = re.compile(r"\b(where you(?:'| a)re (?:going|headed|heading)|going to be in person|"
                             r"on your way to)\b", re.I)


def home_problems(lines: list[dict], home: str) -> list[dict]:
    """The listener lives in `home`: it is "here", never a place they are going."""
    city = (home or "").split(",")[0].strip().lower()
    if not city:
        return []
    return [_p("home_as_destination", "%s is home, so it is \"here in %s\", not a place you are "
               "going: \"%s\"" % (city.title(), city.title(), ln["text"][:90]), i)
            for i, ln in _spoken(lines)
            if _DESTINATION_RE.search(ln["text"]) and city in ln["text"].lower()]


_SENT_RE = re.compile(r"(?<=[.!?])\s+")


def _hard_facts(text: str) -> set:
    """The checkable facts in a sentence: names mid-sentence, figures, days and months."""
    facts = set(_caps(text, initial=False))
    facts |= {w.lower() for w in re.findall(r"\$?\d[\d,.]*[%BbMmKk]?\b", text) if len(w) > 1}
    # A day or month only when written as one: "may" is a verb.
    facts |= {w.lower() for w in re.findall(r"\b[A-Z][a-z]+\b", text) if w.lower() in _DAYS + _MONTHS}
    return facts


def attribution_problems(lines: list[dict], story_list: list[dict], docs: list[dict],
                         home: str = "") -> list[dict]:
    """Every fact in a sentence about one story is in that story: a day, a name
    or a figure from another story is crossed over ("the hearing is set for
    Wednesday" in a sentence about a different incident)."""
    if len(story_list) < 2:
        return []
    words = _cluster_words(story_list)
    shared = set()
    for d in docs:
        if d.get("kind") in ("event", "digest") or d.get("role") in ("event", "digest"):
            shared |= set(_words("%s %s" % (d.get("title") or "", d.get("text") or "")))
    shared |= set(_words(home or ""))
    sids = {s["sid"] for s in story_list}
    out = []
    for i, ln in _spoken(lines):
        cited = [c for c in ln.get("cites") or [] if c in sids]
        for sent in _SENT_RE.split(ln["text"]):
            about = [s for s in story_list if _mentions({"text": sent, "cites": []}, s)]
            if not about and len(cited) == 1:
                about = [s for s in story_list if s["sid"] == cited[0]]
            if len(about) != 1:
                continue
            if cited and not (about[0]["cluster"] & set(cited)):
                out.append(_p("crossed_facts", "A sentence about \"%s\" sits in a line that cites a "
                              "different story: \"%s\"" % (about[0]["title"][:60], sent[:90]),
                              i, about[0]["sid"]))
                break
            own = words[about[0]["sid"]]
            foreign = sorted(f for f in _hard_facts(sent) - own - shared
                             if any(f in words[o] for o in words if o not in about[0]["cluster"]))
            if foreign:
                out.append(_p("crossed_facts", "A sentence about \"%s\" carries %s from another story: "
                              "\"%s\"" % (about[0]["title"][:60], ", ".join(f.title() for f in foreign),
                                          sent[:90]), i, about[0]["sid"]))
                break
    return out


_OPINION_RE = re.compile(r"\b(my read|my take|in my view|i think|i'd argue|i would argue|"
                         r"my sense|the way i see it)\b", re.I)


def is_read(sent: str) -> bool:
    """Friday's labelled opinion ("my read")."""
    return bool(_OPINION_RE.search(sent or ""))


def sentences(text: str) -> list[str]:
    return [s for s in _SENT_RE.split(text or "") if s.strip()]


def names_story(text: str, story: dict) -> bool:
    """Whether the words themselves are about the story (not its citation)."""
    return _mentions({"text": text, "cites": []}, story)


def opinion_problems(lines: list[dict], story_list: list[dict], docs: list[dict]) -> list[dict]:
    """No editorial read on a story of violence or crime; elsewhere a read rests
    on the facts of the stories its line cites."""
    by_sid = {d["sid"]: d for d in docs}
    safety = {s["sid"] for s in story_list if s["safety"]}
    out = []
    for i, ln in _spoken(lines):
        for sent in _SENT_RE.split(ln["text"]):
            if not _OPINION_RE.search(sent):
                continue
            about = {s["sid"] for s in story_list if _mentions({"text": sent, "cites": []}, s)}
            if (set(refs(ln)) | about) & safety:
                out.append(_p("opinion_on_violence", "No read or opinion on a story of violence "
                              "or crime; report what is confirmed: \"%s\"" % sent[:90], i))
                continue
            basis = " ".join("%s %s" % (by_sid[c].get("title") or "", by_sid[c].get("text") or "")
                             for c in refs(ln) if c in by_sid)
            grounded = len({_stem(w) for w in _content(sent)} & {_stem(w) for w in _content(basis)})
            if grounded < 2 and not (_hard_facts(sent) & _hard_facts(basis)):
                out.append(_p("ungrounded_read", "A read must rest on the facts of the stories "
                              "it cites: \"%s\"" % sent[:90], i))
    return out


#: The writer narrating its own process ("I did not check the time, so I am
#: not inventing one"). Never speech: cut before it is spoken, named if it
#: survives.
META_RE = re.compile(
    r"[^.!?]*\b(?:I|I'm|I am|I'll|we)\b[^.!?]*\b(?:did not|didn't|do not|don't|won't|will not|"
    r"am not|'m not|not going to|can't|cannot)\b[^.!?]*\b(?:check\w*|verif\w*|confirm\w*|guess\w*|"
    r"invent\w*|speculat\w*|giv\w+ you|leav\w+ it out|tell you)\b[^.!?]*[.!?]", re.I)


def meta_problems(lines: list[dict]) -> list[dict]:
    return [_p("meta_narration", "The script narrates its own process; leave the unknown out, "
               "or say it once as a fact (\"police haven't released a time\"): \"%s\""
               % m.group(0).strip()[:90], i)
            for i, ln in _spoken(lines) for m in [META_RE.search(ln["text"])] if m]


def lede_problems(lines: list[dict], story_list: list[dict], *, personal: bool = True) -> list[dict]:
    out = []
    fm = first_mentions(lines, story_list)
    for s in story_list:
        i = fm.get(s["sid"])
        if i is None:
            continue
        window = " ".join(ln["text"] for ln in lines[i:i + 2] if not ln.get("signature"))
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
        # A practical line is owed only for a specific tie to today's plans:
        # the story names the venue or the street of an event. Sharing a city
        # is not one, least of all the listener's own.
        close = {w for e in event_list for w in e.get("venue", set()) if w in story_words}
        if close:
            # The practical line ties this story to the event it is near: a
            # line of the story (or the one after it) that names that event.
            near_events = [e for e in event_list if e.get("venue", set()) & close]
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
                out.append(_p("safety_no_practical_line", "\"%s\" names %s, where one of today's "
                              "events is; give the event's time and what it means for it."
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


def is_own_doc(d: dict) -> bool:
    """Friday's own writing (the written digest, the front page's framing):
    her analysis, never a publisher's reporting."""
    return d.get("role") in ("digest", "overview") or d.get("kind") == "digest"


def own_word_counts(lines: list[dict], docs: list[dict]) -> dict:
    """{stem: [(line, word), ...]} for the script's own words: ones no source
    uses (Friday's own notes are hers, so they do not count as a source)."""
    vocab = set()
    for d in docs:
        if not is_own_doc(d):
            vocab |= {_stem(w) for w in _words("%s %s" % (d.get("title") or "", d.get("text") or ""))}
    counts: dict[str, list] = {}
    for i, ln in _spoken(lines):
        for w in _content(ln["text"]):
            if _stem(w) not in vocab and _stem(w) not in _PLAIN_NEWS:
                counts.setdefault(_stem(w), []).append((i, w))
    return counts


def used_up_words(lines: list[dict], docs: list[dict]) -> list[str]:
    """The script's own words already said as often as the limit allows."""
    return sorted(hits[0][1] for hits in own_word_counts(lines, docs).values()
                  if len(hits) >= MAX_OWN_WORD_REPEATS)


def repetition_problems(lines: list[dict], docs: list[dict], n_chapters: int) -> list[dict]:
    out = []
    spoken = _spoken(lines)
    counts = own_word_counts(lines, docs)
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


# ── pass four: reasoning, echoes, one place per story, attribution ──────────

#: The writer's epistemic scaffolding read aloud: what it did not check, how
#: thin its evidence is, and talk between hosts about the talk. Never speech.
REASONING_RE = re.compile(
    r"[^.!?]*\b(?:"
    r"I (?:did not|didn't|have not|haven't|could not|couldn't) (?:check|verify|confirm|see|read|look)\w*"
    r"|(?:the )?evidence (?:is|was|remains) (?:thin|limited|scant|light)"
    r"|(?:a )?gap (?:I|we) (?:need|want|have) to flag|I need to flag|worth flagging"
    r"|(?:you're|you are) right to push back|push(?:ing)? back on that"
    r"|(?:that's|that is) a (?:fair|good|sharp) (?:read|point|question)|good (?:point|question)|fair point"
    r"|a sharp listener|the listener (?:would|might) want"
    r"|I only have (?:the )?\w+"
    r")\b[^.!?]*[.!?]", re.I)

HARD_CODES = frozenset({"reasoning_leak", "duplicate_line", "misattributed", "story_split",
                        "close_recap", "safety_threaded", "no_lede"})


def reasoning_problems(lines: list[dict]) -> list[dict]:
    return [_p("reasoning_leak", "The writer's reasoning is read aloud instead of the news: \"%s\". "
               "Say an unknown once, as a fact about the reporting (\"the company hasn't responded\")."
               % m.group(0).strip()[:100], i)
            for i, ln in _spoken(lines) for m in [REASONING_RE.search(ln["text"])] if m]


_CONTRACTIONS = [(r"\bit's\b", "it is"), (r"\bthat's\b", "that is"), (r"\byou're\b", "you are"),
                 (r"\bwe're\b", "we are"), (r"\bthey're\b", "they are"), (r"\bdon't\b", "do not"),
                 (r"\bdoesn't\b", "does not"), (r"\bdidn't\b", "did not"), (r"\bisn't\b", "is not"),
                 (r"\baren't\b", "are not"), (r"\bcan't\b", "cannot"), (r"\bwon't\b", "will not"),
                 (r"\bI'm\b", "I am"), (r"\bhasn't\b", "has not"), (r"\bhaven't\b", "have not")]


def _normal(text: str) -> str:
    t = (text or "").lower().replace("’", "'")
    for a, b in _CONTRACTIONS:
        t = re.sub(a, b.lower(), t, flags=re.I)
    return re.sub(r"[^a-z0-9$% ]+", "", re.sub(r"\s+", " ", t)).strip()


def is_duplicate(a: str, b: str) -> bool:
    """The same line twice, allowing for expanded contractions and small edits."""
    na, nb = _normal(a), _normal(b)
    if not na or not nb:
        return False
    if na == nb:
        return True
    wa, wb = set(na.split()), set(nb.split())
    if min(len(wa), len(wb)) < 6:
        return False
    common = len(wa & wb)
    return common / len(wa) >= 0.85 and common / len(wb) >= 0.85


def duplicate_problems(lines: list[dict]) -> list[dict]:
    out = []
    spoken = _spoken(lines)
    for k, (i, ln) in enumerate(spoken):
        for j, prev in spoken[:k]:
            if is_duplicate(ln["text"], prev["text"]):
                out.append(_p("duplicate_line", "Line %d repeats line %d: \"%s\"" % (i, j, ln["text"][:90]), i))
                break
    return out


_HEADLINE_RE = re.compile(r"\b(the headline|the (?:single )?(?:most important|biggest) (?:thing|story)|"
                          r"(?:the )?top story|(?:the )?lead story|the big story)\b", re.I)


def _clusters_in(line: dict, story_list: list[dict]) -> set:
    return {s["cluster"] for s in story_list if _mentions(line, s)}


def close_chapter(n_chapters: int) -> int | None:
    """The close's chapter: an episode of three or more chapters ends on one."""
    return n_chapters - 1 if n_chapters >= 3 else None


def retold(lines: list[dict], story_list: list[dict], n_chapters: int) -> dict:
    """{line index: clusters that line comes back to}. A story is told in one
    place: once two lines about other stories have followed it, a later line
    about it re-tells it. The close is not counted here; it is one sentence
    of synthesis (`close_problems`)."""
    close = close_chapter(n_chapters)
    since: dict = {}
    out: dict = {}
    for i, ln in _spoken(lines):
        if close is not None and ln.get("chapter", 0) == close:
            continue
        touched = _clusters_in(ln, story_list)
        if not touched:
            continue
        again = {c for c in touched if since.get(c, 0) >= 2}
        if again:
            out[i] = again
        for c in since:
            if c not in touched:
                since[c] += 1
        for c in touched - again:
            since[c] = 0
    return out


def retellings(lines: list[dict], story_list: list[dict], n_chapters: int) -> list[tuple]:
    """(line index, story) for the first re-telling of each story."""
    seen, out = set(), []
    for i, clusters in sorted(retold(lines, story_list, n_chapters).items()):
        for c in clusters:
            if c not in seen:
                seen.add(c)
                out.append((i, next(s for s in story_list if s["sid"] in c)))
    return out


def close_problems(lines: list[dict], n_chapters: int) -> list[dict]:
    close = close_chapter(n_chapters)
    if close is None:
        return []
    said = [(i, sent) for i, ln in _spoken(lines) if ln.get("chapter", 0) == close
            for sent in _SENT_RE.split(ln["text"]) if sent.strip()]
    if len(said) > 1:
        return [_p("close_recap", "The close is %d sentences; it is one sentence of synthesis "
                   "(what the news adds up to, or the one thing to watch), never a recap."
                   % len(said), said[1][0])]
    return []


def safety_clusters(story_list: list[dict]) -> set:
    return {s["cluster"] for s in story_list if s["safety"]}


def threads_safety(sent: str, story_list: list[dict], *, in_close: bool = False) -> bool:
    """A sentence that folds a story of violence or a threat into another
    story, or into the close's synthesis."""
    hurt = safety_clusters(story_list)
    named = {s["cluster"] for s in story_list if _mentions({"text": sent, "cites": []}, s)}
    return bool(named & hurt) and (in_close or bool(named - hurt))


def safety_thread_problems(lines: list[dict], story_list: list[dict], n_chapters: int) -> list[dict]:
    """A story of violence or a threat to safety stands alone, with its own
    humane introduction: never a thread in another story, never part of a
    summary or the close."""
    hurt = safety_clusters(story_list)
    if not hurt:
        return []
    close = close_chapter(n_chapters)
    out = []
    for i, ln in _spoken(lines):
        in_close = close is not None and ln.get("chapter", 0) == close
        found = next((s for s in _SENT_RE.split(ln["text"])
                      if threads_safety(s, story_list, in_close=in_close)), None)
        cited = {s["cluster"] for s in story_list if s["sid"] in refs(ln)}
        if found is None and cited & hurt and cited - hurt:
            found = ln["text"]
        if found is not None:
            out.append(_p("safety_threaded", "A story of violence or a threat to safety stands "
                          "alone, with its own introduction; it is never a thread in another "
                          "story, a summary or the close: \"%s\"" % found[:90], i))
    return out


#: Below this share of a sentence's content words found in its cited source,
#: the sentence is not the source's reporting.
SUPPORT_MIN = 0.34


def _odd_case(text: str) -> set:
    """Names written in mixed case ("cLaws", "iPhone"): names wherever they stand."""
    return {w.lower() for w in re.findall(r"\b[a-z]+[A-Z][A-Za-z]*\b", text or "")}


def support(sent: str, cited: list[str], story_list: list[dict], docs: list[dict],
            home: str = "") -> tuple[str, list]:
    """Whether a sentence is what the stories it cites report.

    Returns ("ok", []), ("own", words) when it is Friday's commentary (a read,
    or words her own notes hold), or ("cut", facts) when it states a fact
    that a story it does not cite holds, or that no source holds at all.
    """
    by = {s["sid"]: s for s in story_list}
    mine = [by[c] for c in cited if c in by]
    if not mine:
        return "ok", []
    cluster = set().union(*[s["cluster"] for s in mine])
    words = _cluster_words(story_list)
    pool = set().union(*[words[c] for c in cluster])
    pool |= {w for s in story_list if s["sid"] in cluster for a in outlet_aliases(s) for w in a.split()}
    pool |= set(_words(home or ""))
    for d in docs:
        if d.get("kind") == "event":
            pool |= set(_words("%s %s %s" % (d.get("title") or "", d.get("text") or "",
                                              d.get("location") or "")))
    stems = {_stem(w) for w in pool}
    content = {_stem(w) for w in _content(sent)} - _PLAIN_NEWS
    overlap = len(content & stems) / len(content) if content else 1.0
    # Clock times are the calendar's, checked by `time_problems`.
    bare = _CLOCK_RE.sub(" ", sent)
    missing = sorted(f for f in _hard_facts(bare) | _odd_case(bare) if f not in pool)
    # Commentary that names an outlet aloud ("Example Wire reports that ...")
    # puts it in the outlet's mouth however it is cited: it cannot be hers.
    credits = bool(re.search(_ATTRIB_VERB, sent, re.I)) and any(
        said_outlet(sent, outlet_aliases(s)) for s in story_list if outlet_aliases(s))
    verdict, why = _support_verdict(sent, cluster, words, docs, story_list, missing, content,
                                    stems, overlap)
    if verdict == "own" and credits:
        return "cut", why or ["an outlet named for Friday's own words"]
    return verdict, why


def _support_verdict(sent, cluster, words, docs, story_list, missing, content, stems, overlap):
    if _OPINION_RE.search(sent):
        return "own", []
    if missing:
        elsewhere = [f for f in missing if any(f in o["proper"] for o in story_list
                                                if o["sid"] not in cluster)]
        if elsewhere:
            return "cut", elsewhere
        own = set()
        for d in docs:
            if is_own_doc(d):
                own |= set(_words(d.get("text") or "")) | _odd_case(d.get("text") or "")
        if all(f in own for f in missing):
            return "own", missing
        if overlap >= 0.5:
            return "ok", []          # a name the source abbreviates, spelled out
        return "cut", missing
    if len(content) >= 5 and overlap < SUPPORT_MIN:
        return "own", sorted(content - stems)[:4]
    return "ok", []


def support_problems(lines: list[dict], story_list: list[dict], docs: list[dict],
                     home: str = "") -> list[dict]:
    """A line's citations are a claim that its sources report what it says."""
    sids = {s["sid"] for s in story_list}
    by = {s["sid"]: s for s in story_list}
    out = []
    for i, ln in _spoken(lines):
        cited = [c for c in ln.get("cites") or [] if c in sids]
        if not cited:
            continue
        names = "; ".join(dict.fromkeys(spoken_outlet(by[c]) or by[c]["outlet"] for c in cited))
        for sent in _SENT_RE.split(ln["text"]):
            verdict, why = support(sent, cited, story_list, docs, home)
            if verdict == "own":
                out.append(_p("misattributed", "\"%s\" is Friday's own analysis, credited to %s; "
                              "say it as hers, in a line that cites no outlet." % (sent[:90], names), i))
                break
            if verdict == "cut":
                out.append(_p("misattributed", "\"%s\" is credited to %s, but %s is not in that "
                              "report." % (sent[:90], names, ", ".join(w.title() for w in why[:4])), i))
                break
    return out


def placement_problems(lines: list[dict], story_list: list[dict], n_chapters: int = 1) -> list[dict]:
    """Each story in one place; the headline named once, at the top."""
    out = []
    spoken = _spoken(lines)
    for i, s in retellings(lines, story_list, n_chapters):
        out.append(_p("story_split", "\"%s\" is covered in two places; tell each story once, "
                      "in one place." % s["title"][:80], i, s["sid"]))
    heads = [(pos, i) for pos, (i, ln) in enumerate(spoken) if _HEADLINE_RE.search(ln["text"])]
    if len(heads) > 1:
        out.append(_p("two_headlines", "The headline is named %d times; name it once, at the top."
                      % len(heads), heads[1][1]))
    elif heads and heads[0][0] > 2:
        out.append(_p("two_headlines", "The headline is named late; name it once, at the top.",
                      heads[0][1]))
    return out


def read_cap_problems(lines: list[dict], story_list: list[dict]) -> list[dict]:
    """At most one "my read" per story."""
    count: dict = {}
    out = []
    sids = {s["sid"] for s in story_list}
    for i, ln in _spoken(lines):
        n = sum(1 for sent in _SENT_RE.split(ln["text"]) if _OPINION_RE.search(sent))
        for sid in set(refs(ln)) & sids:
            count[sid] = count.get(sid, 0) + n
            if count[sid] > 1 and n:
                out.append(_p("read_cap", "More than one read on the same story; one is enough.", i, sid))
                count[sid] = -99
    return out


_ATTRIB_VERB = r"(?:reports?|reported|says|said|notes|noted|writes|wrote|according to|confirms|confirmed)"


def misattribution_problems(lines: list[dict], story_list: list[dict], docs: list[dict]) -> list[dict]:
    """A clause attributed to an outlet comes from that outlet's item. Friday's
    own analysis (her written notes) is hers, never a publisher's."""
    own_words = set()
    for d in docs:
        if is_own_doc(d):
            own_words |= {w for w in _words(d.get("text") or "") if len(w) >= 6 and w not in _STOP}
    out = []
    for i, ln in _spoken(lines):
        for sent in _SENT_RE.split(ln["text"]):
            if not re.search(_ATTRIB_VERB, sent, re.I):
                continue
            credited = [s for s in story_list if outlet_aliases(s) and said_outlet(sent, outlet_aliases(s))]
            # Several outlets credited together ("X and Y report"): the
            # sentence is checked against all of their items, and the
            # outlets' own names are not facts.
            pool = set()
            cluster = set().union(*[c["cluster"] for c in credited]) if credited else set()
            for c in story_list:
                if c["sid"] in cluster:
                    pool |= set(_words("%s %s" % (c["title"], c["text"])))
                    pool |= {w for a in outlet_aliases(c) for w in a.split()}
            for s in credited[:1]:
                al = outlet_aliases(s)
                story = pool
                words = {w for w in _words(sent) if len(w) >= 6 and w not in _STOP}
                # Friday's own analysis put in a publisher's mouth: hard.
                hers = sorted(w for w in words - story if w in own_words)
                # A fact from another, unrelated story: crossed, not hard.
                other = sorted(f for f in _hard_facts(sent) - story - {a for a in al}
                               if any(f in set(_words("%s %s" % (o["title"], o["text"])))
                                      for o in story_list if o["sid"] not in cluster))
                if hers:
                    out.append(_p("misattributed", "\"%s\" is attributed to %s, but %s is not in "
                                  "that report; Friday's own analysis is hers, said as \"my read\"."
                                  % (sent[:90], spoken_outlet(s) or s["outlet"],
                                     ", ".join(w.title() for w in hers[:4])), i, s["sid"]))
                elif other:
                    out.append(_p("crossed_facts", "\"%s\" is attributed to %s, but %s comes from "
                                  "another story." % (sent[:90], spoken_outlet(s) or s["outlet"],
                                                      ", ".join(w.title() for w in other[:4])), i, s["sid"]))
                break
    return out


CHECKS = ("no reasoning read aloud", "no repeated lines", "one story in one place",
          "one headline", "one read per story", "outlet attribution",
          "citations supported by their source", "safety stories stand alone",
          "a one-sentence close",
          "ledes", "safety stories", "home city", "facts stay with their story",
          "no opinion on violence", "grounded reads", "no process narration",
          "calendar times", "repetition", "restated close", "headings",
          "digest references", "refrains", "addresses", "fragments", "link claim",
          "density")


def script_problems(lines: list[dict], docs: list[dict], *, n_chapters: int = 1,
                    news: bool = False, personal: bool = False, solo: bool = False,
                    home: str = "") -> list[dict]:
    """Every problem with the script, in reading order."""
    story_list = stories(docs)
    event_list = events(docs)
    probs = []
    if news:
        probs += lede_problems(lines, story_list, personal=personal)
        probs += safety_problems(lines, story_list, event_list)
        probs += home_problems(lines, home)
        probs += attribution_problems(lines, story_list, docs, home)
        probs += opinion_problems(lines, story_list, docs)
    probs += meta_problems(lines)
    probs += reasoning_problems(lines)
    probs += duplicate_problems(lines)
    if news:
        probs += placement_problems(lines, story_list, n_chapters)
        probs += read_cap_problems(lines, story_list)
        probs += misattribution_problems(lines, story_list, docs)
        probs += support_problems(lines, story_list, docs, home)
        probs += safety_thread_problems(lines, story_list, n_chapters)
        probs += close_problems(lines, n_chapters)
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

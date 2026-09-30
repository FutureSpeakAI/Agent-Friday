"""A synthetic day for the briefing episode tests.

Invented people, companies, places and outlets (example.* domains). The bad
script reproduces, one for one, the defects found in a real briefing episode:
stories referenced without a lede, a safety story filed as background, a
"before your interviews" that the calendar contradicts, an invented phrase
said four times, a close that re-reads the opening, an outline heading read
aloud, flat fragments, and "linked in the transcript" with nothing linked.
"""
from __future__ import annotations

DATE = "2031-03-12"


def events():
    return [
        {"title": "Interview: Northwind Labs", "start_time": DATE + "T09:00:00-05:00",
         "end_time": DATE + "T10:00:00-05:00", "location": "Video call"},
        {"title": "Interview: Juniper Robotics", "start_time": DATE + "T13:00:00-05:00",
         "end_time": DATE + "T14:00:00-05:00", "location": "Video call"},
        {"title": "Riverside Makers meetup: local models night", "start_time": DATE + "T17:30:00-05:00",
         "end_time": DATE + "T19:30:00-05:00", "location": "12 Harbor St, Springfield"},
    ]


def news():
    return [
        {"title": "Tech chiefs sign a voluntary pledge to police their own AI",
         "source": "examplewire.com", "url": "https://examplewire.com/2031/03/pledge",
         "snippet": "Chief executives of six technology companies signed a voluntary pledge in "
                    "Washington on Tuesday to police their own AI systems. It has no enforcement.",
         "category": "AI/Tech"},
        {"title": "AI data-center spending to reach $400B next year, Brightline Bank estimates",
         "source": "exampleledger.com", "url": "https://exampleledger.com/ai-capex",
         "snippet": "Brightline Bank analysts estimate AI data-center spending will reach $400B "
                    "next year, up from $260B.", "category": "Business"},
        {"title": "Rowan Hale and Imani Cole trade barbs over a model release",
         "source": "examplepost.com", "url": "https://examplepost.com/hale-cole",
         "snippet": "The two lab chiefs traded public criticism on Tuesday over the timing of "
                    "a model release.", "category": "AI/Tech"},
        {"title": "Two injured in shooting at a Springfield bar; police investigating motive",
         "source": "springfieldcourier.com", "url": "https://springfieldcourier.com/bar-shooting",
         "snippet": "Two people were injured in a shooting at a bar in Springfield late Tuesday. "
                    "Police said they are investigating the motive and have not made an arrest.",
         "category": "Local"},
    ]


def digest_markdown():
    return (
        "# Friday Briefing — Wednesday, March 12, 2031\n\nA two-interview day.\n\n"
        "## 1. Today's Calendar (most important first)\n- 9:00 AM Interview: Northwind Labs\n"
        "## 2. Top News (relevant to you)\n- The pledge. The $400B figure. The Hale-Cole feud. "
        "The Springfield bar shooting.\n"
        "### Analysis (my read, clearly labeled)\nVoluntary pledges are promises; signed "
        "constraints are verifiable.\n"
        "## 3. Active Tasks & Commitments\n- Send the portfolio link to Juniper Robotics.\n"
        "## 4. Proactive Insight\nLead with the governance story in both interviews.\n")


def sidecar():
    return {"version": 1, "date": DATE, "calendar": events(), "news": news()}


def docs():
    """The episode's numbered sources, as podcast_news builds them from the sidecar."""
    from agent_friday.services import podcast_news, podcast_sources
    return podcast_sources.number(podcast_news.briefing_docs(sidecar(), digest_markdown(), DATE))


def _sid(ds, title_start):
    return next(d["sid"] for d in ds if d["title"].startswith(title_start))


def bad_lines(ds):
    """The defects, reproduced. Speaker "a" throughout: a solo briefing."""
    pledge, capex = _sid(ds, "Tech chiefs"), _sid(ds, "AI data-center")
    feud, shooting = _sid(ds, "Rowan Hale"), _sid(ds, "Two injured")
    ev = [d["sid"] for d in ds if d.get("kind") == "event"]
    L = [
        (0, "This is The Briefing. I'm Friday.", [], True),
        (0, "Your 9:00 AM interview with Northwind Labs comes first today. The rest is "
            "filler, frankly. That interview is the linchpin.", [ev[0]], False),
        (0, "Juniper Robotics follows at 1:00 PM. The linchpin holds.", [ev[1]], False),
        (1, "The evening meetup is a useful sounding board, and you can read the crowd "
            "there before your interviews.", [ev[2]], False),
        (1, "2. Top News (relevant to you). The pledge is thin on terms. That's the landscape.", [pledge], False),
        (1, "The $400B figure is the size of it. It's context.", [capex], False),
        (1, "The Hale-Cole feud adds to the landscape. It's fuel.", [feud], False),
        (1, "The Springfield bar shooting is still unfolding. Treat it as background noise.", [shooting], False),
        (1, "Your pitch fits that landscape exactly.", [], False),
        (2, "Your 9:00 AM interview with Northwind Labs comes first today. The rest is "
            "filler, frankly. That interview is the linchpin.", [ev[0]], False),
        (2, "The landscape is all governance, start to finish.", [], False),
        (2, "That's The Briefing. Every story you heard is linked in the transcript. I'm Friday.", [], True),
    ]
    return [{"speaker": "a", "chapter": c, "text": t, "cites": cites, **({"signature": True} if sig else {})}
            for c, t, cites, sig in L]


def good_lines(ds):
    """The same day, done right: every story has a lede, times agree with the
    calendar, the safety story is handled with care, and the close adds."""
    pledge, capex = _sid(ds, "Tech chiefs"), _sid(ds, "AI data-center")
    feud, shooting = _sid(ds, "Rowan Hale"), _sid(ds, "Two injured")
    ev = [d["sid"] for d in ds if d.get("kind") == "event"]
    L = [
        (0, "This is The Briefing. I'm Friday.", [], True),
        (0, "Your day turns on the 9:00 AM interview with Northwind Labs, and the news gives "
            "you a timely opening for it.", [ev[0]], False),
        (0, "Example Wire reports that on Tuesday, in Washington, chief executives of six "
            "technology companies signed a voluntary pledge to police their own AI systems. "
            "There is no enforcement in it.", [pledge], False),
        (0, "For you, that is the governance argument your interviews will want: a promise "
            "is not a control you can check.", [pledge], False),
        (1, "Example Ledger reports that Brightline Bank analysts estimate AI data-center "
            "spending will reach $400B next year, up from $260B.", [capex], False),
        (1, "Money at that scale, governed by a pledge, is the gap your Juniper Robotics "
            "interview at 1:00 PM can press on.", [capex, ev[1]], False),
        (1, "And Example Post reports that on Tuesday two lab chiefs, Rowan Hale and Imani "
            "Cole, traded public criticism over the timing of a model release.", [feud], False),
        (1, "If either interviewer raises it, you can say the industry is arguing in public "
            "while asking to police itself.", [feud], False),
        (1, "One more, closer to home. The Springfield Courier reports that two people were "
            "injured in a shooting at a bar in Springfield late Tuesday. Police said they are "
            "investigating the motive and have made no arrest.", [shooting], False),
        (1, "Your 5:30 PM meetup is in Springfield, so check the venue's notices before you "
            "go and give yourself a little extra time.", [shooting, ev[2]], False),
        (2, "Tonight's meetup comes after both interviews, so it is where you can hear how "
            "people who build these systems react to the pledge.", [ev[2]], False),
        (2, "One thing to watch: whether anyone asks the six companies who enforces the pledge.", [pledge], False),
        (2, "That's The Briefing. Every story you heard is linked in the transcript. I'm Friday.", [], True),
    ]
    return [{"speaker": "a", "chapter": c, "text": t, "cites": cites, **({"signature": True} if sig else {})}
            for c, t, cites, sig in L]

"""A front page whose episode was rejected for a missing lede.

The structure is a real episode's: a story of a homicide (a false tip sent to a
police tipline by an AI model), an election story about the same AI company, and
local and national stories around them. The writer's first draft led the
homicide story with a proper lede; the safety pass cut that one sentence
because the story is about violence and the sentence also named the AI
company the election story is about; the story's next line then became its
first mention, with no outlet. Headlines are public wire headlines; the
snippets are invented summaries and the home city is a neutral one.
"""
from __future__ import annotations

from datetime import datetime

from agent_friday.services import podcast_news
from agent_friday.services import podcast_sources

FRIDAY = datetime(2031, 3, 14, 17, 0).timestamp()
HOME = "Riverton, TX"

#: (outlet, title, url slug, snippet)
STORIES = [
    ("The Verge", "Northwind's AI gave Springfield police a fake tip about an unsolved homicide",
     "verge-tip",
     "An AI model built by Northwind sent a false tip about an unsolved homicide to the Springfield "
     "Police Department tipline on July 18th. The department confirmed the tip arrived through a "
     "cold-case website, but said the investigation is ongoing. Northwind says it is hiring a "
     "political programs lead and building a presidential engagement program."),
    ("The Atlantic", "What Northwind's AI Model Told Me About Police and the Election", "atlantic-chatbot",
     "A reporter asked the Northwind AI model about the midterm election. State and local election "
     "officials say they are trying to keep up with AI ahead of the vote, and the model's answers "
     "were wrong about registration deadlines."),
    ("The Guardian", "Federal judge rules against Justice Department policy of collecting state voter rolls",
     "guardian-rolls",
     "A federal judge ruled on Friday that the US justice department's policy of collecting states' "
     "unredacted voter rolls to check them against a federal immigration database is unlawful."),
    ("The Hill", "Jackson holds slight lead over Collins in Maine Senate race survey", "hill-maine",
     "Jackson leads Collins 50 percent to 46 percent in a new survey of the Maine Senate race."),
    ("KVUE", "Owners of JD's Supermarket in southeast Riverton say light rail project could force them to close",
     "kvue-rail",
     "Owners of JD's Supermarket in southeast Riverton say a light rail project could force them to "
     "close, KVUE reported on Friday."),
    ("Cbsriverton", "Riverton Music Weekend Two could go from Mud Fest to Dust Fest, forecast shows",
     "cbs-weekend",
     "Weekend Two of the Riverton Music Festival could go from Mud Fest to Dust Fest, the forecast shows."),
    ("Motherjones", "Why This Long-Sought, Bipartisan Senate Deal Has Environmentalists Feuding",
     "mj-permits",
     "A bipartisan Senate deal to speed permitting of large energy and infrastructure projects has "
     "environmental groups divided."),
]


def edition():
    arts = [{"title": t, "source": o, "url": "https://example.test/%s" % slug, "snippet": snip,
             "ts": FRIDAY} for o, t, slug, snip in STORIES]
    return {"id": "2031-03-14-evening", "headline": "Northwind's fake homicide tip",
            "sections": [{"articles": arts}]}


def docs():
    return podcast_sources.number(podcast_news._front_page_docs(edition()))


def sid(ds, start):
    return next(d["sid"] for d in ds if d["title"].startswith(start))


def writer_lines(ds):
    """What the writer wrote, chapter by chapter, before the check cut anything."""
    tip, chat = sid(ds, "Northwind's AI"), sid(ds, "What Northwind")
    rolls, hill = sid(ds, "Federal judge"), sid(ds, "Jackson")
    rail, music = sid(ds, "Owners of JD"), sid(ds, "Riverton Music")
    L = [
        (0, "The Verge reports that a Northwind AI model sent a false tip about an unsolved homicide "
            "to the Springfield Police Department tipline on July 18th.", [tip]),
        (0, "The department confirmed the tip arrived through a cold-case website on July 18th, but "
            "the investigation is ongoing.", [tip]),
        (1, "The Guardian reports that a federal judge ruled on Friday that the US justice "
            "department's policy of collecting states' unredacted voter rolls to check them against "
            "a federal immigration database is unlawful.", [rolls]),
        (1, "My read: the vote roll ruling and what the Northwind AI model told voters about the "
            "election point to the same tension: institutions are lagging behind both the "
            "technology and the political will to regulate it.", []),
        (1, "Here in Riverton, KVUE reported on Friday that owners of JD's Supermarket in southeast "
            "Riverton say a light rail project could force them to close.", [rail]),
        (1, "Cbsriverton reports that on Friday, in Riverton, Riverton Music Weekend Two could go "
            "from Mud Fest to Dust Fest, forecast shows.", [music]),
        (1, "The Hill reports that on Friday, in Maine, Jackson holds a slight lead over Collins in "
            "the Senate race survey, with Jackson leading 50 percent to 46 percent.", [hill]),
        (2, "That is the week's news for Riverton, and the stories keep pointing at the same gaps "
            "between the rules and the tools.", []),
    ]
    return [{"speaker": "a", "chapter": c, "text": t, "cites": cites} for c, t, cites in L], tip, chat

"""The typed questions Friday asks Laya, in one place.

Laya answers a fixed question over a state. Several questions about the same
state go to the model as ONE batched forward pass (`laya.Agent.predict` takes
a dict of questions), but each question is still encoded together with the
whole state: four questions cost roughly four times one. So a consumer asks
exactly the questions it will act on, and every question is defined here,
once, where a change to its wording is visible and its answers stay
comparable across the decision log.

Two-option choices or single statements (`noul`), deliberately: neither lands
in the `choice:11+` bucket that is distorted on every build of this
checkpoint (see laya_backend.calibration_report).

WORDING IS MEASURED, NOT WRITTEN. Laya answers whatever it is asked,
confidently; a badly worded question fails quietly. Every wording here was
scored by tools/laya_question_eval.py against clear-cut cases before use.

WHO ASKS WHAT

  gate      severity                 the approval gate's question today
  gate      changes_outside          does it change anything outside? (shadow)
  (spare)   leaves_machine           does data leave this PC? Defined, not asked:
                                     this checkpoint answers it near chance
  voice     touches_private          does the request touch private data?
                                     -> the local private-summary hand-off,
                                        owned by the voice session
  voice     direct_command           is this a direct command?
                                     -> the reflex path (next spec)
  chat      source                   which source the request needs (pilot)

`leaves_machine` and `changes_outside` split what `severity` lumps together.
The owner's labels say a read-only call to an outside service (a GitHub
search, a Higgsfield listing) reaches outside his machine, and he still does
not want a card for every lookup. Two answers let a later gate send a
read-only outward tool through a standing grant while a side-effecting one
always gets a card. Until that gate exists they are asked in shadow only.
"""
from __future__ import annotations

from typing import Dict, Iterable

SEVERITY = {
    "type": "choice",
    "instructions": ("Does this action reach outside the machine, spend "
                     "money, or change something that cannot be undone?"),
    "criteria": {
        "hard": ("sends, posts, publishes, emails, deletes, pays, orders, "
                 "or otherwise acts on the outside world or irreversibly"),
        "soft": ("internal only: reading, analysing, summarising, "
                 "drafting, searching, or planning"),
    },
}

LEAVES_MACHINE = {
    "type": "choice",
    "instructions": ("Does carrying out this action send any data, query or "
                     "request to a service outside this computer?"),
    "criteria": {
        "yes": ("it contacts an outside service: a web API, a connected "
                "account, a remote server, even only to read or search"),
        "no": "everything happens on this computer and nothing is sent anywhere",
    },
}

CHANGES_OUTSIDE = {
    "type": "choice",
    "instructions": ("Does this action create, change, send, publish, pay "
                     "for or delete anything outside this computer?"),
    "criteria": {
        "yes": ("it has a side effect in the outside world: a message, a "
                "post, an order, a payment, an edit or deletion somewhere else"),
        "no": "it only reads, lists or searches, or it stays on this computer",
    },
}

# The two voice questions are STATEMENTS (`noul`: the probability that the
# statement holds), because that is the wording this checkpoint answers well.
# Measured by tools/laya_question_eval.py, fp32, on clear-cut cases:
#   touches_private   as a yes/no choice 6/11, as this statement 10/11
#   direct_command    as a yes/no choice 10/15, as this statement 14/15
# A consumer treats noul >= 0.5 as "holds".
TOUCHES_PRIVATE = {
    "type": "noul",
    "instructions": ("Answering this needs the user's own personal records: "
                     "messages, calendar, health, money or family."),
}

DIRECT_COMMAND = {
    "type": "noul",
    "instructions": ("This is a short control command for a device or app "
                     "(play, pause, mute, volume, open, go to, timer)."),
}

#: Do a tool call's ARGUMENTS carry someone's private details out? Asked of
#: connector reads only. Measured on 13 search-shaped calls: 10/13 (the
#: request-shaped touches_private scored 8/13). It misses what the PII check
#: catches (diagnoses, account numbers, medical records) and catches what that
#: check misses (a relative's address, an email, "my son failed ..."); the gate
#: uses both, and on 10 private and 10 clean queries together they were
#: right on all 20.
CARRIES_PRIVATE = {
    "type": "choice",
    "instructions": "What kind of information does this text contain?",
    "criteria": {
        "personal": ("private details about a real person: health, money, "
                     "family, messages, contacts, home address"),
        "public": "public, technical or generic information, or nothing personal",
    },
}

#: The chat pilot's question (services/laya_pilot), registered here so every
#: question Friday asks has one home.
SOURCE = {
    "type": "choice",
    "instructions": ("Which source of information is needed for the CURRENT "
                     "user request? Respect explicit source restrictions."),
    "criteria": {
        "A": "The owner's personal wiki, private notes, archive or knowledge graph.",
        "B": "Public websites, online articles, current external facts or public research.",
        "C": "Local workspace files, source code, attached documents or local datasets.",
        "D": "The owner's connected email, calendar or task manager.",
        "E": "No lookup: answer from the supplied text or basic reasoning.",
        "F": "A combination of two or more of the above information sources.",
    },
}

QUESTIONS: Dict[str, dict] = {
    "severity": SEVERITY,
    "leaves_machine": LEAVES_MACHINE,
    "changes_outside": CHANGES_OUTSIDE,
    "touches_private": TOUCHES_PRIVATE,
    "carries_private": CARRIES_PRIVATE,
    "direct_command": DIRECT_COMMAND,
    "source": SOURCE,
}

#: Named sets a consumer asks in one pass.
#:
#: `leaves_machine` is NOT in the gate's set. No wording tried scored above
#: 17/29 (tools/laya_question_eval.py): this checkpoint cannot tell a lookup
#: on an online service from local work when asked that directly. `severity`
#: already carries that signal (it calls every owner-labelled outside read
#: "hard"), so the shadow asks severity and changes_outside (23/29).
GATE_SHADOW = ("severity", "changes_outside")
VOICE = ("touches_private", "direct_command")
#: A read at a connected service: severity (the union's vote), whether it
#: changes anything outside, and whether its arguments carry private data.
CONNECTOR_READ = ("severity", "changes_outside", "carries_private")


def select(ids: Iterable[str]) -> Dict[str, dict]:
    """The question dict for `ids`, in order. Unknown ids raise KeyError."""
    return {i: QUESTIONS[i] for i in ids}


def holds(qid: str, answer: dict) -> bool:
    """A yes/no reading of any answer: noul >= 0.5, or the first option."""
    q = QUESTIONS[qid]
    if q.get("type") == "noul":
        return float((answer or {}).get("noul") or 0.0) >= 0.5
    return (answer or {}).get("choice") == list(q.get("criteria") or {"": 0})[0]


def bucket(qid: str) -> str:
    """Calibration bucket, as laya_backend.question_bucket derives it."""
    q = QUESTIONS[qid]
    if q.get("type") == "noul":
        return "noul:2"
    k = len(q.get("criteria") or {})
    size = "2" if k <= 2 else "3-5" if k <= 5 else "6-10" if k <= 10 else "11+"
    return "%s:%s" % (q.get("type") or "choice", size)

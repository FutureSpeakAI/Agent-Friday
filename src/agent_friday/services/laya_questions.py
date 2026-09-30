"""The typed questions Friday asks Laya, in one place.

Laya answers a fixed question over a state. Several questions about the same
state go to the model as ONE batched forward pass (`laya.Agent.predict` takes
a dict of questions), but each question is still encoded together with the
whole state: four questions cost roughly four times one. So a consumer asks
exactly the questions it will act on, and every question is defined here,
once, where a change to its wording is visible and its answers stay
comparable across the decision log.

Two-option choices only, deliberately. They land in the `choice:2`
calibration bucket, whose shipped temperature is inside laya's clamp range
(see laya_backend.calibration_report); a question that grew past ten options
would land in the bucket that is distorted on every build of this checkpoint.

WHO ASKS WHAT

  gate      severity                 the approval gate's question today
  gate      leaves_machine           does data leave this PC? (shadow only)
  gate      changes_outside          does it change anything outside? (shadow)
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

TOUCHES_PRIVATE = {
    "type": "choice",
    "instructions": ("Does answering this request need the owner's private "
                     "data: health, family, finances, messages, contacts, "
                     "location, or personal notes?"),
    "criteria": {
        "yes": "it needs private or personal information about the owner or people close to them",
        "no": "it needs only public information, general knowledge, or nothing personal",
    },
}

DIRECT_COMMAND = {
    "type": "choice",
    "instructions": ("Is this a short direct command to operate something "
                     "(open, play, pause, set, turn on or off, go to, call) "
                     "rather than a question or a request for writing or reasoning?"),
    "criteria": {
        "yes": "a direct command that can be carried out at once",
        "no": "a question, a conversation, or work that needs thought",
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
    "direct_command": DIRECT_COMMAND,
    "source": SOURCE,
}

#: Named sets a consumer asks in one pass.
GATE_SHADOW = ("severity", "leaves_machine", "changes_outside")
VOICE = ("touches_private", "direct_command")


def select(ids: Iterable[str]) -> Dict[str, dict]:
    """The question dict for `ids`, in order. Unknown ids raise KeyError."""
    return {i: QUESTIONS[i] for i in ids}


def bucket(qid: str) -> str:
    """Calibration bucket, as laya_backend.question_bucket derives it."""
    q = QUESTIONS[qid]
    k = len(q.get("criteria") or {})
    size = "2" if k <= 2 else "3-5" if k <= 5 else "6-10" if k <= 10 else "11+"
    return "%s:%s" % (q.get("type") or "choice", size)

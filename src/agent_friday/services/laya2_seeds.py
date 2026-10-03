"""The synthetic seed set behind Laya 2's tier-1 heads.

No head here is trained. Each class is a handful of synthetic utterances
written in this file; `services/reflex_turn` embeds them once with the shipped
encoder and keeps one prototype (the normalised mean) per class. A turn is
scored by cosine similarity to those prototypes. That is the whole model, so
the only thing a shipped artifact can contain is what is written below, and
`content_hash()` is how a build proves it.

The seeds are invented. No real person, place, address, file or account
appears in them, and nothing a user ever typed is added here: a per-user
sharpener (when it exists) lives under the user's own home and never ships.

Two heads share the encoder:

  SHAPES    what kind of turn this is, which chooses the route and the
            brain's reasoning effort (services/reasoning_policy);
  MUTATION  whether the words ask to read, to change something, or are a
            question that needs an answer before anything can be chosen.

`out_of_scope` is a shape with its own prototype so the far-from-everything
case has somewhere to land; the scorer also treats a weak best match as out
of scope, which is how anything the seeds never saw reaches the brain.
"""
from __future__ import annotations

import hashlib
import json

SEED_SET_ID = "laya2-seeds-v1"

SHAPES = ("reflex_open", "reflex_navigate", "reflex_organise", "conversation",
          "question_brain", "deep_coding", "deep_analysis", "approval_reply",
          "out_of_scope")
REFLEX_SHAPES = ("reflex_open", "reflex_navigate", "reflex_organise")
DEEP_SHAPES = ("deep_coding", "deep_analysis")
MUTATIONS = ("read_only", "state_changing", "ask")

SHAPE_SEEDS = {
    "reflex_open": [
        "open my inbox",
        "open the downloads folder",
        "open the notes from this morning",
        "open the budget spreadsheet",
        "show me the latest email",
        "bring up the calendar",
        "open the document I was editing yesterday",
        "pull up the meeting notes",
        "open the photo I took last",
        "show the project folder",
        "open the file called draft two",
        "open that email about the invoice",
    ],
    "reflex_navigate": [
        "go to settings",
        "take me to the media workspace",
        "switch to the chat tab",
        "go back to the home screen",
        "show the models screen",
        "jump to the privacy settings",
        "navigate to the knowledge graph",
        "switch workspace to creative",
        "go to the accounts and keys page",
        "take me to the news section",
        "open the voice settings panel",
        "move to the next page",
    ],
    "reflex_organise": [
        "tidy the downloads folder",
        "sort these files by date",
        "move the screenshots into one folder",
        "archive the old notes",
        "rename this file to final draft",
        "group the photos by month",
        "put the receipts in the finance folder",
        "clean up the desktop",
        "mark these emails as read",
        "file this under projects",
        "pin this note to the top",
        "tag these documents as work",
    ],
    "conversation": [
        "good morning",
        "thanks, that was helpful",
        "how are you doing today",
        "that's funny",
        "never mind",
        "okay sounds good",
        "tell me a joke",
        "what do you think about that",
        "nice work",
        "hello there",
        "see you later",
        "I'm a bit tired today",
    ],
    "question_brain": [
        "what is the difference between a lease and a loan",
        "how long does it take to boil an egg",
        "why is the sky blue",
        "what does this error message mean",
        "can you explain how compound interest works",
        "what should I cook tonight with what is in the fridge",
        "is it going to rain this afternoon",
        "which of these two options is cheaper over a year",
        "what happened in the news this morning",
        "how do I convert celsius to fahrenheit",
        "what time zone is three hours ahead of here",
        "what is a good name for a small garden project",
    ],
    "deep_coding": [
        "write a python function that parses this log file and reports the slowest requests",
        "refactor this module so the database calls are behind one interface",
        "fix the failing test and explain why it was failing",
        "implement a rate limiter with a sliding window in this service",
        "review this pull request for race conditions",
        "add type hints to this file and make the linter pass",
        "write a script that renames files by their creation date",
        "debug why this websocket disconnects after a minute",
        "convert this shell script to python with the same behaviour",
        "design the schema for a small inventory app and write the migrations",
        "profile this function and make it faster without changing its output",
        "write unit tests that cover the edge cases of this parser",
    ],
    "deep_analysis": [
        "compare these three proposals and recommend one with the trade-offs",
        "analyse the last quarter of expenses and tell me where the money went",
        "summarise this long report and list the decisions it asks for",
        "work out whether moving to a cheaper plan saves money over two years",
        "give me a detailed plan for the next six weeks of this project",
        "read these notes and find the contradictions between them",
        "evaluate the risks of this approach and how to reduce each one",
        "build a step by step argument for and against the change",
        "research the options for a home backup setup and compare them",
        "draft a thorough review of this document with specific suggestions",
        "estimate the cost of this plan with the assumptions written out",
        "think through the second order effects of this decision",
    ],
    "approval_reply": [
        "yes go ahead",
        "approved",
        "no, don't do that",
        "yes send it",
        "cancel that",
        "okay do it",
        "not now",
        "yes please",
        "confirm",
        "deny",
        "go ahead and delete it",
        "no thanks, leave it",
    ],
    "out_of_scope": [
        "asdf qwer zxcv",
        "lorem ipsum dolor sit amet",
        "the purple elephant sings on tuesdays",
        "banana carpet seventeen",
        "x y z q",
        "translate this into a language that does not exist",
        "beep boop",
        "colourless green ideas sleep furiously",
        "flibbertigibbet",
        "a a a a a a",
        "?????",
        "random words with no request in them",
    ],
}

MUTATION_SEEDS = {
    "read_only": [
        "show me the calendar for next week",
        "what is in my inbox",
        "read the latest note",
        "list the files in the downloads folder",
        "search the wiki for the holiday plan",
        "find the email about the invoice",
        "summarise this document",
        "how many photos are in the album",
        "what did I write yesterday",
        "look up the weather for tomorrow",
        "show the recent transactions",
        "which tasks are still open",
        "preview the draft before anything is sent",
        "check whether the backup finished",
        "tell me what this file contains",
    ],
    "state_changing": [
        "send the email to the team",
        "delete the old screenshots",
        "move the meeting to friday",
        "rename this folder",
        "post this to the channel",
        "pay the invoice",
        "order more printer paper",
        "archive these messages",
        "create a new note called ideas",
        "cancel the subscription",
        "publish the draft",
        "change the password on this account",
        "empty the trash",
        "reply to the message with yes",
        "schedule a reminder for six tomorrow",
    ],
    "ask": [
        "which one do you mean",
        "there are two files with that name, which should I open",
        "did you want the one from this week or last week",
        "should I send it to everyone or just the organiser",
        "do you mean the work calendar or the personal one",
        "which folder should these go into",
        "is that the draft or the final version",
        "before I delete it, is this the right one",
        "which account should this come from",
        "do you want a summary or the full text",
        "the name matches three people, which did you mean",
        "do you want that moved or copied",
        "which of these meetings do you mean",
        "should this stay private or be shared",
        "which day did you mean, this friday or next",
    ],
}


def canonical_json() -> str:
    """The seed set in one canonical encoding; what `content_hash` hashes."""
    return json.dumps({"id": SEED_SET_ID, "shapes": SHAPE_SEEDS,
                       "mutations": MUTATION_SEEDS},
                      sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def content_hash() -> str:
    return hashlib.sha256(canonical_json().encode("utf-8")).hexdigest()


def manifest() -> dict:
    """What a shipped tier-1 head is made of, and nothing else.

    The `datasets` list names the one synthetic seed set; a build reproduces
    `content_hash` from this module. There is no training step and no user
    data: `tests/unit/test_laya2_never_trained_on_user_data.py` is the fence.
    """
    from agent_friday.services import laya2_encoder as _enc
    return {
        "head": "nearest-prototype",
        "trained": False,
        "datasets": [{"id": SEED_SET_ID, "kind": "synthetic",
                      "content_hash": content_hash()}],
        "encoder": {"model_id": _enc.MODEL_ID, "sha256": _enc.MODEL_SHA256,
                    "licence": _enc.MODEL_LICENCE},
    }

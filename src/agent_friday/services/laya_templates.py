"""The fixed words Laya may say on her own, in one place.

In ``cloud_only`` Laya plans and acts but never silently answers (Laya in every
mode addendum, section 1). Any line the owner sees or hears that the CLOUD
MODEL did not write must come from this module: an action confirmation, an ask
or an error, built from a template and the facts of what happened. Nothing here
is composed by a model.

Each line is a ``Line``. It carries ``laya = True`` and the name of its
template, so a surface can show the "Laya" receipt, and so the guard test
(``tests/unit/test_cloud_only_no_silent_answer.py``) can tell a templated line
from a composed one.

Slots are facts, never prose: an item's title, a tool's own first result line,
a provider's name. A slot never carries a model's words.

Existing fixed sets that live next to their code (the router's acknowledgements
and asks in ``laya_router``, the voice defer line in ``routes/voice.py``) are
named in ``EXTERNAL_SETS`` so the inventory is complete; they are not copied.
"""
from __future__ import annotations

from dataclasses import dataclass

#: The kinds of line Laya may originate.
KINDS = ("confirmation", "ask", "error", "notice")

#: What Laya says when a cloud_preferred turn is answered by the local brain
#: because the cloud could not be reached. Fixed on purpose: the owner always
#: sees these exact words.
OUTAGE_LOCAL = "Answering locally: no connection to {provider}."

#: template key -> (kind, format string).
TEMPLATES = {
    # "Open the X" (reflex_chat._run_command)
    "open_done": ("confirmation", "Done: opened {shown} (instant, no model)."),
    "open_asked": ("ask", "Opening {shown} needs your OK first. Shall I go ahead?"),
    "open_card": ("notice", "Opening {shown} is waiting on an approval card. Nothing was opened yet."),
    "open_refused": ("error", "I did not open {shown}: {detail} (instant, no model; nothing ran)."),
    "open_unconfirmed": ("error", "I could not confirm {shown} opened: {detail} (instant, no model)."),
    # Point-and-act (reflex_chat._point_act_turn)
    "point_done": ("confirmation", "{said} (instant, no model)."),
    "point_asked": ("ask", "That needs your OK first. Shall I go ahead?"),
    "point_card": ("notice", "That is waiting on an approval card. Nothing was changed yet."),
    "point_refused": ("error", "I did not do that: {detail} (instant, no model; nothing ran)."),
    "point_unconfirmed": ("error", "I could not confirm that: {detail} (instant, no model)."),
    # Cloud preferred, cloud unreachable.
    "outage_local": ("notice", OUTAGE_LOCAL),
    # A question a rule or the router asked; the words come from the rule's own
    # sealed question templates (laya_router, laya_resolver, point_act), never
    # from a model. The receipt keeps the layer that asked.
    "rule_ask": ("ask", "{question}"),
}

#: Fixed sets that already live beside their code: (module, attribute, kind).
#: ``tests/unit/test_cloud_only_no_silent_answer.py`` imports each one and
#: checks it is non-empty.
EXTERNAL_SETS = (
    ("agent_friday.services.laya_router", "_ACK", "confirmation"),
    ("agent_friday.services.laya_router", "_ASK_OUTWARD", "ask"),
    ("agent_friday.services.laya_router", "_ASK_FOR", "ask"),
    ("agent_friday.services.laya_router", "_ASK_WHICH", "ask"),
)

#: Every surface that can show or speak text, and where its words come from in
#: ``cloud_only``. ``cloud_model``: the cloud model wrote it. ``laya_template``:
#: this module (or an EXTERNAL_SET). ``owner_engine``: the owner chose an
#: engine that runs on this machine (Settings > Voice); the mode does not pick it.
SURFACES = {
    "chat_answer": "cloud_model",
    "chat_reflex_open": "laya_template",
    "chat_reflex_point": "laya_template",
    "chat_tool_routing_ask": "laya_template",
    "chat_tool_routing_run": "cloud_model",
    "voice_cloud_answer": "cloud_model",
    "voice_local_front_ack": "laya_template",
    "voice_local_front_defer": "laya_template",
    "voice_local_front_answer": "owner_engine",
}

#: The voice line the local front speaks when a change goes to the deeper mind.
#: routes/voice.py holds the live copy (DEFER_ACK); the guard test pins them equal.
VOICE_DEFER_ACK = "I'll hand that to my deeper mind; it'll report back."


@dataclass(frozen=True)
class Line:
    """One line Laya originated: its words and its receipt."""
    text: str
    template: str
    kind: str
    laya: bool = True

    def __str__(self) -> str:
        return self.text

    def receipt(self) -> dict:
        """The "Laya" receipt a surface attaches to the line."""
        return {"laya": True, "template": self.template, "kind": self.kind}


def line(key: str, **slots) -> Line:
    """Render template ``key`` with ``slots``. An unknown key or a missing slot
    raises, so a typo cannot become free text."""
    kind, fmt = TEMPLATES[key]
    return Line(fmt.format(**slots), key, kind)


def outage_notice(provider: str) -> Line:
    """"Answering locally: no connection to <provider>." as a receipted line."""
    name = str(provider or "").strip() or "the cloud"
    return line("outage_local", provider=name)


def rule_question(question: str, layer: str = "rule") -> Line:
    """A question a sealed rule asked, carried with its receipt. ``layer`` names
    who asked (``laya_router``, ``resolver``, ``point_act``)."""
    ln = line("rule_ask", question=str(question or ""))
    return Line(ln.text, "rule_ask:" + (str(layer or "rule")), ln.kind)

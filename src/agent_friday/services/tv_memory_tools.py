"""The TV memory setting by voice or chat (services/tv_memory_access, TV-P1): "Friday, let the TV use all my
memories", "turn off TV memories", "what can the TV remember?".

Narrowing (to Only shareable, or None) is made at once: it only keeps more on this PC, and it is written without
the owner flag so core re-checks it against the stored value (test_turning_it_down_by_voice_needs_no_card).
Widening goes through setting_proposals.hold: one card on the PC says old and new and waits for the owner's Yes;
nothing changes before (test_turning_it_up_by_voice_raises_a_card_and_writes_nothing, test_the_cards_yes_writes_it).
Tests: tests/unit/test_tv_memory_controls.py.
"""
from __future__ import annotations

import logging

_log = logging.getLogger(__name__)


def handle_tv_memory(inp: dict) -> str:
    from agent_friday.services import tv_memory_access as tma
    inp = inp if isinstance(inp, dict) else {}
    raw = str(inp.get("mode") or "").strip()
    cur = tma.current()
    if raw.lower() in ("", "status"):
        return "The TV memory setting is %s. %s" % (tma.LABELS[cur], tma.MEANS[cur])
    mode = tma.parse(raw)
    if mode is None:
        return "tv_memory error: mode must be one of all, shareable, none or status"
    if mode == cur:
        return "SETTING_UNCHANGED: what the TV may remember already reads %s." % tma.LABELS[cur]
    undo_note = ""
    if tma.is_widening(cur, mode):
        from agent_friday.services import setting_proposals as _sp
        held = _sp.hold("tv_memory", {"mode": mode}, old=tma.LABELS[cur], new=tma.LABELS[mode],
                        consequence=tma.MEANS[mode])
        if held:
            return held
    else:
        try:
            from agent_friday.services import setting_proposals as _sp
            _sp.record_change("tv_memory", label=_sp.LABELS.get("tv_memory", "what the TV may remember"),
                              old=tma.LABELS[cur], new=tma.LABELS[mode], snapshot={tma.KEY: cur},
                              by="Friday, on your words (narrowing needs no card)")
        except Exception as e:
            # The narrowing still goes ahead (it only keeps more on this PC); the owner is told its Undo row
            # is missing (test_a_narrowing_whose_undo_row_fails_says_so).
            _log.warning("TV memory narrowing: its Undo row was not written (%s)", type(e).__name__)
            undo_note = " Its Undo record could not be written."
    now = tma.write(mode, owner_action=tma.is_widening(cur, mode))
    if now != mode:
        return "NOT DONE: the TV memory setting reads %s; changing it up needs your Yes on the PC." % tma.LABELS[now]
    return "Done: the TV memory setting is now %s. %s%s" % (tma.LABELS[now], tma.MEANS[now], undo_note)


def _tool_tv_memory(inp):
    return handle_tv_memory(inp if isinstance(inp, dict) else {})


TOOLS = [
    {"name": "tv_memory",
     "description": (
         "Which of the owner's memories the household TV may use: all (All my memories, in a grown-up "
         "window on the TV), shareable (only ones marked shareable with the TV; marking is not built yet, "
         "so the TV gets none), none. No mode, or status, says how it is set. Turning it down happens at "
         "once; turning it up raises one card on the PC and waits for the owner's Yes. Kids mode never "
         "gets memories whatever this says. Say the result in one plain sentence."),
     "input_schema": {"type": "object", "properties": {
         "mode": {"type": "string", "enum": ["all", "shareable", "none", "status"]}}}},
]
RINGS = {"tv_memory": 1}
HANDLERS = {"tv_memory": _tool_tv_memory}


def register(claude_tools, handlers, rings):
    known = {t["name"] for t in claude_tools}
    for t in TOOLS:
        if t["name"] not in known:
            claude_tools.append(t)
    handlers.update(HANDLERS)
    rings.update(RINGS)

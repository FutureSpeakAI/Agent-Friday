"""GET/POST /tv/v1/tv-memory: the TV memory-access setting (services/tv_memory_access, TV-P1) for owner devices.

Reads are for any bound device (the TV may show which option is on). A change is for an owner-bound phone,
tablet or watch only (owner_turn.is_owner_device: the binding the owner made at the PC, never a header or a
body field); a TV or a family device gets 403. An owner device is the owner, so its change is an owner action:
it may widen as well as narrow, and it is recorded with thirty days of Undo like any setting change.

Tests: tests/unit/test_tv_memory_controls.py (test_an_owner_device_reads_and_changes_it,
test_a_tv_or_a_family_device_reads_it_but_cannot_change_it, test_a_word_that_is_not_an_option_is_a_400_and_changes_nothing).
"""
from __future__ import annotations

import logging

from flask import g, jsonify, request

_log = logging.getLogger(__name__)


def register(app, refuse):
    from agent_friday.services import tv_memory_access as tma
    from agent_friday.tv_gateway import owner_turn

    @app.get("/tv/v1/tv-memory")
    def tv_memory_get():
        owner = owner_turn.is_owner_device(g.device)
        return jsonify({"ok": True, **tma.describe(), "can_change": bool(owner)})

    @app.post("/tv/v1/tv-memory")
    def tv_memory_set():
        if not owner_turn.is_owner_device(g.device):
            return refuse(403, "the TV memory setting is changed on the owner's phone, tablet or PC",
                          code="owner_only")
        body = request.get_json(silent=True) or {}
        mode = tma.parse(body.get("mode"))
        if mode is None:
            return refuse(400, "say all, shareable or none", code="bad_mode")
        old = tma.current()
        try:
            now = tma.write(mode)
        except Exception as e:
            _log.warning("TV memory setting not saved: %s", type(e).__name__)
            return refuse(503, "this computer could not save that right now")
        try:
            from agent_friday.services import setting_proposals as sp
            if old != now:
                sp.record_change("tv_memory", label=sp.LABELS.get("tv_memory", "what the TV may remember"),
                                 old=tma.LABELS[old], new=tma.LABELS[now], snapshot={tma.KEY: old},
                                 by="the owner's %s" % str(g.device.get("kind") or "device"))
        except Exception as e:
            _log.info("TV memory change not recorded: %s", type(e).__name__)
        return jsonify({"ok": True, **tma.describe()})

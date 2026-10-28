"""
Agent Friday — forget a fact, a conversation or a source; show where something is (NS-15.7-1)

  POST  /api/memory/forget-fact           {text | fact_id | proposal_id, turn_ids?: [message id], confirm: true}
                                          only the turns the owner ticked leave recall
  POST  /api/memory/forget-conversation   {conversation_id, confirm: true}       a press in Friday's page
  POST  /api/memory/stop-using-source     {source, confirm: true}                a press in Friday's page
                                          library:<id> | meeting:<id> | web:<domain> | sender:<address>
  GET   /api/memory/show-everywhere?q=    read-only: where a statement, person or source appears

services/forget_kinds does the work and explains what each removes and what it
keeps. Every route is the owner's alone: signed in (login_required) and the
local owner principal (an observer, or any principal that is not the owner, is
refused before the view runs). The three forgets need ``confirm: true`` (the
page asks first, after showing what Show everywhere found). Inputs are bounded;
nothing the owner typed is logged, only the kind of request and counts.
"""
from __future__ import annotations

import logging
import re

from flask import Blueprint, g, jsonify, request

from agent_friday.core import login_required
from agent_friday.routes._errors import public_result
from agent_friday.services import forget_kinds

memory_forget_bp = Blueprint("memory_forget", __name__)
_log = logging.getLogger("friday.memory_forget")

#: The principals that may forget or look: the local owner only.
OWNER_PRINCIPALS = frozenset({"user"})

_ID = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
_CONV_ID = re.compile(r"^[A-Za-z0-9_.-]{1,80}$")
_TURN_ID = re.compile(r"^[A-Za-z0-9_.:-]{1,80}$")


@memory_forget_bp.before_request
def _owner_only():
    try:
        who = getattr(g, "friday_principal", "user") or "user"
    except Exception:
        who = None
    if who not in OWNER_PRINCIPALS:
        return jsonify({"ok": False, "error": "only the owner can forget, or look up, what Friday holds"}), 403
    return None


def _body():
    d = request.get_json(silent=True)
    return d if isinstance(d, dict) else {}


def _bad(msg: str, status: int = 400):
    return jsonify({"ok": False, "error": msg}), status


def _confirmed(d) -> bool:
    return d.get("confirm") is True


@memory_forget_bp.route("/api/memory/forget-fact", methods=["POST"])
@login_required
def forget_fact():
    d = _body()
    text = d.get("text") if isinstance(d.get("text"), str) else ""
    fact_id = str(d.get("fact_id") or "").strip()
    proposal_id = str(d.get("proposal_id") or "").strip()
    if len(text) > forget_kinds.MAX_TEXT:
        return _bad("a statement is at most %d characters" % forget_kinds.MAX_TEXT)
    if (fact_id and not _ID.match(fact_id)) or (proposal_id and not _ID.match(proposal_id)):
        return _bad("that id is not valid")
    if not (text.strip() or fact_id or proposal_id):
        return _bad("say which statement to forget")
    turn_ids = d.get("turn_ids", [])
    if turn_ids is None:
        turn_ids = []
    if (not isinstance(turn_ids, list) or len(turn_ids) > forget_kinds.MAX_TURN_IDS
            or not all(isinstance(t, str) and _TURN_ID.match(t) for t in turn_ids)):
        return _bad("turn_ids is a list of at most %d message ids" % forget_kinds.MAX_TURN_IDS)
    if not _confirmed(d):
        return _bad("forgetting needs the owner's confirmation")
    out = forget_kinds.forget_fact(text, fact_id=fact_id, proposal_id=proposal_id, turn_ids=turn_ids)
    _log.info("forget-fact requested (ok=%s, %d turns chosen)", bool(out.get("ok")), len(turn_ids))
    return jsonify(public_result(out, "Couldn't forget that"))


@memory_forget_bp.route("/api/memory/forget-conversation", methods=["POST"])
@login_required
def forget_conversation():
    d = _body()
    cid = str(d.get("conversation_id") or "").strip()
    if not _CONV_ID.match(cid):
        return _bad("say which conversation")
    if not _confirmed(d):
        return _bad("forgetting needs the owner's confirmation")
    held = _press()
    if held:
        return held
    out = forget_kinds.forget_conversation(cid)
    _log.info("forget-conversation requested (ok=%s)", bool(out.get("ok")))
    return jsonify(public_result(out, "Couldn't forget that conversation")), (200 if out.get("ok") else 404)


def _press():
    """Forgetting a conversation or stopping a source is the owner's press in
    Friday's own page on this PC (screen_click.screen_session: the page's
    session token, its browser headers and this launch's desk key, all of
    which Friday's page sends on every POST). Anything else gets the coded 403
    the page answers by fetching the desk key and pressing once more."""
    from agent_friday.services import screen_click
    return screen_click.press_refusal(request, "Do this with a press in Friday's page, on the computer Friday "
                                               "runs on.")


def _library_checks():
    """A Library forget is held to the Library's own rules (routes/library):
    Friday's own page, its session token, and the action gate's receipt."""
    from agent_friday import core
    if not core._api_token_valid(request.headers.get("X-Friday-Token")):
        return _bad("the Library answers only Friday's own page", 403)
    return _press()


@memory_forget_bp.route("/api/memory/stop-using-source", methods=["POST"])
@login_required
def stop_using_source():
    d = _body()
    source = d.get("source") if isinstance(d.get("source"), str) else ""
    if not source.strip() or len(source) > forget_kinds.MAX_SOURCE:
        return _bad("say which source (at most %d characters)" % forget_kinds.MAX_SOURCE)
    if not _confirmed(d):
        return _bad("stopping a source needs the owner's confirmation")
    kind, value = forget_kinds.parse_source(source)
    if kind == "library":
        bad = _library_checks()
        if bad:
            return bad
        from agent_friday.routes.library import _gated
        held = _gated("library_forget", {"doc_id": int(value)})
        if held:
            return held
    else:
        held = _press()
        if held:
            return held
    out = forget_kinds.stop_using_source(source)
    _log.info("stop-using-source requested (%s)", kind or "unknown")
    return jsonify(public_result(out, "Couldn't stop using that source"))


@memory_forget_bp.route("/api/memory/show-everywhere", methods=["GET"])
@login_required
def show_everywhere():
    q = str(request.args.get("q") or "")
    if not q.strip() or len(q) > forget_kinds.MAX_TEXT:
        return _bad("say what to look for (at most %d characters)" % forget_kinds.MAX_TEXT)
    return jsonify(public_result(forget_kinds.show_everywhere(q), "Couldn't look that up"))

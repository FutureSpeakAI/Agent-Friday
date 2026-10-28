"""
Agent Friday — Memory card API (services/core_memory)

  GET   /api/core-memory              both files, Friday's recent changes, the review queue
  POST  /api/core-memory/add          {target, text}            the owner adds a line
  POST  /api/core-memory/edit         {target, index, text, expect?}  the owner rewrites a line
  POST  /api/core-memory/delete       {target, index, expect?}        the owner deletes a line
                                      (expect: the line's text as shown; a different line there is 409)
  POST  /api/core-memory/undo         {change_id}               undo one of Friday's changes
  POST  /api/core-memory/review       {id, keep: bool}          keep or discard a queued suggestion

  GET   /api/memory/search  ?q=&kinds=a,b&since=&until=&superseded=1&limit=
                                      the owner's search over everything Friday keeps
  GET   /api/memory/why     ?kind=&id=  where Friday knows one result from
  GET   /api/memory/export  ?q=&kinds=&since=&until=&superseded=1&format=json|md
                                      the search's results (no q: the whole memory, bounded) as a download

Every write here is the owner's own, so it reaches every open conversation's
next turn at once (services/prompt_freeze). The three reads are the owner's
only (principal "user"; an observer or any other is refused), the parameters are bounded, and
neither the query nor a result is logged (services/memory_search). The export also
answers only Friday's own page (its X-Friday-Token, or a same-origin non-navigation
fetch), never a cross-site link or navigation. The card's read, edit, delete and
review are the owner's only too.
"""
from flask import Blueprint, Response, g, jsonify, request, stream_with_context

from agent_friday.core import login_required
from agent_friday.routes._errors import public_result
from agent_friday.services import core_memory

core_memory_bp = Blueprint("core_memory", __name__)


def _body():
    return request.get_json(silent=True) or {}


def _not_owner():
    """Only the owner's own principal reads the owner's memory: an observer, or any other principal
    login_required lets through, is refused (as routes/lanes, routes/traces and the others check it)."""
    try:
        return (getattr(g, "friday_principal", "user") or "user") != "user"
    except Exception:
        return True


def _from_friday_page() -> bool:
    """True for a request Friday's own page made with script: it carries the page's X-Friday-Token
    (apiFetch adds it), or the browser says it is a same-origin fetch (Sec-Fetch-Site: same-origin and a
    Sec-Fetch-Mode that is not navigate). A cross-site page's link, form or top-level navigation to the
    export carries neither (loopback trust and a SameSite=Lax cookie would otherwise let it through), so it
    cannot make the browser download the owner's whole memory."""
    try:
        import agent_friday.core as core
        if core._api_token_valid(request.headers.get("X-Friday-Token")):
            return True
    except Exception:
        pass
    site = (request.headers.get("Sec-Fetch-Site") or "").strip().lower()
    mode = (request.headers.get("Sec-Fetch-Mode") or "").strip().lower()
    return site == "same-origin" and bool(mode) and mode != "navigate"


def _flag(v) -> bool:
    return str(v or "").strip().lower() in ("1", "true", "yes", "on")


@core_memory_bp.route("/api/memory/search", methods=["GET"])
@login_required
def memory_search_route():
    if _not_owner():
        return jsonify({"ok": False, "error": "Only the owner can search Friday's memory."}), 403
    from agent_friday.services import memory_search as ms
    a = request.args
    try:
        q, kinds, since, until, limit = ms.check_params(a.get("q"), a.get("kinds"), a.get("since"),
                                                        a.get("until"), a.get("limit"))
    except ms.BadRequest as e:
        return jsonify({"ok": False, "error": str(e)}), 400
    return jsonify(public_result(ms.search(q, kinds=kinds, since=since, until=until,
                                           include_superseded=_flag(a.get("superseded")), limit=limit),
                                 "Couldn't search memory"))


@core_memory_bp.route("/api/memory/why", methods=["GET"])
@login_required
def memory_why_route():
    if _not_owner():
        return jsonify({"ok": False, "error": "Only the owner can read where Friday knows something from."}), 403
    from agent_friday.services import memory_search as ms
    try:
        kind, item_id = ms.check_why(request.args.get("kind"), request.args.get("id"))
    except ms.BadRequest as e:
        return jsonify({"ok": False, "error": str(e)}), 400
    return jsonify(public_result(ms.why(kind, item_id), "Couldn't look that up"))


@core_memory_bp.route("/api/memory/export", methods=["GET"])
@login_required
def memory_export_route():
    """The owner's search results (or, with no query, the whole memory, bounded) as a file to keep:
    format=json or md. Gathered in memory and sent a piece at a time; nothing is written to disk here
    (services/memory_export). Only Friday's own page may ask (_from_friday_page)."""
    if _not_owner():
        return jsonify({"ok": False, "error": "Only the owner can export Friday's memory."}), 403
    if not _from_friday_page():
        return jsonify({"ok": False, "error": "The memory export answers only Friday's own page."}), 403
    from agent_friday.services import memory_export as me
    a = request.args
    try:
        q, kinds, since, until, fmt = me.check_params(a.get("q") if "q" in a else a.get("query"), a.get("kinds"),
                                                      a.get("since"), a.get("until"), a.get("format"))
    except me.BadRequest as e:
        return jsonify({"ok": False, "error": str(e)}), 400
    exp = me.build(q, kinds=kinds, since=since, until=until, include_superseded=_flag(a.get("superseded")))
    if not exp.get("ok"):
        return jsonify(public_result(exp, "Couldn't export memory")), 500
    if fmt == "md":
        body, mimetype = me.to_markdown(exp), "text/markdown; charset=utf-8"
    else:
        body, mimetype = me.to_json(exp), "application/json; charset=utf-8"
    return Response(stream_with_context(body), mimetype=mimetype,
                    headers={"Content-Disposition": 'attachment; filename="%s"' % me.filename(fmt),
                             "Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"})


def _not_the_owner_card():
    return jsonify({"ok": False, "error": "Only the owner can read or change the memory card."}), 403


def _expect(d):
    """The line's text as the owner saw it, when the caller sends it (None: no check, as before)."""
    e = d.get("expect")
    return e if isinstance(e, str) else None


def _card_write(result, what):
    """A card write's answer: 409 when the line at the index is no longer the one the owner saw."""
    body = public_result(result, what)
    if isinstance(result, dict) and result.get("changed"):
        return jsonify(body), 409
    return jsonify(body)


@core_memory_bp.route("/api/core-memory", methods=["GET"])
@login_required
def get_core_memory():
    if _not_owner():
        return _not_the_owner_card()
    return jsonify(public_result({"ok": True, "card": core_memory.snapshot(),
                                  "changes": core_memory.recent_changes(20),
                                  "pending": core_memory.pending()},
                                 "Couldn't load the memory card"))


@core_memory_bp.route("/api/core-memory/add", methods=["POST"])
@login_required
def add_line():
    d = _body()
    return jsonify(public_result(core_memory.add(str(d.get("target") or ""), str(d.get("text") or ""),
                                                 actor="owner"), "Couldn't add that line"))


@core_memory_bp.route("/api/core-memory/edit", methods=["POST"])
@login_required
def edit_line():
    if _not_owner():
        return _not_the_owner_card()
    d = _body()
    return _card_write(core_memory.owner_set(str(d.get("target") or ""), d.get("index"),
                                             str(d.get("text") or ""), expect=_expect(d)),
                       "Couldn't change that line")


@core_memory_bp.route("/api/core-memory/delete", methods=["POST"])
@login_required
def delete_line():
    if _not_owner():
        return _not_the_owner_card()
    d = _body()
    return _card_write(core_memory.owner_delete(str(d.get("target") or ""), d.get("index"), expect=_expect(d)),
                       "Couldn't delete that line")


@core_memory_bp.route("/api/core-memory/undo", methods=["POST"])
@login_required
def undo_change():
    d = _body()
    return jsonify(public_result(core_memory.undo(str(d.get("change_id") or "")), "Couldn't undo that"))


@core_memory_bp.route("/api/core-memory/review", methods=["POST"])
@login_required
def review_suggestion():
    if _not_owner():
        return _not_the_owner_card()
    d = _body()
    return jsonify(public_result(core_memory.decide(str(d.get("id") or ""), bool(d.get("keep"))),
                                 "Couldn't record that"))

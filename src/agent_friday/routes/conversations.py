"""The HTTP surface for conversations.

THE UI HAS BEEN CALLING THESE ENDPOINTS ALL ALONG. `index.html` has a
conversation switcher, a per-chat model picker and a transcript loader, and
every one of them fetches `/api/conversations...` — six call sites. None of
those routes existed. The service layer underneath
(`services/conversations.py`) is complete: per-conversation directories, an
atomic message log, cost totals and a per-conversation `seat`. Only the doorway
was missing, so the switcher silently did nothing and every turn fell back to
Main.

That is why "multiple chat windows" looked like a big frontend project. Most of
it is already built on both sides; this is the join.

CONTRACT, taken from what the UI actually sends rather than from a design doc:

    GET    /api/conversations              -> {status, conversations, projects, main_id}
    POST   /api/conversations              -> {status, conversation}
    GET    /api/conversations/<cid>        -> {status, conversation}
    PATCH  /api/conversations/<cid>        -> {status, conversation, note?}
    GET    /api/conversations/<cid>/messages -> {status, messages}
    GET    /api/projects                   -> {status, projects}
    POST   /api/projects                   -> {status, project}
    GET    /api/projects/<pid>             -> {status, project, conversations}
    PATCH  /api/projects/<pid>             -> {status, project, note?}
    DELETE /api/projects/<pid>             -> {status, deleted, detached, note}

The chat list carries the projects with it. The sidebar draws folders and
threads in one frame, and making it wait on a second request is how the
folders arrive a beat after their contents.

The PATCH response always carries the conversation as it now IS, never as it
was asked to be. The UI compares the two and tells the user when a binding did
not stick — a refusal that reports success is the failure this whole codebase
keeps having to fix.
"""
from __future__ import annotations

import logging
import time

from flask import Blueprint, jsonify, request

from agent_friday.services import conversations as _conv
from agent_friday.services import projects as _proj

conversations_bp = Blueprint("conversations", __name__)

_log = logging.getLogger("friday.conversations")

#: Fields a client may set. Mirrors what `conversations.patch` accepts; listed
#: again here so an HTTP caller cannot reach a field the UI has no business
#: writing just because the service happens to allow it.
#:
#: `pinned` is NOT here. At the conversation level it holds pinned message ids
#: and is managed by clear/prune, not by the sidebar; thread pinning is
#: `pinned_at`. See the note in conversations._blank.
_PATCHABLE = ("title", "status", "seat", "pinned_at", "project")

#: What a project may have set over HTTP.
_PROJECT_PATCHABLE = ("name", "seat", "instructions", "color", "archived")


def _project_seats() -> dict:
    """`{project_id: seat}` for one request, so summarising N chats costs one
    pass over the projects rather than a disk read per chat."""
    try:
        return {p.get("id"): p.get("seat") for p in _proj.list_all(
            include_archived=True)}
    except Exception:
        return {}


def _summary(conv: dict, project_seats: dict | None = None) -> dict:
    """What the switcher and the sidebar need, without the transcript."""
    return {
        "id": conv.get("id"),
        "title": conv.get("title") or "New chat",
        "status": conv.get("status") or "active",
        # Thread pinning. Older records predate the field and read as unpinned,
        # which is the right answer for them.
        "pinned_at": conv.get("pinned_at"),
        "project": conv.get("project"),
        "seat": conv.get("seat"),
        # What this chat will ACTUALLY run on once its project's default is
        # taken into account. The sidebar shows this, because a chat in a
        # Bonsai project with no binding of its own is going to answer on
        # Bonsai and a row that showed nothing there would be lying by
        # omission. `seat` stays alongside it so the UI can still tell a
        # binding from an inheritance.
        "effective_seat": _conv.effective_seat_of(conv, project_seats),
        "created_at": conv.get("created_at"),
        "last_active_at": conv.get("last_active_at"),
        "totals": conv.get("totals") or {},
    }


def _project_summary(proj: dict, counts: dict | None = None) -> dict:
    pid = proj.get("id")
    return {
        "id": pid,
        "name": proj.get("name") or "Untitled project",
        "seat": proj.get("seat"),
        "instructions": proj.get("instructions") or "",
        "color": proj.get("color"),
        "archived": bool(proj.get("archived")),
        "created_at": proj.get("created_at"),
        "updated_at": proj.get("updated_at"),
        "conversations": (counts or {}).get(pid, 0),
    }


@conversations_bp.route("/api/conversations", methods=["GET"])
def list_conversations():
    _conv.ensure_main()
    include_archived = request.args.get("archived") in ("1", "true", "yes")
    seats = _project_seats()
    convs = [_summary(c, seats)
             for c in _conv.list_all(include_archived=include_archived)]
    # The projects ride along with the chat list. The sidebar needs both to
    # draw one frame, and two endpoints it has to wait on separately is how a
    # sidebar renders its folders a beat after its chats.
    counts = {}
    for c in convs:
        pid = c.get("project")
        if pid:
            counts[pid] = counts.get(pid, 0) + 1
    projects = [_project_summary(p, counts) for p in _proj.list_all()]
    return jsonify({"status": "ok", "conversations": convs,
                    "projects": projects,
                    "main_id": _conv.MAIN_ID})


@conversations_bp.route("/api/conversations", methods=["POST"])
def create_conversation():
    data = request.get_json(silent=True) or {}
    title = str(data.get("title") or "New chat").strip()[:120] or "New chat"
    seat = data.get("seat") if isinstance(data.get("seat"), dict) else None
    conv = _conv.create(title=title, seat=seat)
    # "+ New chat" inside a project opens IN that project. Filed on creation
    # rather than left loose for the user to drag in afterwards, because the
    # folder they were looking at when they clicked is the answer to where it
    # goes.
    pid = str(data.get("project") or "").strip()
    if pid and _proj.load(pid):
        conv = _conv.patch(conv["id"], project=pid) or conv
    _log.info("created conversation %s (%r) project=%s",
              conv.get("id"), title, conv.get("project"))
    return jsonify({"status": "ok",
                    "conversation": _summary(conv, _project_seats())}), 201


@conversations_bp.route("/api/conversations/<cid>", methods=["GET"])
def get_conversation(cid):
    conv = _conv.load(cid)
    if conv is None:
        return jsonify({"status": "error",
                        "error": "no such conversation: %s" % cid}), 404
    return jsonify({"status": "ok",
                    "conversation": _summary(conv, _project_seats())})


@conversations_bp.route("/api/conversations/<cid>", methods=["PATCH"])
def patch_conversation(cid):
    data = request.get_json(silent=True) or {}
    fields = {k: data[k] for k in _PATCHABLE if k in data}
    if not fields:
        conv = _conv.load(cid)
        if conv is None:
            return jsonify({"status": "error",
                            "error": "no such conversation: %s" % cid}), 404
        return jsonify({"status": "ok", "conversation": _summary(conv)})

    note = None
    if "seat" in fields:
        seat = fields["seat"]
        note = _why_this_seat_cannot_be_bound(seat)
        if note:
            # Refuse the binding and say so, rather than accepting it and
            # letting the next turn discover the seat is not there.
            fields.pop("seat")
            _log.info("refused seat binding on %s: %s", cid, note)

    if "project" in fields:
        pid = fields["project"]
        if pid in (None, "", False):
            fields["project"] = None            # dragging a chat back out
        elif not _proj.load(str(pid)):
            # Filing into a folder that is not there would leave the chat
            # pointing at a ghost and invisible in every project view.
            fields.pop("project")
            note = ("there is no project %s, so this chat was left where it "
                    "was" % pid)
            _log.info("refused refile of %s: %s", cid, note)

    if "pinned_at" in fields:
        # The client sends true/false; the store keeps when, so pins order
        # themselves without a second field.
        v = fields["pinned_at"]
        fields["pinned_at"] = (time.time() if v is True
                               else None if v in (False, None, "")
                               else v)

    conv = _conv.patch(cid, **fields) if fields else _conv.load(cid)
    if conv is None:
        return jsonify({"status": "error",
                        "error": "no such conversation: %s" % cid}), 404
    out = {"status": "ok", "conversation": _summary(conv, _project_seats())}
    if note:
        out["note"] = note
    return jsonify(out)


# ── Projects ────────────────────────────────────────────────────────────────
#
# Folders for chats, plus the defaults that come with them. The store's
# docstring carries the reasoning; these are the doors.
#
# The refusal pattern is the one already used for seats above: the response
# always carries the object as it now IS, with a `note` when what was asked
# for did not happen. A refusal that reports success is the bug this codebase
# keeps having to fix.

@conversations_bp.route("/api/projects", methods=["GET"])
def list_projects():
    include_archived = request.args.get("archived") in ("1", "true", "yes")
    projs = _proj.list_all(include_archived=include_archived)
    counts = {}
    for c in _conv.list_all(include_archived=False):
        pid = c.get("project")
        if pid:
            counts[pid] = counts.get(pid, 0) + 1
    return jsonify({"status": "ok",
                    "projects": [_project_summary(p, counts) for p in projs]})


@conversations_bp.route("/api/projects", methods=["POST"])
def create_project():
    data = request.get_json(silent=True) or {}
    proj = _proj.create(
        name=str(data.get("name") or "New project"),
        seat=data.get("seat") if isinstance(data.get("seat"), dict) else None,
        instructions=str(data.get("instructions") or ""),
        color=data.get("color"),
    )
    _log.info("created project %s (%r)", proj.get("id"), proj.get("name"))
    return jsonify({"status": "ok", "project": _project_summary(proj)}), 201


@conversations_bp.route("/api/projects/<pid>", methods=["GET"])
def get_project(pid):
    proj = _proj.load(pid)
    if proj is None:
        return jsonify({"status": "error",
                        "error": "no such project: %s" % pid}), 404
    seats = _project_seats()
    members = [_summary(c, seats) for c in _conv.list_all(include_archived=False)
               if c.get("project") == pid]
    return jsonify({"status": "ok",
                    "project": _project_summary(proj, {pid: len(members)}),
                    "conversations": members})


@conversations_bp.route("/api/projects/<pid>", methods=["PATCH"])
def patch_project(pid):
    data = request.get_json(silent=True) or {}
    fields = {k: data[k] for k in _PROJECT_PATCHABLE if k in data}

    note = None
    if "seat" in fields and isinstance(fields["seat"], dict):
        # A project seat contends for the GPU exactly as a chat seat does, so
        # it answers to the same rule. Refusing here rather than at first use
        # means the user learns it while setting the default, not while
        # waiting on a turn that has already hung.
        note = _why_this_seat_cannot_be_bound(fields["seat"])
        if note:
            fields.pop("seat")
            _log.info("refused seat default on project %s: %s", pid, note)

    proj = _proj.patch(pid, **fields) if fields else _proj.load(pid)
    if proj is None:
        return jsonify({"status": "error",
                        "error": "no such project: %s" % pid}), 404
    out = {"status": "ok", "project": _project_summary(proj)}
    if note:
        out["note"] = note
    return jsonify(out)


@conversations_bp.route("/api/projects/<pid>", methods=["DELETE"])
def delete_project(pid):
    if _proj.load(pid) is None:
        return jsonify({"status": "error",
                        "error": "no such project: %s" % pid}), 404
    detached = _proj.delete(pid)
    _log.info("deleted project %s, detached %d conversation(s)", pid, detached)
    # Say how many chats came loose. Deleting a folder that held nine threads
    # should not look identical to deleting an empty one.
    return jsonify({"status": "ok", "deleted": pid, "detached": detached,
                    "note": ("%d conversation(s) were moved out of the project "
                             "and kept" % detached) if detached else None})


@conversations_bp.route("/api/conversations/<cid>/messages", methods=["GET"])
def conversation_messages(cid):
    if _conv.load(cid) is None:
        return jsonify({"status": "error",
                        "error": "no such conversation: %s" % cid}), 404
    try:
        limit = int(request.args.get("limit") or 0) or None
    except Exception:
        limit = None
    return jsonify({"status": "ok", "messages": _conv.messages(cid, limit)})


def _why_this_seat_cannot_be_bound(seat) -> str | None:
    """A reason this seat cannot be given to a conversation, or None.

    ONE LOCAL MODEL AT A TIME ON THIS HARDWARE. Two 27B seats do not fit in
    12 GB — measured: two bonsai2:27b servers held 11,605 MiB of
    12,282 between them and every turn sent into that state hung. Letting a
    second conversation quietly bind a different local model is offering the
    user something the machine cannot do, and they find out as a hang rather
    than as a sentence.

    CONSERVATIVE ON PURPOSE. Only a POSITIVE reading of a different local
    model actually serving is grounds for refusal; every failure to look
    returns None and the binding is allowed. "I could not check" is not "it is
    occupied", and a picker that refuses on a failed probe is worse than one
    that occasionally over-promises — the same rule applied to seat presence
    everywhere else in this codebase.
    """
    if not isinstance(seat, dict):
        return None
    model = (seat.get("model") or "").strip()
    if not model:
        return None                       # unbinding is always allowed
    try:
        from agent_friday.services import local_seats
        if not local_seats._is_local_name(model):
            return None                   # cloud seats do not contend
    except Exception:
        return None
    try:
        from agent_friday.services.residency_arbiter import survey_live_seats
        live = survey_live_seats() or {}
    except Exception:
        return None
    others = [m for m in live if m and m != model]
    if not others:
        return None
    return ("%s is already serving on this machine and there is only room for "
            "one local model at a time. Free it first, or give this chat a "
            "cloud model." % others[0])

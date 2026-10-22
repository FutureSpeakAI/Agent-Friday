"""The action ledger, as Activity reads it (services/action_ledger.py).

  GET  /ledger                   the Ledger workspace (static/ledger.html)
  GET  /api/ledger               entries, newest first, with filters (?kind=,
                                 ?tool=, ?goal=, ?task=, ?principal=,
                                 ?provider=, ?status=, ?q= text, ?since= and
                                 ?until= dates, ?archive=1 to search the
                                 archive too, ?before= seq, ?limit= max 500),
                                 each with its verification and a plain line
  GET  /api/ledger/export        the same filters, as a file: ?format=jsonl
                                 (whole entries, checkable elsewhere) or
                                 ?format=report (the verification report)
  GET  /api/ledger/verify        check every line now: the full report
  GET  /api/ledger/retention     the retention period and the archive (A22)
  POST /api/ledger/retention     {"days": n}: change it (0 = off, at least 30);
                                 on this PC only. Archives at once; never deletes.
  POST /api/ledger/acknowledge   the owner has looked at the breaks shown;
                                 outward actions may go on. A press on
                                 Friday's own page only (screen_click).

Not on the observer's read-only list (services/observer_access): decisions
and change receipts name what Friday did to the owner's things.
"""
from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from pathlib import Path

from flask import Blueprint, Response, jsonify, request

from agent_friday.core import login_required
from agent_friday.routes._errors import api_error
from agent_friday.services import action_ledger

ledger_bp = Blueprint("ledger", __name__)

#: Fields Activity never shows: signatures and undo data.
_HIDDEN = {"hmac", "sig", "undo", "args_hash", "_migrated"}

#: What each kind is called on the page.
KIND_LABELS = {
    "activity": "Activity",
    "decision": "Checkpoint decision",
    "action": "Change to your things",
    "goal": "Goal receipt",
    "dissent": "Friday pushed back",
    "send_grant": "Pre-approved send",
    "web_site": "Website trust",
    "inbox_cleanup": "Inbox cleanup",
    "media_diet": "Media diet",
    "migration": "Old receipts brought in",
    "integrity_ack": "You reviewed a ledger break",
    "truncation_detected": "Records missing from the end",
    "torn_tail": "An unfinished write was set aside",
    "restore": "Restored from a Friday Bundle",
    "restore_resumed": "Outward actions resumed after a restore",
    "archived": "Older entries moved to the archive",
    "legacy_reappeared": "An old receipt file appeared again",
    "migration_complete": "Old receipts all brought in",
    "setting_changed": "You changed a ledger setting",
    "backup": "Scheduled backup",
}

_LAST_FULL = [0.0]

#: The most entries one export carries (narrow it with the filters).
EXPORT_MAX = 200000

#: Where each filter looks in a record, first match wins.
_FIELDS = {
    "tool": ("tool", "tool_name", "op", "action"),
    "goal": ("goal_id", "goal"),
    "task": ("task_id", "task"),
    "provider": ("provider",),
    "status": ("status", "decision", "outcome", "event", "state"),
}


def _field(e: dict, name: str):
    rec = e.get("rec") if isinstance(e.get("rec"), dict) else {}
    if name == "principal":
        return e.get("principal")
    for k in _FIELDS.get(name, ()):
        v = rec.get(k)
        if v not in (None, ""):
            if isinstance(v, dict):
                v = v.get("id") or v.get("name")
            return v
    if name == "status" and isinstance(rec.get("ok"), bool):
        return "ok" if rec["ok"] else "failed"
    return None


def _when(v: str, end: bool = False):
    """A date (YYYY-MM-DD), an ISO time or unix seconds, as unix seconds."""
    v = (v or "").strip()
    if not v:
        return None
    try:
        return float(v)
    except ValueError:
        pass
    try:
        if len(v) == 10:
            d = datetime.strptime(v, "%Y-%m-%d").replace(tzinfo=timezone.utc)
            return d.timestamp() + (86400.0 if end else 0.0)
        return datetime.fromisoformat(v.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def _filters(args) -> tuple:
    """(kinds, where, limit, archive) from the query string."""
    try:
        limit = max(1, min(int(args.get("limit", 100)), 500))
    except (TypeError, ValueError):
        limit = 100
    kinds = [k for k in (args.get("kind") or "").split(",") if k] or None
    conds = []
    before = args.get("before")
    if before not in (None, ""):
        try:
            b = int(before)
            conds.append(lambda e: isinstance(e.get("seq"), int) and e["seq"] < b)
        except ValueError:
            pass
    for name in ("tool", "goal", "task", "principal", "provider", "status"):
        want = (args.get(name) or "").strip().lower()
        if want:
            conds.append(lambda e, n=name, w=want: str(_field(e, n) or "").lower() == w)
    since, until = _when(args.get("since")), _when(args.get("until"), end=True)
    if since is not None:
        conds.append(lambda e: isinstance(e.get("t"), (int, float)) and e["t"] >= since)
    if until is not None:
        conds.append(lambda e: isinstance(e.get("t"), (int, float)) and e["t"] < until)
    q = (args.get("q") or "").strip().lower()
    if q:
        def _has(e, q=q):
            view = public_entry(e)
            return q in json.dumps([view["label"], view["record"]], default=str).lower()
        conds.append(_has)
    where = (lambda e: all(c(e) for c in conds)) if conds else None
    archive = str(args.get("archive") or "").lower() in ("1", "true", "yes")
    return kinds, where, limit, archive


def plain_line(e: dict) -> str:
    """One sentence a person can read for an entry."""
    k = e.get("kind")
    rec = e.get("rec") if isinstance(e.get("rec"), dict) else {}
    label = KIND_LABELS.get(k, k or "Entry")
    tool, status = _field(e, "tool"), _field(e, "status")
    if k == "decision":
        return "Checkpoint for %s: %s%s." % (tool or "an action", status or "decided",
                                              (", because " + str(rec.get("reason"))[:160])
                                              if rec.get("reason") else "")
    if k == "action":
        return "%s: %s (%s)." % (label, rec.get("summary") or tool or "a change", status or "done")
    if k == "activity":
        what = rec.get("kind") or "event"
        return "%s: %s%s%s." % (label, str(what).replace("_", " "),
                                (" " + str(tool)) if tool else "",
                                (" (" + str(status) + ")") if status else "")
    if k == "archived":
        return "%s records up to %s moved to the encrypted archive (kept, not deleted)." % (
            rec.get("records"), rec.get("through"))
    bits = [str(x) for x in (tool, status) if x]
    return label + (": " + ", ".join(bits) if bits else "") + "."


def _links(rec: dict) -> dict:
    out = {}
    for k in ("approval", "approval_id", "grant", "grant_id", "policy", "policy_version",
              "plan_version", "turn_id", "receipt_id"):
        v = rec.get(k)
        if v not in (None, "", [], {}):
            out[k] = v if isinstance(v, (str, int, float, bool)) else str(v)[:120]
    return out


def _before_after(rec: dict):
    for b, a in (("before", "after"), ("from", "to"), ("old", "new")):
        if b in rec or a in rec:
            return {"before": rec.get(b), "after": rec.get(a)}
    moves = (rec.get("undo") or {}).get("moves") if isinstance(rec.get("undo"), dict) else None
    if isinstance(moves, list) and moves:
        return {"moves": [{"from": m.get("src") or m.get("from"), "to": m.get("dst") or m.get("to")}
                          for m in moves[:20] if isinstance(m, dict)]}
    return None


def public_entry(e: dict) -> dict:
    rec = e.get("rec") if isinstance(e.get("rec"), dict) else {}
    view = {k: v for k, v in rec.items() if k not in _HIDDEN}
    if e.get("kind") == "action":
        view["undoable"] = bool(rec.get("undo")) and not rec.get("undone") \
            and rec.get("status") in ("complete", "partial")
    mig = rec.get("_migrated")
    return {
        "seq": e.get("seq"), "ts": e.get("ts"), "t": e.get("t"),
        "kind": e.get("kind"), "label": KIND_LABELS.get(e.get("kind"), e.get("kind")),
        "principal": e.get("principal"),
        "record": view,
        "migrated": ({"from": mig.get("from"), "legacy_verified": mig.get("legacy_verified")}
                     if isinstance(mig, dict) else None),
    }


def workspace_entry(e: dict, key) -> dict:
    """public_entry plus what the Ledger workspace shows beside it."""
    out = public_entry(e)
    rec = e.get("rec") if isinstance(e.get("rec"), dict) else {}
    out.update(plain=plain_line(e), links=_links(rec), before_after=_before_after(rec),
               verified=bool(key is not None and action_ledger.entry_ok(e, key)),
               fields={n: _field(e, n) for n in ("tool", "goal", "task", "provider", "status")},
               hash=e.get("hash"))
    return out


def _read(kinds, *, archive: bool, **kw):
    """(entries, why the archive could not be read or None). With the archive,
    it is read strictly: a missing or changed archive is said, never dropped."""
    if not archive:
        return action_ledger.entries(kinds, **kw), None
    try:
        return action_ledger.entries(kinds, archive=True, strict=True, gate=False, **kw), None
    except action_ledger.LedgerError as e:
        return action_ledger.entries(kinds, archive=False, **kw), str(e)


def _key_or_none():
    try:
        return action_ledger._key()
    except Exception:
        return None


_PAGE = Path(__file__).resolve().parents[3] / "static" / "ledger.html"


@ledger_bp.route("/ledger", methods=["GET"])
@login_required
def ledger_page():
    import os

    from agent_friday import core
    page = next((p for p in (Path(os.path.abspath("static")) / "ledger.html", _PAGE) if p.is_file()), None)
    if page is None:
        return "Ledger page not found", 404
    html = page.read_text(encoding="utf-8")
    tok = ('<script>window.__FRIDAY_API_TOKEN="%s";</script>'  # pragma: allowlist secret
           % core._current_api_token())
    resp = Response(html.replace("<head>", "<head>\n" + tok, 1), content_type="text/html")
    resp.headers["Cache-Control"] = "no-store"
    return resp


@ledger_bp.route("/api/ledger", methods=["GET"])
@login_required
def api_ledger():
    try:
        kinds, where, limit, archive = _filters(request.args)
        rows, archive_error = _read(kinds, newest_first=True, limit=limit, where=where,
                                    archive=archive)
        key = _key_or_none()
        return jsonify({"status": "ok", "ledger": action_ledger.summary(),
                        "entries": [workspace_entry(e, key) for e in rows], "count": len(rows),
                        "kinds": KIND_LABELS, "archive_searched": archive and not archive_error,
                        "archive_error": archive_error})
    except Exception as e:  # noqa: BLE001
        return api_error(e, "The action ledger could not be read.")


@ledger_bp.route("/api/ledger/verify", methods=["GET"])
@login_required
def api_ledger_verify():
    try:
        from agent_friday.core import _is_local_request
        # a full check opens every archived file: on this PC at most every
        # 30 seconds, from elsewhere the last result (at most FULL_RECHECK_S old)
        now = time.time()
        force = _is_local_request() and now - _LAST_FULL[0] >= 30.0
        if force:
            _LAST_FULL[0] = now
        return jsonify({"status": "ok", "report": action_ledger.verify(force=force)})
    except Exception as e:  # noqa: BLE001
        return api_error(e, "The action ledger could not be checked.")


@ledger_bp.route("/api/ledger/acknowledge", methods=["POST"])
@login_required
def api_ledger_acknowledge():
    from agent_friday.core import _is_local_request
    from agent_friday.services import screen_click
    # this PC only: a page reached through a tunnel or the LAN is not a press here
    if not (_is_local_request() and screen_click.screen_session(request)):
        return jsonify({"status": "error", "code": "press_required",
                        "error": "Acknowledge this on Friday's own page, on this PC."}), 403
    try:
        body = request.get_json(silent=True) or {}
        reviewed = body.get("reviewed")
        if not isinstance(reviewed, list):
            return jsonify({"status": "error", "code": "review_first",
                            "error": "Review the ledger first: the acknowledgement names what you saw."}), 400
        try:
            out = action_ledger.acknowledge(by="owner:ui", note=str(body.get("note") or "")[:200],
                                            reviewed=reviewed[:action_ledger.MAX_PROBLEMS])
        except action_ledger.StaleReview:
            return jsonify({"status": "error", "code": "review_again",
                            "error": "The ledger changed since you reviewed it. Review it again."}), 409
        return jsonify({"status": "ok", **out})
    except Exception as e:  # noqa: BLE001
        return api_error(e, "The acknowledgement could not be written.")


@ledger_bp.route("/api/ledger/export", methods=["GET"])
@login_required
def api_ledger_export():
    """The owner's own record as a file to keep: whole entries (hash, link and
    signature included, so they can be checked elsewhere), or the report."""
    try:
        fmt = (request.args.get("format") or "jsonl").lower()
        stamp = time.strftime("%Y%m%d-%H%M%S")
        if fmt == "report":
            rep = action_ledger.verify(force=True)
            rep["exported_at"] = stamp
            body = json.dumps(rep, indent=1, default=str)
            name, ctype = "friday-ledger-report-%s.json" % stamp, "application/json"
        else:
            args = request.args.to_dict()
            args["limit"] = "500"
            kinds, where, _limit, archive = _filters(args)
            rows, archive_error = _read(kinds, newest_first=False, where=where, archive=archive,
                                        limit=EXPORT_MAX)
            if archive_error:
                return jsonify({"status": "error", "code": "archive_unreadable",
                                "error": "The archive could not be read (%s); nothing was exported." % archive_error}), 409
            body = "".join(json.dumps(e, sort_keys=True, separators=(",", ":")) + "\n" for e in rows)
            name, ctype = "friday-ledger-%s.jsonl" % stamp, "application/x-ndjson"
        resp = Response(body, content_type=ctype)
        resp.headers["Content-Disposition"] = 'attachment; filename="%s"' % name
        resp.headers["Cache-Control"] = "no-store"
        return resp
    except Exception as e:  # noqa: BLE001
        return api_error(e, "The ledger could not be exported.")


@ledger_bp.route("/api/ledger/retention", methods=["GET"])
@login_required
def api_ledger_retention():
    try:
        return jsonify({"status": "ok", "archive": action_ledger.archive_summary(),
                        "default_days": action_ledger.DEFAULT_ARCHIVE_AFTER_DAYS,
                        "min_days": action_ledger.MIN_ARCHIVE_AFTER_DAYS})
    except Exception as e:  # noqa: BLE001
        return api_error(e, "The ledger's archive could not be read.")


@ledger_bp.route("/api/ledger/retention", methods=["POST"])
@login_required
def api_ledger_retention_set():
    from agent_friday.core import _is_local_request
    from agent_friday.services import screen_click
    # this PC only, and a press on Friday's own page (a model's tool or a
    # remote session cannot move the record out of Activity)
    if not (_is_local_request() and screen_click.screen_session(request)):
        return jsonify({"status": "error", "code": "press_required",
                        "error": "Change how long the action record stays live on Friday's own page, on this PC."}), 403
    try:
        body = request.get_json(silent=True) or {}
        if "days" not in body:
            return jsonify({"status": "error", "error": "Say how many days (0 turns archiving off)."}), 400
        days = action_ledger.normalise_archive_days(body.get("days"))
        from agent_friday.core import _load_settings, _save_settings
        before = (_load_settings() or {}).get(action_ledger.ARCHIVE_SETTING,
                                              action_ledger.DEFAULT_ARCHIVE_AFTER_DAYS)
        _save_settings({action_ledger.ARCHIVE_SETTING: days})
        action_ledger.try_append("setting_changed", {"setting": action_ledger.ARCHIVE_SETTING,
                                                      "before": before, "after": days})
        out = action_ledger.archive_old(days=days) if days else {"archived": 0, "why": "archiving is off"}
        return jsonify({"status": "ok", "days": days, "result": out,
                        "archive": action_ledger.archive_summary()})
    except Exception as e:  # noqa: BLE001
        return api_error(e, "The retention period could not be changed.")

"""Reasoning traces API (services/reasoning_trace.py).

    GET  /api/traces/live?snapshot=1     running + recently finished traces, full events
    GET  /api/traces/live?since=<seq>    live events after a cursor (the tray polls this)
    GET  /api/traces                     archived + live traces, filtered (q, model, kind,
                                         task, since, until, subagents, limit, offset)
    GET  /api/traces/<trace_id>          the full tree containing a trace
    GET  /api/traces/verify              walk the hash chain and signatures
    GET  /api/traces/export              every archived trace, decrypted, as NDJSON
    GET  /api/traces/status              archive location, size, pending, retention
    POST /api/traces/retention           {retention_days, apply}

LOCAL ONLY. Reasoning holds whatever the user and their data said to the
model. These routes answer requests from this machine and nobody else: an
observer credential is refused (it never reaches here -- the observer
allowlist does not include them), and so is an authenticated REMOTE session,
because serving the archive to a tunnel is the archive leaving the machine.
"""

import json
import time

from flask import Blueprint, Response, jsonify, request

from agent_friday.services import reasoning_trace as rt

traces_bp = Blueprint('traces', __name__)


def _local_user_only():
    """None when the caller is the local user; otherwise a refusal response."""
    try:
        from flask import g
        if (getattr(g, "friday_principal", "user") or "user") != "user":
            return jsonify({"error": "reasoning traces are only served to the local user"}), 403
        from agent_friday.core import _is_local_request
        if not _is_local_request():
            return jsonify({"error": "reasoning traces are only served on this machine; "
                                     "they are not available over a remote connection"}), 403
    except Exception:
        return jsonify({"error": "could not establish that this request is local"}), 403
    return None


@traces_bp.before_request
def _gate():
    return _local_user_only()


def _float_arg(name):
    raw = request.args.get(name)
    if raw in (None, ''):
        return None
    try:
        return float(raw)
    except (TypeError, ValueError):
        return None


def _int_arg(name, default, lo, hi):
    try:
        return max(lo, min(hi, int(request.args.get(name, default))))
    except (TypeError, ValueError):
        return default


@traces_bp.route('/api/traces/live')
def traces_live():
    if request.args.get('snapshot'):
        return jsonify(rt.snapshot())
    since = _int_arg('since', 0, 0, 2 ** 62)
    out = rt.feed(since)
    out["labels"] = rt.LABELS
    return jsonify(out)


@traces_bp.route('/api/traces')
def traces_search():
    sub = request.args.get('subagents')
    subagents = True if sub in ('1', 'true') else False if sub in ('0', 'false') else None
    return jsonify(rt.search(
        q=(request.args.get('q') or '').strip() or None,
        model=(request.args.get('model') or '').strip() or None,
        kind=(request.args.get('kind') or '').strip() or None,
        task_id=(request.args.get('task') or '').strip() or None,
        since=_float_arg('since'), until=_float_arg('until'), subagents=subagents,
        limit=_int_arg('limit', 100, 1, 500), offset=_int_arg('offset', 0, 0, 10 ** 7),
    ))


@traces_bp.route('/api/traces/verify')
def traces_verify():
    return jsonify(rt.verify())


@traces_bp.route('/api/traces/status')
def traces_status():
    return jsonify(rt.status())


@traces_bp.route('/api/traces/export')
def traces_export():
    def _lines():
        for rec in rt.export_records():
            yield json.dumps(rec, ensure_ascii=False, default=str) + "\n"
    name = "friday-reasoning-traces-%s.ndjson" % time.strftime("%Y-%m-%d")
    return Response(_lines(), mimetype="application/x-ndjson",
                    headers={"Content-Disposition": 'attachment; filename="%s"' % name,
                             "Cache-Control": "no-store"})


@traces_bp.route('/api/traces/retention', methods=['POST'])
def traces_retention():
    body = request.get_json(silent=True) or {}
    try:
        days = int(body.get('retention_days', 0))
    except (TypeError, ValueError):
        return jsonify({"error": "retention_days must be a whole number of days (0 keeps everything)"}), 400
    if days < 0:
        return jsonify({"error": "retention_days cannot be negative"}), 400
    from agent_friday.core import _load_settings, _save_settings
    block = dict((_load_settings() or {}).get("reasoning_traces") or {})
    block["retention_days"] = days
    _save_settings({"reasoning_traces": block})
    result = rt.apply_retention(days) if body.get('apply') and days > 0 else None
    return jsonify({"status": rt.status(), "result": result})


@traces_bp.route('/api/traces/<trace_id>')
def traces_get(trace_id):
    out = rt.get_tree(trace_id)
    if out is None:
        return jsonify({"error": "no trace with that id"}), 404
    return jsonify(out)

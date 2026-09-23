"""Routes for the Studio 3D file browser (services/studio_files.py).

Reads are GETs. The three POSTs that act outside the page (open, reveal,
request-change) require a JSON body and refuse a cross-origin Origin header,
so another web page open in the same browser cannot trigger them against the
loopback-trusted server.
"""
from urllib.parse import urlparse

from flask import Blueprint, Response, jsonify, request, send_file

from agent_friday.core import login_required
from agent_friday.services import studio_files as sf

studio_files_bp = Blueprint('studio_files', __name__)


def _denied(e):
    return jsonify({'status': 'denied', 'error': str(e)}), 403


def _same_origin_json():
    if not request.is_json:
        return jsonify({'status': 'error', 'error': 'JSON body required'}), 415
    origin = request.headers.get('Origin')
    if origin and urlparse(origin).netloc != request.host:
        return jsonify({'status': 'denied', 'error': 'cross-origin request refused'}), 403
    return None


@studio_files_bp.route('/api/studio-files/roots')
@login_required
def sf_roots():
    return jsonify({'status': 'ok', 'roots': sf.list_roots()})


@studio_files_bp.route('/api/studio-files/scan')
@login_required
def sf_scan():
    a = request.args
    try:
        out = sf.scan(a.get('root', ''), a.get('path', ''),
                      limit=a.get('limit', type=int) or sf.DEFAULT_LIMIT,
                      max_depth=a.get('depth', type=int) or 6)
    except sf.Denied as e:
        return _denied(e)
    return jsonify({'status': 'ok', **out})


@studio_files_bp.route('/api/studio-files/thumb')
@login_required
def sf_thumb():
    a = request.args
    try:
        got = sf.thumbnail(a.get('root', ''), a.get('path', ''), a.get('s', type=int) or 128)
    except sf.Denied as e:
        return _denied(e)
    if not got:
        return Response(status=204)
    data, etag = got
    if request.if_none_match and etag in request.if_none_match:
        return Response(status=304)
    resp = Response(data, mimetype='image/webp')
    resp.set_etag(etag)
    resp.headers['Cache-Control'] = 'private, max-age=86400'
    return resp


@studio_files_bp.route('/api/studio-files/raw')
@login_required
def sf_raw():
    a = request.args
    try:
        if a.get('text'):
            return jsonify({'status': 'ok', **sf.text_preview(a.get('root', ''), a.get('path', ''))})
        p = sf.resolve(a.get('root', ''), a.get('path', ''))
    except sf.Denied as e:
        return _denied(e)
    if not p.is_file():
        return _denied('not a file')
    mimetype, inline = sf.serve_type(p)
    resp = send_file(str(p), mimetype=mimetype, conditional=True,
                     as_attachment=not inline and mimetype == 'application/octet-stream',
                     download_name=p.name)
    resp.headers['X-Content-Type-Options'] = 'nosniff'
    if not mimetype.startswith('application/pdf'):
        resp.headers['Content-Security-Policy'] = 'sandbox; default-src \'none\'; img-src \'self\'; media-src \'self\'; style-src \'unsafe-inline\''
    resp.headers['Cache-Control'] = 'private, no-cache'
    return resp


@studio_files_bp.route('/api/studio-files/open', methods=['POST'])
@login_required
def sf_open():
    bad = _same_origin_json()
    if bad:
        return bad
    b = request.get_json(silent=True) or {}
    try:
        return jsonify({'status': 'ok', **sf.open_default(b.get('root', ''), b.get('path', ''))})
    except sf.Denied as e:
        return _denied(e)
    except OSError as e:
        return jsonify({'status': 'error', 'error': str(e)[:200]}), 500


@studio_files_bp.route('/api/studio-files/reveal', methods=['POST'])
@login_required
def sf_reveal():
    bad = _same_origin_json()
    if bad:
        return bad
    b = request.get_json(silent=True) or {}
    try:
        return jsonify({'status': 'ok', **sf.reveal(b.get('root', ''), b.get('path', ''))})
    except sf.Denied as e:
        return _denied(e)
    except OSError as e:
        return jsonify({'status': 'error', 'error': str(e)[:200]}), 500


@studio_files_bp.route('/api/studio-files/request-change', methods=['POST'])
@login_required
def sf_request_change():
    bad = _same_origin_json()
    if bad:
        return bad
    b = request.get_json(silent=True) or {}
    try:
        out = sf.request_change(b.get('op', ''), b.get('root', ''), b.get('path', ''),
                                new_name=b.get('new_name', ''),
                                dest_root=b.get('dest_root', ''),
                                dest_rel=b.get('dest_path', ''))
    except sf.Denied as e:
        return _denied(e)
    appr = out.get('approval') or {}
    return jsonify({'status': out.get('status'), 'approval_id': appr.get('approval_id'),
                    'title': appr.get('title')})

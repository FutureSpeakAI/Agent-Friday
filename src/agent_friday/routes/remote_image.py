"""POST /api/remote-image: load one remote picture the owner clicked.

The page never fetches a remote image itself (its CSP forbids it). This route
fetches it behind the SSRF guard and returns the raster bytes. It requires a
JSON body and refuses a cross-origin Origin header, so another web page open in
the same browser cannot use it as a proxy.
"""
from urllib.parse import urlparse

from flask import Blueprint, Response, jsonify, request

from agent_friday.core import login_required
from agent_friday.routes._errors import error_text
from agent_friday.services import remote_image as ri

remote_image_bp = Blueprint('remote_image', __name__)


@remote_image_bp.route('/api/remote-image', methods=['POST'])
@login_required
def remote_image_load():
    if not request.is_json:
        return jsonify({'status': 'error', 'error': 'JSON body required'}), 415
    origin = request.headers.get('Origin')
    if origin and urlparse(origin).netloc != request.host:
        return jsonify({'status': 'denied', 'error': 'cross-origin request refused'}), 403
    url = (request.get_json(silent=True) or {}).get('url', '')
    try:
        body, ctype = ri.fetch_image(url)
    except ri.RemoteImageRefused as e:
        return jsonify({'status': 'refused', 'error': error_text(e, "That picture couldn't be fetched")}), 422
    resp = Response(body, mimetype=ctype)
    resp.headers['Cache-Control'] = 'private, no-store'
    resp.headers['X-Content-Type-Options'] = 'nosniff'
    resp.headers['Content-Security-Policy'] = "sandbox; default-src 'none'"
    return resp

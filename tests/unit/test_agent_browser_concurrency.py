"""Two real headless browsers working on synthetic pages at the same time.

The tests use only loopback synthetic pages. Profiles and all page content
belong to the temporary test home; no installed browser profile is opened.
"""
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from http.cookies import SimpleCookie
import html
import os
from pathlib import Path
import threading
from urllib.parse import parse_qs, urlsplit

import pytest

pytest.importorskip("playwright.sync_api")

from agent_friday.services import browser_session as bs  # noqa: E402


class FixturePage(BaseHTTPRequestHandler):
    def log_message(self, *_args):
        pass

    def do_GET(self):
        query = parse_qs(urlsplit(self.path).query)
        worker = query.get("worker", ["none"])[0]
        if worker not in {"alpha", "beta", "none"}:
            self.send_error(400)
            return
        cookie = SimpleCookie(self.headers.get("Cookie", ""))
        prior = cookie["worker"].value if "worker" in cookie else "empty"
        body = ("<!doctype html><html><head><title>Agent " + worker + "</title>"
                "<style>body{padding:50px 32px;min-height:2400px;font:18px system-ui}input{width:320px;"
                "padding:12px}button{padding:12px;margin:16px}</style></head><body>"
                "<h1>Workspace " + worker + "</h1><p id='cookie'>Previous cookie: "
                + html.escape(prior) + "</p><label>Draft <input name='draft'></label>"
                "<button type='button' id='preview'>Preview draft</button>"
                "<p id='result'>No preview yet</p><script>"
                "document.getElementById('preview').onclick=()=>{"
                "document.getElementById('result').textContent="
                "document.querySelector('[name=draft]').value;};"
                "</script></body></html>").encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        if worker != "none":
            self.send_header("Set-Cookie", f"worker={worker}; Path=/; SameSite=Strict")
        self.end_headers()
        self.wfile.write(body)


@pytest.fixture
def page_server():
    server = ThreadingHTTPServer(("127.0.0.1", 0), FixturePage)
    server.daemon_threads = True
    serving = threading.Thread(target=server.serve_forever, daemon=True)
    serving.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        server.server_close()
        serving.join(timeout=2)
        assert not serving.is_alive()


def permit(_owner, *, purpose="act"):
    return True


@pytest.fixture
def isolated_browsers(tmp_path, monkeypatch):
    test_home = tmp_path.resolve()
    monkeypatch.setenv("FRIDAY_HOME", str(test_home))
    # Creation and cleanup share the fixture's canonical root across workers.
    monkeypatch.setattr(bs, "friday_home", lambda: test_home)
    # Record the original containment operands without resolving a path twice.
    diagnostic = threading.local()
    dedicated, within = bs.assert_dedicated, bs._within

    def traced_within(candidate, root):
        result = within(candidate, root)
        if getattr(diagnostic, "active", False) and diagnostic.containment is None:
            diagnostic.containment = (str(candidate), str(root), result)
        return result

    def traced_dedicated(path):
        diagnostic.active = True
        diagnostic.containment = None
        try:
            return dedicated(path)
        except bs.BrowserRefused:
            try:
                print(f"Browser profile refusal: {str(path)!r}; "
                      f"original containment: {diagnostic.containment!r}")
            except (OSError, UnicodeError):
                pass
            raise
        finally:
            diagnostic.active = False

    monkeypatch.setattr(bs, "_within", traced_within)
    monkeypatch.setattr(bs, "assert_dedicated", traced_dedicated)
    # Release proofs can select an installed testing binary after home
    # isolation. Ordinary CI retains Playwright's bundled Chromium choice.
    executable = os.environ.get("FRIDAY_TEST_BROWSER_EXECUTABLE")
    if executable:
        from playwright.sync_api import BrowserType

        browser_path = Path(executable)
        assert browser_path.is_absolute() and browser_path.is_file()
        launch = BrowserType.launch_persistent_context

        def launch_test_browser(browser_type, user_data_dir, **options):
            assert browser_type.name == "chromium" and options.get("headless") is True
            assert Path(user_data_dir).resolve().is_relative_to(tmp_path.resolve())
            options["executable_path"] = str(browser_path.resolve())
            return launch(browser_type, user_data_dir, **options)

        monkeypatch.setattr(BrowserType, "launch_persistent_context", launch_test_browser)
    monkeypatch.setattr(bs, "_OWNED", {})
    monkeypatch.setattr(bs, "_OWNER_SURFACES", {})
    monkeypatch.setattr(bs, "_BLOCKED_OWNERS", set())
    monkeypatch.setattr(bs, "_STOP_PERMISSION_FLOOR", -1)
    monkeypatch.setattr(bs, "_STOP_EPOCH", 0)
    monkeypatch.setattr(bs, "_SESSION", None)
    real_session = bs.BrowserSession

    def local_session(**kwargs):
        return real_session(**kwargs, allow_local=True, poll=0.1)

    monkeypatch.setattr(bs, "BrowserSession", local_session)
    # Preserve real context/worker teardown while keeping cleanup within the
    # test's bounded lifetime instead of starting an unjoined cleanup thread.
    monkeypatch.setattr(bs, "_schedule_close", bs._close_owned)
    identities = [bs.BrowserOwner(actor_id=f"crew-{name}", conversation_id="fixture-room",
                                 task_id=f"task-{name}", project_id=f"project-{name}",
                                 conversation_project_id="room-project", profile_revision=1,
                                 room_revision=1, privacy_generation=1, permission_generation=2)
                  for name in ("alpha", "beta")]
    try:
        yield identities
    finally:
        for session in list(bs._OWNED.values()):
            if not session._cleanup_done:
                if session.mode not in ("closed", "revoked"):
                    bs._transition(session, "close")
                bs._close_owned(session)
            assert session._cleanup_done, "The test browser did not confirm cleanup"
            session._worker.thread.join(timeout=2)
            assert not session._worker.thread.is_alive()


def number(snapshot, label):
    for line in snapshot.splitlines():
        if line.startswith("[") and label in line:
            return int(line[1:line.index("]")])
    raise AssertionError(f"Missing synthetic control {label}")


def in_scope(identity, function, *args):
    with bs.bind_owner(identity, permit, label=identity.actor_id):
        return function(*args)


def value(session, selector):
    return session.call(lambda: session._page.locator(selector).input_value(), purpose="view")


def parallel_tools(first, second):
    start = threading.Barrier(2, timeout=10)

    def run(job):
        start.wait()
        return in_scope(*job)

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(run, job) for job in (first, second)]
        return [future.result(timeout=60) for future in futures]


def test_navigation_and_scroll_stay_in_their_own_sessions(page_server, isolated_browsers):
    alpha_owner, beta_owner = isolated_browsers
    alpha_page, beta_page = parallel_tools(
        (alpha_owner, bs.tool_open, page_server + "/work?worker=alpha"),
        (beta_owner, bs.tool_open, page_server + "/work?worker=beta"))
    assert "Workspace alpha" in alpha_page and "Workspace beta" in beta_page
    alpha = bs.resolve_owned_session(alpha_owner, permit)
    beta = bs.resolve_owned_session(beta_owner, permit)
    assert "Typed" in in_scope(alpha_owner, bs.tool_type, number(alpha_page, "Draft"), "Alpha stays here")
    assert "Typed" in in_scope(beta_owner, bs.tool_type, number(beta_page, "Draft"), "Beta before navigation")
    alpha_generation, beta_generation = alpha.page_generation, beta.page_generation

    _, beta_page = parallel_tools(
        (alpha_owner, bs.tool_scroll, "bottom"),
        (beta_owner, bs.tool_open, page_server + "/work?worker=beta&visit=2"))
    assert "Previous cookie: beta" in beta_page
    assert alpha.call(lambda: alpha._page.evaluate("window.scrollY")) > 0
    assert beta.call(lambda: beta._page.evaluate("window.scrollY")) == 0
    assert alpha.page_generation == alpha_generation
    assert beta.page_generation > beta_generation
    assert alpha.call(lambda: alpha._page.url) == page_server + "/work?worker=alpha"
    assert value(alpha, "[name=draft]") == "Alpha stays here"
    assert value(beta, "[name=draft]") == ""

    # Navigation replaces element references. Use the new snapshot for beta's
    # next edit, then navigate alpha while beta independently scrolls its page.
    assert "Typed" in in_scope(beta_owner, bs.tool_type, number(beta_page, "Draft"), "Beta stays here")
    beta_generation = beta.page_generation
    alpha_page, _ = parallel_tools(
        (alpha_owner, bs.tool_open, page_server + "/work?worker=alpha&visit=2"),
        (beta_owner, bs.tool_scroll, "down"))
    assert "Previous cookie: alpha" in alpha_page
    assert alpha.call(lambda: alpha._page.evaluate("window.scrollY")) == 0
    assert beta.call(lambda: beta._page.evaluate("window.scrollY")) > 0
    assert alpha.page_generation > alpha_generation
    assert beta.page_generation == beta_generation
    assert beta.call(lambda: beta._page.url) == page_server + "/work?worker=beta&visit=2"
    assert value(alpha, "[name=draft]") == ""
    assert value(beta, "[name=draft]") == "Beta stays here"


def test_two_agents_keep_cookies_inputs_and_control_separate(page_server, isolated_browsers):
    alpha_owner, beta_owner = isolated_browsers
    start = threading.Barrier(2, timeout=10)

    def open_page(identity, name):
        start.wait()
        return in_scope(identity, bs.tool_open, page_server + "/work?worker=" + name)

    with ThreadPoolExecutor(max_workers=2) as pool:
        alpha_open = pool.submit(open_page, alpha_owner, "alpha")
        beta_open = pool.submit(open_page, beta_owner, "beta")
        alpha_snapshot = alpha_open.result(timeout=60)
        beta_snapshot = beta_open.result(timeout=60)
    assert "Workspace alpha" in alpha_snapshot and "Previous cookie: empty" in alpha_snapshot
    assert "Workspace beta" in beta_snapshot and "Previous cookie: empty" in beta_snapshot
    alpha = bs.resolve_owned_session(alpha_owner, permit)
    beta = bs.resolve_owned_session(beta_owner, permit)
    assert alpha is not beta and alpha.profile != beta.profile
    assert alpha._worker.thread.ident != beta._worker.thread.ident
    assert alpha.headless and beta.headless and bs._SESSION is None
    for session, expected in ((alpha, "alpha"), (beta, "beta")):
        cookies = session.call(lambda: session._ctx.cookies())
        assert {c["value"] for c in cookies if c["name"] == "worker"} == {expected}

    alpha_draft = number(alpha_snapshot, "Draft")
    beta_draft = number(beta_snapshot, "Draft")
    # These element numbers and snapshots were obtained before either agent's
    # edits. Each must remain bound to its own page despite the other writes.
    with ThreadPoolExecutor(max_workers=2) as pool:
        writes = [pool.submit(in_scope, identity, bs.tool_type, element, text)
                  for identity, element, text in (
                      (alpha_owner, alpha_draft, "Alpha local draft"),
                      (beta_owner, beta_draft, "Beta local draft"))]
        assert all("Typed" in future.result(timeout=20) for future in writes)
    assert value(alpha, "[name=draft]") == "Alpha local draft"
    assert value(beta, "[name=draft]") == "Beta local draft"
    in_scope(alpha_owner, bs.tool_click, number(alpha_snapshot, "Preview draft"))
    assert alpha.call(lambda: alpha._page.locator("#result").inner_text()) == "Alpha local draft"
    assert beta.call(lambda: beta._page.locator("#result").inner_text()) == "No preview yet"

    bs.control_owned_session(alpha.surface_id, "pause", alpha.generation, permit)
    with pytest.raises(bs.BrowserRefused):
        in_scope(alpha_owner, bs.tool_type, alpha_draft, "Must not be typed")
    assert "Typed" in in_scope(beta_owner, bs.tool_type, beta_draft, "Beta keeps working")
    assert value(alpha, "[name=draft]") == "Alpha local draft"
    assert value(beta, "[name=draft]") == "Beta keeps working"

    bs.control_owned_session(alpha.surface_id, "takeover", alpha.generation, permit)
    bounds = alpha.call(lambda: alpha._page.locator("[name=draft]").bounding_box(), purpose="view")
    frame = bs.owned_frame(alpha.surface_id, alpha.generation, permit)
    assert frame["image"].startswith("data:image/jpeg;base64,") and frame["frame_sequence"] > 0
    event = {"type": "click", "x": bounds["x"] + 20, "y": bounds["y"] + 15,
             "page_generation": frame["page_generation"], "frame_sequence": frame["frame_sequence"]}
    bs.owned_human_input(alpha.surface_id, alpha.generation, event, permit)
    frame = bs.owned_frame(alpha.surface_id, alpha.generation, permit)
    bs.owned_human_input(alpha.surface_id, alpha.generation, {
        "type": "text", "text": " human edit", "page_generation": frame["page_generation"],
        "frame_sequence": frame["frame_sequence"]}, permit)
    assert "human edit" in value(alpha, "[name=draft]")
    assert value(beta, "[name=draft]") == "Beta keeps working"
    assert alpha.status()["cursor"]["action"] == "text"
    with pytest.raises(bs.BrowserRefused):
        in_scope(alpha_owner, bs.tool_read)

    bs.control_owned_session(alpha.surface_id, "resume", alpha.generation, permit)
    assert "human edit" in in_scope(alpha_owner, bs.tool_read)
    original_profile = alpha.profile
    bs.control_owned_session(alpha.surface_id, "close", alpha.generation, permit)
    assert alpha._cleanup_done and not original_profile.exists()
    reopened = in_scope(alpha_owner, bs.tool_open, page_server + "/work?worker=alpha")
    assert "refused" in reopened and "stopped" in reopened
    assert value(beta, "[name=draft]") == "Beta keeps working"
    assert beta.mode == "agent" and beta.running

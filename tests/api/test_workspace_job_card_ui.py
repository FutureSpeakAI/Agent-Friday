"""A workspace-launched job shows up on the desktop, in a real browser.

The server half is real: News > Briefings > "generate" runs through its route
with a local model double, and WHILE the model call is in flight the desktop's
own endpoints are read (``/api/tasks``, ``/api/tasks/<id>``, ``/api/processes``,
``/api/traces/live``, the task journal, ``/api/lanes/summary``,
``/api/lanes/activity``, ``/api/local-queue``). The browser half is real too: the served
``index.html`` loads on a scratch static server, those recorded responses are
what its API calls get, and the test looks at what a person would see:

  * the holographic desktop has an orb for it, under the task's own id;
  * the bell carries no count: running work lives in Activity, not the tray;
  * the Activity chip in the top bar opens a row for it that names the model
    and its status;
  * the row's Trace button opens a trace that has content.

The frame is saved for a person to look at (AGENTS.md: a passing DOM assertion
is not a look): ``FRIDAY_SHOT_DIR`` (default: the repo's ``test-results`` is
never used) receives ``briefing_task.png``.
"""
from __future__ import annotations

import functools
import http.server
import json
import os
import re
import socketserver
import threading
from pathlib import Path

import pytest

from agent_friday.services import agent as ag
from agent_friday.services import task_journal as tj

REPO = Path(__file__).resolve().parents[2]
LOCAL_MODEL = "bonsai2:27b"
SHOT_DIR = Path(os.environ.get("FRIDAY_SHOT_DIR") or (REPO.parent / "scratch" / "shots"))


class _LocalRouter:
    def route(self, messages, task_context=None):
        return {"provider": "local", "model": LOCAL_MODEL, "reason": "News runs on the local seat"}


@pytest.fixture
def desk(client, tmp_path, monkeypatch):
    """What the desktop's endpoints return while the briefing's model call runs."""
    monkeypatch.setattr(tj, "BASE_DIR_OVERRIDE", tmp_path / "tasks")
    tj.reset_for_tests()
    import agent_friday.core as core
    import agent_friday.notifications_engine as ne
    import agent_friday.routes.news as rn
    from agent_friday.services import activity_ledger as al
    from agent_friday.services import model_router as mr
    from agent_friday.services import news_engine as news

    monkeypatch.setattr(ne, "push", lambda **kw: kw)
    monkeypatch.setattr(al, "LEDGER_FILE", tmp_path / "activity.jsonl")
    monkeypatch.setattr(core, "FRIDAY_DIR", tmp_path)
    monkeypatch.setattr(rn, "FRIDAY_DIR", tmp_path)
    monkeypatch.setattr(news, "_queue_podcast", lambda *a, **k: None)
    monkeypatch.setattr(rn, "_gather_live_briefing_context", lambda: "")
    monkeypatch.setattr(rn, "_get_friday_system_prompt", lambda **k: "")
    monkeypatch.setattr("agent_friday.routing.model_router.get_router",
                        lambda cfg=None: _LocalRouter())
    with ag.TASKS_LOCK:
        saved = dict(ag.TASKS)
        ag.TASKS.clear()
    # Orbs other tests in this process left behind would be counted too.
    with core.PROCESSES_LOCK:
        saved_procs = dict(core.PROCESSES)
        core.PROCESSES.clear()

    seen: dict = {}

    def local_primitive(messages, system=None, model=None, max_tokens=None,
                        temperature=None, orb_label=None, **kw):
        core.process_register("local-test", name="Local Inference", label=orb_label,
                              category="monitoring", icon="x", steps=[], model=model)
        tasks = client.get("/api/tasks").get_json()
        card = [t for t in tasks["tasks"] if t.get("workspace") == "news"][0]
        tid = card["task_id"]
        seen["tasks"] = tasks
        seen["detail"] = client.get("/api/tasks/" + tid).get_json()
        seen["processes"] = client.get("/api/processes").get_json()
        seen["journal"] = client.get("/api/tasks/%s/journal?since=0" % tid).get_json()
        seen["live"] = client.get("/api/traces/live?snapshot=1").get_json()
        seen["tree"] = client.get("/api/traces/" + card["trace_id"]).get_json()
        seen["lanes_summary"] = client.get("/api/lanes/summary").get_json()
        seen["lanes_activity"] = client.get("/api/lanes/activity").get_json()
        seen["local_queue"] = client.get("/api/local-queue").get_json()
        seen["task_id"], seen["trace_id"] = tid, card["trace_id"]
        return "# Briefing\n## Calendar\nNothing today.\n", []

    monkeypatch.setattr(mr, "_call_ollama", local_primitive)
    r = client.post("/api/briefing/generate", json={})
    assert r.status_code == 200, r.get_json()
    yield seen
    with ag.TASKS_LOCK:
        ag.TASKS.clear()
        ag.TASKS.update(saved)
    with core.PROCESSES_LOCK:
        core.PROCESSES.clear()
        core.PROCESSES.update(saved_procs)


class _Handler(http.server.SimpleHTTPRequestHandler):
    def do_GET(self):                                          # noqa: N802
        if self.path.split("?")[0] == "/index.html":
            body = (REPO / "index.html").read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        super().do_GET()

    def log_message(self, *a):
        pass


def _json(route, payload, status=200):
    route.fulfill(status=status, content_type="application/json", body=json.dumps(payload))


def test_the_briefing_task_is_on_the_desktop_with_its_model_and_an_openable_trace(desk):
    sync_api = pytest.importorskip("playwright.sync_api", reason="playwright is not installed")
    tid, trace_id = desk["task_id"], desk["trace_id"]
    httpd = socketserver.ThreadingTCPServer(
        ("127.0.0.1", 0), functools.partial(_Handler, directory=str(REPO)))
    httpd.daemon_threads = True
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    url = "http://127.0.0.1:%d/index.html" % httpd.server_address[1]

    def api(route):
        path = re.sub(r"^https?://[^/]+", "", route.request.url)
        p = path.split("?")[0]
        if p == "/api/settings":
            return _json(route, {"status": "ok", "settings": {}})
        if p == "/api/tasks":
            return _json(route, desk["tasks"])
        if p == "/api/tasks/" + tid:
            return _json(route, desk["detail"])
        if p == "/api/tasks/%s/journal" % tid:
            return _json(route, desk["journal"])
        if p == "/api/processes":
            return _json(route, desk["processes"])
        if p == "/api/traces/live":
            return _json(route, desk["live"])
        if p == "/api/traces/" + trace_id:
            return _json(route, desk["tree"])
        if p == "/api/lanes/summary":
            return _json(route, desk["lanes_summary"])
        if p == "/api/lanes/activity":
            return _json(route, desk["lanes_activity"])
        if p == "/api/local-queue":
            return _json(route, desk["local_queue"])
        return route.continue_()

    try:
        with sync_api.sync_playwright() as pw:
            try:
                browser = pw.chromium.launch(args=["--use-gl=angle", "--use-angle=swiftshader",
                                                   "--enable-unsafe-swiftshader"])
            except Exception as exc:                           # pragma: no cover
                pytest.skip("no chromium for playwright: %s" % exc)
            pg = browser.new_page(viewport={"width": 1440, "height": 900})
            pg.set_default_timeout(45000)
            pg.add_init_script("localStorage.setItem('friday.display-style.v1', 'classic')")
            pg.route("**/api/**", api)
            pg.goto(url, wait_until="domcontentloaded")

            # 1. The holographic desktop has an orb for the job, under the task's id.
            pg.wait_for_function(
                "(id) => typeof window.fridayGetOrbs === 'function' && "
                "window.fridayGetOrbs().some(o => o.id === id)", arg=tid, timeout=45000)
            orb = pg.evaluate("(id) => { const o = window.fridayGetOrbs().find(o => o.id === id);"
                              " return { id: o.id, label: o.label, model: o.model }; }", tid)
            assert orb == {"id": tid, "label": "Daily briefing", "model": LOCAL_MODEL}, orb

            # 2. The bell carries no count; running work is in Activity.
            pg.locator("button[aria-label='Open notifications']").wait_for()
            assert pg.locator("button[aria-label*='running, open notifications']").count() == 0

            # 3. Its row in Activity says who is working and what it is doing.
            pg.locator("[data-testid=local-ai-queue-chip]").click()
            row = pg.locator("[data-testid=lane-task][data-task-id='%s']" % tid)
            row.wait_for()
            meta = row.locator("[data-testid=lane-task-meta]").inner_text()
            assert meta.startswith(LOCAL_MODEL + " · running"), meta

            # 4. The reasoning behind it opens, and has content.
            row.locator("[data-testid=lane-trace]").click()
            pg.wait_for_function(
                "() => /Asking bonsai2:27b/.test(document.body.innerText)", timeout=45000)

            # 5. A person can look at it.
            pg.wait_for_timeout(600)
            SHOT_DIR.mkdir(parents=True, exist_ok=True)
            pg.screenshot(path=str(SHOT_DIR / "briefing_task.png"))
            browser.close()
    finally:
        httpd.shutdown()
        httpd.server_close()
    assert (SHOT_DIR / "briefing_task.png").stat().st_size > 20000

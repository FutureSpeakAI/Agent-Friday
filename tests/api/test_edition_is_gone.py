"""The Friday Edition is removed, and nothing it stood in front of went with it.

Stephen, 2026-09-24: "Edition sucks, and it's the first thing that appears when I
load the Friday desktop. Let's eliminate it entirely. We'll lean on the briefings
instead."

The removal has to be precise, because **two different things were called
"edition"** and only one of them is going:

  * THE FRIDAY EDITION (E0) -- `services/edition_engine.py`,
    `routes/edition.py`, the `edition` dock icon, `EditionWS` in the UI. This is
    what he means. It was first in the dock and auto-opened on load.
  * THE NEWS FRONT PAGE'S MORNING/EVENING EDITIONS -- `news_engine`'s own
    vocabulary ("the two daily editions", "Friday's Front Page - Morning
    edition"), served from `/api/news/front-page/*` whose JSON field is literally
    named `edition`. This STAYS.

A grep-and-delete on the word would have gutted the News front page, so the
surviving half is asserted here as loudly as the removed half.

Edition was a pure composer over artifacts already on disk -- news archive,
front_pages/, editorials/, creations/, dreams -- and made no model calls at all.
The cost ledger agrees: zero rows in `costs.db` match `edition` on any of
workspace, kind, schedule_id, run_id or model. So removing it frees no spend;
it removes a surface, not a bill.

His existing editions are NOT deleted. They stay at `~/.friday/edition/`
(charter.md, verbs.jsonl, editions/ -- 40 files, ~500 KB). Nothing reads them any
more.
"""

import importlib
import pathlib
import re

import pytest


REPO = pathlib.Path(__file__).resolve().parents[2]
INDEX = REPO / "index.html"
APP = REPO / "ui_parts" / "app.html"

EDITION_API_PATHS = [
    "/api/edition/latest",
    "/api/edition/2026-09-24-morning",
    "/api/edition/charter",
]


# ─────────────────────────────────────────────────────────────────────────────
# The endpoints and modules are gone.
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("path", EDITION_API_PATHS)
def test_the_edition_api_is_gone(client, path):
    assert client.get(path).status_code == 404, (
        "%s still answers; the edition blueprint is still registered" % path)


def test_edition_compose_is_gone(client):
    assert client.post("/api/edition/compose", json={}).status_code == 404


@pytest.mark.parametrize("mod", [
    "agent_friday.services.edition_engine",
    "agent_friday.routes.edition",
])
def test_the_edition_modules_are_gone(mod):
    with pytest.raises(ModuleNotFoundError):
        importlib.import_module(mod)


def test_the_blueprint_list_no_longer_names_edition():
    src = (REPO / "src" / "agent_friday" / "server.py").read_text(
        encoding="utf-8", errors="replace")
    assert "'edition'" not in src, (
        "server.py still tries to register the edition blueprint, which will "
        "fail at import now that the module is gone")


def test_the_scheduler_registers_no_edition_job():
    src = (REPO / "src" / "agent_friday" / "services" / "scheduler.py").read_text(
        encoding="utf-8", errors="replace")
    assert "edition_daily" not in src
    assert "run_edition_job" not in src


# ─────────────────────────────────────────────────────────────────────────────
# No dead links.
# ─────────────────────────────────────────────────────────────────────────────

def test_the_edition_workspace_tab_redirects_home(client):
    """`/w/edition` was a real URL and may be bookmarked or linked. It must go
    somewhere useful rather than 404 or render an empty workspace."""
    r = client.get("/w/edition")
    assert r.status_code in (301, 302, 303, 307, 308), (
        "/w/edition returned HTTP %s; a retired workspace URL should redirect"
        % r.status_code)
    target = r.headers.get("Location", "")
    assert "edition" not in target, "redirected back to itself: %r" % target
    assert "home" in target or target.rstrip("/").endswith(("", "/")), (
        "/w/edition redirects to %r, which is not Home" % target)


# ─────────────────────────────────────────────────────────────────────────────
# The UI.
# ─────────────────────────────────────────────────────────────────────────────

def _dock_ids(path, pattern):
    text = path.read_text(encoding="utf-8", errors="replace")
    start = text.find("const DOCK_GROUPS")
    assert start != -1, "DOCK_GROUPS not found in %s" % path.name
    end = text.find("\nconst ", start + 1)
    block = text[start:end if end != -1 else start + 20000]
    ids = re.findall(pattern, block)
    assert len(ids) > 10, "parsed only %d dock ids from %s" % (len(ids), path.name)
    return ids


@pytest.mark.parametrize("path,pattern", [
    (INDEX, r"\bid:\s*'([a-z0-9_-]+)'"),
    (APP, r"\bid:'([a-z0-9_-]+)'"),
])
def test_the_dock_has_no_edition_icon(path, pattern):
    ids = _dock_ids(path, pattern)
    assert "edition" not in ids, "%s still ships an edition dock icon" % path.name


@pytest.mark.parametrize("path,pattern", [
    (INDEX, r"\bid:\s*'([a-z0-9_-]+)'"),
    (APP, r"\bid:'([a-z0-9_-]+)'"),
])
def test_home_is_now_first_in_the_dock(path, pattern):
    """Edition held the first slot. Removing it must leave Home there rather
    than whatever happened to be third."""
    ids = _dock_ids(path, pattern)
    assert ids[0] == "home", (
        "%s dock now starts with %r, not Home" % (path.name, ids[0]))


def test_the_desktop_opens_home_on_load():
    src = INDEX.read_text(encoding="utf-8", errors="replace")
    assert "openWs('edition')" not in src, (
        "the desktop still auto-opens the Edition workspace on load")
    assert "openWs('home')" in src, (
        "the desktop no longer opens anything on load; Stephen asked for Home")


@pytest.mark.parametrize("path", [INDEX, APP])
@pytest.mark.parametrize("sym", ["EditionWS", "EditionCard", "EditionSection"])
def test_the_edition_components_are_gone(path, sym):
    src = path.read_text(encoding="utf-8", errors="replace")
    assert sym not in src, "%s still defines/uses %s" % (path.name, sym)


@pytest.mark.parametrize("path", [INDEX, APP])
def test_no_ui_code_calls_the_edition_api(path):
    src = path.read_text(encoding="utf-8", errors="replace")
    assert "/api/edition/" not in src, (
        "%s still fetches a removed endpoint" % path.name)


# ─────────────────────────────────────────────────────────────────────────────
# What must SURVIVE. This is the half a careless removal breaks.
# ─────────────────────────────────────────────────────────────────────────────

def test_the_news_front_page_still_works(client):
    """Its JSON field is named `edition`, which is exactly why this is here."""
    r = client.get("/api/news/front-page/latest")
    assert r.status_code == 200
    body = r.get_json()
    assert body.get("status") == "ok"
    assert "edition" in body, (
        "the News front page lost its `edition` field -- the removal reached "
        "into news_engine, which it must not")


def test_the_front_pages_list_still_works(client):
    r = client.get("/api/news/front-pages")
    assert r.status_code == 200
    assert isinstance(r.get_json().get("editions"), list)


def test_the_briefings_still_work(client):
    """The surface Stephen said he wants to lean on instead."""
    assert client.get("/api/briefings").status_code == 200
    assert client.get("/api/briefing/status").status_code == 200


def test_news_engine_still_composes_front_pages():
    """The functions Edition used to read from are news_engine's own and stay."""
    from agent_friday.services import news_engine as ne
    assert hasattr(ne, "_list_front_pages")
    assert hasattr(ne, "_read_front_page")


@pytest.mark.parametrize("ws", ["news", "home"])
def test_the_surviving_workspaces_still_serve_as_tabs(client, ws):
    assert client.get("/w/" + ws).status_code == 200


# ─────────────────────────────────────────────────────────────────────────────
# His data is left alone.
# ─────────────────────────────────────────────────────────────────────────────

def test_nothing_deletes_the_existing_edition_data():
    """Stephen asked for the surface gone, not his archive. No shipped code may
    remove `~/.friday/edition`."""
    suspects = []
    for p in (REPO / "src").rglob("*.py"):
        text = p.read_text(encoding="utf-8", errors="replace")
        if '"edition"' not in text and "'edition'" not in text:
            continue
        for line in text.splitlines():
            if "edition" in line and re.search(
                    r"rmtree|unlink|os\.remove|shutil\.rm", line):
                suspects.append("%s: %s" % (p.name, line.strip()[:90]))
    assert not suspects, "code that deletes edition data: %s" % suspects

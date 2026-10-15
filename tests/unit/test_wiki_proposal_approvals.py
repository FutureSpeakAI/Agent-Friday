"""Wiki proposals: one decision, idempotent, readable.

An install report found four faults on one path:

  * Approving the desktop card for ``propose_wiki_update`` queued a SECOND
    pending proposal in Knowledge. The card is the owner's decision
    (``action_gate.owner_decision()``); the call it approved applies, and
    nothing is left waiting.
  * The tool dropped ``old_value``, so every edit APPENDED: a page ended up with
    the same section many times over. ``old_value`` is carried to the apply and
    replaced exactly; an edit whose text is gone is ``stale`` and writes nothing.
  * ``/api/wiki/pending/<id>/approve`` re-applied an applied proposal. A second
    approve now answers ``applied`` with ``already`` and writes nothing.
  * The Knowledge buttons toasted "Approved" whatever the server said. They read
    the JSON answer now.
"""
from __future__ import annotations

import pathlib
import re

import pytest

from agent_friday.governance import action_gate as ag
from agent_friday.services import wiki_engine as we

ROOT = pathlib.Path(__file__).resolve().parents[2]
HTML_FILES = ("index.html", "ui_parts/app.html")

PAGE = "notes/sample-page.md"
OLD = "- Favourite colour: blue"
NEW = "- Favourite colour: green"
BODY = "# Sample Page\n\n## Preferences\n" + OLD + "\n- Pets: one cat\n"


@pytest.fixture(autouse=True)
def wiki(tmp_path, monkeypatch):
    root = tmp_path / "wiki"
    (root / "notes").mkdir(parents=True)
    monkeypatch.setattr(we, "WIKI_DIR", root)
    monkeypatch.setattr(we, "WIKI_PENDING_FILE", tmp_path / "wiki-pending.json")
    monkeypatch.setattr(we, "_wiki_mirror_dir", lambda: None)
    monkeypatch.setattr(we, "_wiki_encrypted_sections", lambda: set())
    (root / PAGE).write_text(BODY, encoding="utf-8")
    yield root


@pytest.fixture
def writes(monkeypatch):
    """Every page write the apply makes."""
    seen = []
    real = we._mirror_wiki_file

    def _rec(rel, content):
        seen.append(rel)
        return real(rel, content)

    monkeypatch.setattr(we, "_mirror_wiki_file", _rec)
    return seen


@pytest.fixture
def client():
    from flask import Flask
    from agent_friday.routes import wiki as rw
    app = Flask(__name__)
    app.secret_key = "test"
    app.register_blueprint(rw.wiki_bp)
    with app.test_client() as c:
        yield c


def _tool(inp):
    from agent_friday.services import agent
    return agent._tool_propose_wiki_update(inp)


def _decided(card_id, inp):
    tok = ag.DECIDED.set(card_id)
    try:
        return _tool(inp)
    finally:
        ag.DECIDED.reset(tok)


def _pending():
    return [p for p in we._load_pending_wiki() if p.get("status") == "pending"]


def _text(wiki):
    return (wiki / PAGE).read_text(encoding="utf-8")


def _edit():
    return {"file": PAGE, "section": "Preferences", "old_value": OLD, "new_value": NEW,
            "reason": "the owner corrected it"}


# ── the approved card is the decision ───────────────────────────────────────

def test_an_approved_card_applies_once_and_queues_nothing(wiki, writes):
    out = _decided("card-0001", _edit())
    assert writes == [PAGE], out                       # one write, now
    assert NEW in _text(wiki) and OLD not in _text(wiki)
    assert _pending() == []                            # nothing waiting in Knowledge
    assert "applied" in out.lower()


def test_without_a_decision_it_waits_for_one_approval(wiki, writes):
    out = _tool(_edit())
    assert "proposed" in out and writes == []
    assert len(_pending()) == 1
    assert _tool(_edit()) and len(_pending()) == 1      # the same change is not queued twice


# ── old_value is carried and replaced exactly ───────────────────────────────

def test_old_value_reaches_the_apply_and_is_replaced_exactly(wiki, client):
    _tool(_edit())
    [item] = _pending()
    assert item["old_value"] == OLD
    r = client.post(f"/api/wiki/pending/{item['id']}/approve")
    assert r.status_code == 200 and r.get_json()["status"] == "applied"
    assert _text(wiki) == BODY.replace(OLD, NEW)        # replaced in place, nothing appended


def test_two_identical_edits_leave_one_copy(wiki, client):
    for _ in range(2):
        _tool(_edit())
        for item in _pending():
            client.post(f"/api/wiki/pending/{item['id']}/approve")
    text = _text(wiki)
    assert text.count(NEW) == 1 and text.count("## Preferences") == 1, text


def test_an_append_already_there_is_not_appended_again(wiki, writes):
    add = {"file": PAGE, "section": "Preferences", "new_value": "- Tea: green", "reason": "r"}
    _decided("card-0002", add)
    _decided("card-0003", add)
    text = _text(wiki)
    assert text.count("- Tea: green") == 1 and text.count("## Preferences") == 1, text
    assert writes == [PAGE]


def test_a_card_never_overwrites_an_existing_page_unseen(wiki, client, writes):
    """A card shows the call, not what the page loses: a decided replace_file on
    an existing page waits in Knowledge with the overwrite preview, and only
    the Knowledge approve writes it."""
    short = "# Sample Page\n\nShort.\n"
    out = _decided("card-0004", {"file": PAGE, "mode": "replace_file",
                                 "new_value": short, "reason": "r"})
    assert _text(wiki) == BODY and writes == [], out          # not applied on the card
    [item] = _pending()
    assert item["mode"] == "replace_file"
    row = client.get("/api/wiki/pending").get_json()["pending"][0]
    assert row["preview"]["badge"] == "overwrite"
    assert "whole page" in row["preview"]["headline"].lower()
    assert row["preview"]["lines_removed"] >= 3
    r = client.post(f"/api/wiki/pending/{item['id']}/approve",
                    json={"before_sha256": row["preview"]["before_sha256"]})
    assert r.status_code == 200 and r.get_json()["status"] == "applied"
    assert _text(wiki) == short


def test_a_card_may_create_a_new_page_with_replace_file(wiki, writes):
    out = _decided("card-0006", {"file": "notes/brand-new.md", "mode": "replace_file",
                                 "new_value": "# Brand New\n\nHello.\n", "reason": "r"})
    assert "applied" in out.lower(), out
    assert (wiki / "notes" / "brand-new.md").read_text(encoding="utf-8") == "# Brand New\n\nHello.\n"
    assert _pending() == []


# ── a stale edit writes nothing ─────────────────────────────────────────────

def test_a_stale_old_value_is_refused_and_the_page_is_unchanged(wiki, client, writes):
    _tool(_edit())
    [item] = _pending()
    changed = BODY.replace(OLD, "- Favourite colour: red")
    (wiki / PAGE).write_text(changed, encoding="utf-8")
    r = client.post(f"/api/wiki/pending/{item['id']}/approve")
    assert r.status_code == 409
    assert r.get_json()["status"] == "stale"
    assert _text(wiki) == changed and writes == []


def test_a_stale_decided_edit_is_reported_not_appended(wiki, writes):
    (wiki / PAGE).write_text(BODY.replace(OLD, "- Favourite colour: red"), encoding="utf-8")
    out = _decided("card-0005", _edit())
    assert "stale" in out and writes == []


# ── approve is idempotent ───────────────────────────────────────────────────

def test_approving_twice_writes_once(wiki, client, writes):
    _tool(_edit())
    [item] = _pending()
    first = client.post(f"/api/wiki/pending/{item['id']}/approve")
    second = client.post(f"/api/wiki/pending/{item['id']}/approve")
    assert first.status_code == 200 and first.get_json()["status"] == "applied"
    assert second.status_code == 200
    body = second.get_json()
    assert body["status"] == "applied" and body["already"] is True
    assert writes == [PAGE]


def test_a_rejected_proposal_does_not_approve_as_success(wiki, client, writes):
    _tool(_edit())
    [item] = _pending()
    client.post(f"/api/wiki/pending/{item['id']}/reject")
    r = client.post(f"/api/wiki/pending/{item['id']}/approve")
    assert r.status_code == 409 and r.get_json()["status"] == "rejected"
    assert writes == []


def test_the_preview_says_what_will_change(wiki, client):
    _tool(_edit())
    [item] = _pending()
    p = client.get(f"/api/wiki/pending/{item['id']}/preview").get_json()
    assert p["badge"] == "edit" and p["state"] == "pending"
    assert p["lines_added"] == 1 and p["lines_removed"] == 1
    assert "+" + NEW in p["diff"] and "-" + OLD in p["diff"]


# ── the Knowledge buttons read the answer ───────────────────────────────────

def _handler(html, name, nxt):
    m = re.search(r"const %s = async id => \{(.*?)const %s" % (name, nxt), html, re.S)
    assert m, f"{name} handler not found"
    return m.group(1)


@pytest.mark.parametrize("rel", HTML_FILES)
def test_the_approve_button_toasts_the_servers_status(rel):
    html = (ROOT / rel).read_text(encoding="utf-8")
    body = _handler(html, "approvePending", "rejectPending")
    assert ".json()" in body and "r.ok" in body, rel
    assert "'stale'" in body and "already" in body, rel
    assert "showToast('Approved')" not in body, rel      # never success regardless of the answer


@pytest.mark.parametrize("rel", HTML_FILES)
def test_the_pending_row_shows_an_overwrite_as_an_overwrite(rel):
    """The row renders the server's preview (badge, headline, diff); without
    one, a replace_file still reads "Replaces the whole page", never append."""
    html = (ROOT / rel).read_text(encoding="utf-8")
    start = html.index("const renderDiff = p =>")
    body = html[start:html.index("const onReaderClick", start)]
    assert "p.preview" in body and "pv.diff" in body and "pv.badge" in body, rel
    assert "'replace_file'" in body and "Replaces the whole page" in body, rel
    assert "p.old_value || h('em', null, '(empty — will append)')" not in body, rel


# ── the page written is the page named ──────────────────────────────────────

def test_a_name_without_extension_writes_the_md_page(wiki, client):
    r = client.post("/api/wiki/update", json={"file": "notes/fresh-page",
                                              "new_value": "- First line", "reason": "r"})
    assert r.status_code == 200, r.get_json()
    assert (wiki / "notes" / "fresh-page.md").is_file()
    assert not (wiki / "notes" / "fresh-page").exists()       # no bare file beside it
    assert r.get_json()["file"] == "notes/fresh-page.md"


def test_a_decided_edit_named_without_extension_reports_applied(wiki, writes):
    out = _decided("card-0007", dict(_edit(), file="notes/sample-page"))
    assert writes == [PAGE] and "applied" in out.lower(), out
    assert _text(wiki) == BODY.replace(OLD, NEW)


@pytest.mark.parametrize("bad", [".git/config", "notes/.obsidian/workspace.md", ".hidden/x"])
def test_hidden_segments_are_refused(wiki, client, bad):
    assert we._safe_wiki_path(bad) is None
    r = client.post("/api/wiki/update", json={"file": bad, "new_value": "x", "reason": "r"})
    assert r.status_code == 400
    assert "invalid wiki path" in _decided("card-0008", {"file": bad, "new_value": "x"})
    assert not any(p.name.startswith(".") for p in wiki.rglob("*")), list(wiki.rglob("*"))


# ── stale is stale, not "already applied" ───────────────────────────────────

def test_an_edit_whose_words_merely_occur_is_stale(wiki, client, writes):
    _tool(_edit())
    [item] = _pending()
    # OLD is gone and NEW occurs only as part of a longer line: not applied.
    changed = BODY.replace(OLD, NEW + "ish, with blue trim")
    (wiki / PAGE).write_text(changed, encoding="utf-8")
    r = client.post(f"/api/wiki/pending/{item['id']}/approve")
    assert r.status_code == 409 and r.get_json()["status"] == "stale", r.get_json()
    assert _text(wiki) == changed and writes == []


def test_an_edit_whose_whole_block_is_there_is_already_applied(wiki, client, writes):
    _tool(_edit())
    [item] = _pending()
    (wiki / PAGE).write_text(BODY.replace(OLD, NEW), encoding="utf-8")
    r = client.post(f"/api/wiki/pending/{item['id']}/approve")
    body = r.get_json()
    assert r.status_code == 200 and body["status"] == "applied" and body["already"] is True
    assert writes == []


# ── an old_value that occurs twice is ambiguous ─────────────────────────────

TWICE = BODY + "\n## Archive\n" + OLD + "\n"


def test_an_old_value_that_occurs_twice_is_refused_as_ambiguous(wiki, client, writes):
    (wiki / PAGE).write_text(TWICE, encoding="utf-8")
    out = _tool(_edit())
    assert "ambiguous" in out and _pending() == [], out      # not queued to fail later
    assert "ambiguous" in _decided("card-0009", _edit())
    pid = we._propose_wiki_update(PAGE, "Preferences", NEW, "r", OLD)
    r = client.post(f"/api/wiki/pending/{pid}/approve")
    assert r.status_code == 422 and r.get_json()["status"] == "ambiguous", r.get_json()
    assert "longer old_value" in r.get_json()["message"]
    assert _text(wiki) == TWICE and writes == []
    assert [p["id"] for p in _pending()] == [pid]              # still waiting, can be redone


# ── one lock around plan + write ────────────────────────────────────────────

class _CountLock:
    """A stand-in for the wiki write lock that records how deep it is held."""

    def __init__(self):
        self.depth = 0

    @property
    def held(self):
        return self.depth > 0

    def __enter__(self):
        self.depth += 1
        return self

    def __exit__(self, *exc):
        self.depth -= 1
        return False


def _count_lock(monkeypatch):
    from agent_friday.routes import wiki as rw
    lock = _CountLock()
    monkeypatch.setattr(we, "_PENDING_LOCK", lock, raising=False)
    monkeypatch.setattr(rw, "_PENDING_LOCK", lock, raising=False)
    return lock


def test_every_write_is_under_the_pending_lock(wiki, client, monkeypatch):
    lock = _count_lock(monkeypatch)
    held = []
    real = we._mirror_wiki_file

    def _rec(rel, content):
        held.append(lock.held)
        return real(rel, content)

    monkeypatch.setattr(we, "_mirror_wiki_file", _rec)
    _decided("card-0010", _edit())                                         # owner-decided tool
    client.post("/api/wiki/update", json={"file": PAGE, "old_value": NEW,  # direct route
                                          "new_value": OLD, "reason": "r"})
    _tool(_edit())                                                         # Knowledge approve
    [item] = _pending()
    client.post(f"/api/wiki/pending/{item['id']}/approve")
    assert held == [True, True, True], held
    assert lock.depth == 0


# ── the page approved is the page the owner saw (before_sha256) ─────────────

def _sha(text):
    import hashlib
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _row(client, pid):
    rows = client.get("/api/wiki/pending").get_json()["pending"]
    return next(r for r in rows if r["id"] == pid)


def _queue(mode):
    """Queue one proposal of ``mode`` on the existing PAGE; returns its id."""
    if mode == "edit":
        inp = _edit()
    elif mode == "append":
        inp = {"file": PAGE, "section": "Preferences", "new_value": "- Tea: green",
               "mode": "append", "reason": "r"}
    else:
        inp = {"file": PAGE, "mode": "replace_file", "new_value": "# Sample Page\n\nShort.\n",
               "reason": "r"}
    out = _tool(inp)
    assert "proposed" in out, out
    [item] = _pending()
    return item["id"]


def test_the_preview_carries_the_hash_of_the_page_it_shows(wiki, client):
    pid = _queue("replace_file")
    assert _row(client, pid)["preview"]["before_sha256"] == _sha(BODY)
    p = client.get(f"/api/wiki/pending/{pid}/preview").get_json()
    assert p["before_sha256"] == _sha(BODY)


@pytest.mark.parametrize("mode", ["edit", "append", "replace_file"])
def test_a_page_changed_after_the_preview_is_stale_in_every_mode(wiki, client, writes, mode):
    """The owner saw the preview; the page then changed (here: a line added
    elsewhere, which leaves every one of these changes still applicable).
    Approving from the old preview writes nothing and answers 409 stale."""
    pid = _queue(mode)
    seen = _row(client, pid)["preview"]["before_sha256"]
    changed = BODY + "- Added later: yes\n"
    (wiki / PAGE).write_text(changed, encoding="utf-8")
    r = client.post(f"/api/wiki/pending/{pid}/approve", json={"before_sha256": seen})
    body = r.get_json()
    assert r.status_code == 409 and body["status"] == "stale", body
    assert "preview" in body["message"].lower()
    assert _text(wiki) == changed and writes == []
    assert [p["id"] for p in _pending()] == [pid]        # still waiting, shown afresh
    fresh = _row(client, pid)["preview"]["before_sha256"]
    assert fresh == _sha(changed)
    r = client.post(f"/api/wiki/pending/{pid}/approve", json={"before_sha256": fresh})
    assert r.status_code == 200 and r.get_json()["status"] == "applied", r.get_json()


@pytest.mark.parametrize("mode", ["append", "replace_file"])
def test_an_overwrite_or_append_without_the_hash_is_refused(wiki, client, writes, mode):
    """Old clients and bare API calls send no hash: an overwrite or append of
    a page that exists cannot show the owner saw it, so it is refused 409
    stale with a message to reopen the preview -- never written blind."""
    pid = _queue(mode)
    r = client.post(f"/api/wiki/pending/{pid}/approve")
    body = r.get_json()
    assert r.status_code == 409 and body["status"] == "stale", body
    assert "reopen the preview" in body["message"].lower()
    assert _text(wiki) == BODY and writes == []
    assert [p["id"] for p in _pending()] == [pid]


def test_an_edit_without_the_hash_still_applies(wiki, client):
    pid = _queue("edit")
    r = client.post(f"/api/wiki/pending/{pid}/approve")
    assert r.status_code == 200 and r.get_json()["status"] == "applied", r.get_json()
    assert _text(wiki) == BODY.replace(OLD, NEW)


def test_the_direct_update_refuses_a_stale_hash(wiki, client, writes):
    r = client.post("/api/wiki/update", json={"file": PAGE, "old_value": OLD, "new_value": NEW,
                                              "reason": "r", "before_sha256": _sha("other")})
    assert r.status_code == 409 and r.get_json()["status"] == "stale", r.get_json()
    assert _text(wiki) == BODY and writes == []


@pytest.mark.parametrize("rel", HTML_FILES)
def test_the_approve_button_sends_the_previews_hash(rel):
    html = (ROOT / rel).read_text(encoding="utf-8")
    body = _handler(html, "approvePending", "rejectPending")
    assert "preview.before_sha256" in body and "before_sha256:" in body, rel
    assert "JSON.stringify" in body, rel


# ── the decided tool path plans and applies in one locked call ──────────────

def test_a_page_created_between_plan_and_apply_is_not_overwritten(wiki, monkeypatch, writes):
    """A decided replace_file planned against a missing page: another writer
    creates the page right after the plan. The overwrite is not applied on
    the card; it waits in Knowledge, and the other writer's page survives."""
    target = wiki / "notes" / "race-page.md"
    theirs = "# Race Page\n\nWritten by someone else first.\n"
    real = we.plan_wiki_proposal
    calls = []

    def _plan_then_create(item):
        plan = real(item)
        if not calls:
            target.write_text(theirs, encoding="utf-8")       # lands after the plan
        calls.append(plan["exists"])
        return plan

    monkeypatch.setattr(we, "plan_wiki_proposal", _plan_then_create)
    out = _decided("card-0011", {"file": "notes/race-page.md", "mode": "replace_file",
                                 "new_value": "# Race Page\n\nMine.\n", "reason": "r"})
    assert target.read_text(encoding="utf-8") == theirs, out
    assert writes == [], out
    assert "not applied" in out.lower(), out
    [item] = _pending()
    assert item["mode"] == "replace_file" and item["file"] == "notes/race-page.md"


def test_the_decided_path_plans_under_the_lock(wiki, monkeypatch):
    lock = _count_lock(monkeypatch)
    seen = []
    real = we.plan_wiki_proposal

    def _plan(item):
        seen.append(lock.held)
        return real(item)

    monkeypatch.setattr(we, "plan_wiki_proposal", _plan)
    out = _decided("card-0012", _edit())
    assert "applied" in out.lower(), out
    assert seen and all(seen), seen


# ── every wiki write is under the lock (edit, correct, correct_wiki) ────────

def test_edit_and_corrections_write_under_the_lock(wiki, client, monkeypatch, tmp_path):
    import threading
    from agent_friday.routes import wiki as rw
    from agent_friday.services import agent
    assert isinstance(we._PENDING_LOCK, type(threading.RLock()))  # re-entrant: nests safely
    empty = tmp_path / "friday-empty"
    empty.mkdir()
    for mod in (rw, agent):
        monkeypatch.setattr(mod, "WIKI_DIR", wiki, raising=False)
        monkeypatch.setattr(mod, "FRIDAY_DIR", empty, raising=False)
    lock = _count_lock(monkeypatch)
    held = []
    real = we.wiki_write_text

    def _rec(path, text):
        held.append(lock.held)
        return real(path, text)

    monkeypatch.setattr(we, "wiki_write_text", _rec)
    r = client.put("/api/wiki/edit", json={"file": PAGE, "content": BODY + "- Edited: yes\n"})
    assert r.status_code == 200, r.get_json()
    r = client.post("/api/wiki/correct", json={"old_text": "one cat", "new_text": "two cats"})
    assert r.get_json()["count"] == 1, r.get_json()
    out = agent._tool_correct_wiki({"old_text": "two cats", "new_text": "three cats"})
    assert '"count": 1' in out, out
    assert held == [True, True, True], held
    assert "three cats" in _text(wiki) and lock.depth == 0


# ── the pending store is replaced atomically ────────────────────────────────

def test_a_failed_pending_write_leaves_the_old_queue_intact(wiki, monkeypatch, tmp_path):
    import builtins
    import io
    pid = we._propose_wiki_update(PAGE, "Preferences", NEW, "r", OLD)
    before = we.WIKI_PENDING_FILE.read_text(encoding="utf-8")
    real_open = io.open

    class _Full:
        """A file opened for writing whose write fails: the disk is full."""

        def __init__(self, f):
            self._f = f

        def write(self, s):
            raise OSError(28, "No space left on device")

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            self._f.close()
            return False

        def __getattr__(self, name):
            return getattr(self._f, name)

    def _open(file, mode="r", *a, **k):
        f = real_open(file, mode, *a, **k)
        return _Full(f) if ("w" in mode or "a" in mode) else f

    monkeypatch.setattr(io, "open", _open)
    monkeypatch.setattr(builtins, "open", _open)
    with pytest.raises(OSError):
        we._propose_wiki_update(PAGE, "Other", "- Another: line", "r")
    monkeypatch.setattr(io, "open", real_open)
    monkeypatch.setattr(builtins, "open", real_open)
    assert we.WIKI_PENDING_FILE.read_text(encoding="utf-8") == before   # old queue whole
    assert [p["id"] for p in _pending()] == [pid]
    leftovers = [p.name for p in we.WIKI_PENDING_FILE.parent.iterdir()
                 if p.name.startswith(we.WIKI_PENDING_FILE.name + ".")]
    assert leftovers == [], leftovers                                    # no temp left behind


# ── what the routes refuse, no listing shows ────────────────────────────────

def test_dot_segment_files_are_not_listed_searched_or_corrected(wiki, client, monkeypatch, tmp_path):
    from agent_friday.routes import wiki as rw
    from agent_friday.services import agent
    from agent_friday.services.knowledge_graph import wiki_graph as wg
    empty = tmp_path / "friday-empty"
    empty.mkdir()
    for mod in (rw, agent):
        monkeypatch.setattr(mod, "WIKI_DIR", wiki, raising=False)
        monkeypatch.setattr(mod, "FRIDAY_DIR", empty, raising=False)
    monkeypatch.setattr(wg, "WIKI_DIR", wiki, raising=False)
    (wiki / ".trash").mkdir()
    hidden = [wiki / ".trash" / "old-zebra.md", wiki / "notes" / ".zebra-draft.md"]
    for p in hidden:
        p.write_text("# Hidden\n\nzebra\n", encoding="utf-8")
    (wiki / "notes" / "visible.md").write_text("# Visible\n\nzebra\n", encoding="utf-8")

    hits = client.post("/api/wiki/search", json={"query": "zebra"}).get_json()["results"]
    assert [h["path"] for h in hits] == ["notes/visible.md"], hits

    names = [f["filename"] for f in client.get("/api/wiki/structure").get_json()["structure"]["notes"]]
    assert ".zebra-draft.md" not in names and "visible.md" in names, names

    out = agent._tool_search_wiki({"query": "zebra", "limit": 20})
    assert "notes/visible.md" in out and ".zebra-draft" not in out and ".trash" not in out, out

    pages = [p.relative_to(wiki).as_posix() for p in wg.list_wiki_pages(wiki)]
    assert "notes/visible.md" in pages, pages
    assert "notes/.zebra-draft.md" not in pages and ".trash/old-zebra.md" not in pages, pages

    r = client.post("/api/wiki/correct", json={"old_text": "zebra", "new_text": "okapi"})
    assert [m["path"] for m in r.get_json()["modified"]] == ["notes/visible.md"], r.get_json()
    agent._tool_correct_wiki({"old_text": "okapi", "new_text": "zebra"})
    for p in hidden:
        assert p.read_text(encoding="utf-8") == "# Hidden\n\nzebra\n"

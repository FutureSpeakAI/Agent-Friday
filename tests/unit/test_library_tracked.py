"""One consent puts everything Friday already tracks in the Library: the files
and folders the owner granted and the folders Media is built from. It is one
ordinary add in the signed ledger, so a deny beats it, removing it purges what
only it covered, a forgotten document stays forgotten, a tampered ledger
suspends it, and a place the Library never reads stays out."""
from __future__ import annotations

import os

import pytest

from tests.library_fixtures import isolate_library, release_library, write_docs

QUOTE = "I thought jevbox would apply to all the files our system tracks"


@pytest.fixture(autouse=True)
def _env(tmp_path, monkeypatch):
    fg, lstore = isolate_library(tmp_path, monkeypatch)
    from agent_friday.services import media_index
    from agent_friday.services.library import grants
    monkeypatch.setattr(media_index, "_roots", lambda: [tmp_path / "Creations"])
    grants._TRACKED_CACHE.clear()
    yield
    grants._TRACKED_CACHE.clear()
    release_library(fg, lstore)


def _world(tmp_path):
    d = write_docs(tmp_path / "Granted", {"brief.txt": "A granted folder's brief.", "sub/notes.txt": "Deeper notes."})
    d |= write_docs(tmp_path / "Creations", {"script.md": "A script Friday made."})
    d |= write_docs(tmp_path / "Loose", {"one.txt": "One granted file.", "other.txt": "Not granted."})
    d |= write_docs(tmp_path / "Untracked", {"stray.txt": "Nothing tracks this."})
    from agent_friday.services import file_grants as fg
    fg.create_scope_grant(str(tmp_path / "Granted"), "folder", 7)
    fg.create_file_grant(str(d["one.txt"]))
    return d


def _fresh():
    from agent_friday.services.library import grants
    grants._TRACKED_CACHE.clear()
    return grants


def test_off_by_default_nothing_tracked_is_in_the_library(tmp_path):
    d = _world(tmp_path)
    grants = _fresh()
    assert grants.tracked_consent("owner") is None
    assert not any(grants.allowed("owner", d[k]) for k in ("brief.txt", "script.md", "one.txt"))


def test_one_consent_covers_granted_folders_files_and_media_and_nothing_else(tmp_path):
    d = _world(tmp_path)
    grants = _fresh()
    ev = grants.add_tracked("owner", said=QUOTE)
    assert ev["type"] == "tracked" and ev["said"] == QUOTE
    for k in ("brief.txt", "sub/notes.txt", "script.md", "one.txt"):
        assert grants.allowed("owner", d[k]), k
    assert not grants.allowed("owner", d["other.txt"]), "a granted file does not open its folder"
    assert not grants.allowed("owner", d["stray.txt"])


def test_it_is_one_ledger_event_never_one_per_file(tmp_path):
    _world(tmp_path)
    grants = _fresh()
    from agent_friday.services import file_grants as fg
    before = len(fg._ledger_path().read_text(encoding="utf-8").splitlines())
    a = grants.add_tracked("owner", said=QUOTE)
    b = grants.add_tracked("owner")
    after = len(fg._ledger_path().read_text(encoding="utf-8").splitlines())
    assert after - before == 1 and a["id"] == b["id"], "turning it on twice is still one consent"


def test_a_deny_beats_it(tmp_path):
    d = _world(tmp_path)
    grants = _fresh()
    grants.add_tracked("owner")
    from agent_friday.services import file_grants as fg
    fg.create_deny_mark(str(d["brief.txt"]), "file")
    assert not grants.allowed("owner", d["brief.txt"])
    assert grants.allowed("owner", d["sub/notes.txt"])


def test_a_place_the_library_never_reads_stays_out(tmp_path, monkeypatch):
    d = _world(tmp_path)
    from agent_friday.services import studio_files as sf
    monkeypatch.setattr(sf, "_excluded", lambda *a: [os.path.normcase(str((tmp_path / "Creations").resolve()))])
    grants = _fresh()
    grants.add_tracked("owner")
    assert not grants.allowed("owner", d["script.md"])
    assert all(r["path"] != str((tmp_path / "Creations").resolve()) for r in grants.tracked_roots())


def test_turning_it_off_purges_what_only_it_covered(tmp_path):
    d = _world(tmp_path)
    grants = _fresh()
    from agent_friday.services.library import indexer, runtime
    from agent_friday.services.library.store import store_for
    ev = grants.add_tracked("owner")
    grants.add_scope("owner", str(tmp_path / "Loose"))       # an ordinary add of its own
    st = store_for("owner")
    for root in (tmp_path / "Granted", tmp_path / "Creations", tmp_path / "Loose"):
        indexer.sweep_scope(st, root, allowed=lambda p: grants.allowed("owner", p))
    assert {r["title"] for r in st.list_documents()} >= {"brief", "notes", "script", "one", "other"}
    grants.remove_scope("owner", ev["id"])
    runtime.purge_uncovered("owner")
    assert {r["title"] for r in st.list_documents()} == {"one", "other"}, "the separate add keeps its own"


def test_a_forgotten_document_stays_forgotten(tmp_path):
    d = _world(tmp_path)
    grants = _fresh()
    from agent_friday.services.library import forget, indexer, runtime
    from agent_friday.services.library.store import store_for
    grants.add_tracked("owner")
    st = store_for("owner")
    indexer.sweep_scope(st, tmp_path / "Granted", allowed=lambda p: grants.allowed("owner", p))
    doc = next(r for r in st.list_documents() if r["title"] == "brief")
    forget.forget_document("owner", doc["id"])
    runtime.purge_uncovered("owner")
    indexer.sweep_scope(st, tmp_path / "Granted", allowed=lambda p: grants.allowed("owner", p))
    assert "brief" not in {r["title"] for r in st.list_documents() if str(r["state"]).startswith("indexed")}


def test_a_tampered_ledger_suspends_it(tmp_path):
    import json
    d = _world(tmp_path)
    grants = _fresh()
    grants.add_tracked("owner")
    assert grants.allowed("owner", d["script.md"])
    from agent_friday.services import file_grants as fg
    ledger = fg._ledger_path()
    lines = ledger.read_text(encoding="utf-8").splitlines()
    rec = json.loads(lines[-1])
    rec["event"]["said"] = "edited after signing"
    lines[-1] = json.dumps(rec)
    ledger.write_text("\n".join(lines) + "\n", encoding="utf-8")
    fg._invalidate_cache()
    assert grants.suspended() and not grants.allowed("owner", d["script.md"])


def test_the_status_reports_the_switch_and_progress(tmp_path):
    _world(tmp_path)
    grants = _fresh()
    from agent_friday.services.library import api
    from agent_friday.services.library.store import store_for
    st = store_for("owner")
    assert api.status(st, "owner")["tracked"] == {"on": False}
    grants.add_tracked("owner", said=QUOTE)
    t = api.status(st, "owner", indexer_pending=4)["tracked"]
    assert t["on"] and t["said"] == QUOTE and t["places"] == 3 and t["reading"] == 4


@pytest.mark.parametrize("rel", ("index.html", "ui_parts/app.html"))
def test_settings_offer_it_labelled_recommended_and_never_preselected(rel):
    from pathlib import Path
    src = (Path(__file__).resolve().parents[2] / rel).read_text(encoding="utf-8")
    fn = src[src.index("function LibraryTrackedToggle()"):src.index("function FileAccessPanel()")]
    assert "already tracks (Recommended)" in fn
    assert "value: on" in fn or "value={on}" in fn, "the switch shows the ledger's state, nothing preselected"
    assert "/api/library/tracked" in fn and "on: !t.on" in fn, "one click turns it off"


# -- Friday's home: her content folders in, everything else out ------------------

HOME_FILES = {
    # her content (allowlisted)
    "podcasts/ep1/transcript.md": "A podcast transcript.",
    "creations/poster.md": "A creation.",
    "wiki/content/people.md": "A wiki page.",
    "content/post.md": "A post.",
    "timelines/2026.md": "A timeline.",
    "media/cards/card.md": "A card.",
    # never read
    "settings.json": "{}",
    "credentials.json": "{\"key\": \"x\"}",
    "vault/notes.md": "Sealed.",
    "privacy/file_grants.jsonl": "{}",
    "friday.log": "a log line",
    "runtime/residency/endpoints.json": "{}",
    "models/readme.md": "A model card.",
    "conversations/c1.json": "{}",
    "pipelines/runs/r1.md": "A pipeline run.",
    "wiki/content/.env": "API_KEY=x",
    "podcasts/id_rsa": "a placeholder: the name alone keeps it out",
}
CONTENT = {k for k in HOME_FILES if k.split("/")[0] in ("podcasts", "creations", "wiki", "content",
                                                          "timelines", "media")} - {"wiki/content/.env",
                                                                                    "podcasts/id_rsa"}


@pytest.fixture()
def home(tmp_path, monkeypatch):
    from agent_friday import core
    from agent_friday.services import file_search
    h = tmp_path / "fh"
    write_docs(h, HOME_FILES)
    monkeypatch.setattr(core, "FRIDAY_DIR", h)
    monkeypatch.setattr(file_search, "_vault_root", lambda: h / "vault")
    return h


def test_only_her_content_folders_in_her_home_are_ever_read(home):
    from agent_friday.services.library import indexer
    for rel in HOME_FILES:
        refused = indexer._refused(home / rel)
        if rel in CONTENT:
            assert refused is None, "her own content is readable: %s" % rel
        else:
            assert refused, "a config, key, vault, log or runtime path got in: %s" % rel


def test_the_allowlist_names_only_content_folders():
    from agent_friday.services.library import indexer
    bad = [s for s in indexer.HOME_CONTENT_ALLOW
           if any(w in s.lower() for w in ("vault", "key", "secret", "cred", "config", "setting", "log",
                                           "runtime", "model", "privacy", "token", "conversation"))]
    assert not bad and indexer.HOME_CONTENT_ALLOW


def test_the_tracked_consent_reads_her_content_and_never_her_config(home, monkeypatch):
    from agent_friday.services import media_index
    monkeypatch.setattr(media_index, "_roots", lambda: [home / "podcasts", home / "wiki" / "content",
                                                        home / "pipelines" / "runs", home / "media" / "cards"])
    grants = _fresh()
    grants.add_tracked("owner")
    assert grants.allowed("owner", home / "podcasts/ep1/transcript.md")
    assert grants.allowed("owner", home / "wiki/content/people.md")
    for rel in ("pipelines/runs/r1.md", "settings.json", "credentials.json", "vault/notes.md",
                "wiki/content/.env", "podcasts/id_rsa"):
        assert not grants.allowed("owner", home / rel), rel
    roots = {r["path"] for r in grants.tracked_roots()}
    assert str((home / "pipelines" / "runs").resolve()) not in roots

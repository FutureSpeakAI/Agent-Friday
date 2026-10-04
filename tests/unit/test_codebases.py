"""Codebases: a chat's panel with a repository behind it
(docs/design/active/vibe-coding-salon.md §4.1, §4.8, Phase 2).

A codebase is a git repository at ~/.friday/codebases/<id>/repo/ (or an
existing folder the user points at, on a salon branch). Every applied change
is a commit whose author line names the model and the key profile, with a
one-line summary for people and a step receipt. Undo is a revert that is
itself a step, walks backwards and does not oscillate. The preview is one
document: index.html with its relative stylesheets and scripts inlined, so
the sandboxed frame can run it with no server.
"""
from __future__ import annotations

import json
import subprocess

import pytest

from agent_friday.services import codebases as cb


@pytest.fixture(autouse=True)
def _root(monkeypatch, tmp_path):
    monkeypatch.setattr(cb, "_root", lambda: tmp_path / "codebases")
    yield


def _git(repo, *args):
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, timeout=40).stdout


# ── creating ─────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("template", ["static", "react", "bundle"])
def test_a_new_codebase_is_a_repo_with_its_template_and_one_starting_commit(template):
    rec = cb.create("Rent Tracker", template=template)
    assert rec["id"].startswith("cb-") and rec["slug"] == "rent-tracker" and rec["tier"] == "B0"
    repo = cb.repo_path(rec["id"])
    assert (repo / ".git").is_dir() and (repo / "index.html").is_file()
    log = _git(repo, "log", "--format=%s")
    assert log.strip().splitlines() == ["Start: Rent Tracker"]
    assert (repo / ".gitignore").read_text(encoding="utf-8").strip().splitlines()[0] == ".friday/"
    files = cb.files(rec["id"])
    assert "index.html" in [f["path"] for f in files]
    assert not any(f["path"].startswith((".git", ".friday")) for f in files)
    if template == "bundle":
        manifest = json.loads(cb.read(rec["id"], "manifest.json"))
        assert manifest["id"] == "rent-tracker" and manifest["friday_api"] == 1
        assert manifest["capabilities"]["network"] == ["none"]
    if template == "react":
        html = cb.read(rec["id"], "index.html")
        assert "https://esm.sh/esbuild-wasm@" in html and "app.jsx" in html


def test_an_unknown_template_is_refused():
    with pytest.raises(ValueError):
        cb.create("x", template="django")


def test_an_existing_folder_gets_a_salon_branch_and_no_commits_on_its_own(tmp_path):
    folder = tmp_path / "mysite"
    folder.mkdir()
    (folder / "index.html").write_text("<h1>mine</h1>", encoding="utf-8")
    subprocess.run(["git", "-C", str(folder), "init", "-q", "-b", "main"], check=True)
    subprocess.run(["git", "-C", str(folder), "-c", "user.name=t", "-c", "user.email=t@t", "add", "-A"], check=True)
    subprocess.run(["git", "-C", str(folder), "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "theirs"], check=True)
    rec = cb.create("My Site", existing_path=str(folder))
    assert rec["existing"] is True and rec["branch"] == "salon/my-site"
    assert _git(folder, "rev-parse", "--abbrev-ref", "HEAD").strip() == "salon/my-site"
    cb.step(rec["id"], {"index.html": "<h1>ours</h1>"}, "Change the heading")
    assert _git(folder, "log", "--format=%s", "main").strip() == "theirs", "their branch is untouched"
    assert _git(folder, "log", "--format=%s").strip().splitlines()[0] == "Change the heading"


def test_a_missing_folder_is_refused(tmp_path):
    with pytest.raises(ValueError):
        cb.create("x", existing_path=str(tmp_path / "nope"))


# ── steps ────────────────────────────────────────────────────────────────────

def test_a_step_is_a_commit_naming_the_model_and_the_key_with_a_receipt():
    rec = cb.create("Tracker")
    st = cb.step(rec["id"], {"index.html": "<h1>v2</h1>", "notes.txt": "hello"}, "Bigger heading",
                 model="bonsai2:27b", key_profile="mine", tests={"ran": 0, "passed": 0})
    assert st["sha"] and st["summary"] == "Bigger heading" and st["kind"] == "step"
    repo = cb.repo_path(rec["id"])
    assert _git(repo, "log", "-1", "--format=%an").strip() == "bonsai2:27b via mine"
    assert cb.read(rec["id"], "notes.txt") == "hello"
    r = st["receipt"]
    assert r["commit"] == st["sha"] and {f["path"] for f in r["files"]} == {"index.html", "notes.txt"}
    assert all(f["sha256"] for f in r["files"]) and r["model"] == "bonsai2:27b" and r["key_profile"] == "mine"
    assert r["tests"] == {"ran": 0, "passed": 0} and "preview_hash" in r and r["network_events"] == []
    assert (repo / ".friday" / "receipts" / (st["sha"] + ".json")).is_file()
    assert ".friday" not in _git(repo, "ls-files")


def test_deleting_a_file_is_a_step_too():
    rec = cb.create("Tracker")
    cb.step(rec["id"], {"extra.txt": "x"}, "Add extra")
    st = cb.step(rec["id"], {"extra.txt": None}, "Remove extra")
    assert st and "extra.txt" in st["receipt"]["deleted"]
    assert cb.read(rec["id"], "extra.txt") is None


def test_a_step_that_changes_nothing_is_no_step():
    rec = cb.create("Tracker")
    same = cb.read(rec["id"], "index.html")
    assert cb.step(rec["id"], {"index.html": same}, "No-op") is None
    assert len(cb.steps(rec["id"])) == 1


@pytest.mark.parametrize("bad", ["../escape.txt", ".git/config", ".friday/receipts/x.json", "/abs.txt", "a\\..\\b"])
def test_paths_outside_the_working_tree_are_refused(bad):
    rec = cb.create("Tracker")
    with pytest.raises(ValueError):
        cb.step(rec["id"], {bad: "x"}, "bad")


def test_a_hand_edit_is_a_step_by_you():
    rec = cb.create("Tracker")
    st = cb.write(rec["id"], "index.html", "<h1>by hand</h1>")
    assert st["author"] == "you" and "index.html" in st["summary"]
    assert _git(cb.repo_path(rec["id"]), "log", "-1", "--format=%an").strip() == "you"


# ── undo walks backwards ─────────────────────────────────────────────────────

def test_undo_reverts_the_last_step_and_a_second_undo_goes_further_back():
    rec = cb.create("Tracker")
    cb.step(rec["id"], {"index.html": "<h1>one</h1>"}, "One")
    cb.step(rec["id"], {"index.html": "<h1>two</h1>"}, "Two")
    cb.step(rec["id"], {"index.html": "<h1>three</h1>"}, "Three")
    u1 = cb.undo(rec["id"])
    assert u1["kind"] == "undo" and u1["summary"].startswith("Undo: Three")
    assert cb.read(rec["id"], "index.html") == "<h1>two</h1>"
    u2 = cb.undo(rec["id"])
    assert u2["summary"].startswith("Undo: Two")
    assert cb.read(rec["id"], "index.html") == "<h1>one</h1>", "the second undo went further back, not round"
    cb.undo(rec["id"])
    assert cb.read(rec["id"], "index.html") == cb.template_files("static", "Tracker")["index.html"]
    with pytest.raises(cb.NothingToUndo):
        cb.undo(rec["id"])


def test_a_new_step_after_an_undo_is_what_the_next_undo_removes():
    rec = cb.create("Tracker")
    cb.step(rec["id"], {"index.html": "<h1>one</h1>"}, "One")
    cb.step(rec["id"], {"index.html": "<h1>two</h1>"}, "Two")
    cb.undo(rec["id"])
    cb.step(rec["id"], {"notes.txt": "n"}, "Notes")
    cb.undo(rec["id"])
    assert cb.read(rec["id"], "notes.txt") is None
    assert cb.read(rec["id"], "index.html") == "<h1>one</h1>"


def test_steps_read_newest_first_with_kinds_and_receipts():
    rec = cb.create("Tracker")
    cb.step(rec["id"], {"index.html": "<h1>one</h1>"}, "One")
    cb.undo(rec["id"])
    s = cb.steps(rec["id"])
    assert [x["kind"] for x in s] == ["undo", "step", "start"]
    assert s[0]["undoes"] == s[1]["sha"]
    assert s[1]["receipt"]["summary"] == "One"
    d = cb.diff(rec["id"], s[1]["sha"])
    assert "+<h1>one</h1>" in d


# ── the preview document ─────────────────────────────────────────────────────

def test_the_preview_inlines_relative_stylesheets_and_scripts():
    rec = cb.create("Tracker")
    cb.step(rec["id"], {
        "index.html": '<!doctype html><html><head><link rel="stylesheet" href="styles.css"></head>'
                      '<body><h1>Hi</h1><script src="app.js"></script><script type="text/jsx" src="app.jsx"></script>'
                      '<script src="https://esm.sh/react@18.3.1"></script></body></html>',
        "styles.css": "h1{color:red}",
        "app.js": "console.log(1)",
        "app.jsx": "const x = <b/>;",
    }, "Files")
    doc = cb.preview(rec["id"])
    assert "<style>h1{color:red}</style>" in doc
    assert "<script>console.log(1)</script>" in doc
    assert '<script type="text/jsx">const x = <b/>;</script>' in doc
    assert 'src="https://esm.sh/react@18.3.1"' in doc, "remote pinned scripts stay as they are"
    assert "styles.css" not in doc.replace("<style>", "")


def test_the_preview_of_a_missing_index_says_so():
    rec = cb.create("Tracker")
    cb.step(rec["id"], {"index.html": None}, "Remove")
    assert "no index.html" in cb.preview(rec["id"]).lower()


# ── what the model is told ───────────────────────────────────────────────────

def test_context_block_names_the_files_and_last_steps_and_is_bounded():
    rec = cb.create("Tracker")
    cb.step(rec["id"], {"big.txt": "x" * 100_000, "small.txt": "tiny"}, "Add files")
    block = cb.context_block_for(rec["id"])
    assert cb.CONTEXT_HEADER in block and rec["id"] in block and "Tracker" in block
    assert "small.txt" in block and "tiny" in block
    assert "big.txt" in block and "x" * 20_000 not in block, "large files are listed, not inlined"
    assert "codebase_edit" in block and "Add files" in block
    assert len(block) < 60_000


def test_every_step_and_undo_is_announced_to_the_page(monkeypatch):
    from agent_friday.services import desktop_bus
    seen = []
    monkeypatch.setattr(desktop_bus, "broadcast", lambda ev, kind="chat": seen.append((ev, kind)) or 1)
    rec = cb.create("Tracker")
    cb.step(rec["id"], {"index.html": "<h1>x</h1>"}, "X")
    cb.undo(rec["id"])
    assert [e["kind"] for e, _ in seen] == ["step", "undo"]
    assert all(k == "chat" and e["type"] == "codebase_step" and e["codebase_id"] == rec["id"] for e, k in seen)
    assert "content" not in seen[0][0] and "<h1>" not in json.dumps(seen[0][0])


def test_for_conversation_finds_the_bound_codebase(monkeypatch):
    bound = {}
    from agent_friday.services import conversations as convs
    monkeypatch.setattr(convs, "load", lambda cid: {"id": cid, "codebase": bound.get(cid)})
    rec = cb.create("Tracker")
    bound["conv-1"] = rec["id"]
    assert cb.for_conversation("conv-1")["id"] == rec["id"]
    assert cb.for_conversation("conv-2") is None
    assert cb.context_block("conv-2") == ""
    assert rec["id"] in cb.context_block("conv-1")

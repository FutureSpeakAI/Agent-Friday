"""Start with your knowledge: the native folder picker and the detected suggestions.

The picker is never opened for real: every test replaces the subprocess, and a
test that forgets to do so fails loudly instead of showing a dialog. The trees
are synthetic and live under tmp_path.
"""
from __future__ import annotations

import base64
import os
from pathlib import Path

import pytest

from agent_friday.services import knowledge_folders as kf
from tests.library_fixtures import isolate_library, release_library


class RealDialogOpened(BaseException):
    """A BaseException, so no `except Exception` in the code under test can swallow it."""


@pytest.fixture(autouse=True)
def _env(tmp_path, monkeypatch):
    fg, lstore = isolate_library(tmp_path, monkeypatch)
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    monkeypatch.setattr(kf, "documents_dir", lambda: home / "Documents")
    monkeypatch.setattr(kf, "_is_windows", lambda: True)

    def _no_real_dialog(*a, **k):
        raise RealDialogOpened("a test tried to start the real folder window")
    monkeypatch.setattr(kf.subprocess, "Popen", _no_real_dialog)
    yield home
    release_library(fg, lstore)


def _write(path: Path, size: int = 10):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"x" * size)


# ── the picker ───────────────────────────────────────────────────────────────

class _FakeProc:
    def __init__(self, out=b"", code=0, timeout_first=False):
        self._out, self.returncode, self._timeout_first, self.killed = out, code, timeout_first, False

    def communicate(self, timeout=None):
        if self._timeout_first and not self.killed:
            raise kf.subprocess.TimeoutExpired("powershell", timeout)
        return self._out, b""

    def kill(self):
        self.killed = True


def _picker_returns(monkeypatch, out, code=0, timeout_first=False):
    seen = {}

    def popen(argv, **kw):
        seen["argv"], seen["kw"] = argv, kw
        proc = _FakeProc(out, code, timeout_first)
        seen["proc"] = proc
        return proc
    monkeypatch.setattr(kf.subprocess, "Popen", popen)
    return seen


def test_the_helper_asks_for_folders_and_many_of_them():
    script = kf.picker_script()
    assert "DC1C5A9C-E88A-4dde-A5A1-60F82A20AEF7" in script          # the FileOpenDialog class
    assert "d57c7288-d4ad-4768-be02-9d969532d960" in script          # IFileOpenDialog
    assert "0x20u | 0x200u" in script                                # FOS_PICKFOLDERS | FOS_ALLOWMULTISELECT
    assert "0x80058000u" in script                                   # SIGDN_FILESYSPATH


def test_the_dialog_is_launched_like_reveal_launches_explorer_without_a_shell(monkeypatch, _env):
    d = _env / "Documents"
    d.mkdir()
    seen = _picker_returns(monkeypatch, ("PATH:%s\r\n" % d).encode("utf-8"))
    kf.pick_folders()
    argv = seen["argv"]
    assert isinstance(argv, list) and "-STA" in argv and "-NonInteractive" in argv
    assert seen["kw"].get("shell") is not True
    enc = argv[argv.index("-EncodedCommand") + 1]
    assert "0x20u | 0x200u" in base64.b64decode(enc).decode("utf-16-le")


def test_chosen_folders_come_back_described_and_nothing_is_preselected(monkeypatch, _env):
    a, b = _env / "Documents" / "Notes", _env / "Work"
    _write(a / ".obsidian" / "app.json")
    _write(a / "one.md", 100)
    _write(b / "two.md", 200)
    _picker_returns(monkeypatch, ("PATH:%s\r\nPATH:%s\r\n" % (a, b)).encode("utf-8"))
    res = kf.pick_folders()
    assert res["ok"] and not res["cancelled"] and res["refused"] == []
    by = {f["name"]: f for f in res["folders"]}
    assert by["Notes"]["kind"] == "obsidian" and by["Notes"]["file_count"] == 1
    assert by["Work"]["kind"] == "folder"
    assert all(f["preselected"] is False for f in res["folders"])


def test_cancelling_the_window_returns_nothing(monkeypatch):
    _picker_returns(monkeypatch, b"CANCELLED\r\n")
    res = kf.pick_folders()
    assert res["ok"] and res["cancelled"] is True and res["folders"] == [] and res["refused"] == []


def test_every_returned_path_goes_through_both_validators(monkeypatch, _env, tmp_path):
    from agent_friday.services import file_grants as fg
    folder = _env / "Fine"
    folder.mkdir()
    calls = {"unsafe": [], "broad": []}
    real_u, real_b = fg.unsafe_path_reason, fg.too_broad_folder_reason
    monkeypatch.setattr(fg, "unsafe_path_reason", lambda t: calls["unsafe"].append(t) or real_u(t))
    monkeypatch.setattr(fg, "too_broad_folder_reason", lambda p: calls["broad"].append(str(p)) or real_b(p))
    _picker_returns(monkeypatch, ("PATH:%s\r\n" % folder).encode("utf-8"))
    kf.pick_folders()
    assert any(str(folder) in c for c in calls["unsafe"]) and any(str(folder) in c for c in calls["broad"])


def test_unsafe_and_too_broad_paths_are_refused_not_returned(monkeypatch, _env, tmp_path):
    ok = _env / "Fine"
    ok.mkdir()
    afile = _env / "a.txt"
    _write(afile)
    drive_root = Path(tmp_path.anchor)
    out = "\r\n".join(["PATH:" + p for p in (
        r"\\server\share\notes", str(drive_root), str(_env), str(afile), "relative\\dir", str(ok))])
    _picker_returns(monkeypatch, out.encode("utf-8"))
    res = kf.pick_folders()
    assert [f["name"] for f in res["folders"]] == ["Fine"]
    why = " | ".join(r["error"] for r in res["refused"])
    assert "network paths are not allowed" in why
    assert "whole drive is too broad" in why
    assert "whole home folder is too broad" in why
    assert "not a folder" in why
    assert "full path" in why
    assert len(res["refused"]) == 5


def test_a_folder_the_library_will_not_read_is_refused_here_too(monkeypatch, _env):
    from agent_friday.services import file_grants as fg
    from agent_friday.services.library import indexer
    secret = _env / "Elsewhere"
    secret.mkdir()
    monkeypatch.setattr(indexer, "_refused", lambda p: Path(p) == secret)
    _picker_returns(monkeypatch, ("PATH:%s\r\n" % secret).encode("utf-8"))
    res = kf.pick_folders()
    assert res["folders"] == [] and "doesn't read this location" in res["refused"][0]["error"]


def test_off_windows_the_typed_path_is_the_fallback(monkeypatch):
    monkeypatch.setattr(kf, "_is_windows", lambda: False)
    res = kf.pick_folders()
    assert res["ok"] is False and res["fallback"] == "type" and "Type the folder" in res["error"]


def test_a_dialog_that_cannot_start_falls_back_to_typing(monkeypatch):
    def boom(*a, **k):
        raise OSError("no powershell")
    monkeypatch.setattr(kf.subprocess, "Popen", boom)
    res = kf.pick_folders()
    assert res["ok"] is False and res["fallback"] == "type"
    assert "powershell" not in res["error"].lower()                     # no raw error text reaches the page


def test_a_dialog_left_open_too_long_is_closed(monkeypatch):
    seen = _picker_returns(monkeypatch, b"", timeout_first=True)
    res = kf.pick_folders(timeout=0.01)
    assert res["ok"] is False and seen["proc"].killed


def test_a_failed_helper_with_no_output_falls_back_to_typing(monkeypatch):
    _picker_returns(monkeypatch, b"", code=1)
    res = kf.pick_folders()
    assert res["ok"] is False and res["fallback"] == "type"


def test_only_one_window_at_a_time(monkeypatch):
    assert kf._PICK_LOCK.acquire(blocking=False)
    try:
        res = kf.pick_folders()
    finally:
        kf._PICK_LOCK.release()
    assert res["ok"] is False and res["fallback"] == "wait"


def test_a_real_dialog_in_a_test_is_loud(monkeypatch):
    with pytest.raises(RealDialogOpened):
        kf.pick_folders()


# ── what a folder is ─────────────────────────────────────────────────────────

def test_classify_folder_reads_the_markers(_env):
    docs = _env / "Documents"
    (docs / "V" / ".obsidian").mkdir(parents=True)
    (docs / "L" / "logseq").mkdir(parents=True)
    _write(docs / "L" / "logseq" / "config.edn", 2)
    (docs / "P" / ".git").mkdir(parents=True)
    _write(docs / "E" / "old.enex", 5)
    for i in range(3):
        _write(docs / "N" / ("Page%d %s.md" % (i, "c" * 32)), 5)
    _write(_env / "Zotero" / "zotero.sqlite", 5)
    (_env / "Zotero" / "storage").mkdir()
    got = {n: kf.classify_folder(docs / n) for n in ("V", "L", "P", "E", "N")}
    assert got == {"V": "obsidian", "L": "logseq", "P": "project", "E": "evernote", "N": "notion"}
    assert kf.classify_folder(docs) == "documents"
    assert kf.classify_folder(_env / "Zotero" / "storage") == "zotero"
    assert kf.classify_folder(docs / "V" / ".obsidian") == "folder"


def test_estimate_counts_files_and_skips_dot_folders_and_caches(_env):
    root = _env / "Documents" / "Proj"
    _write(root / "a.txt", 100)
    _write(root / "sub" / "b.txt", 300)
    _write(root / ".git" / "huge", 99999)
    _write(root / "node_modules" / "pkg" / "x.js", 99999)
    est = kf.estimate(root)
    assert est == {"size_bytes": 400, "file_count": 2, "approximate": False}


def test_estimate_says_so_when_it_stops_early(_env):
    root = _env / "Documents" / "Big"
    for i in range(8):
        _write(root / ("f%d.txt" % i), 1)
    est = kf.estimate(root, max_files=3)
    assert est["file_count"] == 3 and est["approximate"] is True
    assert kf.estimate(root, box_s=-1)["approximate"] is True           # a spent time box is "about"


def test_estimate_does_not_follow_a_link_out_of_the_folder(_env, tmp_path):
    outside = tmp_path / "outside"
    _write(outside / "secret.txt", 5000)
    root = _env / "Documents" / "Linked"
    _write(root / "mine.txt", 10)
    try:
        os.symlink(outside, root / "out", target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("this account cannot make symlinks")
    assert kf.estimate(root)["size_bytes"] == 10


def test_human_sizes():
    assert kf.human_bytes(0) == "0 B" and kf.human_bytes(1536) == "1.5 KB"
    assert kf.human_bytes(5 * 1024 * 1024) == "5.0 MB" and kf.human_bytes(300 * 1024 ** 3) == "300 GB"


# ── the suggestions ──────────────────────────────────────────────────────────

def _lay_out(home: Path):
    docs = home / "Documents"
    for i in range(30):
        _write(docs / ("d%d.txt" % i), 50_000)
    (docs / "Notes" / ".obsidian").mkdir(parents=True)
    _write(docs / "Notes" / "n.md", 40)
    (docs / "Graph" / "logseq").mkdir(parents=True)
    _write(docs / "Graph" / "logseq" / "config.edn", 2)
    _write(docs / "Graph" / "pages" / "p.md", 20)
    for i in range(3):
        _write(docs / "Imp" / ("Page%d %s.md" % (i, "b" * 32)), 5)
    _write(docs / "loose.enex", 12)
    (home / "Projects" / "app" / ".git").mkdir(parents=True)
    _write(home / "Projects" / "app" / "main.py", 8)
    _write(home / "Zotero" / "zotero.sqlite", 5)
    _write(home / "Zotero" / "storage" / "AB12" / "paper.pdf", 700)
    return docs


def test_every_kind_is_found_with_an_estimate(_env):
    _lay_out(_env)
    rows = kf.suggestions()
    kinds = {r["kind"] for r in rows}
    assert {"documents", "obsidian", "logseq", "notion", "evernote", "zotero", "project"} <= kinds
    by = {r["kind"]: r for r in rows}
    assert by["obsidian"]["file_count"] == 1 and by["obsidian"]["size_label"] == "40 B"
    assert by["project"]["name"] == "app" and by["project"]["file_count"] == 1
    assert by["zotero"]["path"].endswith("storage") and by["zotero"]["file_count"] == 1
    assert by["evernote"]["path"].endswith("loose.enex")               # a loose file is offered as the file
    assert all(isinstance(r["size_bytes"], int) and isinstance(r["file_count"], int) for r in rows)


def test_nothing_is_preselected_and_recommended_is_a_label_only(_env):
    _lay_out(_env)
    rows = kf.suggestions()
    assert all(r["preselected"] is False for r in rows)
    assert [r["kind"] for r in rows if r["recommended"]] == ["documents"]


def test_documents_is_recommended_only_when_it_is_non_trivial(_env):
    docs = _env / "Documents"
    for i in range(3):
        _write(docs / ("t%d.txt" % i), 10)
    assert not [r for r in kf.suggestions() if r["recommended"]]         # a few tiny files
    for i in range(30):
        _write(docs / ("big%d.txt" % i), 60_000)
    assert [r["kind"] for r in kf.suggestions() if r["recommended"]] == ["documents"]
    # many files but under a megabyte in all: still trivial
    for f in docs.iterdir():
        f.unlink()
    for i in range(40):
        _write(docs / ("s%d.txt" % i), 100)
    assert not [r for r in kf.suggestions() if r["recommended"]]


def test_the_cloud_choice_is_never_offered_for_a_vault_or_private_folder(_env):
    _lay_out(_env)
    _write(_env / "Documents" / "Tax Records 2020" / "a.pdf", 10)
    rows = kf.suggestions()
    for r in rows:
        if r["kind"] in ("obsidian", "logseq", "notion", "evernote", "zotero"):
            assert r["private"] and r["cloud_offer"] is False, r["kind"]
    by = {r["kind"]: r for r in rows}
    assert by["documents"]["cloud_offer"] is True and by["project"]["cloud_offer"] is True
    row = kf.describe_folder(str(_env / "Documents" / "Tax Records 2020"))
    assert row["ok"] and row["private"] and row["cloud_offer"] is False   # a private-looking name counts too
    pick = kf.describe_folder(str(_env / "Documents" / "Notes"))
    assert pick["ok"] and pick["kind"] == "obsidian" and pick["cloud_offer"] is False


def test_a_vault_deeper_than_three_levels_is_not_looked_for(_env):
    docs = _env / "Documents"
    (docs / "a" / "b" / "Near" / ".obsidian").mkdir(parents=True)      # depth 3: found
    (docs / "a" / "b" / "c" / "Far" / ".obsidian").mkdir(parents=True)  # depth 4: not looked for
    names = [r["name"] for r in kf.suggestions() if r["kind"] == "obsidian"]
    assert names == ["Near"]


def test_a_detector_stops_at_its_time_box(_env):
    docs = _env / "Documents"
    (docs / "V" / ".obsidian").mkdir(parents=True)
    assert kf._find_marked_hidden([docs], ".obsidian", 5.0) == [docs / "V"]
    assert kf._find_marked_hidden([docs], ".obsidian", -1.0) == []        # spent box: quietly nothing


def test_one_failing_detector_hides_only_its_own_suggestions(_env, monkeypatch):
    _lay_out(_env)

    def boom(roots, box_s):
        raise RuntimeError("disk error")
    monkeypatch.setattr(kf, "_detect_logseq", boom)
    kinds = {r["kind"] for r in kf.suggestions()}
    assert "logseq" not in kinds and {"documents", "obsidian", "project"} <= kinds


def test_suggestions_are_read_only(_env):
    docs = _lay_out(_env)
    before = sorted((str(p), p.stat().st_mtime_ns, p.stat().st_size) for p in docs.rglob("*") if p.is_file())
    kf.suggestions()
    after = sorted((str(p), p.stat().st_mtime_ns, p.stat().st_size) for p in docs.rglob("*") if p.is_file())
    assert before == after


def test_a_notion_zip_is_suggested_but_cannot_be_added(_env):
    import zipfile
    zdir = _env / "Documents"
    zdir.mkdir(exist_ok=True)
    with zipfile.ZipFile(zdir / "Export-1234.zip", "w") as z:
        for i in range(4):
            z.writestr("Export/Page%d %s.md" % (i, "d" * 32), "hi")
    rows = [r for r in kf.suggestions() if r["kind"] == "notion"]
    assert rows and rows[0]["can_add"] is False and "Unzip" in rows[0]["note"]


def test_a_folder_already_in_the_library_says_so():
    scopes = [{"path": r"C:\Notes", "type": "folder"}]
    assert kf._covered_by_scope(r"C:\Notes", scopes) and kf._covered_by_scope(r"C:\Notes\sub", scopes)
    assert not kf._covered_by_scope(r"C:\Notes2", scopes)
    assert not kf._covered_by_scope(r"C:\Other", scopes)


# ── by voice ─────────────────────────────────────────────────────────────────

_ROWS = [
    {"kind": "documents", "label": "Your Documents folder", "name": "Documents", "size_label": "1.2 GB",
     "file_count": 3400, "approximate": False, "recommended": True, "added": False},
    {"kind": "obsidian", "label": "Obsidian vault", "name": "Journal Vault", "size_label": "40 MB",
     "file_count": 800, "approximate": True, "recommended": False, "added": True},
]


def test_the_spoken_suggestions_name_folders_only_for_a_local_voice_model():
    cloud = kf.spoken_suggestions(_ROWS, local_model=False)
    local = kf.spoken_suggestions(_ROWS, local_model=True)
    assert "Journal Vault" not in cloud and "Journal Vault" in local
    for text in (cloud, local):
        assert "1.2 GB in 3,400 files" in text and "about 40 MB in about 800 files" in text
        assert "the one I'd suggest" in text and "already in your Library" in text


def test_nothing_found_is_said_plainly():
    assert "didn't find anything" in kf.spoken_suggestions([], local_model=True)


def test_start_voice_pick_raises_one_card_for_the_picks_and_adds_nothing(monkeypatch, _env):
    folder = _env / "Documents" / "Vault"
    (folder / ".obsidian").mkdir(parents=True)
    _picker_returns(monkeypatch, ("PATH:%s\r\n" % folder).encode("utf-8"))
    raised = []
    from agent_friday.services.library import cards, grants
    monkeypatch.setattr(cards, "request_add", lambda items, **kw: raised.append((items, kw)) or {"ok": True})
    added = []
    monkeypatch.setattr(grants, "add_scope", lambda *a, **k: added.append(a))
    import threading
    done = threading.Event()
    real = kf.pick_folders

    def wrapped(**kw):
        try:
            return real(**kw)
        finally:
            done.set()
    monkeypatch.setattr(kf, "pick_folders", wrapped)
    assert kf.start_voice_pick("owner") == {"started": True}
    assert done.wait(5)
    for _ in range(100):
        if raised:
            break
        threading.Event().wait(0.02)
    assert raised and raised[0][0] == [{"path": str(folder.resolve())}]
    assert raised[0][1]["requested_by"] == "friday" and added == []


# ── the words ────────────────────────────────────────────────────────────────

def test_the_two_kinds_of_access_are_worded_as_in_the_addendum():
    c = kf.COPY
    assert c["local_title"] == "Let {agent} read and index it on this PC."
    assert c["cloud_title"] == "Also let cloud models read it."
    assert "stay here" in c["local_body"] and "Nothing leaves this PC" in c["local_body"]
    assert "30 days" in c["cloud_body"] and "separate permission" in c["cloud_body"]
    assert "never suggests it for a vault or a private folder" in c["cloud_body"]
    assert c["skip"] == "Not now"
    for text in c.values():
        assert "\u2014" not in text                                        # no em dashes in user-facing copy

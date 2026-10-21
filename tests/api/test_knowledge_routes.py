"""The "Your knowledge" routes: the native folder picker, the suggestions and the typed-path check.

The picker route opens a window on the owner's screen, so it answers only the
page's own click (same-origin, the page's session token). No test opens a real
dialog: the subprocess is replaced before the request.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from agent_friday.services import knowledge_folders as kf
from tests.library_fixtures import isolate_library, release_library
from tests.desk_press_support import desk_headers      # a press on this PC's screen (services/desk_press)


class RealDialogOpened(BaseException):
    pass


_REAL_POPEN = subprocess.Popen


def _is_acl_hardening(a, k) -> bool:
    """credential_store.harden_permissions restricting a new key file with icacls: a console
    tool with no window, run whenever this process mints its first ledger signing key. Every
    other process a test here starts is refused as a real folder window."""
    argv = a[0] if a else k.get("args")
    return isinstance(argv, (list, tuple)) and bool(argv) and str(argv[0]).lower() == "icacls"


@pytest.fixture(autouse=True)
def _env(tmp_path, monkeypatch):
    fg, lstore = isolate_library(tmp_path, monkeypatch)
    from agent_friday.services.library import runtime
    monkeypatch.setattr(runtime, "start_background", lambda: None)
    monkeypatch.setattr(runtime, "index_scope", lambda *a, **k: None)      # no indexing, no encoder, in this file
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    monkeypatch.setattr(kf, "documents_dir", lambda: home / "Documents")
    monkeypatch.setattr(kf, "_is_windows", lambda: True)

    def _no_real_dialog(*a, **k):
        if _is_acl_hardening(a, k):
            return _REAL_POPEN(*a, **k)
        raise RealDialogOpened("a test tried to start the real folder window")
    monkeypatch.setattr(kf.subprocess, "Popen", _no_real_dialog)
    yield home
    release_library(fg, lstore)


@pytest.fixture(autouse=True)
def _as_the_page(client):
    import agent_friday.core as core
    client.environ_base.update({"HTTP_SEC_FETCH_SITE": "same-origin", "HTTP_ORIGIN": "http://localhost",
                                "HTTP_X_FRIDAY_TOKEN": core._API_SESSION_TOKEN,
                                # a press on this PC's screen (services/desk_press)
                                "HTTP_X_FRIDAY_DESK": __import__("tests.desk_press_support", fromlist=["x"]).desk_key()})


class _Proc:
    returncode = 0

    def __init__(self, out):
        self._out = out

    def communicate(self, timeout=None):
        return self._out, b""

    def kill(self):
        pass


def _picker_returns(monkeypatch, *paths):
    out = "".join("PATH:%s\r\n" % p for p in paths).encode("utf-8") or b"CANCELLED\r\n"
    opened = []
    monkeypatch.setattr(kf.subprocess, "Popen", lambda argv, **kw: opened.append(argv) or _Proc(out))
    return opened


def test_routes_are_registered(client):
    rules = {str(r.rule) for r in client.application.url_map.iter_rules()}
    for r in ("/api/files/pick-folders", "/api/files/knowledge-suggestions", "/api/files/describe-folder"):
        assert r in rules


def test_the_picker_returns_the_chosen_folders(client, monkeypatch, _env):
    d = _env / "Notes"
    (d / ".obsidian").mkdir(parents=True)
    (d / "a.md").write_text("hello", encoding="utf-8")
    _picker_returns(monkeypatch, d)
    j = client.post("/api/files/pick-folders", json={}).get_json()
    assert j["status"] == "ok" and j["cancelled"] is False and j["refused"] == []
    assert [f["name"] for f in j["folders"]] == ["Notes"] and j["folders"][0]["kind"] == "obsidian"
    assert j["folders"][0]["preselected"] is False and j["folders"][0]["cloud_offer"] is False


def test_the_picker_adds_nothing_to_the_library_or_the_grants(client, monkeypatch, _env):
    d = _env / "Notes"
    d.mkdir()
    _picker_returns(monkeypatch, d)
    client.post("/api/files/pick-folders", json={})
    assert client.get("/api/library/status").get_json()["scopes"] == []
    from agent_friday.services import file_grants as fg
    assert fg.list_grants() == []


def test_unsafe_paths_never_come_back_from_the_picker(client, monkeypatch, _env, tmp_path):
    _picker_returns(monkeypatch, r"\\server\share", tmp_path.anchor, _env)
    j = client.post("/api/files/pick-folders", json={}).get_json()
    assert j["folders"] == [] and len(j["refused"]) == 3


def test_the_picker_only_answers_the_pages_own_click(client, monkeypatch, _env):
    opened = _picker_returns(monkeypatch, _env)
    # another site
    r = client.post("/api/files/pick-folders", json={}, headers={"Origin": "http://evil.example"})
    assert r.status_code == 403
    # a same-origin request with no session token (a bare call, a key, a script)
    client.environ_base.pop("HTTP_X_FRIDAY_TOKEN", None)
    assert client.post("/api/files/pick-folders", json={}).status_code == 403
    # a non-JSON body
    import agent_friday.core as core
    client.environ_base["HTTP_X_FRIDAY_TOKEN"] = core._API_SESSION_TOKEN
    assert client.post("/api/files/pick-folders", data="x", content_type="text/plain").status_code == 415
    assert opened == []                                                   # no window was ever started


def test_a_cross_site_request_gets_nothing_from_the_suggestions(client):
    r = client.get("/api/files/knowledge-suggestions", headers={"Sec-Fetch-Site": "cross-site"})
    assert r.status_code == 403
    client.environ_base.pop("HTTP_X_FRIDAY_TOKEN", None)
    assert client.get("/api/files/knowledge-suggestions").status_code == 403


def test_when_the_window_cannot_open_the_page_is_told_to_type(client, monkeypatch):
    monkeypatch.setattr(kf, "_is_windows", lambda: False)
    j = client.post("/api/files/pick-folders", json={}).get_json()
    assert j["status"] == "unavailable" and j["fallback"] == "type" and "Type the folder" in j["error"]


def test_the_suggestions_carry_estimates_the_words_and_no_preselection(client, _env):
    docs = _env / "Documents"
    docs.mkdir()
    (docs / "Vault" / ".obsidian").mkdir(parents=True)
    (docs / "Vault" / "n.md").write_text("x" * 40, encoding="utf-8")
    j = client.get("/api/files/knowledge-suggestions").get_json()
    assert j["status"] == "ok" and j["copy"]["skip"] == "Not now"
    kinds = {r["kind"]: r for r in j["suggestions"]}
    assert "obsidian" in kinds and kinds["obsidian"]["file_count"] == 1 and kinds["obsidian"]["cloud_offer"] is False
    assert all(r["preselected"] is False for r in j["suggestions"])
    assert "copy" in client.get("/api/files/knowledge-suggestions?copy=only").get_json()
    assert "suggestions" not in client.get("/api/files/knowledge-suggestions?copy=only").get_json()


def test_a_typed_path_passes_the_same_checks(client, _env, tmp_path):
    ok = _env / "Mine"
    ok.mkdir()
    good = client.post("/api/files/describe-folder", json={"path": str(ok)})
    assert good.status_code == 200 and good.get_json()["folder"]["name"] == "Mine"
    for bad in (r"\\server\share\x", tmp_path.anchor, str(_env), "relative\\dir", str(_env / "nope")):
        r = client.post("/api/files/describe-folder", json={"path": bad})
        assert r.status_code == 400 and r.get_json()["error"], bad


def test_recording_a_local_scope_is_the_existing_library_add_and_the_cloud_grant_is_separate(client, _env):
    """What the panel's Save does: a Library scope (source "you") and, only when ticked, a
    separate 30-day folder grant. One does not create the other."""
    d = _env / "Notes"
    d.mkdir()
    (d / "a.md").write_text("hello", encoding="utf-8")
    from agent_friday.services import file_grants as fg
    r = client.post("/api/library/add", json={"path": str(d), "recursive": True})
    assert r.status_code == 200
    assert fg.list_grants() == []                                          # reading here grants the cloud nothing
    ev = [e for e in fg._load_state().library.values()]
    assert ev and ev[0]["source"] == "you" and ev[0]["path"] == str(d.resolve())
    r2 = client.post("/api/privacy/file-grants", json={"path": str(d), "scope": "folder", "expiry_days": 30}, headers=desk_headers())
    assert r2.status_code == 200 and r2.get_json()["grant"]["type"] == "folder"
    assert len(fg.list_grants()) == 1 and len(fg._load_state().library) == 1
    over = client.post("/api/privacy/file-grants", json={"path": str(d), "scope": "folder", "expiry_days": 31}, headers=desk_headers())
    assert over.status_code == 400                                         # never longer than 30 days

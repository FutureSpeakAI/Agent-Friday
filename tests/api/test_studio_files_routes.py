"""The Studio file browser exposes only Friday's browsable folders, serves
nothing as an active document, and changes files only after an approval."""
from __future__ import annotations

import io

import pytest

from agent_friday.services import studio_files as sf


@pytest.fixture(autouse=True)
def _iso(tmp_path, monkeypatch, friday_dir):
    from agent_friday.services import approvals, file_grants as fg
    from agent_friday.services import dissent_gate as dg
    monkeypatch.setattr(fg, "_ledger_path", lambda: friday_dir / "privacy" / "file_grants.jsonl")
    monkeypatch.setattr(approvals, "APPROVALS_FILE", tmp_path / "approvals.json")
    monkeypatch.setattr(dg, "EVENTS_PATH", tmp_path / "dissent_events.jsonl")
    monkeypatch.setattr(sf, "_cache_dir", lambda: _mk(tmp_path / "thumbs"))
    fg._invalidate_cache()
    yield
    fg._invalidate_cache()


def _mk(p):
    p.mkdir(parents=True, exist_ok=True)
    return p


@pytest.fixture
def docs(tmp_path, monkeypatch):
    root = _mk(tmp_path / "Documents")
    other = _mk(tmp_path / "Projects")
    monkeypatch.setattr(sf, "roots", lambda: {"documents": root, "projects": other})
    (root / "photo.png").write_bytes(_png())
    (root / "notes.md").write_text("# Notes\nhello\n", encoding="utf-8")
    (root / "page.html").write_text("<script>alert(1)</script>", encoding="utf-8")
    (root / "tool.exe").write_bytes(b"MZ")
    (root / "run.py").write_text("print(1)\n", encoding="utf-8")
    (root / ".env").write_text("SECRET=1", encoding="utf-8")
    (root / "id_rsa").write_text("key", encoding="utf-8")
    (root / "server.pem").write_text("key", encoding="utf-8")
    _mk(root / "node_modules" / "x").joinpath("a.js").write_text("1", encoding="utf-8")
    _mk(root / ".git").joinpath("HEAD").write_text("ref", encoding="utf-8")
    sub = _mk(root / "Album")
    (sub / "one.jpg").write_bytes(_png("JPEG"))
    (sub / "two.txt").write_text("two", encoding="utf-8")
    return root


def _png(fmt="PNG"):
    from PIL import Image
    buf = io.BytesIO()
    Image.new("RGB", (400, 300), (200, 40, 90)).save(buf, fmt)
    return buf.getvalue()


def _names(resp):
    return {e[0] for e in resp.get_json()["entries"]}


def test_endpoints_are_registered(client):
    rules = {str(r.rule) for r in client.application.url_map.iter_rules()}
    for r in ("/api/studio-files/roots", "/api/studio-files/scan", "/api/studio-files/thumb",
              "/api/studio-files/raw", "/api/studio-files/open", "/api/studio-files/reveal",
              "/api/studio-files/request-change"):
        assert r in rules


def test_scan_lists_files_and_skips_private_ones(client, docs):
    r = client.get("/api/studio-files/scan?root=documents")
    assert r.status_code == 200
    names = _names(r)
    assert {"photo.png", "notes.md", "Album", "Album/one.jpg", "Album/two.txt"} <= names
    for hidden in (".env", "id_rsa", "server.pem", "node_modules", ".git",
                   "node_modules/x/a.js", ".git/HEAD"):
        assert hidden not in names
    album = next(e for e in r.get_json()["entries"] if e[0] == "Album")
    assert album[1] == 1 and album[2] == (docs / "Album" / "one.jpg").stat().st_size + 3


@pytest.mark.parametrize("path", ["../", "..\\..", "Album/../../x", "C:/Windows", "id_rsa", ".env"])
def test_paths_outside_or_private_are_refused(client, docs, path):
    assert client.get(f"/api/studio-files/scan?root=documents&path={path}").status_code == 403
    assert client.get(f"/api/studio-files/raw?root=documents&path={path}").status_code == 403


def test_scan_reports_where_creations_live(client, docs, monkeypatch):
    from agent_friday import core
    monkeypatch.setattr(core, "CREATIONS_DIR", docs / "Album")
    assert client.get("/api/studio-files/scan?root=documents").get_json()["creations_prefix"] == "Album"
    monkeypatch.setattr(core, "CREATIONS_DIR", docs.parent / "elsewhere")
    assert client.get("/api/studio-files/scan?root=documents").get_json()["creations_prefix"] is None


def test_unknown_root_is_refused(client, docs):
    assert client.get("/api/studio-files/scan?root=windows").status_code == 403


def test_friday_home_inside_a_root_is_never_listed(client, docs, monkeypatch):
    home = _mk(docs / "FridayHome")
    (home / "vault.db").write_text("x", encoding="utf-8")
    import os
    monkeypatch.setattr(sf, "_excluded", lambda root_real=None: [os.path.normcase(str(home.resolve()))])
    assert "FridayHome" not in _names(client.get("/api/studio-files/scan?root=documents"))
    assert client.get("/api/studio-files/raw?root=documents&path=FridayHome/vault.db").status_code == 403


def test_a_root_inside_friday_home_is_browsable_but_not_its_vault(client, tmp_path, monkeypatch):
    from agent_friday import core
    from agent_friday.services import file_search
    home = _mk(tmp_path / "fh")
    creations = _mk(home / "friday-creations")
    (creations / "art.png").write_bytes(_png())
    vault = _mk(creations / "vault")
    (vault / "secret.txt").write_text("x", encoding="utf-8")
    monkeypatch.setattr(core, "FRIDAY_DIR", home)
    monkeypatch.setattr(file_search, "_vault_root", lambda: vault)
    monkeypatch.setattr(sf, "roots", lambda: {"creations": creations})
    names = _names(client.get("/api/studio-files/scan?root=creations"))
    assert "art.png" in names
    assert "vault" not in names and "vault/secret.txt" not in names
    assert client.get("/api/studio-files/raw?root=creations&path=vault/secret.txt").status_code == 403


def test_a_copy_of_friday_home_is_hidden(client, docs):
    backup = _mk(docs / "friday-backup" / "dot-friday-data")
    for f in ("SOUL.md", "settings.json", "activity_ledger.jsonl"):
        (backup / f).write_text("x", encoding="utf-8")
    _mk(backup / "vault").joinpath("k.bin").write_bytes(b"k")
    names = _names(client.get("/api/studio-files/scan?root=documents"))
    assert "friday-backup" in names
    assert not any(n.startswith("friday-backup/dot-friday-data") for n in names)
    assert client.get("/api/studio-files/raw?root=documents&path=friday-backup/dot-friday-data/SOUL.md").status_code == 403
    assert client.get("/api/studio-files/scan?root=documents&path=friday-backup/dot-friday-data").status_code == 403


def test_deny_mark_hides_folder_and_its_files(client, docs):
    from agent_friday.services import file_grants as fg
    fg.create_deny_mark(str(docs / "Album"), "folder")
    fg._invalidate_cache()
    names = _names(client.get("/api/studio-files/scan?root=documents"))
    assert "Album" not in names and "Album/one.jpg" not in names
    assert client.get("/api/studio-files/thumb?root=documents&path=Album/one.jpg").status_code == 403
    assert client.get("/api/studio-files/raw?root=documents&path=Album/one.jpg").status_code == 403


def test_thumbnails_are_webp_and_cached(client, docs):
    r = client.get("/api/studio-files/thumb?root=documents&path=photo.png&s=128")
    assert r.status_code == 200 and r.mimetype == "image/webp"
    from PIL import Image
    im = Image.open(io.BytesIO(r.data))
    assert max(im.size) == 128
    etag = r.headers["ETag"]
    again = client.get("/api/studio-files/thumb?root=documents&path=photo.png&s=128",
                       headers={"If-None-Match": etag})
    assert again.status_code == 304
    assert client.get("/api/studio-files/thumb?root=documents&path=notes.md").status_code == 200
    assert client.get("/api/studio-files/thumb?root=documents&path=tool.exe").status_code == 204


def test_raw_never_serves_an_active_document(client, docs):
    r = client.get("/api/studio-files/raw?root=documents&path=page.html")
    assert r.status_code == 200
    assert r.mimetype == "text/plain"
    assert r.headers["X-Content-Type-Options"] == "nosniff"
    assert "sandbox" in r.headers["Content-Security-Policy"]
    img = client.get("/api/studio-files/raw?root=documents&path=photo.png")
    assert img.mimetype == "image/png"
    t = client.get("/api/studio-files/raw?root=documents&path=notes.md&text=1").get_json()
    assert t["text"].startswith("# Notes") and t["binary"] is False


def test_open_launches_only_non_executable_types(client, docs, monkeypatch):
    launched = []
    monkeypatch.setattr(sf.os, "startfile", lambda p: launched.append(p), raising=False)
    monkeypatch.setattr(sf.sys, "platform", "win32")
    for bad in ("tool.exe", "run.py", "page.html"):
        r = client.post("/api/studio-files/open", json={"root": "documents", "path": bad})
        assert r.status_code == 403
    assert launched == []
    ok = client.post("/api/studio-files/open", json={"root": "documents", "path": "photo.png"})
    assert ok.status_code == 200 and launched and launched[0].endswith("photo.png")


def test_posts_refuse_cross_origin_and_non_json(client, docs, monkeypatch):
    monkeypatch.setattr(sf, "reveal", lambda *a: pytest.fail("must not run"))
    r = client.post("/api/studio-files/reveal", json={"root": "documents", "path": "photo.png"},
                    headers={"Origin": "https://evil.example"})
    assert r.status_code == 403
    r = client.post("/api/studio-files/reveal", data="root=documents&path=photo.png",
                    content_type="application/x-www-form-urlencoded")
    assert r.status_code == 415


def _request(client, **body):
    r = client.post("/api/studio-files/request-change", json=body)
    assert r.status_code == 200, r.get_json()
    j = r.get_json()
    assert j["status"] == "pending" and j["approval_id"]
    return j["approval_id"]


def test_delete_waits_for_approval_then_recycles(client, docs, monkeypatch):
    recycled = []
    monkeypatch.setattr(sf, "_recycle", lambda p: recycled.append(p.name))
    aid = _request(client, op="delete", root="documents", path="notes.md")
    assert recycled == [] and (docs / "notes.md").exists()
    d = client.post(f"/api/approvals/{aid}/decide", json={"decision": "approve"})
    assert d.status_code == 200
    assert recycled == ["notes.md"]
    from agent_friday.services import approvals
    rec = approvals.get(aid) if hasattr(approvals, "get") else None
    rec = rec or client.get(f"/api/approvals/{aid}").get_json()
    rec = rec.get("approval", rec)
    assert rec["consumed"] and rec["used_detail"]["ok"]


def test_denied_change_touches_nothing(client, docs, monkeypatch):
    monkeypatch.setattr(sf, "_recycle", lambda p: pytest.fail("must not recycle"))
    aid = _request(client, op="delete", root="documents", path="notes.md")
    client.post(f"/api/approvals/{aid}/decide", json={"decision": "deny"})
    assert (docs / "notes.md").exists()


def test_rename_and_move_run_only_after_approval(client, docs):
    aid = _request(client, op="rename", root="documents", path="Album/two.txt", new_name="deux.txt")
    assert (docs / "Album" / "two.txt").exists()
    client.post(f"/api/approvals/{aid}/decide", json={"decision": "approve"})
    assert (docs / "Album" / "deux.txt").exists() and not (docs / "Album" / "two.txt").exists()
    aid = _request(client, op="move", root="documents", path="Album/deux.txt",
                   dest_root="documents", dest_path="")
    client.post(f"/api/approvals/{aid}/decide", json={"decision": "approve"})
    assert (docs / "deux.txt").exists()


def test_copy_runs_only_after_approval_and_never_overwrites(client, docs):
    aid = _request(client, op="copy", root="documents", path="notes.md", dest_root="documents", dest_path="")
    assert not (docs / "notes (copy).md").exists()
    client.post(f"/api/approvals/{aid}/decide", json={"decision": "approve"})
    assert (docs / "notes (copy).md").read_text(encoding="utf-8") == (docs / "notes.md").read_text(encoding="utf-8")
    aid = _request(client, op="copy", root="documents", path="notes.md", dest_root="documents", dest_path="Album")
    client.post(f"/api/approvals/{aid}/decide", json={"decision": "approve"})
    assert (docs / "Album" / "notes.md").exists()
    r = client.post("/api/studio-files/request-change",
                    json={"op": "copy", "root": "documents", "path": "Album", "dest_root": "documents", "dest_path": ""})
    assert r.status_code == 403


@pytest.mark.parametrize("name", ["../x.txt", ".hidden", "a/b", "evil.pem", ""])
def test_rename_rejects_bad_names(client, docs, name):
    r = client.post("/api/studio-files/request-change",
                    json={"op": "rename", "root": "documents", "path": "notes.md", "new_name": name})
    assert r.status_code == 403


def test_top_level_root_cannot_be_changed(client, docs):
    r = client.post("/api/studio-files/request-change",
                    json={"op": "delete", "root": "documents", "path": ""})
    assert r.status_code == 403

"""API tests for H4 (vault-passphrase arming) and H6 (data export/erase).

The export/erase routes operate on the module-level insights.FRIDAY_DIR. These
tests monkeypatch it to an isolated tmp dir so erase can't touch the shared
session home (and so the assertions don't depend on the api-conftest home-
aliasing that makes the `friday_dir` fixture point at a different temp home
than the route resolves at import time).
"""
from __future__ import annotations

import io
import zipfile

import pytest

from agent_friday.routes import insights as ins


@pytest.fixture
def route_home(tmp_path, monkeypatch):
    home = tmp_path / ".friday"
    home.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(ins, "FRIDAY_DIR", home)
    return home


class TestVaultPassphrase:
    @pytest.fixture(autouse=True)
    def isolated_credentials(self, monkeypatch, tmp_path):
        # Redirecting HOME does not isolate the operating system keychain.
        # Exercise the real save route with a process-local credential store.
        import sys
        import types
        from agent_friday.services import vault_passphrase as vp
        values = {}
        fake = types.SimpleNamespace(
            set_password=lambda service, account, value: values.__setitem__((service, account), value),
            get_password=lambda service, account: values.get((service, account)))
        monkeypatch.setitem(sys.modules, "keyring", fake)
        monkeypatch.setattr(vp, "file_homes", lambda: [tmp_path / "vault-credential"])
        vp.reset_cache()
        yield
        vp.reset_cache()

    def test_arm_passphrase_ok(self, client):
        r = client.post("/api/vault/passphrase", json={"passphrase": "correct horse battery"})
        assert r.status_code == 200
        assert r.get_json()["status"] == "ok"

    def test_arming_a_passphrase_drops_the_parsed_wiki(self, client):
        # The cached wiki index holds summaries read with the old key; a new
        # passphrase must not leave them in memory until the next query.
        from agent_friday.services.knowledge_graph import wiki_graph
        with wiki_graph._index_cache_lock:
            wiki_graph._index_cache.update(key=("primed",), index={"page": {}})
        r = client.post("/api/vault/passphrase", json={"passphrase": "correct horse battery"})
        assert r.status_code == 200
        assert wiki_graph._index_cache["index"] is None

    def test_too_short_rejected(self, client):
        r = client.post("/api/vault/passphrase", json={"passphrase": "abc"})
        assert r.status_code == 400

    def test_missing_rejected(self, client):
        assert client.post("/api/vault/passphrase", json={}).status_code == 400


class TestDataExport:
    def test_export_returns_zip(self, client, route_home):
        (route_home / "settings.json").write_text('{"agent_name":"Friday"}', encoding="utf-8")
        r = client.get("/api/data/export")
        assert r.status_code == 200
        assert r.mimetype == "application/zip"
        names = zipfile.ZipFile(io.BytesIO(r.data)).namelist()
        assert any(n.endswith("settings.json") for n in names)

    def test_export_skips_cache_dirs(self, client, route_home):
        (route_home / "settings.json").write_text("{}", encoding="utf-8")
        cache = route_home / "audio-cache"
        cache.mkdir(parents=True, exist_ok=True)
        (cache / "clip.wav").write_bytes(b"RIFF")
        r = client.get("/api/data/export")
        names = zipfile.ZipFile(io.BytesIO(r.data)).namelist()
        assert not any("audio-cache" in n for n in names)


class TestDataErase:
    def test_erase_requires_confirm_token(self, client, route_home):
        assert client.post("/api/data/erase", json={}).status_code == 400

    def test_erase_wrong_token_rejected(self, client, route_home):
        assert client.post("/api/data/erase", json={"confirm": "yes"}).status_code == 400

    def test_erase_with_token_removes_dir(self, client, route_home):
        (route_home / "marker.txt").write_text("x", encoding="utf-8")
        assert route_home.exists()
        r = client.post("/api/data/erase", json={"confirm": "ERASE"})
        assert r.status_code == 200
        assert r.get_json()["erased"] is True
        assert not route_home.exists()

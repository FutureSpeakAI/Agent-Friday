"""North star §34.4-6: a cloud key goes in through the secure field and
nowhere else.

The secure field posts to POST /api/providers/<name>/key. After a key goes in
that way it must be stored protected (never readable on disk), and it must not
come back out: not in that response, not in any settings or provider
listing, not in settings.json, not in Friday's logs or any other file the save
touched. A key typed into the setup chat is refused there
(tests/api/test_setup_chat_routes.py); this is the other half.
"""
from __future__ import annotations

import logging
import secrets
import time
from pathlib import Path

import pytest

pytestmark = pytest.mark.real_provider_paths


def _files_touched_since(root: Path, since: float, skip: set) -> list:
    out = []
    for p in root.rglob("*"):
        try:
            if p.is_file() and p not in skip and p.stat().st_mtime >= since - 1 \
                    and p.stat().st_size < 20_000_000:
                out.append(p)
        except OSError:
            continue
    return out


def test_a_key_through_the_secure_field_is_stored_protected_and_never_comes_back(client, caplog):
    import agent_friday.core as core
    from agent_friday.services import credential_store as cs
    decoy = "sk-ant-api03-" + secrets.token_hex(20) + "-SECUREFIELDDECOY"
    caplog.set_level(logging.DEBUG)
    started = time.time()
    try:
        r = client.post("/api/providers/anthropic/key", json={"key": decoy})
        assert r.status_code == 200
        body = r.get_data(as_text=True)
        assert decoy not in body and decoy[-12:] not in body, "the save response echoed the key"
        assert r.get_json()["status"] == "connected"

        # Stored protected: the file on disk is a protected blob, not the key.
        key_file = cs._provider_key_path("anthropic")
        blob = key_file.read_bytes()
        assert decoy.encode() not in blob, "the key is readable on disk"
        assert cs.looks_protected(blob), "the stored key is not in a protected form"
        assert cs.get_provider_key("anthropic") == decoy, "the stored key does not read back"

        # Nothing that lists settings or providers carries it.
        for path in ("/api/settings", "/api/providers", "/api/health"):
            resp = client.get(path)
            assert resp.status_code == 200, path
            text = resp.get_data(as_text=True)
            assert decoy not in text and decoy[-12:] not in text, "%s echoed the key" % path

        # No file the save touched holds it: settings.json, friday.log, receipts.
        home = Path(core.FRIDAY_DIR)
        touched = _files_touched_since(home, started, skip={key_file})
        settings_file = Path(core.SETTINGS_FILE)
        if settings_file.exists():
            touched.append(settings_file)
        for p in touched:
            try:
                data = p.read_bytes()
            except OSError:
                continue
            assert decoy.encode() not in data, "%s holds the key in the clear" % p

        # And no log line, at any level, carried it.
        for rec in caplog.records:
            assert decoy not in rec.getMessage(), "a log line carried the key: %s" % rec.name
    finally:
        client.delete("/api/providers/anthropic/key")
    assert decoy not in client.get("/api/providers").get_data(as_text=True)

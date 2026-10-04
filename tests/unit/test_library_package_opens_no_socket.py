"""The Library opens no network connection and imports no HTTP client of its own.
The one thing that talks to a model is `answer.py`, through the local-call
module, to the model seat on this PC."""
from __future__ import annotations

import re
import socket
from pathlib import Path

from tests.library_fixtures import install_fake_encoder, isolate_library, make_pdf, release_library, write_docs

PKG = Path(__file__).resolve().parents[2] / "src" / "agent_friday" / "services" / "library"
BANNED = re.compile(r"^\s*(?:import|from)\s+(requests|urllib3|httpx|aiohttp|http\.client|urllib\.request|socket|websocket|ftplib|smtplib)\b",
                    re.M)


def test_no_module_of_the_package_imports_an_http_client_or_a_socket():
    hits = [(f.name, m.group(1)) for f in PKG.glob("*.py") for m in BANNED.finditer(f.read_text(encoding="utf-8"))]
    assert hits == [], hits


def test_a_full_index_and_search_opens_no_connection(tmp_path, monkeypatch):
    fg, lstore = isolate_library(tmp_path, monkeypatch)
    try:
        install_fake_encoder(monkeypatch)
        from agent_friday.services.library import grants, indexer, search, shelf
        from agent_friday.services.library.store import store_for
        monkeypatch.setattr(shelf, "_vault_key", lambda: None)
        monkeypatch.setattr(shelf, "tier_of", lambda title, sample: 1)

        def refuse(*a, **k):
            raise AssertionError("the Library tried to open a connection")

        monkeypatch.setattr(socket.socket, "connect", refuse)
        monkeypatch.setattr(socket.socket, "connect_ex", refuse)
        monkeypatch.setattr(socket, "create_connection", refuse)
        monkeypatch.setattr(socket, "getaddrinfo", refuse)
        root = tmp_path / "Lib"
        write_docs(root, {"a.pdf": make_pdf([["# Lease", "Ninety days notice."]]), "b.md": "# Notes\n\nGardens.\n"})
        grants.add_scope("owner", str(root))
        st = store_for("owner")
        indexer.sweep_scope(st, root, allowed=lambda p: grants.allowed("owner", p))
        assert search.run("how much notice")["evidence"]
    finally:
        release_library(fg, lstore)


def test_the_encoder_runs_offline_with_telemetry_off():
    import os
    from agent_friday.services.library import embed  # noqa: F401  (importing it sets the offline flag)
    assert os.environ.get("HF_HUB_OFFLINE") == "1"
    assert "disable_telemetry_events" in (PKG / "embed.py").read_text(encoding="utf-8")

"""The Library opens no network connection and imports no HTTP client of its own.
The one thing that talks to a model is `answer.py`, through the local-call
module, to the model seat on this PC."""
from __future__ import annotations

import ast
import socket
from pathlib import Path

from tests.library_fixtures import install_fake_encoder, isolate_library, make_pdf, release_library, write_docs

PKG = Path(__file__).resolve().parents[2] / "src" / "agent_friday" / "services" / "library"
BANNED_MODULES = ("requests", "urllib3", "httpx", "aiohttp", "http.client", "urllib.request", "socket", "websocket",
                  "websockets", "ftplib", "smtplib", "telnetlib", "imaplib", "poplib", "xmlrpc.client", "ssl")


def _imported_names(tree: ast.AST) -> set[str]:
    """Every module a source file can load: import statements in any form (several names on one line,
    `from urllib import request`, inside a function), and `__import__` / `import_module` with a literal."""
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom):
            base = node.module or ""
            names.add(base)
            names.update(f"{base}.{a.name}" for a in node.names)
        elif isinstance(node, ast.Call):
            fn = node.func
            called = getattr(fn, "id", None) or getattr(fn, "attr", None)
            if called in ("__import__", "import_module") and node.args and isinstance(node.args[0], ast.Constant):
                names.add(str(node.args[0].value))
    return names


def _banned(name: str) -> bool:
    return any(name == b or name.startswith(b + ".") for b in BANNED_MODULES)


def test_no_module_of_the_package_imports_an_http_client_or_a_socket():
    hits = [(f.name, n) for f in sorted(PKG.glob("*.py"))
            for n in sorted(_imported_names(ast.parse(f.read_text(encoding="utf-8")))) if _banned(n)]
    assert hits == [], hits


def test_the_detector_sees_every_way_to_load_a_network_module():
    source = ("import json, socket\n"
              "from urllib import request\n"
              "x = __import__('http.client')\n"
              "import importlib\n"
              "y = importlib.import_module('ssl')\n"
              "def f():\n"
              "    import requests as r\n")
    found = sorted(n for n in _imported_names(ast.parse(source)) if _banned(n))
    assert found == ["http.client", "requests", "socket", "ssl", "urllib.request"], found


def test_the_only_process_the_package_starts_is_its_own_worker():
    users = sorted(f.name for f in PKG.glob("*.py")
                   if any(n in ("subprocess", "os.system", "os.popen") or n.startswith("subprocess.")
                          for n in _imported_names(ast.parse(f.read_text(encoding="utf-8")))))
    assert users == ["procrun.py"], users
    src = (PKG / "procrun.py").read_text(encoding="utf-8")
    for word in ("curl", "wget", "Invoke-WebRequest", "powershell"):
        assert word not in src, word


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

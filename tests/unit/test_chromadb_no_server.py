"""ChromaDB stays embedded: the property that makes its advisories unreachable.

Four Dependabot alerts (two critical) are open against chromadb 1.5.9, which is
also the newest release upstream has published, so there is no patched version
to move to. All four are in ChromaDB's client/server deployment: the HTTP API
routes and the authorisation providers guarding them. Friday constructs only an
in-process `PersistentClient` and passes its embedding function explicitly, so
none of that code runs. docs/security/dependency-advisories.md has the detail.

These tests pin the property. If a change adds an HttpClient, starts a Chroma
server, or reads `collection.configuration` (which rebuilds an embedding function,
and its `trust_remote_code` argument, from data on disk), they fail.

Source-level on purpose: no chromadb import, so they run where the memory tier is
not installed.
"""
from __future__ import annotations

from pathlib import Path

import pytest

_SRC = Path(__file__).resolve().parents[2] / "src" / "agent_friday"
_MEMORY = _SRC / "conversation_memory.py"


def _python_sources():
    return sorted(_SRC.rglob("*.py"))


class TestChromaStaysEmbedded:
    def test_memory_module_exists(self):
        assert _MEMORY.is_file(), f"expected {_MEMORY} to exist"

    def test_uses_persistent_client(self):
        assert "chromadb.PersistentClient(" in _MEMORY.read_text(encoding="utf-8")

    @pytest.mark.parametrize("banned", ["HttpClient", "chromadb.Client("])
    def test_no_server_backed_client_anywhere(self, banned):
        """A server-backed client would put the vulnerable API surface in reach."""
        hits = [p.name for p in _python_sources() if banned in p.read_text(encoding="utf-8")]
        assert hits == [], f"{banned} appeared in {hits}"

    @pytest.mark.parametrize("banned", ["chromadb.server", "chroma_server", "trust_remote_code"])
    def test_no_server_or_remote_code_references(self, banned):
        hits = [p.name for p in _python_sources() if banned in p.read_text(encoding="utf-8")]
        assert hits == [], f"{banned} appeared in {hits}"

    def test_embedding_function_is_passed_not_rehydrated(self):
        """Passing the embedding function keeps build_from_config() out of the path."""
        src = _MEMORY.read_text(encoding="utf-8")
        assert "embedding_function=embed_fn" in src
        assert ".configuration" not in src

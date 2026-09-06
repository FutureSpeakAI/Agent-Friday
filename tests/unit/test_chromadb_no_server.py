"""ChromaDB stays embedded — the property that makes its CVEs unreachable.

Four Dependabot alerts (two critical) sit permanently open against chromadb,
because 1.5.9 is simultaneously the version we pin and the newest release
upstream has ever published — there is no patched version to move to. All four
live in ChromaDB's client/server deployment: the `/api/v2/tenants/...` HTTP
routes and the auth providers guarding them. None of it is reachable here,
because Friday only ever constructs an in-process `PersistentClient` and never
reads the persisted collection configuration back.

That is the whole argument, and until now it was a fact about today's code
rather than one the suite defended. These tests pin it. If a future change adds
an HttpClient, starts a Chroma server, or begins reading
`collection.configuration` (the path that rebuilds an embedding function — and
therefore its `trust_remote_code` kwarg — out of on-disk data), the reasoning in
docs/audits/dependency-security-2026-09-01.md stops holding and this fails.

Deliberately source-level: no chromadb import, so it runs in CI, which does not
install the memory tier.
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
        """Passing the EF explicitly is what keeps build_from_config() out of the path.

        Reading `.configuration` would rebuild the embedding function from the
        on-disk config instead, forwarding whatever kwargs it carries.
        """
        src = _MEMORY.read_text(encoding="utf-8")
        assert "embedding_function=embed_fn" in src
        assert ".configuration" not in src

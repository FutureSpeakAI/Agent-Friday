"""Phase 0 — manifest delta: canonical keys, change detection, idempotency."""

from pathlib import Path

from agent_friday.services.knowledge_graph.store import (
    KnowledgeGraphManifest, canonical, fingerprint)


def _write(p: Path, text: str) -> Path:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")
    return p


class TestCanonical:
    def test_expands_home_and_absolutizes(self, tmp_path, monkeypatch):
        monkeypatch.setenv("HOME", str(tmp_path))
        monkeypatch.setenv("USERPROFILE", str(tmp_path))
        assert canonical("~/x.md") == canonical(str(tmp_path / "x.md"))

    def test_same_file_two_spellings_one_key(self, tmp_path):
        f = _write(tmp_path / "a" / "x.md", "hi")
        assert canonical(f) == canonical(str(tmp_path / "a" / ".." / "a" / "x.md"))


class TestDelta:
    def test_new_changed_unchanged_removed(self, tmp_path):
        kg_dir = tmp_path / "kg"
        a = _write(tmp_path / "src" / "a.md", "alpha")
        b = _write(tmp_path / "src" / "b.md", "beta")

        m = KnowledgeGraphManifest(base_dir=kg_dir)
        d = m.delta([a, b])
        assert set(d["new"]) == {canonical(a), canonical(b)}

        m.record(a, kind="wiki", produced=["page:a"])
        m.record(b, kind="wiki", produced=["page:b"])
        m.save()

        # No change → all unchanged (reindex delta after no change = no-op).
        m2 = KnowledgeGraphManifest(base_dir=kg_dir)
        d = m2.delta([a, b])
        assert d["new"] == [] and d["changed"] == [] and d["removed"] == []
        assert set(d["unchanged"]) == {canonical(a), canonical(b)}

        # Content change is detected even if mtime were unreliable.
        _write(a, "alpha CHANGED")
        d = m2.delta([a, b])
        assert d["changed"] == [canonical(a)]
        assert d["unchanged"] == [canonical(b)]

        # A source that disappears from the scan is reported removed.
        d = m2.delta([a])
        assert d["removed"] == [canonical(b)]

    def test_record_is_idempotent(self, tmp_path):
        kg_dir = tmp_path / "kg"
        a = _write(tmp_path / "src" / "a.md", "alpha")
        m = KnowledgeGraphManifest(base_dir=kg_dir)
        m.record(a, kind="wiki", produced=["page:a"])
        m.record(a, kind="wiki", produced=["page:a"])
        assert len(m.sources) == 1
        assert m.sources[canonical(a)]["produced"] == ["page:a"]

    def test_fingerprint_missing_file_is_none(self, tmp_path):
        assert fingerprint(tmp_path / "ghost.md") is None

    def test_corrupt_manifest_recovers_empty(self, tmp_path):
        kg_dir = tmp_path / "kg"
        kg_dir.mkdir()
        (kg_dir / ".manifest.json").write_text("{not json", encoding="utf-8")
        m = KnowledgeGraphManifest(base_dir=kg_dir)
        assert m.sources == {}

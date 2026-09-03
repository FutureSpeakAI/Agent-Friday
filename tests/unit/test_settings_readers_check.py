"""Unit tests for scripts/check_settings_readers.py -- the static guard
against a settings control that writes a key nothing reads (docs/design/
security-boundary.md #18).

WHY THIS FILE EXISTS, NOT JUST THE TWO CODE FIXES: `test_real_tree_is_clean`
is what makes the guard's job durable. The two bugs it was built to catch
(`egress_mode`, the top-level `vault_local_only`) are gone now -- but a
checker that only ever ran once, by hand, at the moment it was written,
protects nothing going forward. This is the test that fails the next time
someone wires a Privacy-tab control to a key that never reaches
DEFAULT_SETTINGS, or edits one HTML file's copy of a control without the
other.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

SCRIPTS_DIR = Path(__file__).resolve().parent.parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))
import check_settings_readers as checker  # noqa: E402


class TestObjectLiteralKeyExtraction:
    def test_flat_object(self):
        assert checker._keys_from_object_literal(
            "{ foo: 1, bar: 2 }") == ["foo", "bar"]

    def test_quoted_keys(self):
        assert checker._keys_from_object_literal(
            "{ 'foo': 1, \"bar\": 2 }") == ["foo", "bar"]

    def test_one_level_of_nesting_is_dotted(self):
        keys = checker._keys_from_object_literal(
            "{ model_routing: { ...(s.model_routing||{}), "
            "vault_local_only: false } }")
        assert "model_routing" in keys
        assert "model_routing.vault_local_only" in keys

    def test_spread_only_entry_names_no_key(self):
        # No `key:` prefix on a bare spread -- nothing to extract from it.
        keys = checker._keys_from_object_literal("{ ...rest, foo: 1 }")
        assert keys == ["foo"]

    def test_string_and_template_literals_do_not_confuse_depth(self):
        keys = checker._keys_from_object_literal(
            "{ label: `a, b: {c}`, egress_mode: m }")
        assert keys == ["label", "egress_mode"]

    def test_not_an_object_literal_yields_nothing(self):
        assert checker._keys_from_object_literal("patch") == []


class TestWrittenKeyExtraction:
    def test_finds_save_and_save_agent_settings_calls(self, tmp_path):
        f = tmp_path / "page.html"
        f.write_text(
            "onClick={()=>save({egress_mode:m})}\n"
            "onChange={()=>saveAgentSettings({voice_engine:v})}\n",
            encoding="utf-8")
        keys = checker.extract_written_keys(f)
        assert set(keys) == {"egress_mode", "voice_engine"}

    def test_records_line_numbers(self, tmp_path):
        f = tmp_path / "page.html"
        f.write_text("\n\nsave({foo:1})\n", encoding="utf-8")
        keys = checker.extract_written_keys(f)
        assert keys["foo"] == [3]

    def test_unrelated_function_named_save_like_is_ignored(self, tmp_path):
        f = tmp_path / "page.html"
        f.write_text("saveToClipboard({text:1})\n", encoding="utf-8")
        # `saveToClipboard(` does not match the `save(`/`saveAgentSettings(`
        # boundary (word boundary + exact name) -- nothing should be found.
        keys = checker.extract_written_keys(f)
        assert keys == {}


class TestDefaultSettingsKeys:
    def test_real_tree_has_known_keys(self):
        keys = checker.default_settings_keys()
        assert "model_routing" in keys
        assert "knowledge_graph" in keys
        assert "egress_mode" not in keys
        assert "vault_local_only" not in keys  # only the nested form exists


class TestCheckLogic:
    def test_flags_a_key_absent_from_default_settings(self, tmp_path, monkeypatch):
        core = tmp_path / "core.py"
        core.write_text("DEFAULT_SETTINGS = {'model_routing': {}}\n",
                        encoding="utf-8")
        idx = tmp_path / "index.html"
        idx.write_text("save({ghost_key:1})\n", encoding="utf-8")
        app = tmp_path / "app.html"
        app.write_text("save({ghost_key:1})\n", encoding="utf-8")

        monkeypatch.setattr(checker, "ROOT", tmp_path)
        monkeypatch.setattr(checker, "CORE_INIT", core)
        monkeypatch.setattr(checker, "SRC", tmp_path / "nosrc")
        monkeypatch.setattr(checker, "HTML_FILES", ("index.html", "app.html"))
        (tmp_path / "nosrc").mkdir()

        violations = checker.check()
        assert any("ghost_key" in v and "DEFAULT_SETTINGS" in v
                   for v in violations)

    def test_flags_a_key_with_no_python_reader(self, tmp_path, monkeypatch):
        core = tmp_path / "core.py"
        core.write_text("DEFAULT_SETTINGS = {'orphan_key': True}\n",
                        encoding="utf-8")
        idx = tmp_path / "index.html"
        idx.write_text("save({orphan_key:1})\n", encoding="utf-8")
        app = tmp_path / "app.html"
        app.write_text("save({orphan_key:1})\n", encoding="utf-8")
        src = tmp_path / "src"
        src.mkdir()
        (src / "reader.py").write_text("x = 1  # never mentions the key\n",
                                       encoding="utf-8")

        monkeypatch.setattr(checker, "ROOT", tmp_path)
        monkeypatch.setattr(checker, "CORE_INIT", core)
        monkeypatch.setattr(checker, "SRC", src)
        monkeypatch.setattr(checker, "HTML_FILES", ("index.html", "app.html"))

        violations = checker.check()
        assert any("orphan_key" in v and "nowhere under src" in v
                   for v in violations)

    def test_flags_files_that_disagree(self, tmp_path, monkeypatch):
        core = tmp_path / "core.py"
        core.write_text("DEFAULT_SETTINGS = {'shared_key': True}\n",
                        encoding="utf-8")
        idx = tmp_path / "index.html"
        idx.write_text("save({shared_key:1})\n", encoding="utf-8")
        app = tmp_path / "app.html"
        app.write_text("// nothing here\n", encoding="utf-8")
        src = tmp_path / "src"
        src.mkdir()
        (src / "reader.py").write_text('s.get("shared_key")\n', encoding="utf-8")

        monkeypatch.setattr(checker, "ROOT", tmp_path)
        monkeypatch.setattr(checker, "CORE_INIT", core)
        monkeypatch.setattr(checker, "SRC", src)
        monkeypatch.setattr(checker, "HTML_FILES", ("index.html", "app.html"))

        violations = checker.check()
        assert any("shared_key" in v and "disagree" in v for v in violations)

    def test_clean_tree_is_silent(self, tmp_path, monkeypatch):
        core = tmp_path / "core.py"
        core.write_text("DEFAULT_SETTINGS = {'real_key': True}\n",
                        encoding="utf-8")
        idx = tmp_path / "index.html"
        idx.write_text("save({real_key:1})\n", encoding="utf-8")
        app = tmp_path / "app.html"
        app.write_text("save({real_key:1})\n", encoding="utf-8")
        src = tmp_path / "src"
        src.mkdir()
        (src / "reader.py").write_text('s.get("real_key")\n', encoding="utf-8")

        monkeypatch.setattr(checker, "ROOT", tmp_path)
        monkeypatch.setattr(checker, "CORE_INIT", core)
        monkeypatch.setattr(checker, "SRC", src)
        monkeypatch.setattr(checker, "HTML_FILES", ("index.html", "app.html"))

        assert checker.check() == []

    def test_allowlisted_key_is_never_flagged(self, tmp_path, monkeypatch):
        core = tmp_path / "core.py"
        core.write_text("DEFAULT_SETTINGS = {}\n", encoding="utf-8")
        idx = tmp_path / "index.html"
        idx.write_text("save({personality:1})\n", encoding="utf-8")
        app = tmp_path / "app.html"
        app.write_text("// nothing\n", encoding="utf-8")
        src = tmp_path / "src"
        src.mkdir()

        monkeypatch.setattr(checker, "ROOT", tmp_path)
        monkeypatch.setattr(checker, "CORE_INIT", core)
        monkeypatch.setattr(checker, "SRC", src)
        monkeypatch.setattr(checker, "HTML_FILES", ("index.html", "app.html"))

        assert checker.check() == []


# ── The regression guard that matters: the REAL tree, right now ────────────
def test_real_tree_is_clean():
    """Calibration: this failed with 4 violations (egress_mode absent from
    DEFAULT_SETTINGS; the top-level vault_local_only absent from
    DEFAULT_SETTINGS and disagreeing between the two files; the nested
    model_routing.vault_local_only written by only one file) before the
    fixes in this same change landed. If this test ever goes red again, a
    Privacy-tab control has been wired to a key that will not survive the
    next reload."""
    violations = checker.check()
    assert violations == [], "\n".join(violations)


def test_main_exits_zero_on_the_real_tree():
    assert checker.main() == 0

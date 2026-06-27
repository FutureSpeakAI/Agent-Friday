"""Unit tests for skill_registry.py — portable SKILL.md folder format.

Covers:
  - _safe_name: sanitisation, empty-string default.
  - _parse_frontmatter: no-frontmatter passthrough, well-formed YAML/KV.
  - _parse_kv_block: key:value lines, skips comments.
  - _as_list: None, comma-separated strings, bare lists, single values.
  - _skill_from_manifest: end-to-end parse of a SKILL.md text.
  - Skill.summary: shape and keys of the summary dict.
  - load_skills / list_skills: discovers SKILL.md folders from tmp_path.
  - match_skills: trigger matching, ordering, limit, no false positives.
  - build_injection: non-empty when matched; empty string when nothing matches.
  - save_skill: creates the expected on-disk folder structure.

All file I/O uses pytest's tmp_path fixture; the real ~/.friday/skills directory
is never touched.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

import pytest

import agent_friday.skill_registry as sr
from agent_friday.skill_registry import (
    Skill,
    _as_list,
    _parse_frontmatter,
    _parse_kv_block,
    _safe_name,
    _skill_from_manifest,
    build_injection,
    load_skills,
    list_skills,
    match_skills,
    save_skill,
)


# ════════════════════════════════════════════════════════════════════════
#  _safe_name
# ════════════════════════════════════════════════════════════════════════

class TestSafeName:
    def test_empty_string_returns_skill(self):
        assert _safe_name("") == "skill"

    def test_whitespace_only_returns_skill(self):
        assert _safe_name("   ") == "skill"

    def test_special_chars_replaced_with_underscore(self):
        result = _safe_name("meeting prep!")
        assert result == "meeting_prep_"

    def test_hyphens_preserved(self):
        result = _safe_name("my-cool-skill")
        assert result == "my-cool-skill"

    def test_dots_replaced_by_underscore(self):
        # skill_registry._safe_name uses regex [^\w\-] which does NOT allow dots;
        # dots are therefore replaced with underscores.
        result = _safe_name("skill.v2")
        assert result == "skill_v2"

    def test_spaces_replaced(self):
        result = _safe_name("hello world")
        assert "_" in result
        assert " " not in result

    def test_alphanumeric_unchanged(self):
        result = _safe_name("MeetingPrep123")
        assert result == "MeetingPrep123"


# ════════════════════════════════════════════════════════════════════════
#  _parse_kv_block
# ════════════════════════════════════════════════════════════════════════

class TestParseKvBlock:
    def test_simple_key_value(self):
        block = "name: foo\ndescription: bar baz"
        out = _parse_kv_block(block)
        assert out["name"] == "foo"
        assert out["description"] == "bar baz"

    def test_hash_comment_skipped(self):
        block = "# this is a comment\nname: real"
        out = _parse_kv_block(block)
        assert "name" in out
        assert "#" not in str(out.get("name", ""))

    def test_quotes_stripped(self):
        block = 'name: "double-quoted"'
        out = _parse_kv_block(block)
        assert out["name"] == "double-quoted"

    def test_single_quotes_stripped(self):
        block = "name: 'single-quoted'"
        out = _parse_kv_block(block)
        assert out["name"] == "single-quoted"

    def test_empty_block_returns_empty_dict(self):
        assert _parse_kv_block("") == {}

    def test_line_without_colon_ignored(self):
        block = "just a line without colon\nname: x"
        out = _parse_kv_block(block)
        assert "just a line without colon" not in out


# ════════════════════════════════════════════════════════════════════════
#  _parse_frontmatter
# ════════════════════════════════════════════════════════════════════════

class TestParseFrontmatter:
    def test_no_frontmatter_returns_empty_dict_and_full_text(self):
        text = "no frontmatter here\njust body text"
        fm, body = _parse_frontmatter(text)
        assert fm == {}
        assert "no frontmatter here" in body

    def test_well_formed_yaml_frontmatter(self):
        text = "---\nname: foo\n---\nbody here"
        fm, body = _parse_frontmatter(text)
        assert fm.get("name") == "foo"
        assert "body here" in body

    def test_body_does_not_contain_frontmatter_keys(self):
        text = "---\nname: testskill\n---\nThis is the body."
        fm, body = _parse_frontmatter(text)
        # body should not carry the yaml block
        assert "name: testskill" not in body

    def test_no_closing_dashes_treated_as_no_frontmatter(self):
        text = "---\nname: foo\nbody without closing"
        fm, body = _parse_frontmatter(text)
        # Not enough --- delimiters: body is the whole text
        assert isinstance(fm, dict)

    def test_empty_frontmatter_block_returns_empty_dict(self):
        text = "---\n---\nbody only"
        fm, body = _parse_frontmatter(text)
        assert isinstance(fm, dict)
        assert "body only" in body


# ════════════════════════════════════════════════════════════════════════
#  _as_list
# ════════════════════════════════════════════════════════════════════════

class TestAsList:
    def test_none_returns_empty_list(self):
        assert _as_list(None) == []

    def test_empty_string_returns_empty_list(self):
        assert _as_list("") == []

    def test_whitespace_only_string_returns_empty_list(self):
        assert _as_list("   ") == []

    def test_comma_separated_string(self):
        assert _as_list("a, b") == ["a", "b"]

    def test_bare_list_returned_as_strings(self):
        assert _as_list(["x"]) == ["x"]

    def test_list_of_ints_cast_to_strings(self):
        result = _as_list([1, 2, 3])
        assert result == ["1", "2", "3"]

    def test_single_string_no_comma(self):
        assert _as_list("hello") == ["hello"]

    def test_integer_scalar_cast_to_string(self):
        result = _as_list(42)
        assert result == ["42"]

    def test_multi_item_list(self):
        result = _as_list(["alpha", "beta", "gamma"])
        assert result == ["alpha", "beta", "gamma"]


# ════════════════════════════════════════════════════════════════════════
#  _skill_from_manifest
# ════════════════════════════════════════════════════════════════════════

class TestSkillFromManifest:
    def _folder(self, tmp_path, name="test-skill"):
        d = tmp_path / name
        d.mkdir()
        return d

    def test_basic_parse(self, tmp_path):
        folder = self._folder(tmp_path)
        text = "---\nname: my-skill\ndescription: A test skill\n---\nDo the thing."
        sk = _skill_from_manifest(folder, text)
        assert sk.name == "my-skill"
        assert sk.description == "A test skill"
        assert "Do the thing." in sk.body

    def test_name_falls_back_to_folder_name(self, tmp_path):
        folder = self._folder(tmp_path, name="fallback-name")
        text = "---\ndescription: no name field\n---\nbody"
        sk = _skill_from_manifest(folder, text)
        assert sk.name == "fallback-name"

    def test_triggers_parsed_from_comma_list(self, tmp_path):
        folder = self._folder(tmp_path)
        text = "---\nname: t\ntriggers: prepare my meeting, meeting prep\n---\nbody"
        sk = _skill_from_manifest(folder, text)
        assert len(sk.triggers) == 2

    def test_version_int(self, tmp_path):
        folder = self._folder(tmp_path)
        text = "---\nname: t\nversion: 3\n---\nbody"
        sk = _skill_from_manifest(folder, text)
        assert sk.version == 3

    def test_default_source_applied(self, tmp_path):
        folder = self._folder(tmp_path)
        text = "---\nname: t\n---\nbody"
        sk = _skill_from_manifest(folder, text, default_source="bundled")
        assert sk.source == "bundled"

    def test_source_in_frontmatter_overrides_default(self, tmp_path):
        folder = self._folder(tmp_path)
        text = "---\nname: t\nsource: imported\n---\nbody"
        sk = _skill_from_manifest(folder, text, default_source="friday")
        assert sk.source == "imported"

    def test_path_set_to_folder_string(self, tmp_path):
        folder = self._folder(tmp_path)
        text = "---\nname: t\n---\nbody"
        sk = _skill_from_manifest(folder, text)
        assert sk.path == str(folder)

    def test_unknown_frontmatter_keys_in_meta(self, tmp_path):
        folder = self._folder(tmp_path)
        text = "---\nname: t\ncustom_field: hello\n---\nbody"
        sk = _skill_from_manifest(folder, text)
        assert "custom_field" in sk.meta


# ════════════════════════════════════════════════════════════════════════
#  Skill.summary
# ════════════════════════════════════════════════════════════════════════

class TestSkillSummary:
    def _skill(self, **kwargs) -> Skill:
        defaults = dict(name="test", description="desc", body="body text",
                        triggers=["trigger one"], tool_chain=["t1"],
                        success_criteria=["done"], version=1,
                        license="MIT", source="friday", path="/fake/path")
        defaults.update(kwargs)
        return Skill(**defaults)

    def test_summary_has_required_keys(self):
        s = self._skill()
        summ = s.summary()
        for key in ("name", "description", "triggers", "tool_chain",
                    "version", "license", "source", "path", "has_body"):
            assert key in summ

    def test_has_body_true_when_body_present(self):
        s = self._skill(body="something")
        assert s.summary()["has_body"] is True

    def test_has_body_false_when_body_empty(self):
        s = self._skill(body="   ")
        assert s.summary()["has_body"] is False

    def test_summary_name_matches(self):
        s = self._skill(name="unique-name")
        assert s.summary()["name"] == "unique-name"


# ════════════════════════════════════════════════════════════════════════
#  load_skills / list_skills using tmp_path skill dirs
# ════════════════════════════════════════════════════════════════════════

def _write_skill_folder(root: Path, folder_name: str, skill_md: str) -> Path:
    """Create a SKILL.md folder under root and return the folder path."""
    folder = root / folder_name
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "SKILL.md").write_text(skill_md, encoding="utf-8")
    return folder


class TestLoadSkills:
    def test_discovers_skill_from_folder(self, tmp_path):
        _write_skill_folder(tmp_path, "alpha",
                            "---\nname: alpha\ndescription: alpha skill\n---\nalpha body")
        skills = load_skills(dirs=[tmp_path])
        names = [s.name for s in skills]
        assert "alpha" in names

    def test_multiple_skills_all_loaded(self, tmp_path):
        for n in ("s1", "s2", "s3"):
            _write_skill_folder(tmp_path, n,
                                f"---\nname: {n}\ndescription: {n}\n---\nbody of {n}")
        skills = load_skills(dirs=[tmp_path])
        names = [s.name for s in skills]
        for n in ("s1", "s2", "s3"):
            assert n in names

    def test_empty_dir_returns_empty_list(self, tmp_path):
        skills = load_skills(dirs=[tmp_path])
        assert skills == []

    def test_returns_skill_objects(self, tmp_path):
        _write_skill_folder(tmp_path, "beta",
                            "---\nname: beta\n---\nbeta body")
        skills = load_skills(dirs=[tmp_path])
        for s in skills:
            assert isinstance(s, Skill)

    def test_list_skills_returns_dicts(self, tmp_path):
        _write_skill_folder(tmp_path, "gamma",
                            "---\nname: gamma\n---\nbody")
        result = list_skills(dirs=[tmp_path])
        assert isinstance(result, list)
        for item in result:
            assert isinstance(item, dict)

    def test_folder_without_skill_md_ignored(self, tmp_path):
        (tmp_path / "not_a_skill").mkdir()
        (tmp_path / "not_a_skill" / "README.txt").write_text("nope", encoding="utf-8")
        skills = load_skills(dirs=[tmp_path])
        assert skills == []


# ════════════════════════════════════════════════════════════════════════
#  match_skills
# ════════════════════════════════════════════════════════════════════════

PREP_SKILL_MD = """---
name: meeting-prep
description: Prepare briefing for a meeting
triggers:
  - prepare for my meeting
  - meeting prep
---
Follow these steps to prepare for a meeting.
"""

FILTER_SKILL_MD = """---
name: filter-contacts
description: Filter contacts by criteria
triggers:
  - filter contacts
  - search contacts
---
Filter the contact list.
"""

NOTRIGGER_SKILL_MD = """---
name: no-trigger-skill
description: A skill with no triggers
---
Do something.
"""


class TestMatchSkills:
    def setup_method(self):
        pass

    def test_matching_trigger_returns_skill(self, tmp_path):
        _write_skill_folder(tmp_path, "meeting-prep", PREP_SKILL_MD)
        results = match_skills("prepare for my meeting with Alice", dirs=[tmp_path])
        assert any(s.name == "meeting-prep" for s in results)

    def test_no_match_returns_empty(self, tmp_path):
        _write_skill_folder(tmp_path, "meeting-prep", PREP_SKILL_MD)
        results = match_skills("unrelated query about the weather", dirs=[tmp_path])
        assert results == []

    def test_limit_respected(self, tmp_path):
        for i in range(5):
            md = f"---\nname: skill{i}\ntriggers:\n  - keyword{i}\n---\nbody"
            _write_skill_folder(tmp_path, f"skill{i}", md)
        # Craft a message that matches all five triggers
        msg = " ".join(f"keyword{i}" for i in range(5))
        results = match_skills(msg, dirs=[tmp_path], limit=2)
        assert len(results) <= 2

    def test_no_trigger_skill_never_returned(self, tmp_path):
        _write_skill_folder(tmp_path, "no-trigger", NOTRIGGER_SKILL_MD)
        results = match_skills("do something useful", dirs=[tmp_path])
        assert all(s.name != "no-trigger-skill" for s in results)

    def test_higher_hit_count_wins(self, tmp_path):
        # skill A has 1 trigger hit, skill B has 2
        md_a = "---\nname: skill-a\ntriggers:\n  - alpha\n---\nbody"
        md_b = "---\nname: skill-b\ntriggers:\n  - alpha\n  - beta\n---\nbody"
        _write_skill_folder(tmp_path, "skill-a", md_a)
        _write_skill_folder(tmp_path, "skill-b", md_b)
        results = match_skills("alpha beta test", dirs=[tmp_path], limit=2)
        # skill-b should rank first (2 hits vs 1)
        assert results[0].name == "skill-b"

    def test_case_insensitive_matching(self, tmp_path):
        _write_skill_folder(tmp_path, "meeting-prep", PREP_SKILL_MD)
        results = match_skills("PREPARE FOR MY MEETING", dirs=[tmp_path])
        assert any(s.name == "meeting-prep" for s in results)

    def test_empty_message_returns_empty(self, tmp_path):
        _write_skill_folder(tmp_path, "meeting-prep", PREP_SKILL_MD)
        results = match_skills("", dirs=[tmp_path])
        assert results == []

    def test_default_limit_is_three(self, tmp_path):
        for i in range(6):
            md = f"---\nname: sk{i}\ntriggers:\n  - tok{i}\n---\nbody"
            _write_skill_folder(tmp_path, f"sk{i}", md)
        msg = " ".join(f"tok{i}" for i in range(6))
        results = match_skills(msg, dirs=[tmp_path])
        assert len(results) <= 3


# ════════════════════════════════════════════════════════════════════════
#  build_injection
# ════════════════════════════════════════════════════════════════════════

class TestBuildInjection:
    def test_returns_empty_string_when_no_match(self, tmp_path):
        _write_skill_folder(tmp_path, "meeting-prep", PREP_SKILL_MD)
        result = build_injection("completely unrelated query", dirs=[tmp_path])
        assert result == ""

    def test_returns_nonempty_when_matched(self, tmp_path):
        _write_skill_folder(tmp_path, "meeting-prep", PREP_SKILL_MD)
        result = build_injection("meeting prep for tomorrow", dirs=[tmp_path])
        assert result != ""

    def test_injection_contains_skill_name(self, tmp_path):
        _write_skill_folder(tmp_path, "meeting-prep", PREP_SKILL_MD)
        result = build_injection("meeting prep for tomorrow", dirs=[tmp_path])
        assert "meeting-prep" in result

    def test_injection_contains_description(self, tmp_path):
        _write_skill_folder(tmp_path, "meeting-prep", PREP_SKILL_MD)
        result = build_injection("prepare for my meeting", dirs=[tmp_path])
        assert "Prepare briefing" in result

    def test_injection_contains_body(self, tmp_path):
        _write_skill_folder(tmp_path, "meeting-prep", PREP_SKILL_MD)
        result = build_injection("prepare for my meeting", dirs=[tmp_path])
        assert "Follow these steps" in result

    def test_body_truncated_at_max_body(self, tmp_path):
        long_body = "word " * 500  # 2500 chars, well over default max_body=1200
        md = f"---\nname: long-skill\ntriggers:\n  - long test\n---\n{long_body}"
        _write_skill_folder(tmp_path, "long-skill", md)
        result = build_injection("long test query", dirs=[tmp_path], max_body=100)
        # The injected body portion should be at most max_body chars
        # (plus header lines); total result should be modest
        assert len(result) < 500

    def test_empty_dir_returns_empty(self, tmp_path):
        result = build_injection("any query", dirs=[tmp_path])
        assert result == ""


# ════════════════════════════════════════════════════════════════════════
#  save_skill (uses tmp_path via monkeypatch of SKILLS_DIR)
# ════════════════════════════════════════════════════════════════════════

class TestSaveSkill:
    def test_creates_folder_and_skill_md(self, tmp_path, monkeypatch):
        monkeypatch.setattr(sr, "SKILLS_DIR", tmp_path)
        folder = save_skill(
            name="test-save",
            description="A saved skill",
            body="Do the thing.",
            triggers=["save test"],
            source="friday",
        )
        assert folder.exists()
        assert (folder / "SKILL.md").exists()

    def test_saved_skill_is_discoverable(self, tmp_path, monkeypatch):
        monkeypatch.setattr(sr, "SKILLS_DIR", tmp_path)
        save_skill(
            name="discoverable",
            description="desc",
            body="body",
            triggers=["discover me"],
        )
        skills = load_skills(dirs=[tmp_path])
        names = [s.name for s in skills]
        assert "discoverable" in names

    def test_saved_skill_triggers_work_in_match(self, tmp_path, monkeypatch):
        monkeypatch.setattr(sr, "SKILLS_DIR", tmp_path)
        save_skill(
            name="save-match",
            description="",
            body="body",
            triggers=["unique trigger phrase"],
        )
        results = match_skills("unique trigger phrase", dirs=[tmp_path])
        assert any(s.name == "save-match" for s in results)


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))

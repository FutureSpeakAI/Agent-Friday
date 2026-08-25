"""Unit tests for the WO-2 chokepoint followup (2026-08-25): making
`provider`/`vault_control` required, keyword-only arguments on
`_get_friday_system_prompt`, plus the static AST checker
(scripts/check_gated_prompt_callers.py) that makes a missing decision loud at
COMMIT time rather than only at whatever runtime path eventually exercises
it.

WHY THE STATIC CHECK EXISTS, NOT JUST THE REQUIRED ARGUMENT: a required
argument only raises when something calls the function. Two of the 27 real
call sites in this codebase were background jobs (a daily 16:00 unattended
briefing, a session-summary distiller) that would not have been re-run, and
so would not have raised, until their own next scheduled trigger — hours or
a day after a regression landed. `test_static_checker_flags_missing_kwargs`
and `test_real_source_tree_is_fully_gated` are the part of this file that
actually tests "loud at commit time"; the TypeError tests below test the
runtime backstop, which is necessary but not, by itself, sufficient.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

import pytest

import agent_friday.services.model_router as mr

SCRIPTS_DIR = Path(__file__).resolve().parent.parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))
import check_gated_prompt_callers as checker  # noqa: E402


# ── Runtime backstop: the required-argument TypeError ──────────────────────
class TestRequiredArgsRuntimeBackstop:
    def test_bare_call_raises(self):
        """The exact shape of the original bug: no provider, no
        vault_control. This must be loud, not a silent ungated default."""
        with pytest.raises(TypeError):
            mr._get_friday_system_prompt()

    def test_keywords_and_workspace_only_still_raises(self):
        """The exact call shape every one of the 27 original sites used."""
        with pytest.raises(TypeError):
            mr._get_friday_system_prompt(keywords="x", workspace="chat")

    def test_provider_without_vault_control_raises(self):
        with pytest.raises(TypeError):
            mr._get_friday_system_prompt(keywords="x", provider="cloud")

    def test_vault_control_without_provider_raises(self):
        with pytest.raises(TypeError):
            mr._get_friday_system_prompt(keywords="x", vault_control=None)

    def test_positional_provider_still_raises(self):
        """provider/vault_control are keyword-ONLY — confirms a caller can't
        satisfy the requirement by accident via positional args either."""
        with pytest.raises(TypeError):
            mr._get_friday_system_prompt("x", "chat", "cloud", None)

    def test_both_explicit_succeeds(self):
        # Deliberately ungated is still a valid, explicit choice.
        out = mr._get_friday_system_prompt(
            keywords="x", workspace="chat", provider="local", vault_control=None)
        assert isinstance(out, str) and out


# ── Static check: loud at commit time ───────────────────────────────────────
class TestStaticCheckerLogic:
    def test_flags_call_with_no_gating_kwargs(self, tmp_path):
        f = tmp_path / "bad.py"
        f.write_text(
            "from agent_friday.services.model_router import _get_friday_system_prompt\n"
            "def foo():\n"
            "    return _get_friday_system_prompt(keywords='x', workspace='y')\n",
            encoding="utf-8",
        )
        violations = checker._check_file(f)
        assert violations == [(3, ["provider", "vault_control"])]

    def test_flags_call_missing_only_vault_control(self, tmp_path):
        f = tmp_path / "bad2.py"
        f.write_text(
            "from agent_friday.services.model_router import _get_friday_system_prompt\n"
            "def foo():\n"
            "    return _get_friday_system_prompt(keywords='x', provider='cloud')\n",
            encoding="utf-8",
        )
        violations = checker._check_file(f)
        assert violations == [(3, ["vault_control"])]

    def test_passes_fully_gated_call(self, tmp_path):
        f = tmp_path / "good.py"
        f.write_text(
            "from agent_friday.services.model_router import _get_friday_system_prompt\n"
            "def foo():\n"
            "    return _get_friday_system_prompt(\n"
            "        keywords='x', workspace='y', provider='cloud', vault_control=None)\n",
            encoding="utf-8",
        )
        assert checker._check_file(f) == []

    def test_splat_call_given_benefit_of_the_doubt(self, tmp_path):
        """An AST scan cannot see what a **-splat contributes — flagging it
        would be a guess, not a finding. No real caller does this today."""
        f = tmp_path / "splat.py"
        f.write_text(
            "from agent_friday.services.model_router import _get_friday_system_prompt\n"
            "def foo(kw):\n"
            "    return _get_friday_system_prompt(**kw)\n",
            encoding="utf-8",
        )
        assert checker._check_file(f) == []

    def test_unrelated_call_with_the_same_kwarg_names_is_ignored(self, tmp_path):
        """Only calls to the exact target function name are inspected."""
        f = tmp_path / "unrelated.py"
        f.write_text(
            "def some_other_function(keywords='x', workspace='y'):\n"
            "    pass\n"
            "some_other_function(keywords='x', workspace='y')\n",
            encoding="utf-8",
        )
        assert checker._check_file(f) == []

    def test_main_exits_nonzero_on_a_violation(self, tmp_path, monkeypatch):
        bad_src = tmp_path / "src"
        bad_src.mkdir()
        (bad_src / "leaky.py").write_text(
            "from agent_friday.services.model_router import _get_friday_system_prompt\n"
            "_get_friday_system_prompt(keywords='x')\n",
            encoding="utf-8",
        )
        monkeypatch.setattr(checker, "SRC", bad_src)
        assert checker.main() == 1

    def test_main_exits_zero_when_clean(self, tmp_path, monkeypatch):
        clean_src = tmp_path / "src"
        clean_src.mkdir()
        (clean_src / "gated.py").write_text(
            "from agent_friday.services.model_router import _get_friday_system_prompt\n"
            "_get_friday_system_prompt(keywords='x', provider='cloud', vault_control=None)\n",
            encoding="utf-8",
        )
        monkeypatch.setattr(checker, "SRC", clean_src)
        assert checker.main() == 0


# ── The regression guard that matters: the REAL tree, right now ────────────
def test_real_source_tree_is_fully_gated():
    """This is the test that would have caught all ten sites fixed today —
    including the tenth (routes/chat.py's source dossier), which was found
    by this exact migration, not by a manual audit pass. If this test ever
    fails, so does the pre-commit hook (scripts/check_gated_prompt_callers.py
    wired into .githooks/pre-commit) — this just says so without needing a
    commit to find out."""
    assert checker.main() == 0, (
        "at least one real call site in src/ builds Friday's system prompt "
        "without deciding provider/vault_control explicitly"
    )

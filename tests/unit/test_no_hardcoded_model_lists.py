"""Spec A2 regression guard: NO hardcoded hosted-model lists.

Incident-2 forensics found the CLI/wizard Claude lineup hardcoded (predating
Opus 5 / Fable 5 / Haiku 4.5) and a stale `anthropic/claude-3.7-sonnet`
OpenRouter default in core. These tests pin the removal structurally (AST +
text), so a future convenience literal reintroducing the list fails loudly.
"""
from __future__ import annotations

import ast
import re
from pathlib import Path

SRC = Path(__file__).resolve().parents[2] / "src" / "agent_friday"

# Ids that existed ONLY in the removed hardcoded lists — their presence
# anywhere in these modules means someone reintroduced a static lineup.
REMOVED_LIST_IDS = ("claude-opus-4-7", "claude-opus-4-6")


def _text(rel: str) -> str:
    # utf-8-sig: some modules carry a BOM, which breaks ast.parse otherwise.
    return (SRC / rel).read_text(encoding="utf-8-sig")


def _assigned_list_len(text: str, name: str):
    """Length of a module-level `name = [...]` list literal, or None."""
    for node in ast.walk(ast.parse(text)):
        if isinstance(node, ast.Assign) and isinstance(node.value, ast.List):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == name:
                    return len(node.value.elts)
    return None


def test_cli_orchestrator_list_is_dynamic():
    text = _text("cli.py")
    # The six-tuple ORCHESTRATOR_MODELS literal is gone.
    assert not re.search(r"^ORCHESTRATOR_MODELS\s*=\s*\[", text, re.M), \
        "hardcoded ORCHESTRATOR_MODELS list reintroduced in cli.py"
    assert not re.search(r"^CREATIVE_MODELS\s*=\s*\[", text, re.M), \
        "hardcoded CREATIVE_MODELS list reintroduced in cli.py"
    for mid in REMOVED_LIST_IDS:
        assert mid not in text, f"{mid} back in cli.py — static lineup returned"


def test_cli_offline_fallback_is_two_ids_max():
    text = _text("cli.py")
    for name in ("_FALLBACK_ORCHESTRATOR_MODELS", "_FALLBACK_CREATIVE_MODELS"):
        n = _assigned_list_len(text, name)
        assert n is not None, f"{name} missing from cli.py"
        assert n <= 2, (f"{name} has {n} entries — the offline fallback is "
                        f"capped at two by design; the real list is dynamic")


def test_setup_wizard_lineup_is_dynamic():
    text = _text("setup_wizard.py")
    for mid in REMOVED_LIST_IDS:
        assert mid not in text, \
            f"{mid} back in setup_wizard.py — static lineup returned"
    # The old CREATIVE_ENGINES module literal is gone too.
    assert not re.search(r"^CREATIVE_ENGINES\s*=\s*\[", text, re.M)


def test_default_openrouter_model_is_modern():
    text = _text("core/__init__.py")
    assert "anthropic/claude-3.7-sonnet" not in text, \
        "stale OpenRouter default resurfaced"
    m = re.search(r'"openai_model":\s*"([^"]+)"', text)
    assert m, "openai_model default missing from DEFAULT_SETTINGS"
    assert m.group(1) == "anthropic/claude-sonnet-5"


def test_default_settings_carry_custom_models_key():
    # The escape hatch key must exist in DEFAULT_SETTINGS so /api/settings
    # accepts and persists it (the route only saves known keys).
    text = _text("core/__init__.py")
    assert re.search(r'"custom_models":\s*\[\]', text), \
        "custom_models escape hatch missing from DEFAULT_SETTINGS"

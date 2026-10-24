"""North star §34.2-12: the documentation's examples are real.

Links are checked by scripts/check_doc_links.py; this runs
scripts/check_doc_examples.py's rules: JSON examples parse, the `friday`
commands the docs name exist, every endpoint api.md documents is a route with
that method, and every setting configuration.md lists is one Friday has. Each
rule is also shown to catch what it exists for.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "check_doc_examples.py"


@pytest.fixture(scope="module")
def de():
    assert SCRIPT.exists(), "no scripts/check_doc_examples.py"
    spec = importlib.util.spec_from_file_location("check_doc_examples", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_the_docs_examples_are_real(de):
    assert de.problems() == []


def test_a_broken_json_example_is_caught_and_an_elision_is_not(de, tmp_path):
    good = tmp_path / "good.md"
    good.write_text('```json\n{ "a": { ... }, "b": [ ... ], "c": "..." }\n```\n', encoding="utf-8")
    bad = tmp_path / "bad.md"
    bad.write_text('Text.\n\n```json\n{ "a": 1, }\n```\n', encoding="utf-8")
    assert de.json_problems([good]) == []
    found = de.json_problems([bad])
    assert len(found) == 1 and ":3:" in found[0]


def test_a_command_the_cli_does_not_have_is_caught(de, tmp_path):
    f = tmp_path / "x.md"
    f.write_text("Run `friday doctor`, then `friday frobnicate`.\n", encoding="utf-8")
    found = de.cli_problems([f], {"doctor", "setup"})
    assert len(found) == 1 and "friday frobnicate" in found[0]


def test_an_endpoint_the_app_does_not_have_is_caught(de, tmp_path):
    api = tmp_path / "api.md"
    api.write_text("### `GET /api/health`\n\n### `POST /api/health`\n\n### `GET /api/items/<id>`\n\n"
                   "### `GET /api/nowhere`\n", encoding="utf-8")
    rules = {"/api/health": {"GET", "HEAD"}, "/api/items/<int:item_id>": {"GET"}}
    found = " | ".join(de.endpoint_problems(api, rules))
    assert "GET /api/nowhere is not a route" in found
    assert "POST /api/health: the route does not accept POST" in found
    assert "/api/items" not in found


def test_a_setting_friday_does_not_have_is_caught(de, tmp_path):
    cfg = tmp_path / "configuration.md"
    cfg.write_text("## Settings keys\n\n| Key | Default |\n|---|---|\n| `agent_name` | x |\n"
                   "| `model_routing.mode` | x |\n| `mode` | x |\n| `no_such_setting` | x |\n\n"
                   "## Environment variables\n\n| `FRIDAY_PORT` | x |\n", encoding="utf-8")
    defaults = {"agent_name": "F", "model_routing": {"mode": "cloud_only"}}
    found = de.setting_problems(cfg, defaults)
    assert len(found) == 1 and "no_such_setting" in found[0]

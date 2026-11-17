"""scripts/check_spec_lists.py: a spec CI never runs is named, unless it carries a dated reason."""
from __future__ import annotations

import datetime as dt
import importlib.util
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "check_spec_lists.py"
TODAY = dt.date(2026, 11, 16)


def _guard():
    spec = importlib.util.spec_from_file_location("check_spec_lists", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _tree(tmp_path, workflow_names, specs):
    (tmp_path / ".github" / "workflows").mkdir(parents=True)
    (tmp_path / ".github" / "workflows" / "tests.yml").write_text(
        "run: npx playwright test " + " ".join(workflow_names), encoding="utf-8")
    d = tmp_path / "tests" / "app" / "specs"
    d.mkdir(parents=True)
    for name, body in specs.items():
        (d / name).write_text(body, encoding="utf-8")
    return tmp_path


def test_a_spec_the_workflow_names_passes(tmp_path):
    root = _tree(tmp_path, ["a.spec.ts"], {"a.spec.ts": "test()"})
    assert _guard().offenders(root, TODAY) == []


def test_a_spec_nobody_runs_is_reported(tmp_path):
    root = _tree(tmp_path, ["a.spec.ts"], {"a.spec.ts": "", "b.spec.ts": "test()"})
    [line] = _guard().offenders(root, TODAY)
    assert line.startswith("b.spec.ts")


def test_a_dated_reason_on_the_first_lines_exempts_it_until_it_expires(tmp_path):
    root = _tree(tmp_path, [], {"v.spec.ts": "// ci-list: exempt needs a model judge until 2026-12-01\ntest()"})
    assert _guard().offenders(root, TODAY) == []
    [line] = _guard().offenders(root, dt.date(2026, 12, 2))
    assert "expired 2026-12-01" in line


def test_a_reason_without_an_expiry_does_not_exempt(tmp_path):
    root = _tree(tmp_path, [], {"v.spec.ts": "// ci-list: exempt needs a model judge\ntest()"})
    assert len(_guard().offenders(root, TODAY)) == 1


def test_the_real_repo_is_reported_not_failed_by_default():
    assert _guard().main([]) == 0

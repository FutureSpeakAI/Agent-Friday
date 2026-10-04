"""One command in a codebase (Chat Hub M3b, the Terminal): `codebases.run` runs
a single command in the codebase's own folder, bounded, with the same refusals
as run_command (key material, the blocklist, Friday's own API), keeps the run
as a receipt, and never runs inside Friday's own source: a codebase is never
the live checkout, in `create` or in `run`.
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

from agent_friday.services import codebases as cb


@pytest.fixture(autouse=True)
def _root(monkeypatch, tmp_path):
    monkeypatch.setattr(cb, "_root", lambda: tmp_path / "codebases")
    yield


def _friday_checkout(tmp_path: Path) -> Path:
    d = tmp_path / "friday-desktop"
    (d / "src" / "agent_friday" / "core").mkdir(parents=True)
    (d / "src" / "agent_friday" / "core" / "__init__.py").write_text("# Friday\n", encoding="utf-8")
    (d / ".git").mkdir()
    return d


def test_a_command_runs_in_the_codebases_own_folder_and_is_kept_as_a_run():
    rec = cb.create("Rent tracker", template="static")
    out = cb.run(rec["id"], "Write-Output ('cwd=' + (Get-Location).Path); Write-Output 'hello'")
    assert out["status"] == "ok" and out["exit"] == 0
    assert "hello" in out["output"]
    assert str(cb.repo_path(rec["id"]).resolve()).lower() in out["output"].lower().replace("/", "\\")
    runs = cb.runs(rec["id"])
    assert len(runs) == 1 and runs[0]["command"].startswith("Write-Output") and runs[0]["exit"] == 0
    cb.run(rec["id"], "exit 3")
    runs = cb.runs(rec["id"])
    assert [r["exit"] for r in runs] == [3, 0], "newest first"


def test_the_same_refusals_as_run_command_apply():
    rec = cb.create("Rent tracker", template="static")
    assert cb.run(rec["id"], "")["status"] == "refused"
    r = cb.run(rec["id"], "curl http://127.0.0.1:3000/api/settings")
    assert r["status"] == "refused" and "own" in r["say"].lower()
    r = cb.run(rec["id"], "Get-Content ~/.ssh/id_rsa")
    assert r["status"] == "refused"
    assert cb.runs(rec["id"]) == [], "a refused command is not a run"


def test_output_is_bounded_and_passes_through_the_secret_redactor(monkeypatch):
    rec = cb.create("Rent tracker", template="static")
    out = cb.run(rec["id"], "Write-Output ('x' * 50000)")
    assert len(out["output"]) <= cb.RUN_OUTPUT_MAX_CHARS + 100
    # whatever a command prints goes through credential_paths.redact_secrets before it is kept
    from agent_friday.services import credential_paths
    monkeypatch.setattr(credential_paths, "redact_secrets", lambda text: text.replace("TOKEN-IN-OUTPUT", "[withheld]"))
    out = cb.run(rec["id"], "Write-Output 'the TOKEN-IN-OUTPUT is here'")
    assert "TOKEN-IN-OUTPUT" not in out["output"] and "[withheld]" in out["output"]
    assert "TOKEN-IN-OUTPUT" not in cb.runs(rec["id"])[0]["output"]


def test_a_codebase_is_never_fridays_own_checkout(tmp_path):
    co = _friday_checkout(tmp_path)
    assert cb.is_friday_checkout(co) is True
    assert cb.is_friday_checkout(co / "src") is True, "anywhere inside it"
    assert cb.is_friday_checkout(tmp_path / "elsewhere") is False
    with pytest.raises(ValueError, match="copy of herself"):
        cb.create("Friday herself", existing_path=str(co))
    with pytest.raises(ValueError, match="copy of herself"):
        cb.create("Friday herself", existing_path=str(co / "src"))
    # a managed codebase whose repo later turns out to be a checkout is refused at run time too
    rec = cb.create("Rent tracker", template="static")
    (cb.repo_path(rec["id"]) / "src" / "agent_friday" / "core").mkdir(parents=True)
    (cb.repo_path(rec["id"]) / "src" / "agent_friday" / "core" / "__init__.py").write_text("x", encoding="utf-8")
    r = cb.run(rec["id"], "Write-Output hi")
    assert r["status"] == "refused" and "copy of herself" in r["say"]


def test_the_runs_file_is_bounded(tmp_path):
    rec = cb.create("Rent tracker", template="static")
    for i in range(cb.RUNS_KEPT + 5):
        cb.run(rec["id"], "Write-Output %d" % i)
    assert len(cb.runs(rec["id"], limit=1000)) == cb.RUNS_KEPT

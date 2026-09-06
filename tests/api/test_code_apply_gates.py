"""`/api/code/apply` must consult the gates that already existed to stop it.

`services/boot_guard.py` has shipped two refusal functions since 2026-08-17 and
**neither had a single caller anywhere in `src/`** (`docs/design/active/grow-button.md`
§18.2, findings F2 and F3):

  * `check_self_edit(path)` refuses a write to any `BOOT_CRITICAL` file, on the
    grounds that *"a Friday that cannot start cannot undo it"*;
  * `check_scope(paths)` refuses a single change touching more than five files,
    written against the nine-identical-images incident — *"the model did what it
    thought was asked, at a scale nobody wanted, and nothing stopped to check."*

`code_apply` was the one write path in the system that should have called both,
and it called neither: it resolved each path through `_safe_project_path` and
wrote the file. Every test in this file failed at `30cb426` except the two
marked GUARD, which pin behaviour the change must not break.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

import agent_friday.server as friday_server


def _repo(name: str) -> str:
    repo = friday_server.PROJECTS_DIR / name
    repo.mkdir(parents=True, exist_ok=True)
    (repo / ".git").mkdir(exist_ok=True)
    return name


def _plan(plan_id: str, repo_name: str, files) -> Path:
    record = {
        "id": plan_id,
        "created": "2026-09-03T00:00:00",
        "repo": repo_name,
        "repo_path": str(friday_server.PROJECTS_DIR / repo_name),
        "instruction": "test",
        "summary": "",
        "steps": [],
        "files": [{"path": p, "action": "write", "new_content": c,
                   "rationale": "test"} for p, c in files],
        "applied": False,
    }
    p = friday_server.CODE_PLANS_DIR / f"{plan_id}.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(record), encoding="utf-8")
    return p


@pytest.fixture
def repo_and_plan(request):
    made = []

    def _make(plan_id, files, repo_name=None):
        repo_name = repo_name or ("gates_" + plan_id[-6:])
        _repo(repo_name)
        pf = _plan(plan_id, repo_name, files)
        made.append((pf, repo_name, [f for f, _ in files]))
        return repo_name

    yield _make

    for pf, repo_name, rels in made:
        pf.unlink(missing_ok=True)
        for rel in rels:
            (friday_server.PROJECTS_DIR / repo_name / rel).unlink(missing_ok=True)


class TestBootCriticalIsRefused:

    def test_a_plan_touching_a_boot_critical_file_is_refused(
            self, client, repo_and_plan):
        """RED at 30cb426: the file was written and 200 returned.

        `server.py` is the first entry in `BOOT_CRITICAL`. Writing a broken one
        through this route is the exact failure `boot_guard` was written to
        prevent, and the function that would have prevented it was never called.
        """
        repo = repo_and_plan("20260903-000001-crit", [
            ("src/agent_friday/server.py", "def main(:\n"),
        ])

        resp = client.post("/api/code/apply", json={"plan_id": "20260903-000001-crit"})

        assert resp.status_code == 409, resp.get_json()
        body = resp.get_json()
        assert body["status"] == "refused"
        assert "server.py" in json.dumps(body)
        written = friday_server.PROJECTS_DIR / repo / "src/agent_friday/server.py"
        assert not written.exists(), "the boot-critical file was written anyway"

    def test_the_refusal_explains_itself_in_the_module_s_own_words(
            self, client, repo_and_plan):
        repo_and_plan("20260903-000002-crit", [
            ("src/agent_friday/core/__init__.py", "boom\n"),
        ])
        resp = client.post("/api/code/apply", json={"plan_id": "20260903-000002-crit"})
        assert "boot-critical" in json.dumps(resp.get_json()).lower()

    def test_one_boot_critical_file_refuses_the_whole_plan(
            self, client, repo_and_plan):
        """A half-applied plan whose important file was skipped is a worse state
        than a refused one, and `check_self_edit` says these need a separately
        confirmed action rather than a partial success."""
        repo = repo_and_plan("20260903-000003-crit", [
            ("harmless.txt", "fine"),
            ("src/agent_friday/services/agent.py", "boom\n"),
        ])
        resp = client.post("/api/code/apply", json={"plan_id": "20260903-000003-crit"})
        assert resp.status_code == 409
        assert not (friday_server.PROJECTS_DIR / repo / "harmless.txt").exists(), (
            "the plan was partially applied around the refusal")


class TestScopeIsConfirmed:

    def test_more_than_five_files_needs_confirmation(self, client, repo_and_plan):
        """RED at 30cb426: all six were written with no pause.

        `check_scope`'s own docstring asks for a pause, not a refusal — so this
        is a 409 that a caller can satisfy, not a dead end.
        """
        files = [("f%d.txt" % i, "x") for i in range(6)]
        repo = repo_and_plan("20260903-000010-scope", files)

        resp = client.post("/api/code/apply", json={"plan_id": "20260903-000010-scope"})

        assert resp.status_code == 409, resp.get_json()
        body = resp.get_json()
        assert body.get("needs_confirmation") == "scope"
        assert "6" in json.dumps(body)
        assert not (friday_server.PROJECTS_DIR / repo / "f0.txt").exists()

    def test_five_files_apply_without_a_pause(self, client, repo_and_plan):
        """GUARD (passed at 30cb426). The cap is five, and five is under it."""
        files = [("g%d.txt" % i, "x") for i in range(5)]
        repo = repo_and_plan("20260903-000011-scope", files)
        resp = client.post("/api/code/apply", json={"plan_id": "20260903-000011-scope"})
        assert resp.status_code == 200, resp.get_json()
        assert (friday_server.PROJECTS_DIR / repo / "g4.txt").exists()

    def test_confirming_the_scope_applies_the_plan(self, client, repo_and_plan):
        files = [("h%d.txt" % i, "x") for i in range(6)]
        repo = repo_and_plan("20260903-000012-scope", files)

        resp = client.post("/api/code/apply",
                           json={"plan_id": "20260903-000012-scope",
                                 "confirm_scope": True})

        assert resp.status_code == 200, resp.get_json()
        assert (friday_server.PROJECTS_DIR / repo / "h5.txt").exists()

    def test_confirming_the_scope_does_not_also_confirm_a_boot_critical_write(
            self, client, repo_and_plan):
        """The two gates are independent; the scope confirmation is not a
        master key. `check_self_edit`'s refusal is not scope-shaped."""
        repo = repo_and_plan("20260903-000013-scope", [
            ("src/agent_friday/services/model_router.py", "boom\n"),
        ])
        resp = client.post("/api/code/apply",
                           json={"plan_id": "20260903-000013-scope",
                                 "confirm_scope": True})
        assert resp.status_code == 409
        assert resp.get_json().get("needs_confirmation") != "scope"


class TestSafeModeStopsTheWritePath:

    def test_safe_mode_refuses_every_apply(self, client, repo_and_plan, monkeypatch):
        """RED at 30cb426: FRIDAY_SAFE_MODE had no effect on this route.

        Safe mode is documented as the outside-the-app switch that *"disables
        self-modification entirely"*. It reached the workspace studio and not the
        route that writes source files.
        """
        repo = repo_and_plan("20260903-000020-safe", [("safe.txt", "x")])
        monkeypatch.setenv("FRIDAY_SAFE_MODE", "1")

        resp = client.post("/api/code/apply", json={"plan_id": "20260903-000020-safe"})

        assert resp.status_code == 409, resp.get_json()
        assert "safe mode" in json.dumps(resp.get_json()).lower()
        assert not (friday_server.PROJECTS_DIR / repo / "safe.txt").exists()


class TestOrdinaryApplyStillWorks:

    def test_a_single_ordinary_file_still_applies(self, client, repo_and_plan):
        """GUARD (passed at 30cb426). The gates must not become the feature."""
        repo = repo_and_plan("20260903-000030-ok", [("ordinary.txt", "hello")])
        resp = client.post("/api/code/apply", json={"plan_id": "20260903-000030-ok"})
        assert resp.status_code == 200, resp.get_json()
        written = friday_server.PROJECTS_DIR / repo / "ordinary.txt"
        assert written.read_text(encoding="utf-8") == "hello"

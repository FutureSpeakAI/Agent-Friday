"""Beta CodeQL alerts, path injection: a workspace id or a workflow slug names
one file under a Friday-owned folder, and nothing else.

The id check uses `fullmatch` (a `$` anchor accepts a trailing newline) and
the joined path is contained under its root. A workflow slug from a
saved-workflow request is refused unless it is already the plain form
`_chain_slug` produces; before, it was normalised for the lookup and then used
raw for the file path.
"""
from __future__ import annotations

import pytest

from agent_friday.user_errors import UserFacingValueError


# -- path injection -----------------------------------------------------------

@pytest.mark.parametrize("bad", ["notes\n", "a/b", "..", "a\\b", "C:x", "", "x" * 60])
def test_workspace_studio_ids_that_are_not_one_plain_name_are_refused(bad):
    from agent_friday.services import workspace_studio as ws
    with pytest.raises(ValueError):
        ws.check_ws_id(bad)
    with pytest.raises(ValueError):
        ws._ws_path(bad)


def test_workspace_studio_path_stays_under_its_folder():
    from agent_friday.services import workspace_studio as ws
    p = ws._ws_path("notes")
    assert p.parent == ws.WS_STUDIO_DIR.resolve() and p.name == "notes.json"


@pytest.mark.parametrize("bad", ["chore\n", "a/b", "..", "A", ""])
def test_workspace_bundle_ids_that_are_not_a_slug_are_refused(bad):
    from agent_friday.services import workspace_bundles as wb
    if bad.strip() == "chore":          # check_ws_id strips, as it always has
        assert wb.check_ws_id(bad) == "chore"
        return
    with pytest.raises(ValueError):
        wb.check_ws_id(bad)


def test_a_manifest_id_with_a_trailing_newline_is_not_a_slug():
    import json
    from agent_friday.services import workspace_bundles as wb
    problems = wb.check_manifest(json.dumps({"id": "chore\n", "name": "x", "friday_api": 1}))
    assert any("id must be a slug" in p for p in problems)


@pytest.fixture
def workflows(friday_dir, monkeypatch, tmp_path):
    from agent_friday.services import agent as ag
    from agent_friday.services import scheduler as sch
    monkeypatch.setattr(ag, "WORKFLOWS_DIR", tmp_path / "workflows")
    for f in (sch.SCHEDULES_FILE, sch.RUNS_FILE):
        if f.exists():
            f.unlink()
    sch._RUNNING.clear()
    yield tmp_path


STEPS = [{"name": "Gather", "prompt": "Search my inbox for reader tips."}]


@pytest.mark.parametrize("slug", ["../outside-victim", "..\\outside-victim", "outside-victim/../..",
                                  "Outside-Victim", "outside-victim\n"])
def test_a_saved_workflow_slug_cannot_name_a_file_outside_the_folder(workflows, slug):
    from agent_friday.services import workflow_overview as wo
    wo.save({"name": "Outside victim", "steps": STEPS})        # slug: outside-victim
    before = sorted(p.name for p in workflows.rglob("*"))
    with pytest.raises(UserFacingValueError):
        wo.save({"name": "Outside victim", "steps": STEPS, "slug": slug})
    assert sorted(p.name for p in workflows.rglob("*")) == before
    assert not (workflows / "outside-victim.json").exists()


def test_the_plain_slug_still_saves_in_place(workflows):
    from agent_friday.services import agent as ag
    from agent_friday.services import workflow_overview as wo
    out = wo.save({"name": "Tip roundup", "steps": STEPS})
    again = wo.save({"name": "Reader tips", "steps": STEPS, "slug": out["slug"]})
    assert again["slug"] == out["slug"]
    assert ag.load_workflow_chain(out["slug"])["name"] == "Reader tips"

"""CI runs the workspace / Laya contract test on every PR (docs audit 2026-10-17, decision 3).

The file holds the "every workspace has a manifest" check, the
gate-class equality check and the Laya coverage ratchet. It sits outside tests/unit and tests/api,
so the pytest job never collected it. The workflow must name it in a step that runs on pull requests.
"""
from __future__ import annotations

from pathlib import Path

REPO = Path(__file__).resolve().parents[2]


def test_the_tests_workflow_runs_the_contract_file():
    wf = (REPO / ".github" / "workflows" / "tests.yml").read_text(encoding="utf-8")
    assert "pull_request" in wf
    runs = [line.strip() for line in wf.splitlines() if line.strip().startswith("run: pytest")]
    assert any("tests/test_laya_workspace_contracts.py" in r for r in runs), runs
    assert (REPO / "tests" / "test_laya_workspace_contracts.py").exists()


# The two contract failures the first CI run of that file found on main, kept as unit tests.

def test_open_the_first_person_points_at_a_row():
    """contacts.open_person's own phrasing: 'person' is a row word, so 'the first person' is an
    ordinal on screen, not a description to look up."""
    from agent_friday.services import point_act as pa
    p = pa.parse("open the first person")
    assert p is not None and p.kind == "ordinals" and pa.is_referential(p)
    # 'people' stays the People workspace's name, not a row word
    assert "people" not in pa._HEAD


def test_the_library_draft_job_entry_is_where_the_visible_job_opens():
    import json
    m = json.loads((REPO / "src/agent_friday/resources/laya_manifests/library.json").read_text(encoding="utf-8"))
    entry = next(j["entry"] for j in m["jobs"] if j["id"] == "library.stage_drafts")
    mod, _, qual = entry.partition(":")
    src = (REPO / "src" / Path(*mod.split("."))).with_suffix(".py").read_text(encoding="utf-8")
    fn = qual.rsplit(".", 1)[-1]
    import re
    body = src[src.index("def %s(" % fn):]
    nxt = re.search(r"\n(?:    )?(?:def|class) ", body[1:])
    body = body[:nxt.start() + 1] if nxt else body
    assert 'kind="library_stage"' in body, entry

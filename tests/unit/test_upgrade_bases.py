"""The release before this one always has an upgrade leg (north star NS-34.3-6).

Before: the installer workflow's upgrade matrix stopped at v1.2.0-beta.1 although
v1.2.0-beta.2 was the latest published release, and nothing noticed: the list is
written by hand. Now it names v1.2.0-beta.2, and the release workflow runs
scripts/check_upgrade_bases.py before it publishes, which refuses a release whose
matrix misses the release before it or the oldest supported base (v5.14.3).
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
WORKFLOW = (REPO / ".github" / "workflows" / "installer.yml").read_text(encoding="utf-8")
SCRIPT = REPO / "scripts" / "check_upgrade_bases.py"

sys.path.insert(0, str(REPO / "scripts"))
try:
    import check_upgrade_bases as cub  # noqa: E402
except ImportError:  # the tree a red run is aimed at: the tests fail on their assertions
    cub = None

PUBLISHED = [  # what GitHub lists today (2026-11-02), newest first; 1.0.2 is a draft
    {"tag_name": "v1.2.0-beta.2", "draft": False},
    {"tag_name": "v1.2.0-beta.1", "draft": False},
    {"tag_name": "v1.1.0-beta.1", "draft": False},
    {"tag_name": "v1.0.2-beta.1", "draft": True},
    {"tag_name": "v1.0.1-beta.1", "draft": False},
    {"tag_name": "v1.0.0-beta.1", "draft": False},
    {"tag_name": "v5.14.3", "draft": False},
]


def test_the_matrix_proves_the_latest_published_release():
    assert '"v1.2.0-beta.1","v1.2.0-beta.2"]' in WORKFLOW, "the upgrade matrix stops before the latest published release"
    assert cub, "no release-time check of the upgrade bases"
    assert "v1.2.0-beta.2" in cub.matrix_bases(WORKFLOW)
    assert cub.check(WORKFLOW, PUBLISHED, "v1.2.0-beta.3") == []
    assert cub.check(WORKFLOW, PUBLISHED, "v1.2.0") == []   # the final 1.2.0 sorts after every 1.2 beta


def test_a_release_whose_prior_has_no_leg_is_refused():
    newer = [{"tag_name": "v1.2.0-beta.3", "draft": False}] + PUBLISHED
    problems = cub.check(WORKFLOW, newer, "v1.2.0-beta.4")
    assert problems and "v1.2.0-beta.3" in problems[0]


def test_a_draft_needs_no_leg_and_the_tag_itself_is_not_its_own_prior():
    assert cub.prior_release(PUBLISHED, "v1.0.2-beta.2") == "v1.0.1-beta.1"
    assert cub.prior_release(PUBLISHED, "v1.2.0-beta.2") == "v1.2.0-beta.1"


def test_versions_sort_like_releases():
    tags = ["v1.0.0", "v1.0.0-rc.1", "v1.0.0-beta.2", "v1.0.0-beta.1", "v0.9.9", "v5.14.3"]
    assert sorted(tags, key=cub.order) == ["v0.9.9", "v1.0.0-beta.1", "v1.0.0-beta.2", "v1.0.0-rc.1", "v1.0.0", "v5.14.3"]


def test_the_oldest_supported_base_must_stay():
    shrunk = WORKFLOW.replace('"v5.14.3",', "")
    assert any("v5.14.3" in p for p in cub.check(shrunk, PUBLISHED, "v1.2.0-beta.3"))


@pytest.mark.parametrize("tag,code", [("v1.2.0-beta.3", 0), ("v1.2.0-beta.4", 1), ("not-a-tag", 3)])
def test_the_command_line(tmp_path, tag, code):
    rel = tmp_path / "releases.json"
    data = PUBLISHED if tag != "v1.2.0-beta.4" else [{"tag_name": "v1.2.0-beta.3", "draft": False}] + PUBLISHED
    rel.write_text(json.dumps(data), encoding="utf-8")
    r = subprocess.run([sys.executable, str(SCRIPT), "--workflow", str(REPO / ".github" / "workflows" / "installer.yml"),
                        "--tag", tag, "--releases", str(rel)], capture_output=True, text=True)
    assert r.returncode == code, r.stdout + r.stderr


def test_the_release_waits_for_it():
    rel = WORKFLOW[WORKFLOW.index("  release:"):]
    assert "upgrade-bases" in rel[:rel.index("steps:")]
    job = WORKFLOW[WORKFLOW.index("  upgrade-bases:"):]
    assert "scripts/check_upgrade_bases.py --workflow .github/workflows/installer.yml" in job[:job.index("\n\n")]

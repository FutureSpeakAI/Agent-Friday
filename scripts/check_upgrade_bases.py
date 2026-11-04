#!/usr/bin/env python3
"""The upgrade matrix covers the release before this one (north star NS-34.3-6).

NS-34.3-6 asks every release to prove an upgrade from the oldest supported version
AND from the immediately prior one. The installer workflow's upgrade job runs a
matrix of published bases; that list is written by hand, so a new release can ship
while the list still stops at an older one (it stopped at v1.2.0-beta.1 after
v1.2.0-beta.2 was published). This check, run by the release workflow before it
publishes, reads the published releases and refuses when the newest one before the
tag being released is not in the matrix's default list. The oldest supported base
(v5.14.3) must be there too.

    check_upgrade_bases.py --workflow .github/workflows/installer.yml --tag v1.2.0-beta.3 \
        --releases releases.json            # a `gh api repos/{repo}/releases` dump
    check_upgrade_bases.py ... --repo owner/name   # asks the GitHub API (GH_TOKEN)

Drafts are not published, so they need no leg. Exit codes: 0 covered, 1 refused,
3 usage error. Standard library only: the release job runs on a bare ubuntu runner.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.request
from pathlib import Path

OLDEST_SUPPORTED = "v5.14.3"
_TAG = re.compile(r"^v(\d+)\.(\d+)\.(\d+)(?:-(alpha|beta|rc)\.(\d+))?$")
_PRE = {"alpha": 0, "beta": 1, "rc": 2}


def order(tag: str):
    """Sort key for a release tag: a pre-release sorts before its final version."""
    m = _TAG.match(tag or "")
    if not m:
        return None
    major, minor, patch, kind, n = m.groups()
    return (int(major), int(minor), int(patch), _PRE[kind] if kind else 3, int(n or 0))


def matrix_bases(workflow_text: str) -> list:
    """The default list of the upgrade job's matrix (what an unattended tag run proves)."""
    job = workflow_text[workflow_text.index("\n  upgrade:"):]
    m = re.search(r"base:\s*\$\{\{.*?\|\|\s*'(\[[^\]]*\])'\s*\)\s*\}\}", job)
    if not m:
        raise ValueError("the upgrade job's default base list was not found")
    return json.loads(m.group(1))


def prior_release(releases: list, tag: str):
    """The newest published (not draft) release that sorts before `tag`."""
    mine = order(tag)
    best = None
    for r in releases:
        if r.get("draft"):
            continue
        t = r.get("tag_name") or ""
        k = order(t)
        if k is None or (mine is not None and k >= mine):
            continue
        if best is None or k > order(best):
            best = t
    return best


def check(workflow_text: str, releases: list, tag: str) -> list:
    """What is wrong; an empty list means the matrix covers what NS-34.3-6 asks."""
    problems = []
    bases = matrix_bases(workflow_text)
    if OLDEST_SUPPORTED not in bases:
        problems.append(f"the oldest supported base {OLDEST_SUPPORTED} has no upgrade leg")
    prior = prior_release(releases, tag)
    if prior is None:
        problems.append(f"no published release before {tag} was found to upgrade from")
    elif prior not in bases:
        problems.append(f"{prior}, the release before {tag}, has no upgrade leg (matrix: {', '.join(bases)})")
    return problems


def _fetch(repo: str) -> list:
    req = urllib.request.Request(f"https://api.github.com/repos/{repo}/releases?per_page=100",
                                 headers={"Accept": "application/vnd.github+json",
                                          "Authorization": "Bearer " + os.environ.get("GH_TOKEN", "")})
    with urllib.request.urlopen(req, timeout=30) as r:  # noqa: S310 (fixed https URL)
        return json.loads(r.read().decode("utf-8"))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--workflow", required=True)
    ap.add_argument("--tag", required=True)
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--releases", help="a JSON list of releases (gh api repos/{repo}/releases)")
    src.add_argument("--repo", help="owner/name: read the releases from the GitHub API")
    a = ap.parse_args(argv)
    if order(a.tag) is None:
        print(f"not a release tag: {a.tag!r}", file=sys.stderr)
        return 3
    try:
        text = Path(a.workflow).read_text(encoding="utf-8")
        releases = json.loads(Path(a.releases).read_text(encoding="utf-8")) if a.releases else _fetch(a.repo)
        problems = check(text, releases, a.tag)
    except (OSError, ValueError) as e:
        print(f"REFUSED: could not check the upgrade bases: {e}", file=sys.stderr)
        return 1
    if problems:
        for p in problems:
            print("REFUSED: " + p, file=sys.stderr)
        return 1
    print(f"upgrade bases cover {tag_prior(releases, a.tag)} (the release before {a.tag}) and {OLDEST_SUPPORTED}")
    return 0


def tag_prior(releases: list, tag: str) -> str:
    return prior_release(releases, tag) or "-"


if __name__ == "__main__":
    sys.exit(main())

"""Release identity, and the one way two releases are ordered.

Agent Friday Beta 1.0 is numerically LOWER than the 5.x line it replaces
(1.0.0b1 against 5.14.3). Comparing version numbers would therefore tell every
Beta install that 5.14.3 is an upgrade, and would let the Beta installer treat
a 5.14.3 machine as newer than itself. Releases are ordered by a monotonically
increasing **build sequence** instead:

  * the 5.x line (tags ``v5.x.y``, no pre-release label) maps to
    ``major * 10000 + minor * 100 + patch``; 5.14.3 is 51403, and the whole
    line stays below ``ERA_FLOOR``;
  * every later release sits above ``ERA_FLOOR`` and orders by
    ``ERA_FLOOR + major * 1_000_000 + minor * 10_000 + patch * 100 + stage``
    where ``stage`` is the beta number (``beta.N`` -> N), 50 + N for a release
    candidate, and 99 for the final release.

``BUILD_SEQUENCE`` is the number this tree stamps on what it ships; the
installer writes it beside the version, and a published release carries it as
a ``Build sequence: N`` line in its notes so an updater needs no arithmetic on
tags it has never seen. A test holds ``BUILD_SEQUENCE`` equal to the sequence
computed from the version in ``pyproject.toml``, so the two cannot drift.

The installer (``packaging/windows/installer/AgentFriday.iss``) implements the
same ordering for the version it finds on disk, and a test holds its constants
to the ones here.
"""
from __future__ import annotations

import re
from typing import Optional

#: What the app calls itself wherever it shows its version.
RELEASE_NAME = "Agent Friday Beta 1.0"

#: The tag this tree is released under.
RELEASE_TAG = "v1.0.0-beta.1"

#: Everything at or above this is a release after the 5.x line.
ERA_FLOOR = 100_000_000

#: Bumped by every release; see the module docstring for the shape.
BUILD_SEQUENCE = 101_000_001

_VERSION = re.compile(
    r"^\s*[vV]?(\d+)(?:\.(\d+))?(?:\.(\d+))?"
    r"(?:(?:[-.]?(a|alpha|b|beta|rc|c|pre|preview|dev)[-.]?(\d+)?)|(?:[-+].*))?\s*$",
    re.IGNORECASE,
)

_STAGE_BASE = {"a": 1, "alpha": 1, "b": 1, "beta": 1, "dev": 1, "pre": 1,
               "preview": 1, "rc": 50, "c": 50}

_SEQUENCE_LINE = re.compile(r"(?im)^\s*build[ _-]sequence\s*[:=]\s*(\d{1,12})\s*$")


def sequence_for_version(raw: Optional[str]) -> Optional[int]:
    """The build sequence a version string or release tag stands for, or None
    when it is not a version at all.

    Accepts ``5.14.3``, ``v5.14.3``, ``1.0.0b1`` and ``v1.0.0-beta.1`` alike.
    """
    if not raw:
        return None
    m = _VERSION.match(str(raw))
    if not m:
        return None
    major = int(m.group(1))
    minor = int(m.group(2) or 0)
    patch = int(m.group(3) or 0)
    label = (m.group(4) or "").lower()
    number = int(m.group(5)) if m.group(5) else 0
    if label == "" and major == 5:
        return major * 10_000 + minor * 100 + patch
    if label:
        base = _STAGE_BASE.get(label, 1)
        stage = base + max(0, number - 1)
        stage = min(stage, 49 if base == 1 else 98)
    else:
        stage = 99
    return ERA_FLOOR + major * 1_000_000 + minor * 10_000 + patch * 100 + stage


def sequence_of_release(release: dict) -> Optional[int]:
    """The build sequence of a GitHub release payload.

    The ``Build sequence: N`` line in the release notes wins; a release without
    one (every 5.x release) is placed by its tag.
    """
    if not isinstance(release, dict):
        return None
    body = str(release.get("body") or "")
    m = _SEQUENCE_LINE.search(body)
    if m:
        return int(m.group(1))
    return sequence_for_version(release.get("tag_name"))


def is_prerelease_version(raw: Optional[str]) -> bool:
    """True for a beta, alpha, release-candidate or dev version string."""
    if not raw:
        return False
    m = _VERSION.match(str(raw))
    return bool(m and m.group(4))

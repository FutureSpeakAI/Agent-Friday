"""This suite refuses a write to the owner's real data root (pytest_real_root_guard, loaded from the root conftest).

The probe path is in a directory that does not exist under the real ~/.friday, so nothing is created even where the guard is
absent: there the open fails with FileNotFoundError, which is how this test fails before the fix. With the guard the open is
refused before it reaches the operating system, with a PermissionError that names the real data root.
"""
import os
import sys
from pathlib import Path

import pytest


def test_a_write_to_the_real_data_root_from_a_test_is_refused():
    probe = Path(os.environ["FRIDAY_REAL_HOME"]) / ".friday" / ".pytest_guard_probe_does_not_exist" / "x.txt"
    with pytest.raises(PermissionError) as refused:
        open(probe, "w")
    assert "real data root" in str(refused.value)
    guard = sys.modules.get("pytest_real_root_guard")        # the probe is deliberate: it must not fail this test
    if guard is not None:
        del guard.VIOLATIONS[-1:]
        guard._baseline[0] = len(guard.VIOLATIONS)

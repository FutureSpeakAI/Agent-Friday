"""Friday's home in a test run is the run's own test home.

tests/conftest.py makes one temp home per pytest process. A FRIDAY_HOME the
process inherited (a developer's shell, or a test that runs pytest in a child
with its own environment) would win over it in paths.friday_home() while core
had already settled on another folder. A child that inherited a stray
FRIDAY_HOME saw a folder with no install record, read it as a brand-new
install, and A16a held its platforms and mail (CI #254).
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PROBE = "tests/unit/test_friday_home_is_the_test_home.py::test_the_home_is_this_runs_test_home"


def test_the_home_is_this_runs_test_home():
    from agent_friday.paths import friday_home
    from agent_friday import core
    home = Path(os.environ["HOME"]) / ".friday"
    assert friday_home() == home
    assert Path(core.FRIDAY_DIR) == home


def test_an_inherited_friday_home_is_not_used(tmp_path):
    stray = tmp_path / "stray-home"
    stray.mkdir()
    env = {k: v for k, v in os.environ.items()
           if not k.startswith("PYTEST_XDIST") and k != "PYTEST_CURRENT_TEST"}
    env["FRIDAY_HOME"] = str(stray)
    r = subprocess.run(
        [sys.executable, "-m", "pytest", PROBE, "-n", "0", "-p", "no:randomly",
         "-p", "no:cacheprovider", "-q"],
        cwd=str(ROOT), env=env, capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=600)
    tail = "\n".join((r.stdout + r.stderr).strip().splitlines()[-25:])
    assert r.returncode == 0, "a child given FRIDAY_HOME used it:\n%s" % tail
    assert not any(stray.iterdir()), "the child wrote into the inherited FRIDAY_HOME"

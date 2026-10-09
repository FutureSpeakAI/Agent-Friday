"""The app suite's scratch server must never run the residency Arbiter.

An Arbiter boots by adopting or reaping every llama-server serving a known
model on the machine, so a scratch Friday started for browser specs would kill
or capture the live Friday's brain seat. tests/app/target.js starts that server;
it has to pass FRIDAY_NO_ARBITER=1, which server.py honours before booting it.
"""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


def test_server_py_skips_the_arbiter_when_told_to():
    src = (ROOT / "src" / "agent_friday" / "server.py").read_text(encoding="utf-8")
    assert 'os.environ.get("FRIDAY_NO_ARBITER") != "1"' in src


@pytest.mark.skipif(shutil.which("node") is None, reason="needs Node.js to evaluate tests/app/target.js")
def test_the_scratch_server_env_turns_the_arbiter_off():
    script = (
        "const t = require(%s);"
        "const s = t.scratchServer(t.resolveTarget({}), '.', 'python', 'home');"
        "process.stdout.write(JSON.stringify(s.env));"
    ) % json.dumps(str(ROOT / "tests" / "app" / "target.js"))
    out = subprocess.run(["node", "-e", script], capture_output=True, text=True, timeout=60, check=True)
    env = json.loads(out.stdout)
    assert env.get("FRIDAY_TESTING") == "1"
    assert env.get("FRIDAY_NO_ARBITER") == "1"

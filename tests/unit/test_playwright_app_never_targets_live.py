"""The app suite never writes to the owner's live Friday.

Playwright app specs once defaulted to http://localhost:3000, which is the live
server, and ran there overnight; their requests are indistinguishable from the
owner's in the access log. The rule is now in the config:

* with FRIDAY_BASE unset, the suite runs against a scratch server with its own
  temporary FRIDAY_HOME, started by Playwright's webServer;
* the live server (the live port on loopback, or any non-loopback host) is
  refused unless LIVE_READONLY=1;
* in that mode every non-GET request is blocked and fails the test, which only
  holds if every spec takes `test` from tests/app/fixtures.ts.
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
APP = REPO / "tests" / "app"
NODE = shutil.which("node")


def _resolve(env: dict) -> dict:
    script = ("const t=require(%s);try{console.log(JSON.stringify(t.resolveTarget(%s)))}"
              "catch(e){console.log(JSON.stringify({refused:String(e.message)}))}"
              % (json.dumps(str(APP / "target.js")), json.dumps(env)))
    out = subprocess.run([NODE, "-e", script], capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout.strip().splitlines()[-1])


needs_node = pytest.mark.skipif(NODE is None, reason="node is not installed")


@needs_node
@pytest.mark.parametrize("base", ["http://localhost:3000", "http://127.0.0.1:3000/",
                                  "https://agent.friday", "http://192.168.1.20:3197"])
def test_the_live_server_is_refused_without_live_readonly(base):
    r = _resolve({"FRIDAY_BASE": base})
    assert "refused" in r and "never write to live" in r["refused"], r


@needs_node
def test_live_readonly_allows_the_live_server_as_live():
    r = _resolve({"FRIDAY_BASE": "http://localhost:3000", "LIVE_READONLY": "1"})
    assert r.get("live") is True and r["baseURL"] == "http://localhost:3000"


@needs_node
def test_the_default_is_a_scratch_server_off_the_live_port():
    r = _resolve({})
    assert r.get("scratch") is True and r.get("live") is False
    assert r["baseURL"] == "http://127.0.0.1:3197"
    assert _resolve({"FRIDAY_LIVE_PORT": "3197"}).get("refused") is None   # scratch is never live
    assert "refused" in _resolve({"FRIDAY_BASE": "http://127.0.0.1:3197", "FRIDAY_LIVE_PORT": "3197"})


def test_the_config_starts_a_scratch_server_and_names_no_live_default():
    cfg = (REPO / "playwright.app.config.ts").read_text(encoding="utf-8")
    assert "resolveTarget(process.env)" in cfg and "scratchServer(" in cfg and "webServer" in cfg
    assert "localhost:3000" not in cfg
    for helper in (APP / "harness.ts", APP / "scenarios" / "scenario.ts"):
        text = helper.read_text(encoding="utf-8")
        assert "localhost:3000" not in text, f"{helper.name} defaults to the live server"
        assert "resolveTarget(process.env)" in text


def test_every_spec_takes_test_from_the_read_only_fixtures():
    offenders = []
    for spec in APP.rglob("*.spec.ts"):
        text = spec.read_text(encoding="utf-8")
        for m in re.finditer(r"import \{([^}]*)\} from '@playwright/test'", text):
            names = [n.strip() for n in m.group(1).split(",")]
            if any(n in ("test", "expect") for n in names):
                offenders.append(spec.relative_to(REPO).as_posix())
    assert not offenders, ("specs that bypass tests/app/fixtures.ts (no live read-only "
                           f"guard): {offenders}")


def test_the_fixtures_block_and_fail_on_writes_in_live_mode():
    fx = (APP / "fixtures.ts").read_text(encoding="utf-8")
    assert "context.route('**/*'" in fx and "route.abort(" in fx
    assert "throw new Error('LIVE_READONLY: this test tried to write" in fx
    assert "auto: true" in fx
    for verb in ("post", "put", "patch", "delete"):
        assert f"'{verb}'" in fx


def test_no_spec_writes_through_page_request():
    """page.request is not covered by the page's routes; it may only read."""
    for f in APP.rglob("*.ts"):
        text = f.read_text(encoding="utf-8")
        assert not re.search(r"(page|context)\.request\.(post|put|patch|delete|fetch)\(", text), f

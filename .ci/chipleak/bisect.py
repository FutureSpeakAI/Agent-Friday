"""CI-only: reproduce and bisect the order-dependent failure of
tests/api/test_workspace_job_card_ui.py (the Activity chip drawn but hidden).

Usage:
  python .ci/chipleak/bisect.py alone  <out>
  python .ci/chipleak/bisect.py chain  <out> <list>      # list = files run before the target
  python .ci/chipleak/bisect.py bisect <out> <list>

Every run is ONE pytest process, lane flags (-n 1 by default; BISECT_N overrides),
files in the given order, then the target. The verdict is read from the junit XML
for the target test only, so a failure elsewhere in the set never counts.
Not for the PC: this file only runs on GitHub runners.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import xml.etree.ElementTree as ET
from pathlib import Path

TARGET_FILE = "tests/api/test_workspace_job_card_ui.py"
TARGET_NAME = "test_the_briefing_task_is_on_the_desktop_with_its_model_and_an_openable_trace"
FLAGS = ["-q", "-rfE", "-p", "no:cacheprovider", "-p", "no:pytest_resource_guard",
         "-o", "faulthandler_timeout=600", "--timeout=300", "--timeout-method=thread",
         "-n", os.environ.get("BISECT_N", "1"), "--dist", "loadfile"]

OUT = Path(sys.argv[2])
OUT.mkdir(parents=True, exist_ok=True)
LOG = OUT / "bisect.log"
_cache: dict = {}
_runs = [0]


def log(msg: str) -> None:
    line = time.strftime("%H:%M:%S ") + msg
    print(line, flush=True)
    with LOG.open("a", encoding="utf-8") as fh:
        fh.write(line + "\n")


def target_verdict(xml_path: Path) -> str:
    try:
        root = ET.parse(xml_path).getroot()
    except Exception as exc:                                   # no XML = the run itself died
        return "no-xml (%s)" % exc
    for tc in root.iter("testcase"):
        if tc.get("name") == TARGET_NAME:
            if tc.find("failure") is not None or tc.find("error") is not None:
                return "FAIL"
            if tc.find("skipped") is not None:
                return "SKIP"
            return "PASS"
    return "missing"


def run(files: list[str], tag: str = "") -> str:
    key = tuple(files)
    if key in _cache:
        return _cache[key]
    _runs[0] += 1
    n = _runs[0]
    xml = OUT / ("run%03d.xml" % n)
    shots = OUT / ("shots%03d" % n)
    env = dict(os.environ, FRIDAY_SHOT_DIR=str(shots))
    cmd = [sys.executable, "-m", "pytest", *FLAGS, "--junitxml", str(xml), *files,
           TARGET_FILE + "::" + TARGET_NAME]
    t0 = time.time()
    p = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace",
                       env=env)
    (OUT / ("run%03d.txt" % n)).write_text(p.stdout[-60000:] + "\n--- stderr ---\n" + p.stderr[-20000:],
                                         encoding="utf-8")
    v = target_verdict(xml)
    log("run %03d %s %-5s %4.0fs  %d files before the target%s" % (
        n, tag, v, time.time() - t0, len(files),
        (": " + ", ".join(Path(f).name for f in files)) if len(files) <= 8 else
        " (%s .. %s)" % (Path(files[0]).name, Path(files[-1]).name)))
    if v in ("SKIP", "missing") or v.startswith("no-xml"):
        log("ABORT: the target did not run (%s); see run%03d.txt" % (v, n))
        sys.exit(3)
    _cache[key] = v
    return v


def fails(files: list[str]) -> bool:
    return run(files) == "FAIL"


def split(c: list[str], n: int) -> list[list[str]]:
    k, m = divmod(len(c), n)
    out, i = [], 0
    for j in range(n):
        size = k + (1 if j < m else 0)
        out.append(c[i:i + size])
        i += size
    return [x for x in out if x]


def ddmin(c: list[str]) -> list[str]:
    """Zeller's delta debugging; order inside every subset is the gate's order."""
    n = 2
    while len(c) >= 2:
        chunks = split(c, n)
        reduced = False
        for ch in chunks:
            if fails(ch):
                c, n, reduced = ch, 2, True
                break
        if not reduced and n > 2:
            for i in range(len(chunks)):
                comp = [x for j, ch in enumerate(chunks) if j != i for x in ch]
                if fails(comp):
                    c, n, reduced = comp, max(n - 1, 2), True
                    break
        if not reduced:
            if n >= len(c):
                break
            n = min(len(c), 2 * n)
    return c


def main() -> int:
    mode = sys.argv[1]
    if mode == "alone":
        v = []
        for i in (1, 2, 3):
            _cache.clear()
            v.append(run([], "alone#%d" % i))
        log("ALONE: %s" % v)
        return 0 if v == ["PASS"] * 3 else 1
    pre = [ln.strip().lstrip("﻿") for ln in Path(sys.argv[3]).read_text(encoding="utf-8").splitlines()
           if ln.strip()]
    if mode == "chain":
        v = run(pre, "chain")
        log("CHAIN (%d files + target): %s" % (len(pre), v))
        return 1 if v == "FAIL" else 0
    assert mode == "bisect", mode
    if run([], "alone") != "PASS":
        log("target fails ALONE: not order-dependent here")
        return 2
    if not fails(pre):
        _cache.pop(tuple(pre), None)
        if not fails(pre):
            log("NOT REPRODUCED: the chain passed twice")
            return 4
    culprit = ddmin(pre)
    log("MINIMAL SET: %s" % culprit)
    # Confirm, uncached: the minimal set fails twice, the target alone passes once more.
    _cache.clear()
    a = run(culprit, "confirm1")
    _cache.clear()
    b = run(culprit, "confirm2")
    _cache.clear()
    c = run([], "alone-again")
    log("CONFIRM minimal set: %s %s; alone: %s" % (a, b, c))
    (OUT / "result.json").write_text(json.dumps({"minimal": culprit, "confirm": [a, b], "alone": c,
                                                 "runs": _runs[0]}, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())

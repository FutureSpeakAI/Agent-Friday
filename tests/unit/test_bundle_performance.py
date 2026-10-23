"""Backup and restore performance (north star §34.15-2, the backup/restore
part; BD-32): making, verifying and restoring a Friday Bundle take time in
proportion to the data, not faster than it grows. Each is timed on a
synthetic data folder and on one ten times larger; the larger may take at most
twenty times as long (plus a fixed allowance for start-up costs such as the
key derivation), and each stays under a wide ceiling so a slow shared runner
does not fail it. Timings are reported as PerfNote warnings, which pytest
shows in its summary on every run.

The check itself is tested: a step that grows with the square of the data
fails it."""
from __future__ import annotations

import json
import sqlite3
import sys
import time
import warnings
from pathlib import Path

import pytest

from agent_friday.governance import proof_of_integrity as poi

PASS = "orbit lantern pepper tundra violet 42"   # pragma: allowlist secret
VAULT_PASS = "vault passphrase for the test"  # pragma: allowlist secret

#: Files of each kind in the 1x folder; the 10x folder has ten times as many.
BASE = {"wiki": 30, "conversations": 20, "workspaces": 20, "ledger_entries": 40}
#: The larger folder may take this many times as long as the smaller one ...
GROWTH = 20.0
#: ... plus this many seconds of fixed cost (Argon2, process set-up).
ALLOWANCE_S = 3.0
#: No step on the 10x folder may take longer than this on a CI runner.
CEILING_S = 120.0


class PerfNote(Warning):
    """A timing, for the test summary (not a problem)."""


def grows_no_faster(t1: float, t10: float, *, growth: float = GROWTH, allowance: float = ALLOWANCE_S) -> bool:
    """Whether a step timed at t1 on the 1x folder and t10 on the 10x folder
    grew no faster than the data (with the fixed allowance)."""
    return t10 <= growth * t1 + allowance


def _seed(home: Path, k: int) -> None:
    from agent_friday.services import action_ledger
    home.mkdir(parents=True)
    (home / "settings.json").write_text(json.dumps({"agent_name": "Friday"}), encoding="utf-8")
    (home / "core_memory").mkdir()
    (home / "core_memory" / "ME.md").write_text("- notes\n" * 50 * k, encoding="utf-8")
    page = ("Lorem ipsum dolor sit amet, consectetur adipiscing elit. " * 70).encode("utf-8")   # ~4 KB
    for i in range(BASE["wiki"] * k):
        p = home / "wiki" / ("folder%d" % (i % 7)) / ("page%d.md" % i)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(page)
    for i in range(BASE["conversations"] * k):
        c = home / "conversations" / ("c%d" % i)
        c.mkdir(parents=True)
        (c / "messages.jsonl").write_text("".join(json.dumps({"role": "user", "text": "hello %d" % j}) + "\n"
                                                  for j in range(40)), encoding="utf-8")
    for i in range(BASE["workspaces"] * k):
        p = home / "workspaces" / ("ws%d" % i) / "notes.txt"
        p.parent.mkdir(parents=True)
        p.write_bytes(page)
    con = sqlite3.connect(home / "user_model.db")
    con.execute("CREATE TABLE facts(id INTEGER PRIMARY KEY, text TEXT)")
    con.executemany("INSERT INTO facts(text) VALUES (?)", [("fact %d" % i,) for i in range(2000 * k)])
    con.commit()
    con.close()
    for i in range(BASE["ledger_entries"] * k):
        action_ledger.append("activity", {"kind": "tool_call", "tool": "probe", "n": i})


def _point_at(home: Path, monkeypatch) -> None:
    monkeypatch.setenv("FRIDAY_HOME", str(home))
    monkeypatch.setenv("FRIDAY_LEDGER_DIR", str(home / "ledger"))
    monkeypatch.setattr(poi, "_GOV_KEY_FILE", home / "vault" / ".governance-key")
    from agent_friday.services import action_ledger
    action_ledger.reset_caches()


def _timed(fn):
    t0 = time.perf_counter()
    out = fn()
    return time.perf_counter() - t0, out


@pytest.fixture
def isolated(monkeypatch):
    monkeypatch.setitem(sys.modules, "keyring", None)
    monkeypatch.setenv("FRIDAY_VAULT_PASSPHRASE", VAULT_PASS)
    monkeypatch.setenv("FRIDAY_PASSWORD", VAULT_PASS)
    from agent_friday.services import vault_passphrase
    vault_passphrase.reset_cache()


def _round(tmp_path: Path, k: int, monkeypatch) -> dict:
    from agent_friday.privacy import vault_crypto as vc
    from agent_friday.services import friday_bundle as fb
    a, b = tmp_path / ("a%d" % k) / ".friday", tmp_path / ("b%d" % k) / ".friday"
    _point_at(a, monkeypatch)
    _seed(a, k)
    out = tmp_path / ("x%d.fridaybundle" % k)
    t_make, m = _timed(lambda: fb.create(out, PASS, home=a, kdf_profile=vc.FAST_PROFILE))
    t_verify, v = _timed(lambda: fb.verify(out, PASS))
    _point_at(b, monkeypatch)
    t_restore, r = _timed(lambda: fb.restore(out, PASS, home=b))
    # a step that failed quickly would look fast: each one must have done its work
    assert r["checked_in_place"]["files_match"] is True
    assert r["checked_in_place"]["ledger"]["ok"], r["checked_in_place"]
    assert r["checked_in_place"]["ledger"]["records"] >= BASE["ledger_entries"] * k
    assert v["files"] == len(m["files"]) and "governance-key" in m["secrets"]
    files, size = len(m["files"]), out.stat().st_size
    warnings.warn(PerfNote("[perf] bundle %dx: %d files, %.1f MB bundle; make %.2fs, verify %.2fs, "
                           "restore %.2fs" % (k, files, size / 1e6, t_make, t_verify, t_restore)))
    return {"make": t_make, "verify": t_verify, "restore": t_restore, "files": files, "bytes": size,
            "redo": {"make": lambda: _timed(lambda: fb.create(tmp_path / ("y%d.fridaybundle" % k), PASS,
                                                              home=a, kdf_profile=vc.FAST_PROFILE))[0],
                     "verify": lambda: _timed(lambda: fb.verify(out, PASS))[0]}}


def test_making_verifying_and_restoring_grow_no_faster_than_the_data(tmp_path, monkeypatch, isolated):
    small = _round(tmp_path, 1, monkeypatch)
    large = _round(tmp_path, 10, monkeypatch)
    assert large["files"] >= 8 * small["files"], (small, large)     # the folder really is ~10x
    assert large["bytes"] >= 8 * small["bytes"], (small, large)
    for step in ("make", "verify", "restore"):
        t10 = large[step]
        if not grows_no_faster(small[step], t10) and step in large["redo"]:
            # one slow moment on a shared runner is not growth: timed once more
            t10 = min(t10, large["redo"][step]())
        assert t10 < CEILING_S, (step, t10)
        assert grows_no_faster(small[step], t10), \
            "%s took %.2fs on 1x and %.2fs on 10x: it grows faster than the data" % (
                step, small[step], t10)


def test_the_check_fails_a_step_that_grows_with_the_square_of_the_data():
    # 1x: 0.5 s. A linear step on 10x: about 5 s. A quadratic one: about 50 s.
    assert grows_no_faster(0.5, 5.0)
    assert not grows_no_faster(0.5, 50.0)
    # the allowance covers a fixed start-up cost, not growth
    assert grows_no_faster(0.05, 3.5)
    assert not grows_no_faster(0.05, 10.0)


def test_the_check_catches_a_real_quadratic_step(tmp_path):
    def step(n):
        # touches every pair: what a per-file rescan of the whole folder would cost
        t0 = time.perf_counter()
        acc = 0
        for i in range(n):
            for j in range(n):
                acc ^= (i * 31 + j) & 0xFF
        return time.perf_counter() - t0
    t1 = min(step(300) for _ in range(7))    # the least disturbed of several short runs
    t10 = step(3000)                         # a slow long run only makes the check easier
    warnings.warn(PerfNote("[perf] quadratic probe: 1x %.3fs, 10x %.3fs" % (t1, t10)))
    assert not grows_no_faster(t1, t10, allowance=0.0)


"""A SQLite connection opened for one `with` block is closed when the block ends.

`with sqlite3.connect(...) as con:` commits or rolls back on exit but does NOT
close. A connection also holds a statement cache that refers back to it, so only
the cycle collector frees one, and on a large heap that runs rarely: the live
server held thousands of open arbiter.db and content_pipeline.db connections,
each with its file handles and its native page cache, growing with every poll.
These stores' connection helpers close on exit; the collector is paused here so
the test sees exactly what the code closes.
"""
from __future__ import annotations

import gc
import sqlite3

import pytest


def _open_connections_to(path) -> int:
    n = 0
    for o in gc.get_objects():
        if isinstance(o, sqlite3.Connection):
            try:
                rows = o.execute("PRAGMA database_list").fetchall()
            except sqlite3.ProgrammingError:     # closed
                continue
            if any(str(r[2]).lower() == str(path).lower() for r in rows):
                n += 1
    return n


@pytest.fixture
def no_cycle_collector():
    gc.collect()
    gc.disable()
    yield
    gc.enable()
    gc.collect()


@pytest.mark.parametrize("module,helper", [
    ("arbiter", "_conn"),
    ("content_pipeline", "_connect"),
    ("content_policies", "_conn"),
])
def test_a_with_block_connection_is_closed_on_exit(tmp_path, monkeypatch, no_cycle_collector, module, helper):
    import importlib
    mod = importlib.import_module(f"agent_friday.services.{module}")
    db = tmp_path / f"{module}.db"
    monkeypatch.setattr(mod, "DB_PATH", db)
    if hasattr(mod, "_SCHEMA_DONE"):
        monkeypatch.setattr(mod, "_SCHEMA_DONE", False)
    open_conn = getattr(mod, helper)
    for _ in range(25):
        with open_conn() as con:
            con.execute("SELECT 1").fetchall()
    assert _open_connections_to(db) == 0, f"{module}.{helper}() leaves connections open after its with-block"


def test_a_with_block_still_commits(tmp_path, monkeypatch, no_cycle_collector):
    from agent_friday.services import content_policies as mod
    db = tmp_path / "commit.db"
    monkeypatch.setattr(mod, "DB_PATH", db)
    with mod._conn() as con:
        con.execute("CREATE TABLE t (x INTEGER)")
        con.execute("INSERT INTO t VALUES (1)")
    check = sqlite3.connect(str(db))
    try:
        assert check.execute("SELECT x FROM t").fetchall() == [(1,)]
    finally:
        check.close()

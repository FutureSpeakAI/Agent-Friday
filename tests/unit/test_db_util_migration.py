"""H10 — the shared additive-migration helper (services/db_util.py).

CREATE TABLE IF NOT EXISTS never alters an existing table, so a user upgrading
from an older schema keeps the old columns. ensure_columns/ensure_schema close
that gap by ALTER-ing in the missing columns, forward-only and crash-safe.
"""
from __future__ import annotations

import sqlite3

import pytest

from agent_friday.services import db_util


@pytest.fixture
def conn():
    c = sqlite3.connect(":memory:")
    yield c
    c.close()


class TestEnsureColumns:
    def test_adds_missing_column(self, conn):
        conn.execute("CREATE TABLE t(a TEXT)")
        added = db_util.ensure_columns(conn, "t", [("b", "INTEGER DEFAULT 0")])
        assert added == ["b"]
        cols = [r[1] for r in conn.execute("PRAGMA table_info(t)")]
        assert "b" in cols

    def test_idempotent_existing_column_skipped(self, conn):
        conn.execute("CREATE TABLE t(a TEXT, b INTEGER)")
        assert db_util.ensure_columns(conn, "t", [("b", "INTEGER")]) == []

    def test_absent_table_is_noop(self, conn):
        # No table yet → the module's CREATE TABLE will make it; migrate nothing.
        assert db_util.ensure_columns(conn, "missing", [("x", "TEXT")]) == []

    def test_existing_rows_get_default(self, conn):
        conn.execute("CREATE TABLE t(a TEXT)")
        conn.execute("INSERT INTO t(a) VALUES ('row1')")
        conn.commit()
        db_util.ensure_columns(conn, "t", [("weight", "REAL DEFAULT 1.5")])
        assert conn.execute("SELECT weight FROM t").fetchone()[0] == 1.5

    def test_partial_add_only_missing(self, conn):
        conn.execute("CREATE TABLE t(a TEXT, b INTEGER)")
        added = db_util.ensure_columns(
            conn, "t", [("a", "TEXT"), ("b", "INTEGER"), ("c", "TEXT")])
        assert added == ["c"]

    def test_never_raises_on_bad_decl(self, conn):
        conn.execute("CREATE TABLE t(a TEXT)")
        # Nonsense declaration must be swallowed, not raised.
        added = db_util.ensure_columns(conn, "t", [("b", "NOT A REAL TYPE ((")])
        assert isinstance(added, list)


class TestEnsureSchema:
    def test_multi_table(self, conn):
        conn.execute("CREATE TABLE t1(a TEXT)")
        conn.execute("CREATE TABLE t2(x TEXT)")
        result = db_util.ensure_schema(conn, {
            "t1": [("b", "INTEGER")],
            "t2": [("x", "TEXT"), ("y", "TEXT")],  # x exists, y is new
        })
        assert result == {"t1": ["b"], "t2": ["y"]}

    def test_no_changes_returns_empty(self, conn):
        conn.execute("CREATE TABLE t(a TEXT)")
        assert db_util.ensure_schema(conn, {"t": [("a", "TEXT")]}) == {}


class TestRealisticUpgrade:
    """Simulate a returning user: old DB missing a v5.1 column."""

    def test_learning_style_upgrade(self, conn):
        # Old schema (pre-upgrade)
        conn.execute("CREATE TABLE skills(skill_id TEXT PRIMARY KEY, pattern TEXT)")
        conn.execute("INSERT INTO skills VALUES ('s1', 'prefer tests first')")
        conn.commit()
        # New code adds a column
        db_util.ensure_columns(conn, "skills", [("source_obs_json", "TEXT")])
        # Old row survives, new column readable
        row = conn.execute(
            "SELECT pattern, source_obs_json FROM skills WHERE skill_id='s1'"
        ).fetchone()
        assert row[0] == "prefer tests first"
        assert row[1] is None

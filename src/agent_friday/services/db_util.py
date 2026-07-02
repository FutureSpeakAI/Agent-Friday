"""
Agent Friday — shared SQLite helpers (H10)
FutureSpeak.AI · Asimov's Mind

Additive, forward-only schema migration for the ~dozen leaf-module SQLite DBs
under ~/.friday (learning.db, user_model.db, dreams.db, economy.db, budgets.db,
costs.db, work_log.db, …). Each module still owns its `CREATE TABLE IF NOT
EXISTS`; this helper handles the case that CREATE-IF-NOT-EXISTS does NOT: adding
a column to a table that already exists on an upgrading user's machine.

Leaf-safe: stdlib only, never raises to the caller (schema evolution must never
brick a returning user). Follows the leaf-module contract used across services/.
"""
from __future__ import annotations

import sqlite3
from typing import Dict, Iterable, List, Tuple


def _existing_columns(conn: sqlite3.Connection, table: str) -> List[str]:
    """Column names currently on `table` ([] if the table doesn't exist)."""
    try:
        cur = conn.execute(f"PRAGMA table_info({table})")
        return [row[1] for row in cur.fetchall()]
    except sqlite3.Error:
        return []


def ensure_columns(conn: sqlite3.Connection, table: str,
                   columns: Iterable[Tuple[str, str]]) -> List[str]:
    """Add any missing `columns` to `table` via ALTER TABLE ADD COLUMN.

    `columns` is an iterable of (name, decl) pairs, where `decl` is the column
    type + constraints, e.g. ("weight", "REAL DEFAULT 1.0"). SQLite's
    ADD COLUMN only allows constant defaults (no CURRENT_TIMESTAMP on a
    non-empty table), which the callers already respect.

    Returns the list of column names that were added (empty if none / on error).
    Never raises — a migration failure logs nothing and leaves the schema as-is,
    which is strictly safer than crashing a returning user's startup.
    """
    added: List[str] = []
    have = set(_existing_columns(conn, table))
    if not have:
        # Table absent — the module's CREATE TABLE will make it with the full
        # current schema; nothing to migrate.
        return added
    for name, decl in columns:
        if name in have:
            continue
        try:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {decl}")
            added.append(name)
        except sqlite3.Error:
            # Column may have been added by a concurrent writer, or the decl is
            # rejected — either way, do not brick startup.
            pass
    if added:
        try:
            conn.commit()
        except sqlite3.Error:
            pass
    return added


def ensure_schema(conn: sqlite3.Connection,
                  spec: Dict[str, Iterable[Tuple[str, str]]]) -> Dict[str, List[str]]:
    """Apply ensure_columns across several tables at once.

    `spec` maps table name → iterable of (column, decl). Returns table → added
    columns for the ones that changed (useful for logging/tests)."""
    result: Dict[str, List[str]] = {}
    for table, cols in spec.items():
        added = ensure_columns(conn, table, cols)
        if added:
            result[table] = added
    return result

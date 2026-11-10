"""The test-credit issuer journal and the exchange arithmetic (Federation / Economy spec,
economy section; roadmap F5). TEST CREDITS ONLY: nothing here moves real money, holds a
wallet key, talks to a processor or reads the legacy `services/economy.py` balances (those
are a separate, historical dataset and are never merged into this one).

Two parts, both pure of the network:

* Arithmetic with exact integer rules: the buyer fee on a cash trade is
  floor((C + 200) / 400) of the principal C (0.25% rounded to the nearest unit, applied to
  the CUMULATIVE gross so a split order pays what one order would), the work-side operator
  share is floor(gross * bps / 10000) on the cumulative gross (contributors keep the rest,
  never less than 99% at the default 100 bps), proportional shares go one unit at a time to
  the largest weight / (allocated + 1) with ties broken by key bytes, and a market match is
  the largest whole lot count the buyer's cash covers with its fee.
* `IssuerJournal`: one serialized authority for one issuer ID with a fixed supply. A command
  posts zero-sum integer postings once per economic effect: the same effect again is a
  duplicate (even under a fresh delivery action ID), the same effect key with different
  postings or the same action ID with a different effect is a conflict, an unbalanced
  command or one that would take any account below zero is refused, and total supply never
  changes. The commit is one SQLite transaction, so a crash leaves either all of a posting
  or none of it.

The journal commits balances exclusively; a peer event (services/peer_wire_v2) about a
payment is only a notification of it.
"""
from __future__ import annotations

import json
import sqlite3
import threading
from fractions import Fraction

MAX_UNITS = 2 ** 53 - 1


def cash_fee(minor_units: int) -> int:
    """Buyer fee on a cash principal: 0.25%, rounded to nearest, in the same minor unit."""
    return (minor_units + 200) // 400


def incremental_fee(quantities, calc) -> list[int]:
    """The fee owed by each successive increment, so the total equals calc(total gross)."""
    gross = 0
    fees = []
    for amount in quantities:
        before = calc(gross)
        gross += amount
        fees.append(calc(gross) - before)
    return fees


def operator_share(gross: int, operator_bps: int) -> int:
    if not 0 <= operator_bps <= 100:
        raise ValueError("operator_bps must be 0..100 (the operator never takes more than 1%)")
    return gross * operator_bps // 10000


def allocate(weights: dict, units: int) -> dict:
    """Split `units` whole units in proportion to `weights`, one unit at a time to the key with
    the largest weight / (already allocated + 1); ties go to the first key in raw byte order."""
    paid = dict.fromkeys(weights, 0)
    order = sorted(weights, key=lambda key: key.encode("utf-8"))
    for _ in range(units):
        chosen = max(order, key=lambda key: Fraction(weights[key], paid[key] + 1))
        paid[chosen] += 1
    return paid


def match_lots(case: dict) -> tuple[int, int | None]:
    """(lots, price per lot) for a crossing order pair the buyer can fund, else (0, None)."""
    if case["bid"] < case["ask"]:
        return 0, None
    price = case[case["resting"]]
    lots = min(case["buy_lots"], case["sell_lots"])
    while lots and lots * price + cash_fee(lots * price) > case["cash_available"]:
        lots -= 1
    return lots, (price if lots else None)


class IssuerJournal:
    """One issuer's fixed-supply test-credit ledger in a SQLite file."""

    def __init__(self, path, issuer: str, supply: int, treasury: str = "treasury"):
        if type(supply) is not int or not 0 <= supply <= MAX_UNITS:
            raise ValueError("supply must be a whole number of units")
        self._path = str(path)
        self.issuer = issuer
        self.supply = supply
        self._lock = threading.Lock()
        with self._connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS journal_meta (issuer TEXT PRIMARY KEY, supply INTEGER NOT NULL);
                CREATE TABLE IF NOT EXISTS journal_balances (account TEXT PRIMARY KEY, amount INTEGER NOT NULL);
                CREATE TABLE IF NOT EXISTS journal_effects (
                    effect TEXT PRIMARY KEY, postings TEXT NOT NULL, posted_at REAL NOT NULL DEFAULT (strftime('%s','now')));
                CREATE TABLE IF NOT EXISTS journal_actions (action TEXT PRIMARY KEY, effect TEXT NOT NULL);
            """)
            row = db.execute("SELECT supply FROM journal_meta WHERE issuer=?", (issuer,)).fetchone()
            if row is None:
                if db.execute("SELECT 1 FROM journal_meta").fetchone():
                    raise ValueError("this journal file belongs to another issuer")
                db.execute("INSERT INTO journal_meta VALUES (?,?)", (issuer, supply))
                db.execute("INSERT INTO journal_balances VALUES (?,?)", (treasury, supply))
            elif row[0] != supply:
                raise ValueError("this journal was created with a different supply")

    def _connect(self):
        return sqlite3.connect(self._path)

    def balances(self) -> dict:
        with self._connect() as db:
            return {a: v for a, v in db.execute("SELECT account, amount FROM journal_balances") if v}

    def execute(self, command: dict) -> str:
        """One command {issuer, effect, action, postings}. Returns one of: posted, duplicate,
        issuer, units, action-conflict, effect-conflict, unbalanced, insufficient."""
        issuer, effect_key, action = command.get("issuer"), command.get("effect"), command.get("action")
        postings = command.get("postings")
        if issuer != self.issuer:
            return "issuer"
        if (not isinstance(postings, dict) or not all(type(v) is int and abs(v) <= MAX_UNITS
                                                      for v in postings.values())):
            return "units"
        encoded = json.dumps(sorted(postings.items()), separators=(",", ":"))
        with self._lock, self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            known = db.execute("SELECT effect FROM journal_actions WHERE action=?", (action,)).fetchone()
            stored = db.execute("SELECT postings FROM journal_effects WHERE effect=?", (effect_key,)).fetchone()
            if known:
                first = db.execute("SELECT postings FROM journal_effects WHERE effect=?", (known[0],)).fetchone()
                if known[0] != effect_key or first is None or first[0] != encoded:
                    return "action-conflict"
            if stored is not None:
                if stored[0] != encoded:
                    return "effect-conflict"
                db.execute("INSERT OR IGNORE INTO journal_actions VALUES (?,?)", (action, effect_key))
                return "duplicate"
            if sum(postings.values()) != 0:
                return "unbalanced"
            future = {}
            for account, amount in postings.items():
                row = db.execute("SELECT amount FROM journal_balances WHERE account=?", (account,)).fetchone()
                future[account] = (row[0] if row else 0) + amount
            if min(future.values(), default=0) < 0:
                return "insufficient"
            for account, amount in future.items():
                db.execute("INSERT INTO journal_balances VALUES (?,?) "
                           "ON CONFLICT(account) DO UPDATE SET amount=excluded.amount", (account, amount))
            db.execute("INSERT INTO journal_effects (effect, postings) VALUES (?,?)", (effect_key, encoded))
            db.execute("INSERT INTO journal_actions VALUES (?,?)", (action, effect_key))
            return "posted"

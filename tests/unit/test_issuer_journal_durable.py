"""The issuer journal as a durable log: sequence, receipts, outbox, corrections, recovery
(NS-40.4-3 and NS-40.4-5, design Part B, PR 2). Test credits only."""
import itertools
import json
import sqlite3
import subprocess
import sys
import textwrap
import threading
from pathlib import Path

import pytest

from agent_friday.services import issuer_journal as ij
from agent_friday.services import peer_wire_v2 as wire

SRC = str(Path(ij.__file__).resolve().parents[2])
ISSUER = "test.one"


def make(path, supply=100):
    return ij.IssuerJournal(path, ISSUER, supply)


def cmd(effect, action=None, **postings):
    return {"issuer": ISSUER, "effect": effect, "action": action or "act-" + effect, "postings": postings}


def tables(path):
    with sqlite3.connect(path) as db:
        return {name: db.execute(f"SELECT * FROM {name} ORDER BY 1").fetchall()
                for name in ("journal_balances", "journal_effects", "journal_receipts", "journal_outbox",
                             "journal_actions")}


def test_the_journal_is_write_ahead_logged_with_full_sync(tmp_path):
    journal = make(tmp_path / "j.sqlite")
    with journal._connect() as db:
        assert db.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
        assert db.execute("PRAGMA synchronous").fetchone()[0] == 2


def test_a_posting_gets_the_next_sequence_a_request_hash_a_receipt_and_an_outbox_row(tmp_path):
    journal = make(tmp_path / "j.sqlite")
    outcome, receipt = journal.execute_receipt(cmd("e1", treasury=-5, alice=5))
    assert outcome is ij.Outcome.POSTED and journal.head() == 1
    body = wire.decode(receipt)
    assert body["seq"] == 1 and body["effect"] == "e1" and body["protocol"] == ij.RECEIPT_PROTOCOL
    assert body["request_hash"] == ij.request_hash(ISSUER, "e1", {"treasury": -5, "alice": 5}, None, None)
    assert [p["effect"] for p in journal.outbox_pending()] == ["e1"]
    assert journal.execute(cmd("e2", alice=-5, bob=5)) is ij.Outcome.POSTED and journal.head() == 2


def test_a_duplicate_under_a_fresh_action_returns_the_original_receipt(tmp_path):
    journal = make(tmp_path / "j.sqlite")
    _, first = journal.execute_receipt(cmd("e1", "a1", treasury=-5, alice=5))
    outcome, second = journal.execute_receipt(cmd("e1", "a2-fresh", treasury=-5, alice=5))
    assert outcome is ij.Outcome.DUPLICATE and second == first
    assert journal.head() == 1 and journal.balances() == {"treasury": 95, "alice": 5}
    assert len(journal.outbox_pending()) == 1


def test_the_same_effect_with_other_postings_or_another_correction_is_a_conflict(tmp_path):
    journal = make(tmp_path / "j.sqlite")
    journal.execute(cmd("e1", treasury=-5, alice=5))
    assert journal.execute(cmd("e1", "x", treasury=-6, alice=6)) is ij.Outcome.EFFECT_CONFLICT
    again = cmd("e1", "y", treasury=-5, alice=5) | {"corrects": "e1", "reason": "r"}
    assert journal.execute(again) is ij.Outcome.EFFECT_CONFLICT


@pytest.mark.parametrize("bad", [{"effect": "café"}, {"effect": ""}, {"action": "x" * 201},
                                 {"postings": {}}, {"postings": {"treasury": -1.5, "a": 1.5}},
                                 {"postings": {"treasury": -True, "a": True}}, {"postings": {"bé": 0}}])
def test_a_malformed_command_is_refused_before_anything_is_written(tmp_path, bad):
    journal = make(tmp_path / "j.sqlite")
    before = tables(tmp_path / "j.sqlite")
    command = cmd("e1", treasury=-5, alice=5) | bad
    assert journal.execute(command) is ij.Outcome.UNITS
    assert tables(tmp_path / "j.sqlite") == before


def test_a_correction_names_an_existing_effect_and_a_reason(tmp_path):
    journal = make(tmp_path / "j.sqlite")
    journal.execute(cmd("e1", treasury=-5, alice=5))
    original = tables(tmp_path / "j.sqlite")["journal_effects"]
    undo = cmd("c1", alice=-5, treasury=5)
    assert journal.execute(undo | {"corrects": "nope", "reason": "typo"}) is ij.Outcome.CORRECTION_INVALID
    assert journal.execute(undo | {"corrects": "e1"}) is ij.Outcome.CORRECTION_INVALID
    assert journal.execute(undo | {"reason": "typo"}) is ij.Outcome.CORRECTION_INVALID
    assert journal.execute(undo | {"corrects": "e1", "reason": "sent in error"}) is ij.Outcome.POSTED
    rows = tables(tmp_path / "j.sqlite")["journal_effects"]
    assert [r for r in rows if r[0] == "e1"] == original, "the original effect is unchanged"
    assert journal.balances() == {"treasury": 100}
    assert wire.decode(journal.outbox_pending()[1]["receipt"])["corrects"] == "e1"


@pytest.mark.parametrize("point", ["validated", "applied", "receipt", "outbox"])
def test_a_crash_at_every_point_leaves_all_or_none(tmp_path, point):
    path = tmp_path / "j.sqlite"
    journal = make(path)
    journal.execute(cmd("e0", treasury=-1, alice=1))
    before = tables(path)

    def crash(name):
        if name == point:
            raise RuntimeError("killed")
    journal._point = crash
    with pytest.raises(RuntimeError):
        journal.execute(cmd("e1", treasury=-5, alice=5))
    assert tables(path) == before, "nothing of the crashed command remains"
    reopened = make(path)
    assert reopened.halted is None
    outcome, receipt = reopened.execute_receipt(cmd("e1", treasury=-5, alice=5))
    assert outcome is ij.Outcome.POSTED and wire.decode(receipt)["seq"] == 2
    assert reopened.execute_receipt(cmd("e1", "other", treasury=-5, alice=5)) == (ij.Outcome.DUPLICATE, receipt)


KILL = textwrap.dedent("""
    import os, sys
    sys.path.insert(0, {src!r})
    from agent_friday.services import issuer_journal as ij
    j = ij.IssuerJournal({path!r}, "test.one", 100)
    stop = {stop!r}
    if stop != "after-commit":
        j._point = lambda name: os._exit(3) if name == stop else None
    outcome = j.execute({{"issuer": "test.one", "effect": "e1", "action": "a1", "postings": {{"treasury": -5, "alice": 5}}}})
    print(outcome.value, flush=True)
    os._exit(3)
""")


def _killed(path, stop):
    run = subprocess.run([sys.executable, "-c", KILL.format(src=SRC, path=str(path), stop=stop)],
                         capture_output=True, text=True, timeout=60)
    assert run.returncode == 3, run.stderr
    return run.stdout.strip()


@pytest.mark.parametrize("stop", ["validated", "applied", "receipt", "outbox"])
def test_a_killed_process_mid_command_leaves_none_of_it(tmp_path, stop):
    path = tmp_path / "j.sqlite"
    _killed(path, stop)
    journal = make(path)
    assert journal.halted is None and journal.balances() == {"treasury": 100} and journal.head() == 0


def test_posted_means_durable_after_the_process_is_killed(tmp_path):
    path = tmp_path / "j.sqlite"
    assert _killed(path, "after-commit") == "posted"
    journal = make(path)
    assert journal.halted is None and journal.balances() == {"treasury": 95, "alice": 5} and journal.head() == 1


def test_two_threads_cannot_both_spend_the_same_balance(tmp_path):
    journal = make(tmp_path / "j.sqlite", supply=10)
    results = []

    def spend(n):
        results.append(journal.execute(cmd(f"e{n}", treasury=-3, **{f"acct{n}": 3})))
    threads = [threading.Thread(target=spend, args=(n,)) for n in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert results.count(ij.Outcome.POSTED) == 3 and results.count(ij.Outcome.INSUFFICIENT) == 5
    assert journal.balances()["treasury"] == 1 and journal.head() == 3


RACE = textwrap.dedent("""
    import sys
    sys.path.insert(0, {src!r})
    from agent_friday.services import issuer_journal as ij
    j = ij.IssuerJournal({path!r}, "test.one", 10)
    posted = 0
    for n in range(6):
        o = j.execute({{"issuer": "test.one", "effect": "p{tag}-%d" % n, "action": "p{tag}-%d" % n,
                        "postings": {{"treasury": -1, "acct{tag}": 1}}}})
        posted += o.value == "posted"
    print(posted)
""")


def test_two_processes_cannot_overdraw_one_balance(tmp_path):
    path = tmp_path / "j.sqlite"
    make(path, supply=10)
    procs = [subprocess.Popen([sys.executable, "-c", RACE.format(src=SRC, path=str(path), tag=tag)],
                              stdout=subprocess.PIPE, text=True) for tag in ("a", "b")]
    posted = sum(int(p.communicate(timeout=120)[0].strip()) for p in procs)
    journal = make(path, supply=10)
    assert posted == 10 and journal.balances().get("treasury", 0) == 0 and journal.head() == 10
    assert journal.halted is None


def test_an_outbox_consumer_that_fails_does_not_repeat_the_debit(tmp_path):
    journal = make(tmp_path / "j.sqlite")
    journal.execute(cmd("e1", treasury=-5, alice=5))
    attempts = []
    for _ in range(4):
        for row in journal.outbox_pending():
            attempts.append(row["effect"])
            break
    assert attempts == ["e1"] * 4 and journal.balances() == {"treasury": 95, "alice": 5}
    journal.outbox_delivered("e1")
    assert journal.outbox_pending() == [] and journal.balances() == {"treasury": 95, "alice": 5}
    journal.outbox_delivered("e1")
    assert journal.head() == 1


def _tamper(path, sql, *args):
    with sqlite3.connect(path) as db:
        db.execute(sql, args)


@pytest.mark.parametrize("name,sql,args", [
    ("balance", "UPDATE journal_balances SET amount=amount+1 WHERE account='alice'", ()),
    ("dropped effect", "DELETE FROM journal_effects WHERE effect='e1'", ()),
    ("reordered", "UPDATE journal_effects SET seq=CASE effect WHEN 'e1' THEN 2 ELSE 1 END", ()),
    ("receipt gone", "DELETE FROM journal_receipts WHERE effect='e2'", ()),
    ("effect rewritten", "UPDATE journal_effects SET postings='[[\"alice\",-1],[\"bob\",1]]' WHERE effect='e2'", ()),
])
def test_tampering_halts_every_write_and_reads_still_work(tmp_path, name, sql, args):
    path = tmp_path / "j.sqlite"
    journal = make(path)
    journal.execute(cmd("e1", treasury=-5, alice=5))
    journal.execute(cmd("e2", alice=-2, bob=2))
    _tamper(path, sql, *args)
    reopened = make(path)
    assert reopened.halted, name
    assert reopened.execute(cmd("e3", treasury=-1, carol=1)) is ij.Outcome.HALTED
    assert isinstance(reopened.balances(), dict)
    assert reopened.execute(cmd("e1", "again", treasury=-5, alice=5)) is ij.Outcome.HALTED


def test_a_journal_from_before_sequence_numbers_is_given_them_on_open(tmp_path):
    path = tmp_path / "old.sqlite"
    with sqlite3.connect(path) as db:
        db.executescript("""
            CREATE TABLE journal_meta (issuer TEXT PRIMARY KEY, supply INTEGER NOT NULL);
            CREATE TABLE journal_balances (account TEXT PRIMARY KEY, amount INTEGER NOT NULL);
            CREATE TABLE journal_effects (effect TEXT PRIMARY KEY, postings TEXT NOT NULL,
                posted_at REAL NOT NULL DEFAULT (strftime('%s','now')));
            CREATE TABLE journal_actions (action TEXT PRIMARY KEY, effect TEXT NOT NULL);
            INSERT INTO journal_meta VALUES ('test.one', 100);
            INSERT INTO journal_balances VALUES ('treasury', 95), ('alice', 5);
            INSERT INTO journal_effects (effect, postings) VALUES ('e1', '[["alice",5],["treasury",-5]]');
            INSERT INTO journal_actions VALUES ('a1', 'e1');
        """)
    journal = make(path)
    assert journal.halted is None and journal.head() == 1 and journal.balances() == {"treasury": 95, "alice": 5}
    assert journal.execute(cmd("e1", "a1", treasury=-5, alice=5)) is ij.Outcome.DUPLICATE
    assert journal.execute(cmd("e2", alice=-1, bob=1)) is ij.Outcome.POSTED and journal.head() == 2


def test_every_short_sequence_keeps_supply_sequence_and_replay(tmp_path):
    steps = [{"treasury": -n, "a": n} for n in (0, 1, 2)] + [{"a": -n, "treasury": n} for n in (0, 1, 2)]
    count = 0
    for length in (1, 2, 3):
        for chosen in itertools.product(range(len(steps)), repeat=length):
            path = tmp_path / f"s{count}.sqlite"
            count += 1
            journal = make(path, supply=3)
            posted = 0
            for k, index in enumerate(chosen):
                if journal.execute(cmd(f"e{k}", **steps[index])) is ij.Outcome.POSTED:
                    posted += 1
            balances = journal.balances()
            assert sum(balances.values()) == 3 and min(balances.values()) >= 0
            assert journal.head() == posted
            replay = make(path, supply=3)
            assert replay.halted is None and replay.balances() == balances
            with sqlite3.connect(path) as db:
                assert [r[0] for r in db.execute("SELECT seq FROM journal_effects ORDER BY seq")] == list(
                    range(1, posted + 1))
    assert count == 6 + 36 + 216


def test_a_closed_journal_leaves_a_complete_file_behind(tmp_path):
    path = tmp_path / "j.sqlite"
    journal = make(path)
    journal.execute(cmd("e1", treasury=-5, alice=5))
    del journal
    copy = tmp_path / "copy.sqlite"
    copy.write_bytes(path.read_bytes())
    assert make(copy).balances() == {"treasury": 95, "alice": 5}

"""One serialized authority, signed heads, equivocation (NS-40.4-3, design Part B, PR 3). Test credits only."""
import hashlib
import itertools
import json
import shutil
import sqlite3
import subprocess
import sys
import textwrap
import threading
from pathlib import Path

import pytest

from agent_friday.services import issuer_authority as auth
from agent_friday.services import issuer_journal as ij
from agent_friday.services import issuer_registry as reg
from agent_friday.services import peer_wire_v2 as wire

SRC = str(Path(auth.__file__).resolve().parents[2])
SEED = bytes(range(32))
OTHER_SEED = bytes(range(1, 33))


def key(seed=SEED, generation=0):
    return auth.Ed25519Key(seed, generation)


def charter(**over):
    base = dict(issuer_id="test.one", unit_code="TESTP", precision=0, genesis_supply=100,
                treasury_account="treasury", cash_claim=False, genesis_authority=key().public_key,
                created="2026-11-16T00:00:00Z")
    return reg.IssuerCharter(**{**base, **over})


def cmd(effect, **postings):
    return {"issuer": "test.one", "effect": effect, "action": "act-" + effect, "postings": postings}


def open_authority(path, **over):
    return auth.Authority(path, charter(), key(**over))


def test_a_second_authority_is_refused_until_the_first_closes(tmp_path):
    path = tmp_path / "j.sqlite"
    first = open_authority(path)
    with pytest.raises(auth.AuthorityHeld):
        open_authority(path)
    first.close()
    open_authority(path).close()


HOLD = textwrap.dedent("""
    import sys, time
    sys.path.insert(0, {src!r})
    from agent_friday.services import issuer_authority as a, issuer_registry as r
    k = a.Ed25519Key(bytes(range(32)))
    c = r.IssuerCharter(issuer_id="test.one", unit_code="TESTP", precision=0, genesis_supply=100,
        treasury_account="treasury", cash_claim=False, genesis_authority=k.public_key, created="2026-11-16T00:00:00Z")
    held = a.Authority({path!r}, c, k)
    print("held", flush=True)
    sys.stdin.read()
""")


def test_another_process_holding_the_lock_refuses_this_one(tmp_path):
    path = tmp_path / "j.sqlite"
    child = subprocess.Popen([sys.executable, "-c", HOLD.format(src=SRC, path=str(path))],
                             stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
    try:
        assert child.stdout.readline().strip() == "held"
        with pytest.raises(auth.AuthorityHeld):
            open_authority(path)
    finally:
        child.stdin.close()
        child.wait(timeout=30)
    open_authority(path).close()


def test_only_the_charters_own_key_can_be_the_authority(tmp_path):
    with pytest.raises(ValueError):
        auth.Authority(tmp_path / "j.sqlite", charter(), key(OTHER_SEED))
    with pytest.raises(ValueError):
        auth.Authority(tmp_path / "k.sqlite", charter(), key(SEED, generation=1))
    assert not (tmp_path / "j.sqlite.authority").exists() or open_authority(tmp_path / "j.sqlite")


def test_each_head_links_to_the_one_before(tmp_path):
    with open_authority(tmp_path / "j.sqlite") as a:
        for n in range(3):
            a.execute(cmd(f"e{n}", treasury=-1, **{f"p{n}": 1}))
        digest = reg.charter_digest(charter())
        last = auth.verify_chain(a.heads(), key().public_key, digest)
        assert last.seq == 3 and last.prev_head_hash != auth.GENESIS_HEAD
        heads = a.heads()
        assert [auth.Head.parse(h).seq for h, _ in heads] == [1, 2, 3]
        with pytest.raises(auth.HeadError):
            auth.verify_chain([heads[0], heads[2]], key().public_key, digest)
        with pytest.raises(auth.HeadError):
            auth.verify_chain([heads[1], heads[0]], key().public_key, digest)
        with pytest.raises(auth.HeadError):
            auth.verify_chain([], key().public_key, digest)
        with pytest.raises(auth.HeadError):
            auth.verify_chain(heads[1:], key().public_key, digest)


def test_a_receipt_verifies_against_the_head_chain(tmp_path):
    with open_authority(tmp_path / "j.sqlite") as a:
        outcome, signed = a.execute(cmd("e1", treasury=-5, alice=5))
        assert outcome is ij.Outcome.POSTED
        head = auth.verify_receipt(signed, key().public_key, reg.charter_digest(charter()))
        assert head.seq == 1 and head.state_hash == auth.state_hash({"treasury": 95, "alice": 5})
        again_outcome, again = a.execute(cmd("e1", treasury=-5, alice=5) | {"action": "fresh"})
        assert again_outcome is ij.Outcome.DUPLICATE and again == signed


def test_a_changed_receipt_head_or_signature_fails(tmp_path):
    with open_authority(tmp_path / "j.sqlite") as a:
        a.execute(cmd("e0", treasury=-1, bob=1))
        _, signed = a.execute(cmd("e1", treasury=-5, alice=5))
    digest = reg.charter_digest(charter())
    pub = key().public_key
    other_receipt = wire.canonical({**wire.decode(signed.receipt), "seq": 1})
    forged_head = auth.Head.parse(signed.head)
    forged = wire.canonical({"issuer_id": forged_head.issuer_id, "charter_digest": forged_head.charter_digest,
                             "generation": 0, "seq": 2, "state_hash": "0" * 64, "receipt_hash": forged_head.receipt_hash,
                             "prev_head_hash": forged_head.prev_head_hash})
    cases = [auth.SignedReceipt(other_receipt, signed.head, signed.signature),
             auth.SignedReceipt(signed.receipt, forged, signed.signature),
             auth.SignedReceipt(signed.receipt, signed.head, "00" * 64),
             auth.SignedReceipt(signed.receipt, signed.head, signed.signature[:-2] + "00"),
             auth.SignedReceipt(signed.receipt, None, None)]
    for bad in cases:
        with pytest.raises(auth.HeadError):
            auth.verify_receipt(bad, pub, digest)
    auth.verify_receipt(signed, pub, digest)
    with pytest.raises(auth.HeadError):
        auth.verify_receipt(signed, key(OTHER_SEED).public_key, digest)


def test_a_head_signature_is_bound_to_its_purpose_issuer_and_charter(tmp_path):
    with open_authority(tmp_path / "j.sqlite") as a:
        _, signed = a.execute(cmd("e1", treasury=-5, alice=5))
    pub = key().public_key
    with pytest.raises(auth.HeadError):
        auth.verify_receipt(signed, pub, reg.charter_digest(charter(genesis_supply=999)))
    plain = key().sign(signed.head)
    with pytest.raises(auth.HeadError):
        auth.verify_head(signed.head, plain, pub)
    assert auth.verify_head(signed.head, signed.signature, pub).issuer_id == "test.one"


def test_a_head_missing_after_a_crash_is_signed_on_the_next_open(tmp_path):
    path = tmp_path / "j.sqlite"
    with open_authority(path) as a:
        a.execute(cmd("e1", treasury=-5, alice=5))
    with sqlite3.connect(path) as db:
        db.execute("DELETE FROM journal_heads")
    with open_authority(path) as a:
        assert [auth.Head.parse(h).seq for h, _ in a.heads()] == [1]
        auth.verify_chain(a.heads(), key().public_key, reg.charter_digest(charter()))


def test_a_copy_without_the_key_reads_as_of_a_sequence_and_posts_nothing(tmp_path):
    path = tmp_path / "j.sqlite"
    with open_authority(path) as a:
        a.execute(cmd("e1", treasury=-5, alice=5))
    copy = tmp_path / "copy.sqlite"
    shutil.copy(path, copy)
    view = auth.ReadOnlyView(copy)
    assert view.as_of_seq() == 1 and view.balances() == {"treasury": 95, "alice": 5}
    auth.verify_chain(view.heads(), key().public_key, reg.charter_digest(charter()))
    assert not [n for n in dir(view) if n.startswith(("exec", "post", "write", "sign", "insert"))]
    with pytest.raises(sqlite3.OperationalError):
        with view._db() as db:
            db.execute("DELETE FROM journal_balances")
    with pytest.raises(ValueError):
        auth.Authority(copy, charter(), key(OTHER_SEED))


def test_threads_share_one_authority_and_overdraw_nothing(tmp_path):
    with open_authority(tmp_path / "j.sqlite") as a:
        results = []

        def spend(n):
            results.append(a.execute(cmd(f"e{n}", treasury=-40, **{f"p{n}": 40}))[0])
        threads = [threading.Thread(target=spend, args=(n,)) for n in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert results.count(ij.Outcome.POSTED) == 2 and a.journal.balances()["treasury"] == 20
        auth.verify_chain(a.heads(), key().public_key, reg.charter_digest(charter()))


def pin_setup(tmp_path):
    with open_authority(tmp_path / "j.sqlite") as a:
        a.execute(cmd("e1", treasury=-5, alice=5))
        a.execute(cmd("e2", treasury=-5, bob=5))
        return a.heads(), reg.charter_digest(charter())


def test_two_heads_at_one_sequence_refuse_the_issuer_and_report_once(tmp_path):
    heads, digest = pin_setup(tmp_path)
    pub = key().public_key
    pins = auth.HeadPins()
    assert pins.accept(*heads[0], pub, digest) is auth.PinOutcome.ACCEPTED
    assert pins.accept(*heads[0], pub, digest) is auth.PinOutcome.KNOWN
    parsed = auth.Head.parse(heads[0][0])
    rival = auth.Head(parsed.issuer_id, parsed.charter_digest, 0, parsed.seq, "f" * 64, parsed.receipt_hash,
                      parsed.prev_head_hash)
    signed_rival = (rival.canonical_bytes(), key().sign(auth.HEAD_PREFIX + rival.canonical_bytes()))
    assert pins.accept(*signed_rival, pub, digest) is auth.PinOutcome.EQUIVOCATION
    assert pins.stopped("test.one") and len(pins.reports) == 1
    assert pins.accept(*heads[1], pub, digest) is auth.PinOutcome.REFUSED
    assert pins.accept(*signed_rival, pub, digest) is auth.PinOutcome.REFUSED
    assert len(pins.reports) == 1, "reported once"


def test_a_pinned_head_survives_a_restart_and_so_does_the_stop(tmp_path):
    heads, digest = pin_setup(tmp_path)
    pub = key().public_key
    file = tmp_path / "pins.json"
    pins = auth.HeadPins(file)
    pins.accept(*heads[0], pub, digest)
    assert auth.HeadPins(file).highest("test.one") == 1
    parsed = auth.Head.parse(heads[0][0])
    rival = auth.Head(parsed.issuer_id, parsed.charter_digest, 0, parsed.seq, "f" * 64, parsed.receipt_hash,
                      parsed.prev_head_hash)
    pins.accept(rival.canonical_bytes(), key().sign(auth.HEAD_PREFIX + rival.canonical_bytes()), pub, digest)
    again = auth.HeadPins(file)
    assert again.stopped("test.one") and len(again.reports) == 1
    assert again.accept(*heads[1], pub, digest) is auth.PinOutcome.REFUSED


def test_a_head_that_does_not_verify_is_never_pinned(tmp_path):
    heads, digest = pin_setup(tmp_path)
    pins = auth.HeadPins()
    assert pins.accept(heads[0][0], "00" * 64, key().public_key, digest) is auth.PinOutcome.INVALID
    assert pins.accept(*heads[0], key(OTHER_SEED).public_key, digest) is auth.PinOutcome.INVALID
    assert pins.accept(*heads[0], key().public_key, "0" * 64) is auth.PinOutcome.INVALID
    assert pins.highest("test.one") == 0 and not pins.stopped("test.one")


def test_the_key_is_never_written_to_the_journal_file(tmp_path):
    path = tmp_path / "j.sqlite"
    with open_authority(path) as a:
        a.execute(cmd("e1", treasury=-5, alice=5))
    for name in tmp_path.iterdir():
        data = name.read_bytes()
        assert SEED not in data and SEED.hex().encode() not in data, name.name


# ── second-review findings (PR #901) and design 10.1-10.4, written red first ──────────────────────

def test_a_receipt_whose_effect_or_hash_was_changed_fails_under_the_genuine_head(tmp_path):
    """Finding 1 / T1: the head did not commit to the receipt, so a forged receipt verified."""
    with open_authority(tmp_path / "j.sqlite") as a:
        _, signed = a.execute(cmd("pay-alice", treasury=-5, alice=5))
    digest = reg.charter_digest(charter())
    body = wire.decode(signed.receipt)
    for change in ({"effect": "pay-mallory"}, {"request_hash": "f" * 64}, {"corrects": "x", "reason": "y"},
                   {"issuer": "other"}):
        forged = auth.SignedReceipt(wire.canonical({**body, **change}), signed.head, signed.signature)
        with pytest.raises(auth.HeadError):
            auth.verify_receipt(forged, key().public_key, digest)
    assert auth.Head.parse(signed.head).receipt_hash == hashlib.sha256(signed.receipt).hexdigest()


def test_after_a_crash_that_skipped_heads_the_next_open_signs_every_missing_one_with_the_right_state(tmp_path):
    """T2: heads for every sequence number, each rebuilt from the effects up to it."""
    path = tmp_path / "j.sqlite"
    with open_authority(path) as a:
        for n in range(4):
            a.execute(cmd(f"e{n}", treasury=-(n + 1), **{f"p{n}": n + 1}))
    with sqlite3.connect(path) as db:
        db.execute("DELETE FROM journal_heads WHERE seq IN (3, 4)")      # a crash loses the trailing heads
    with open_authority(path) as a:
        heads = a.heads()
        assert [auth.Head.parse(h).seq for h, _ in heads] == [1, 2, 3, 4]
        auth.verify_chain(heads, key().public_key, reg.charter_digest(charter()))
        for h, _ in heads:
            head = auth.Head.parse(h)
            assert head.state_hash == auth.state_hash(a.journal.balances_at(head.seq)), head.seq
        assert auth.Head.parse(heads[1][0]).state_hash != auth.Head.parse(heads[3][0]).state_hash


def test_an_old_duplicate_returns_its_receipt_with_its_own_head_even_after_a_gap(tmp_path):
    path = tmp_path / "j.sqlite"
    with open_authority(path) as a:
        _, first = a.execute(cmd("e1", treasury=-1, a1=1))
        a.execute(cmd("e2", treasury=-1, a2=1))
        a.execute(cmd("e3", treasury=-1, a3=1))
    with sqlite3.connect(path) as db:
        db.execute("DELETE FROM journal_heads WHERE seq IN (2, 3)")
    with open_authority(path) as a:
        again_command = cmd("e2", treasury=-1, a2=1)
        again_command["action"] = "fresh"
        outcome, again = a.execute(again_command)
        assert outcome is ij.Outcome.DUPLICATE and again.head is not None
        auth.verify_receipt(again, key().public_key, reg.charter_digest(charter()))
        assert auth.Head.parse(again.head).seq == 2 and again.receipt != first.receipt


def _heads_for_pins(tmp_path):
    with open_authority(tmp_path / "j.sqlite") as a:
        for n in range(3):
            a.execute(cmd(f"e{n}", treasury=-1, **{f"p{n}": 1}))
        return a.heads(), reg.charter_digest(charter())


def _unlinked_third(heads):
    third = auth.Head.parse(heads[2][0])
    rival = auth.Head(third.issuer_id, third.charter_digest, 0, 3, third.state_hash, third.receipt_hash, "e" * 64)
    return rival.canonical_bytes(), key().sign(auth.HEAD_PREFIX + rival.canonical_bytes())


def test_a_head_that_does_not_link_to_its_pinned_neighbour_is_equivocation_in_every_arrival_order(tmp_path):
    """Finding 4 / T3: two histories that never reuse a sequence number passed."""
    heads, digest = _heads_for_pins(tmp_path)
    pub = key().public_key
    bad = _unlinked_third(heads)
    for order in itertools.permutations(range(3)):
        pins = auth.HeadPins()
        seen = []
        for index in order:
            item = bad if index == 2 else heads[index]
            seen.append(pins.accept(*item, pub, digest))
        assert auth.PinOutcome.EQUIVOCATION in seen, (order, seen)
        assert pins.stopped("test.one") and len(pins.reports) == 1, order
    pins = auth.HeadPins()
    assert [pins.accept(*h, pub, digest) for h in heads] == [auth.PinOutcome.ACCEPTED] * 3
    assert not pins.stopped("test.one")


def test_a_head_whose_neighbours_are_not_pinned_yet_is_accepted_unlinked_and_checked_later(tmp_path):
    heads, digest = _heads_for_pins(tmp_path)
    pins = auth.HeadPins()
    assert pins.accept(*heads[2], key().public_key, digest) is auth.PinOutcome.ACCEPTED
    assert pins.accept(*heads[0], key().public_key, digest) is auth.PinOutcome.ACCEPTED
    assert pins.accept(*heads[1], key().public_key, digest) is auth.PinOutcome.ACCEPTED, "the middle links both sides"
    assert not pins.stopped("test.one")


def test_a_deep_tamper_that_re_hashes_everything_is_caught_by_the_last_head_on_open(tmp_path):
    """Finding 2 / T4 with an authority: the journal's own checks pass a tamper that rewrites the effect, its hash,
    its receipt and the balances together; the signed head's state hash does not."""
    path = tmp_path / "j.sqlite"
    with open_authority(path) as a:
        a.execute(cmd("e1", treasury=-5, alice=5))
    postings = {"treasury": -5, "mallory": 5}
    digest = ij.request_hash("test.one", "e1", postings, None, None)
    with sqlite3.connect(path) as db:
        db.execute("UPDATE journal_effects SET postings=?, request_hash=? WHERE effect='e1'",
                   (json.dumps(sorted(postings.items()), separators=(",", ":")), digest))
        db.execute("UPDATE journal_receipts SET receipt=? WHERE effect='e1'",
                   (ij._receipt("test.one", "e1", 1, digest, None, None),))
        db.execute("DELETE FROM journal_balances WHERE account='alice'")
        db.execute("INSERT INTO journal_balances VALUES ('mallory', 5)")
    plain = ij.IssuerJournal(path, charter=charter())
    assert plain.halted is None, "the journal alone cannot tell"
    del plain
    with open_authority(path) as a:
        assert a.journal.halted, "the signed head's state hash differs from the rebuilt balances"
        outcome, signed = a.execute(cmd("e2", treasury=-1, bob=1))
        assert outcome is ij.Outcome.HALTED and signed is None


SNEAK = textwrap.dedent("""
    import sys
    sys.path.insert(0, {src!r})
    from agent_friday.services import issuer_authority as a, issuer_journal as j, issuer_registry as r
    k = a.Ed25519Key(bytes(range(32)))
    c = r.IssuerCharter(issuer_id="test.one", unit_code="TESTP", precision=0, genesis_supply=100,
        treasury_account="treasury", cash_claim=False, genesis_authority=k.public_key, created="2026-11-16T00:00:00Z")
    try:
        j.IssuerJournal({path!r}, charter=c).execute({{"issuer": "test.one", "effect": "sneak", "action": "sneak",
                                                         "postings": {{"treasury": -1, "mallory": 1}}}})
        print("POSTED")
    except a.AuthorityHeld:
        print("AuthorityHeld")
""")


def test_while_an_authority_holds_an_issuer_no_other_object_can_post_to_it(tmp_path):
    """Finding 3 / T6: a bare IssuerJournal posted while an Authority held the lock."""
    path = tmp_path / "j.sqlite"
    with open_authority(path) as a:
        a.execute(cmd("e1", treasury=-1, a1=1))
        with pytest.raises(auth.AuthorityHeld):
            ij.IssuerJournal(path, charter=charter()).execute(cmd("sneak", treasury=-1, mallory=1))
        other = subprocess.run([sys.executable, "-c", SNEAK.format(src=SRC, path=str(path))],
                               capture_output=True, text=True, timeout=60)
        assert "AuthorityHeld" in other.stdout, other.stdout + other.stderr
        assert a.journal.head() == 1 and "mallory" not in a.journal.balances()
    ij.IssuerJournal(path, charter=charter()).execute(cmd("fine", treasury=-1, bob=1))


def test_the_writing_journal_is_constructed_only_where_the_guard_allows():
    import importlib.util
    guard_path = Path(__file__).resolve().parents[2] / "scripts" / "check_journal_writers.py"
    spec = importlib.util.spec_from_file_location("check_journal_writers", guard_path)
    guard = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(guard)
    assert guard.run() == []
    assert guard.findings("src/agent_friday/services/other.py", "j = IssuerJournal(p, charter=c)")
    assert guard.findings("src/agent_friday/services/other.py", "j = ij.IssuerJournal(p, charter=c)")
    assert not guard.findings("src/agent_friday/services/issuer_authority.py", "j = IssuerJournal(p, charter=c)")
    assert not guard.findings("src/agent_friday/services/other.py", "x = 1  # IssuerJournal is mentioned here")


def test_a_missing_head_in_the_middle_of_the_chain_is_tampering_and_halts_the_journal(tmp_path):
    path = tmp_path / "j.sqlite"
    with open_authority(path) as a:
        for n in range(3):
            a.execute(cmd(f"e{n}", treasury=-1, **{f"p{n}": 1}))
    with sqlite3.connect(path) as db:
        db.execute("DELETE FROM journal_heads WHERE seq=2")
    with open_authority(path) as a:
        assert a.journal.halted
        assert a.execute(cmd("e9", treasury=-1, z=1))[0] is ij.Outcome.HALTED


def test_a_head_that_names_a_sequence_beyond_the_journal_halts_it(tmp_path):
    path = tmp_path / "j.sqlite"
    with open_authority(path) as a:
        a.execute(cmd("e1", treasury=-1, a1=1))
        a.execute(cmd("e2", treasury=-1, a2=1))
    with sqlite3.connect(path) as db:
        db.execute("DELETE FROM journal_effects WHERE seq=2")
        db.execute("DELETE FROM journal_receipts WHERE seq=2")
        db.execute("UPDATE journal_balances SET amount=99 WHERE account='treasury'")
        db.execute("DELETE FROM journal_balances WHERE account='a2'")
    with open_authority(path) as a:
        assert a.journal.halted


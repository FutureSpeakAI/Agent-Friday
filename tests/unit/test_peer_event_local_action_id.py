"""A peer's action_id is durably mapped to one canonical local action id (Federation / Economy spec NS-40.2-6). TEST DATA ONLY.

The peer names a logical action with (realm, actor, action_id). Friday's own receipts name actions by a local `act_` + 20 hex id
(governance/action_gate). The mapping must be stable per logical action, reused on a retry, durable across a restart, and must not
move when a different event arrives under the same identity (the first stays authoritative). What each check bites on:
  1. before anything is recorded there is no local id; after the first event there is one, in the local `act_<20 hex>` form;
  2. a replay of the same event and an equivocating event under the same (realm, actor, action_id) both leave the id unchanged;
  3. another action id, and another actor under the same action id, get other local ids (no collisions between logical actions);
  4. a new store object on the same file (a restart) returns the same id;
  5. a store file written before the mapping existed (no column) is opened, keeps every row, and gets the SAME ids a fresh store
     would give those events (a stored-or-derived id never changes with the schema);
  6. two separate stores that record the same event agree on its id (the id is a function of the identity, nothing random);
     the check can fail: with local_action_id_for replaced by a random id the two stores disagree (the mutant below), and
     test 5 (an old store against a fresh one) breaks the same way;
  7. two processes opening one old file at once: the loser of the ALTER race must not fail (the column is already there).
"""
import json
import re
import sqlite3
from pathlib import Path

import pytest

from agent_friday.services import peer_wire_v2 as wire

BUNDLE = Path(__file__).resolve().parents[1] / "federation" / "bundle"
VECTORS = json.loads((BUNDLE / "fixtures" / "wire-v2.json").read_text(encoding="utf-8"))
TRUST = set(VECTORS["peer_keys"].values())
BY_NAME = {c["name"]: c for c in VECTORS["cases"]}
LOCAL = re.compile(r"^act_[0-9a-f]{20}$")


def _raw(name):
    case = BY_NAME[name]
    return bytes.fromhex(case["raw_hex"]) if "raw_hex" in case else wire.canonical(case["envelope"])


def _identity(name):
    body = BY_NAME[name]["envelope"]["body"]
    return body["realm"], body["actor"], body["action_id"]


def test_no_local_id_before_the_first_event_and_a_local_form_id_after_it(tmp_path):
    store = wire.PeerEventStore(tmp_path / "peer.db")
    assert store.local_action_id(*_identity("signed_offer")) is None
    wire.receive(_raw("signed_offer"), TRUST, store)
    assert LOCAL.match(store.local_action_id(*_identity("signed_offer")))


def test_a_replay_and_an_equivocating_event_leave_the_local_id_unchanged(tmp_path):
    store = wire.PeerEventStore(tmp_path / "peer.db")
    wire.receive(_raw("signed_offer"), TRUST, store)
    first = store.local_action_id(*_identity("signed_offer"))
    assert wire.receive(_raw("repeat_signed_offer"), TRUST, store)[0] == "replay"
    assert wire.receive(_raw("new_bytes_same_action"), TRUST, store)[0] == "equivocation"
    assert store.local_action_id(*_identity("signed_offer")) == first


def test_other_logical_actions_get_other_local_ids(tmp_path):
    store = wire.PeerEventStore(tmp_path / "peer.db")
    for name in ("signed_offer", "countersign", "payer_requests_exact_reservation"):
        wire.receive(_raw(name), TRUST, store)
    ids = [store.local_action_id(*_identity(n)) for n in ("signed_offer", "countersign", "payer_requests_exact_reservation")]
    assert len(set(ids)) == 3 and all(LOCAL.match(i) for i in ids)


def test_the_mapping_survives_a_restart(tmp_path):
    path = tmp_path / "peer.db"
    store = wire.PeerEventStore(path)
    wire.receive(_raw("countersign"), TRUST, store)
    before = store.local_action_id(*_identity("countersign"))
    assert wire.PeerEventStore(path).local_action_id(*_identity("countersign")) == before


def test_a_store_written_before_the_mapping_existed_keeps_its_rows_and_gets_the_same_ids(tmp_path):
    fresh = wire.PeerEventStore(tmp_path / "fresh.db")
    wire.receive(_raw("signed_offer"), TRUST, fresh)
    wanted = fresh.local_action_id(*_identity("signed_offer"))

    old = tmp_path / "old.db"
    with sqlite3.connect(old) as db:      # the schema as it was before the mapping
        db.executescript("""
            CREATE TABLE peer_events_v2 (
                realm TEXT NOT NULL, actor TEXT NOT NULL, action_id TEXT NOT NULL,
                event_id TEXT NOT NULL UNIQUE, raw BLOB NOT NULL, received_at REAL NOT NULL,
                PRIMARY KEY (realm, actor, action_id));
            CREATE TABLE peer_equivocations_v2 (
                realm TEXT NOT NULL, actor TEXT NOT NULL, action_id TEXT NOT NULL,
                first_event_id TEXT NOT NULL, other_event_id TEXT NOT NULL, other_raw BLOB NOT NULL,
                seen_at REAL NOT NULL, PRIMARY KEY (realm, actor, action_id, other_event_id));""")
        verified = wire.verify_event(_raw("signed_offer"), TRUST)
        db.execute("INSERT INTO peer_events_v2 VALUES (?,?,?,?,?,?)",
                   (*_identity("signed_offer"), verified.event_id, _raw("signed_offer"), 1.0))
    reopened = wire.PeerEventStore(old)
    assert reopened.local_action_id(*_identity("signed_offer")) == wanted
    assert wire.receive(_raw("signed_offer"), TRUST, reopened)[0] == "replay", "the old row still counts"


def test_two_stores_that_record_the_same_event_agree_on_its_id(tmp_path):
    a, b = wire.PeerEventStore(tmp_path / "a.db"), wire.PeerEventStore(tmp_path / "b.db")
    wire.receive(_raw("signed_offer"), TRUST, a)
    wire.receive(_raw("signed_offer"), TRUST, b)
    assert a.local_action_id(*_identity("signed_offer")) == b.local_action_id(*_identity("signed_offer"))


def test_the_check_can_fail_a_random_id_makes_two_stores_disagree(tmp_path, monkeypatch):
    import uuid
    monkeypatch.setattr(wire, "local_action_id_for", lambda realm, actor, action_id: "act_" + uuid.uuid4().hex[:20])
    a, b = wire.PeerEventStore(tmp_path / "a.db"), wire.PeerEventStore(tmp_path / "b.db")
    wire.receive(_raw("signed_offer"), TRUST, a)
    wire.receive(_raw("signed_offer"), TRUST, b)
    assert a.local_action_id(*_identity("signed_offer")) != b.local_action_id(*_identity("signed_offer")), \
        "a mapping that is not a function of the identity is what the agreement test catches"


def test_the_loser_of_the_migration_race_does_not_fail(tmp_path, monkeypatch):
    path = tmp_path / "peer.db"
    wire.receive(_raw("signed_offer"), TRUST, wire.PeerEventStore(path))   # a migrated file: the column exists
    # a second opener that read the table before the first one's ALTER still sees "no column"
    monkeypatch.setattr(wire.PeerEventStore, "_has_local_action_column", lambda self, db: False)
    store = wire.PeerEventStore(path)                                      # must not raise "duplicate column name"
    assert store.local_action_id(*_identity("signed_offer")) is not None

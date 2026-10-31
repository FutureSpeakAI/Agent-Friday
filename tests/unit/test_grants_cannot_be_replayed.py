"""Grants expire and count their uses, and no token or grant can be replayed (§34.5:
NS-34.5-5, NS-34.5-6).

A grant lets unattended work run named outward tools without a card, for one scope,
until it expires or its uses run out. Before, its count and its very existence lived
only in governance/grants.json: putting back an older copy of that file gave spent
uses back and brought a revoked grant back, and a grant written into it by hand was
honoured. Now a grant is signed with the governance key, and its uses and its
revocation are durable entries in the action ledger, which is chained and anchored
outside the data folder. The file is only a list.

The approval-card side of replay is pinned here too: a decided card cannot be decided
again, and an approved card is carried out once.
"""
from __future__ import annotations

import json
import time

import pytest

from agent_friday.governance import action_gate as gate


@pytest.fixture
def grants_file(tmp_path, monkeypatch):
    f = tmp_path / "grants.json"
    monkeypatch.setattr(gate, "_grants_file", lambda: f)
    monkeypatch.setattr(gate, "_gov_dir", lambda: tmp_path)
    return f


def _grant(uses=2, seconds=600, scope=None):
    import uuid
    return gate.create_grant(tools=["send_email"], scope=scope or ("job_" + uuid.uuid4().hex[:8]),
                             expires_in_seconds=seconds, max_uses=uses, created_by="owner")


def _use(g):
    return gate.consume_grant("send_email", g["scope"])


def test_a_grant_counts_its_uses_and_stops(grants_file):
    g = _grant(uses=2)
    assert _use(g) and _use(g)
    assert _use(g) is None, "a third use of a two-use grant ran"


def test_putting_back_an_older_grants_file_does_not_give_uses_back(grants_file):
    g = _grant(uses=2)
    before = grants_file.read_bytes()            # uses_left 2
    assert _use(g) and _use(g)
    grants_file.write_bytes(before)              # an older copy put back
    assert _use(g) is None, "spent uses came back with an older grants.json"
    assert not [x for x in gate.list_grants() if x["grant_id"] == g["grant_id"]]


def test_putting_back_an_older_grants_file_does_not_bring_a_revoked_grant_back(grants_file):
    g = _grant(uses=5)
    before = grants_file.read_bytes()
    assert gate.revoke_grant(g["grant_id"]) is True
    grants_file.write_bytes(before)
    assert _use(g) is None, "a revoked grant came back with an older grants.json"


def test_a_grant_written_or_edited_by_hand_is_not_honoured(grants_file):
    g = _grant(uses=1)
    rows = json.loads(grants_file.read_text(encoding="utf-8"))
    # Edited: more uses and a later expiry on a real grant.
    rows[0]["max_uses"] = 999
    rows[0]["uses_left"] = 999
    rows[0]["expires_at"] = time.time() + 10 ** 9
    # Written: a grant nobody approved.
    rows.append({"grant_id": "grant_forged", "tools": ["send_email"], "scope": "job_forged",
                 "expires_at": time.time() + 3600, "max_uses": 50, "uses_left": 50,
                 "created_by": "owner", "note": "", "created_at": time.time()})
    grants_file.write_text(json.dumps(rows), encoding="utf-8")
    assert _use(g) is None, "an edited grant was honoured"
    assert gate.consume_grant("send_email", "job_forged") is None, "a hand-written grant was honoured"
    assert not gate.list_grants()


def test_a_grant_expires(grants_file):
    g = _grant(uses=5, seconds=0.3)
    time.sleep(0.5)
    assert _use(g) is None


def test_a_use_that_cannot_be_recorded_does_not_happen(grants_file, monkeypatch):
    from agent_friday.services import action_ledger
    g = _grant(uses=1)

    def broken(*a, **k):
        raise OSError("disk full")
    # Only the broken append is put back afterwards: monkeypatch.undo() would
    # also undo the root conftest's per-test action ledger (FRIDAY_LEDGER_DIR),
    # and the second use would then read whatever the shared home's ledger holds.
    with monkeypatch.context() as m:
        m.setattr(action_ledger, "append", broken)
        assert _use(g) is None, "a use ran with no record of it"
    assert _use(g), "the unrecorded attempt spent the grant"


def test_a_revocation_is_recorded_even_when_the_list_had_lost_the_grant(grants_file):
    g = _grant(uses=3)
    before = grants_file.read_bytes()
    grants_file.write_text("[]", encoding="utf-8")         # the list lost it
    gate.revoke_grant(g["grant_id"])
    grants_file.write_bytes(before)                          # and it is put back
    assert _use(g) is None


def test_no_grant_is_spent_while_outward_actions_are_held(grants_file, monkeypatch):
    g = _grant(uses=3)
    monkeypatch.setattr(gate, "outward_hold", lambda: "the action ledger was changed on disk")
    assert gate.consume_grant("send_email", g["scope"]) is None


def test_grants_from_before_signing_are_signed_once_then_unsigned_ones_are_refused(tmp_path, monkeypatch):
    # Each test has its own action ledger (conftest); this process has not yet seen
    # the one-time signing.
    from agent_friday.services import action_ledger
    monkeypatch.setattr(gate, "_MIGRATION_SEEN", False, raising=False)
    gov = tmp_path / "gov"
    gov.mkdir()
    f = gov / "grants.json"
    monkeypatch.setattr(gate, "_gov_dir", lambda: gov)
    monkeypatch.setattr(gate, "_grants_file", lambda: f)
    old = {"grant_id": "grant_legacy01", "tools": ["send_email"], "scope": "job_legacy",
           "expires_at": time.time() + 3600, "uses_left": 1, "created_by": "owner", "note": "",
           "created_at": time.time()}
    f.write_text(json.dumps([old]), encoding="utf-8")
    assert gate.consume_grant("send_email", "job_legacy"), "a grant made before this version stopped working"
    assert gate.consume_grant("send_email", "job_legacy") is None
    rows = json.loads(f.read_text(encoding="utf-8"))
    rows.append(dict(old, grant_id="grant_legacy02", scope="job_late"))
    rows[-1].pop("sig", None)
    f.write_text(json.dumps(rows), encoding="utf-8")
    # Deleting the marker file does not get it signed: the one-time signing is in the ledger.
    for m in gov.glob("grants.signed*"):
        m.unlink()
    assert gate.consume_grant("send_email", "job_late") is None, "an unsigned grant added later was honoured"
    assert action_ledger.entries(["grants_migrated"]), "the one-time signing was not recorded"


def test_on_a_fresh_install_a_grant_written_by_hand_is_never_signed(tmp_path, monkeypatch):
    gov = tmp_path / "gov"
    gov.mkdir()
    f = gov / "grants.json"
    monkeypatch.setattr(gate, "_gov_dir", lambda: gov)
    monkeypatch.setattr(gate, "_grants_file", lambda: f)
    monkeypatch.setattr(gate, "_MIGRATION_SEEN", False, raising=False)
    assert gate.list_grants() == []                      # the first read on this version
    f.write_text(json.dumps([{"grant_id": "grant_planted1", "tools": ["send_email"],
                              "scope": "job_planted", "expires_at": time.time() + 3600,
                              "uses_left": 9, "max_uses": 9, "created_by": "owner", "note": "",
                              "created_at": time.time() - 60}]), encoding="utf-8")
    monkeypatch.setattr(gate, "_MIGRATION_SEEN", False, raising=False)   # even after a restart
    assert gate.consume_grant("send_email", "job_planted") is None, "a planted grant was signed"


def test_revoke_all_ends_a_grant_taken_off_the_list_and_put_back_later(grants_file):
    g = _grant(uses=3)
    saved = grants_file.read_bytes()
    grants_file.write_text("[]", encoding="utf-8")       # hidden before the owner revokes all
    revoke_all = getattr(gate, "revoke_all_grants", None)
    assert revoke_all is not None, "no ledger-recorded revoke-all on this tree"
    revoke_all()
    grants_file.write_bytes(saved)                       # and put back afterwards
    assert _use(g) is None
    fresh = _grant(uses=1)                               # a grant made after still works
    assert _use(fresh)


# ── approval cards: decided once, carried out once ───────────────────────────

@pytest.fixture
def cards(tmp_path, monkeypatch):
    from agent_friday.services import approvals as ap
    monkeypatch.setattr(ap, "APPROVALS_FILE", tmp_path / "approvals.json")
    monkeypatch.setattr(ap, "FRIDAY_DIR", tmp_path)
    return ap


def test_a_decided_card_cannot_be_decided_again_or_carried_out_twice(cards):
    ap = cards
    rec = ap.create_approval(kind="replay_probe", subject_type="probe", subject_id="p1",
                             title="probe", force_gate=True)
    ap.decide(rec["approval_id"], "deny")
    again = ap.decide(rec["approval_id"], "approve")
    assert (again or {}).get("status") == "denied", "a denied card was approved later"
    rec2 = ap.create_approval(kind="replay_probe", subject_type="probe", subject_id="p2",
                              title="probe", force_gate=True)
    ap.decide(rec2["approval_id"], "approve")
    assert ap.claim_for_execution(rec2["approval_id"]) is True
    assert ap.claim_for_execution(rec2["approval_id"]) is False, "an approved card ran twice"


def _key_reads(monkeypatch):
    import secrets
    from agent_friday.governance import proof_of_integrity as poi
    key = secrets.token_bytes(32)
    reads = []
    monkeypatch.setattr(poi, "get_governance_key", lambda: (reads.append(1), key)[1])
    return reads


def test_checking_grants_reads_the_governance_key_once_not_once_per_grant(grants_file, monkeypatch):
    # Each read of the governance key is a call into the OS keychain: checking a
    # list of grants costs about the same number of reads for one grant as for
    # five (the action ledger's own reads vary by one), never one per grant.
    reads = _key_reads(monkeypatch)
    first = _grant(uses=4)
    assert _use(first)                    # a first use carries one-time reads
    reads.clear()
    assert len(gate.list_grants()) == 1
    list_one = len(reads)
    reads.clear()
    assert _use(first)
    use_one = len(reads)
    for _ in range(4):
        _grant(uses=3)
    reads.clear()
    assert len(gate.list_grants()) == 5
    assert len(reads) <= list_one + 1, "listing 5 grants read the key %d times, 1 grant %d" % (len(reads), list_one)
    reads.clear()
    assert _use(first)
    assert len(reads) <= use_one + 1, "using a grant among 5 read the key %d times, among 1 %d" % (len(reads), use_one)

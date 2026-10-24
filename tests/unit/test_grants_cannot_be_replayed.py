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
    monkeypatch.setattr(action_ledger, "append", broken)
    assert _use(g) is None, "a use ran with no record of it"
    monkeypatch.undo()
    monkeypatch.setattr(gate, "_grants_file", lambda: grants_file)
    assert _use(g), "the unrecorded attempt spent the grant"


def test_grants_from_before_signing_are_signed_once_then_unsigned_ones_are_refused(tmp_path, monkeypatch):
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
    assert gate.consume_grant("send_email", "job_late") is None, "an unsigned grant added later was honoured"


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

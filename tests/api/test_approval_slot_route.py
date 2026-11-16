"""GET /api/liquid/approval-slot: ids for one subject, and nothing else (NS-41.3-1)."""
import json

import pytest

from agent_friday.services import action_ledger as L
from agent_friday.services import approvals as ap

URL = "/api/liquid/approval-slot"
CANARY = "CANARY-8d21"


@pytest.fixture(autouse=True)
def clean(friday_dir):
    if ap.APPROVALS_FILE.exists():
        ap.APPROVALS_FILE.unlink()
    yield


def _card(subject_id="c1", **kw):
    return ap.create_approval(kind="send_email", subject_type="crew", subject_id=subject_id,
                              title=CANARY, description=CANARY, action_description=CANARY,
                              force_gate=True, **kw)


def test_the_answer_holds_only_ids_and_no_card_text(client):
    card = _card()
    r = client.get(URL, query_string={"subject": "crew:c1"})
    assert r.status_code == 200
    assert r.get_json() == {"ok": True, "ids": [card["approval_id"]]}
    assert CANARY.encode() not in r.data


def test_an_unknown_subject_is_an_empty_list_not_an_error(client):
    _card()
    r = client.get(URL, query_string={"subject": "crew:nobody"})
    assert r.status_code == 200 and r.get_json() == {"ok": True, "ids": []}


@pytest.mark.parametrize("subject", [None, "", "crew", "crew:c1:x", "Crew:c1", "<script>:c1"])
def test_a_malformed_subject_is_refused_without_an_echo(client, subject):
    q = {} if subject is None else {"subject": subject}
    r = client.get(URL, query_string=q)
    assert r.status_code == 400 and r.get_json() == {"ok": False, "error": "bad_subject"}
    if subject:
        assert subject.encode() not in r.data


def test_a_malformed_kind_is_refused(client):
    r = client.get(URL, query_string={"subject": "crew:c1", "kind": "A b"})
    assert r.status_code == 400 and r.get_json() == {"ok": False, "error": "bad_kind"}


def test_reading_the_slot_writes_nothing(client):
    _card()
    approvals_before = ap.APPROVALS_FILE.read_bytes()
    ledger_before = len(L.entries(None))
    for _ in range(3):
        client.get(URL, query_string={"subject": "crew:c1"})
    assert ap.APPROVALS_FILE.read_bytes() == approvals_before
    assert len(L.entries(None)) == ledger_before


def test_a_caller_the_approvals_list_refuses_is_refused_here_too(client):
    _card()
    remote = {"REMOTE_ADDR": "203.0.113.5"}
    listing = client.get("/api/approvals", environ_base=remote)
    slot = client.get(URL, query_string={"subject": "crew:c1"}, environ_base=remote)
    assert slot.status_code == listing.status_code
    if listing.status_code != 200:
        assert CANARY.encode() not in slot.data

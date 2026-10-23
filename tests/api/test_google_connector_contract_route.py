"""NS-23.1-2 / NS-8.9-2: the API serves what the Google connection lets Friday do, reads and
changes separately, with the permission each needs and whether it waits for a card."""
from __future__ import annotations


def test_the_contract_is_served_reads_and_changes_apart(client):
    r = client.get("/api/google/accounts/contract")
    assert r.status_code == 200
    c = r.get_json().get("contract") or {}
    reads = {o["tool"] for o in c.get("reads", [])}
    changes = {o["tool"]: o for o in c.get("changes", [])}
    assert "search_email" in reads and "query_calendar" in reads
    assert changes.get("delete_calendar_event", {}).get("action_class") == "outward"
    assert "https://www.googleapis.com/auth/calendar" in changes["create_calendar_event"]["scopes"]
    assert "https://www.googleapis.com/auth/calendar" not in c.get("connect_scopes", [])
    assert "complete_task" in c.get("changes_without_a_card", [])

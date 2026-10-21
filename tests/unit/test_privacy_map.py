"""The privacy map (north star §26.22, BD-15 slice P1): what Friday holds,
where it lives, how long it is kept, and what left this PC and to whom.

Each line is read from the store or setting that decides it; what left the PC
is regenerated from the egress log on every read; payloads and the gate's
reasons never appear in the map itself. Every fixture is synthetic.
"""
from __future__ import annotations

import importlib
import json
import time

import pytest

NOW = time.time()


def _module():
    try:
        return importlib.import_module("agent_friday.services.privacy_map")
    except ImportError:
        return None


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("FRIDAY_HOME", str(tmp_path))
    import agent_friday.paths as paths
    import agent_friday.core as core
    from agent_friday.services import egress_gate
    monkeypatch.setattr(paths, "friday_home", lambda: tmp_path)
    monkeypatch.setattr(core, "FRIDAY_DIR", tmp_path, raising=False)
    monkeypatch.setattr(egress_gate, "_DEFAULT_LOG", tmp_path / "vault" / "egress-log.jsonl")
    monkeypatch.setattr(core, "_VAULT_ENCRYPTION_STATE", {"enabled": False}, raising=False)
    (tmp_path / "vault").mkdir()
    return tmp_path


def _egress(home, *rows):
    with open(home / "vault" / "egress-log.jsonl", "a", encoding="utf-8") as fh:
        for r in rows:
            fh.write(json.dumps(r) + "\n")


def _map():
    pm = _module()
    assert pm is not None, "there is no privacy map"
    return pm


def test_what_left_is_counted_by_destination_with_withheld_and_stopped_sends(home):
    pm = _map()
    _egress(home,
            {"ts": NOW - 300, "provider": "anthropic", "field": "messages", "tier": "TIER_0",
             "action": "allow", "reason": "ok", "turn_id": "t1"},
            {"ts": NOW - 200, "provider": "anthropic", "field": "messages", "tier": "TIER_1",
             "action": "redact", "reason": "span-level: withheld 1/3", "turn_id": "t2"},
            {"ts": NOW - 100, "provider": "anthropic", "field": "system", "tier": "TIER_3",
             "action": "block", "reason": "vault content"},
            {"ts": NOW - 50, "provider": "brave", "field": "query", "tier": "TIER_0",
             "action": "allow", "reason": "public query"})
    left = pm.view()["left"]
    by = {d["to"]: d for d in left["destinations"]}
    assert set(by) == {"anthropic", "brave"}
    a = by["anthropic"]
    assert (a["sent"], a["sent_with_parts_withheld"], a["stopped"]) == (2, 1, 1)
    assert a["background"] == 1, "the send with no turn is background work"
    assert a["first"] == pytest.approx(NOW - 300) and a["last"] == pytest.approx(NOW - 100)
    assert a["label"] == "Anthropic (Claude)"
    assert a["receipts"] == "/api/privacy/map/left?to=anthropic"


def test_a_new_send_appears_on_the_next_read(home):
    pm = _map()
    assert pm.view()["left"]["destinations"] == []
    _egress(home, {"ts": NOW, "provider": "openai", "field": "messages", "tier": "TIER_0",
                   "action": "allow", "reason": "ok", "turn_id": "t9"})
    dests = pm.view()["left"]["destinations"]
    assert [d["to"] for d in dests] == ["openai"] and dests[0]["sent"] == 1


def test_the_map_never_carries_a_payload_or_a_reason(home):
    pm = _map()
    _egress(home, {"ts": NOW, "provider": "anthropic", "field": "messages", "tier": "TIER_2",
                   "action": "redact", "reason": "judge: SYNTHETIC-SECRET-PHRASE about the owner",
                   "turn_id": "t1"})
    assert "SYNTHETIC-SECRET-PHRASE" not in json.dumps(pm.view())


def test_the_receipts_for_one_destination_are_its_own_rows_newest_first(home):
    pm = _map()
    _egress(home,
            {"ts": NOW - 3, "provider": "anthropic", "field": "a", "tier": "TIER_0", "action": "allow", "reason": "r", "turn_id": "t1"},
            {"ts": NOW - 2, "provider": "openai", "field": "b", "tier": "TIER_0", "action": "allow", "reason": "r"},
            {"ts": NOW - 1, "provider": "anthropic", "field": "c", "tier": "TIER_0", "action": "block", "reason": "r"})
    out = pm.left_rows("anthropic", limit=10)
    assert [r["field"] for r in out["rows"]] == ["c", "a"]
    assert out["rows"][1]["turn_receipt"] == "/api/turns/t1/receipt"
    assert len(pm.left_rows("anthropic", limit=1)["rows"]) == 1


def test_a_torn_or_oversized_log_line_is_skipped_not_fatal(home):
    pm = _map()
    with open(home / "vault" / "egress-log.jsonl", "w", encoding="utf-8") as fh:
        fh.write('{"ts": 1, "provider": "anthropic", "act\n')
        fh.write('{"x": "' + "y" * (pm.MAX_LINE + 10) + '"}\n')
        fh.write(json.dumps({"ts": NOW, "provider": "anthropic", "field": "m", "tier": "TIER_0",
                             "action": "allow", "reason": "ok"}) + "\n")
    dests = pm.view()["left"]["destinations"]
    assert dests[0]["sent"] == 1


def test_channels_the_log_does_not_see_yet_are_named(home):
    pm = _map()
    keys = {n["key"] for n in pm.view()["left"]["not_recorded"]}
    assert {"mail", "mcp", "connectors"} <= keys


def test_every_store_says_where_it_lives_how_long_and_how_to_remove_it(home):
    pm = _map()
    (home / "conversations").mkdir()
    (home / "conversations" / "c1.json").write_text("x" * 2048, encoding="utf-8")
    stores = {s["key"]: s for s in pm.view()["stores"]}
    for key in ("conversations", "memory_card", "vault", "tasks", "goals", "credentials",
                "receipts", "library", "meetings"):
        assert key in stores, key
    for s in stores.values():
        assert s["where"] and all(not w.startswith(("/", "\\")) and ".." not in w for w in s["where"])
        assert s["retention_words"] and s["encrypted_words"]
        assert any(c["kind"] in ("delete", "export") for c in s["controls"]), \
            s["key"] + " has no delete or export control"
        for c in s["controls"]:
            assert c["kind"] in ("delete", "export", "settings")
            assert c.get("href") or c.get("nav"), (s["key"], c)
    conv = stores["conversations"]
    assert conv["present"] is True and conv["bytes"] == 2048 and conv["files"] == 1
    assert stores["goals"]["present"] is False and stores["goals"]["size_words"] == "Nothing stored yet."


def test_retention_is_read_from_the_stores_own_setting(home, monkeypatch):
    pm = _map()
    from agent_friday.services import task_journal
    monkeypatch.setattr(task_journal, "settings", lambda: {"retention_days": 30, "encrypt_at_rest": True})
    stores = {s["key"]: s for s in pm.view()["stores"]}
    assert stores["tasks"]["retention_words"] == "Kept 30 days, then deleted."
    assert stores["tasks"]["encrypted"] == "yes"
    monkeypatch.setattr(task_journal, "settings", lambda: {"retention_days": 0, "encrypt_at_rest": False})
    stores = {s["key"]: s for s in pm.view()["stores"]}
    assert stores["tasks"]["retention_words"] == "Kept until you delete it."
    assert stores["tasks"]["encrypted"] == "no"


def test_the_vault_line_follows_the_real_lock(home, monkeypatch):
    pm = _map()
    import agent_friday.core as core
    monkeypatch.setattr(core, "_VAULT_ENCRYPTION_STATE", {"enabled": True}, raising=False)
    assert {s["key"]: s for s in pm.view()["stores"]}["vault"]["encrypted"] == "yes"


def test_mail_friday_sent_is_counted_by_recipient(home):
    pm = _map()
    with open(home / "sent_mail.jsonl", "w", encoding="utf-8") as fh:
        for row in ({"at": NOW - 10, "ok": True, "to": "a@example.com", "cc": ["b@example.com"]},
                    {"at": NOW - 5, "ok": True, "to": "a@example.com"},
                    {"at": NOW - 1, "ok": False, "to": "c@example.com", "error": "x"}):
            fh.write(json.dumps(row) + "\n")
    m = pm.view()["mail"]
    assert (m["sent"], m["failed"]) == (2, 1)
    assert {r["to"]: r["count"] for r in m["recipients"]} == {"a@example.com": 2, "b@example.com": 1}


def test_off_the_record_says_connected_services_still_keep_records(home):
    pm = _map()
    words = pm.view()["off_record"]["words"]
    assert "can still keep their own records" in words


def test_the_map_is_whole_even_when_a_source_cannot_be_read(home, monkeypatch):
    pm = _map()
    from agent_friday.services import setup_connections

    def broken():
        raise RuntimeError("synthetic")
    monkeypatch.setattr(setup_connections, "checklist", broken)
    v = pm.view()
    assert v["services"]["readable"] is False
    assert v["stores"] and "honest_limit" in v

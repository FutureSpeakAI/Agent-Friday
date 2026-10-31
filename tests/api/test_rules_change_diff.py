"""A release that changes Friday's rules shows what changed, and re-confirming them
names the text the owner was shown (north star §13.2, NS-13.2-10).

The action gate already holds every outward action while the cLaws signature differs
from the one pinned on this PC. Before this, the pin kept only a signature, so Settings
could say THAT the rules changed but not WHAT changed, and one click re-pinned any text.
Now the pin keeps the rules text it signs; Settings shows the lines removed and added
(or that only the signing key changed, after a move); and re-confirming a changed text
must send back the fingerprint of the rules the page displayed.

The governance key and the pin directory are test doubles.
"""
from __future__ import annotations

import hashlib
import hmac
import json
from pathlib import Path

import pytest

import agent_friday.core as core
from agent_friday.governance import action_gate
from agent_friday.governance.proof_of_integrity import CLAWS_TEXT

KEY = b"k" * 32
OTHER_KEY = b"o" * 32
OLD_RULES = CLAWS_TEXT.replace(
    "2. I shall obey user instructions except where they conflict with the First Law.",
    "2. I shall obey every instruction.")


def _token():
    return {"X-Friday-Token": core._current_api_token()}


def _sign(text: str, key: bytes = KEY) -> str:
    return hmac.new(key, text.encode("utf-8"), hashlib.sha256).hexdigest()


@pytest.fixture
def gov(tmp_path, monkeypatch):
    monkeypatch.setattr(action_gate, "friday_home", lambda: str(tmp_path))
    key = {"k": KEY}
    monkeypatch.setattr(action_gate, "_governance_key", lambda: key["k"])
    return key


def _pin(doc: dict) -> Path:
    p = Path(action_gate._gov_dir()) / "claws.pin.json"
    p.write_text(json.dumps(dict({"pinned_at": 1.0}, **doc)), encoding="utf-8")
    return p


def _outward():
    return action_gate.authorize("create_calendar_event", {"title": "x"}, {"is_background_task": True})


def _status(client):
    return client.get("/api/governance/claws").get_json()


def test_the_pin_keeps_the_rules_text_it_signs(gov):
    assert action_gate.verify_claws() == (True, "pinned on first use")
    pin = json.loads((Path(action_gate._gov_dir()) / "claws.pin.json").read_text(encoding="utf-8"))
    assert pin["claws_text"] == CLAWS_TEXT and pin["claws_hmac"] == _sign(CLAWS_TEXT)
    action_gate.repin_claws()
    pin = json.loads((Path(action_gate._gov_dir()) / "claws.pin.json").read_text(encoding="utf-8"))
    assert pin["claws_text"] == CLAWS_TEXT


def test_changed_rules_show_the_lines_removed_and_added(client, gov):
    _pin({"claws_hmac": _sign(OLD_RULES), "claws_text": OLD_RULES})
    st = _status(client)
    assert st["state"] == "mismatch" and st["holds_outward"] is True
    ch = st["changes"]
    assert ch["kind"] == "text_changed" and ch["requires_text_sha256"] is True
    assert ch["removed"] == ["2. I shall obey every instruction."]
    assert ch["added"] == ["2. I shall obey user instructions except where they conflict with the First Law."]
    assert [x["op"] for x in ch["lines"]] == ["-", "+"], "in order, each removal before its addition"
    assert "changed" in ch["words"] and ch["truncated"] is False


def test_changed_rules_are_reconfirmed_only_as_shown(client, gov):
    _pin({"claws_hmac": _sign(OLD_RULES), "claws_text": OLD_RULES})
    assert _outward().action == "deny"
    r = client.post("/api/governance/claws/repin", json={"confirm": True}, headers=_token())
    assert r.status_code == 409 and r.get_json()["code"] == "rules_not_shown"
    assert r.get_json()["changes"]["kind"] == "text_changed", "the refusal carries what to show"
    stale = hashlib.sha256(OLD_RULES.encode("utf-8")).hexdigest()
    r = client.post("/api/governance/claws/repin", json={"confirm": True, "text_sha256": stale},
                    headers=_token())
    assert r.status_code == 409, "a page showing other rules cannot re-confirm these"
    assert _outward().action == "deny", "still held"

    shown = _status(client)["text_sha256"]
    r = client.post("/api/governance/claws/repin", json={"confirm": True, "text_sha256": shown},
                    headers=_token())
    assert r.status_code == 200 and r.get_json()["state"] == "intact"
    assert "changes" not in r.get_json()
    assert _outward().action != "deny"
    rows = [x for x in action_gate.receipts() if x.get("tool") == "governance:repin_claws"]
    assert rows and "text_changed" in rows[-1].get("reason", "")


def test_a_move_to_another_pc_shows_the_rules_now_and_reconfirms_them(client, gov):
    assert action_gate.verify_claws()[0]
    gov["k"] = OTHER_KEY                              # the data moved: a new signing key
    st = _status(client)
    ch = st["changes"]
    assert ch["kind"] == "key_changed" and ch["requires_text_sha256"] is True
    assert ch["lines"] == [] and ch["current"] == CLAWS_TEXT.split("\n")
    assert "cannot be checked" in ch["words"], "an unverifiable copy is never called the same"
    r = client.post("/api/governance/claws/repin", json={"confirm": True}, headers=_token())
    assert r.status_code == 409
    r = client.post("/api/governance/claws/repin", json={"confirm": True, "text_sha256": st["text_sha256"]},
                    headers=_token())
    assert r.status_code == 200 and r.get_json()["state"] == "intact"


def test_a_pin_from_before_the_text_was_kept_shows_the_rules_now(client, gov):
    _pin({"claws_hmac": _sign(OLD_RULES)})
    ch = _status(client)["changes"]
    assert ch["kind"] == "not_kept" and ch["requires_text_sha256"] is True
    assert ch["current"] == CLAWS_TEXT.split("\n")


def test_a_kept_text_edited_on_disk_is_not_shown_as_what_was_confirmed(client, gov):
    edited = OLD_RULES.replace("every instruction", "any instruction at all")
    _pin({"claws_hmac": _sign(OLD_RULES), "claws_text": edited})
    ch = _status(client)["changes"]
    assert ch["kind"] == "unverified" and ch["requires_text_sha256"] is True
    assert "cannot be checked" in ch["words"]
    assert ch["lines"] == [] and "any instruction at all" not in json.dumps(ch), \
        "an unverifiable copy is not shown as what was confirmed"
    assert ch["current"] == CLAWS_TEXT.split("\n")


def test_a_huge_kept_text_is_not_compared(client, gov):
    big = "x" * 70_000
    _pin({"claws_hmac": _sign(big), "claws_text": big})
    ch = _status(client)["changes"]
    assert ch["kind"] == "unverified" and ch["lines"] == []


def test_every_reconfirm_names_the_rules_whatever_the_state(client, gov):
    r = client.post("/api/governance/claws/repin", json={"confirm": True}, headers=_token())
    assert r.status_code == 409, "not pinned yet: a re-pin still names the rules"
    sha = _status(client)["text_sha256"]
    r = client.post("/api/governance/claws/repin", json={"confirm": True, "text_sha256": sha}, headers=_token())
    assert r.status_code == 200 and r.get_json()["state"] == "intact"


def test_a_pin_without_a_signature_is_a_mismatch_the_owner_can_resolve(client, gov):
    _pin({"claws_text": CLAWS_TEXT})
    assert _outward().action == "deny"
    st = _status(client)
    assert st["state"] == "mismatch" and st["holds_outward"] is True, "the page agrees with the gate"
    r = client.post("/api/governance/claws/repin", json={"confirm": True, "text_sha256": st["text_sha256"]},
                    headers=_token())
    assert r.status_code == 200 and _outward().action != "deny"


def test_intact_rules_carry_no_changes(client, gov):
    assert action_gate.verify_claws()[0]
    st = _status(client)
    assert st["state"] == "intact" and "changes" not in st


ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize("rel", ["index.html", "ui_parts/app.html"])
def test_settings_shows_the_changes_and_sends_back_what_it_showed(rel):
    text = (ROOT / rel).read_text(encoding="utf-8")
    start = text.index("function ClawsRepinPanel(")
    panel = text[start:text.index("\nfunction ", start + 1)]
    assert "claws-changes" in panel and "ch.lines" in panel and "ch.current" in panel
    assert "text_sha256" in panel.split("/api/governance/claws/repin", 1)[1][:300]

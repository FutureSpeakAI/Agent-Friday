"""Every private-context share leaves a signed receipt saying what left.

The egress log already recorded that a share happened and how long it was.
That is enough to notice one and not enough to audit one: it cannot answer
"what did you tell the cloud model about me?". So each handoff now signs a
receipt into the decision BOM carrying the exact text that left, where it
went, which local model produced it, and the categories of what was held
back.

Two properties matter more than the contents:

  * it is signed with the governance key, so a share cannot be quietly
    edited out of the record afterwards; and
  * it is signed BEFORE the text leaves, so a receipt that cannot be written
    holds the share instead of following it. An unauditable disclosure is
    exactly the thing the receipt exists to prevent.
"""
import json

import pytest

lc = pytest.importorskip("agent_friday.services.local_context")
ag = pytest.importorskip("agent_friday.governance.action_gate")

PAYLOAD = {
    "text": "His sister enjoys hiking; reach her on [PII:phone:abc12345].",
    "placeholders": [{"placeholder": "[PII:phone:abc12345]",
                      "category": "phone number"},
                     {"placeholder": "[his sister]", "category": "relationship"}],
    "local_model": "qwen3:4b",
    "cloud_model": "gemini-3.8-live",
    "conversation_id": "conv-receipt",
    "question": "What does his sister enjoy?",
    "version": 1,
}


def _receipts():
    p = ag.receipts_path()
    if not p.exists():
        return []
    out = []
    for line in p.read_text(encoding="utf-8").splitlines():
        try:
            out.append(json.loads(line))
        except Exception:
            pass
    return out


def _shares():
    return [r for r in _receipts() if r.get("tool") == "voice.local_context_share"]


@pytest.fixture
def delivered(monkeypatch):
    sent = []
    monkeypatch.setattr(lc, "_deliver",
                        lambda cid, text, kind: sent.append((kind, text)) or True)
    monkeypatch.setattr(lc, "_audit", lambda *a, **k: None)
    monkeypatch.setattr(lc, "_note_in_conversation", lambda *a, **k: None)
    return sent


def test_a_share_writes_a_receipt_that_verifies(delivered):
    before = len(_shares())
    assert lc._send("a-r-1", "conv-receipt", PAYLOAD["text"], PAYLOAD) is True
    rows = _shares()
    assert len(rows) == before + 1, "each share gets exactly one receipt"
    rec = rows[-1]
    assert ag.verify_receipt(rec), (
        "an unsigned or altered receipt is no record at all")


def test_the_receipt_says_exactly_what_left_and_where_it_went(delivered):
    lc._send("a-r-2", "conv-receipt", PAYLOAD["text"], PAYLOAD)
    rec = _shares()[-1]
    assert rec["shared_text"] == PAYLOAD["text"], (
        "a character count cannot answer 'what did you tell them about me'")
    assert rec["destination"] == "gemini-3.8-live"
    assert rec["local_model"] == "qwen3:4b"
    assert rec["approval"] == "a-r-2"
    assert rec["chars"] == len(PAYLOAD["text"])
    assert rec["decision"] == "owner_approved"


def test_the_receipt_names_what_was_held_back_without_naming_the_value():
    """The categories say a phone number was replaced. Never the number."""
    lc.sign_receipt("a-r-3", PAYLOAD["text"], PAYLOAD)
    rec = _shares()[-1]
    assert "phone number" in rec["withheld"]
    assert "relationship" in rec["withheld"]
    blob = json.dumps(rec)
    assert "abc12345" not in blob or "[PII:phone:abc12345]" in rec["shared_text"], (
        "the only tag in the receipt should be the placeholder that left")


def test_a_grant_share_is_recorded_as_a_grant_not_an_approval(delivered):
    lc._send("grant-xyz", "conv-receipt", PAYLOAD["text"], PAYLOAD,
             under_grant="g-123")
    rec = _shares()[-1]
    assert rec["decision"] == "granted"
    assert rec["grant"] == "g-123"


# ── The receipt is a precondition, not a footnote ──────────────────────────

def test_a_share_whose_receipt_cannot_be_signed_does_not_happen(delivered,
                                                                monkeypatch):
    def refuse(entry):
        raise OSError("the ledger is read-only")

    monkeypatch.setattr(ag, "_receipt", refuse)
    ok = lc._send("a-r-4", "conv-receipt", PAYLOAD["text"], PAYLOAD)
    assert ok is False, "no receipt, no share"
    assert not [t for k, t in delivered if k == "context"], (
        "the private text must not reach the call when it cannot be recorded")


def test_the_user_is_told_when_a_share_was_held_for_that_reason(delivered,
                                                               monkeypatch):
    """Silence would read as 'Friday ignored me'."""
    monkeypatch.setattr(ag, "_receipt",
                        lambda e: (_ for _ in ()).throw(OSError("nope")))
    said = []
    monkeypatch.setattr(lc, "_note_in_conversation",
                        lambda cid, text: said.append(text))
    lc._send("a-r-5", "conv-receipt", PAYLOAD["text"], PAYLOAD)
    assert said and "NOT sent" in said[0]
    assert [t for k, t in delivered if k == "notice"], (
        "the call itself should be told so it can say something")


def test_the_receipt_is_signed_before_the_text_is_handed_over(monkeypatch):
    """Order is the whole guarantee: a receipt written afterwards cannot
    promise that nothing went out unrecorded."""
    order = []
    monkeypatch.setattr(ag, "_receipt", lambda e: order.append("receipt"))
    monkeypatch.setattr(lc, "_deliver",
                        lambda *a, **k: order.append("deliver") or True)
    monkeypatch.setattr(lc, "_audit", lambda *a, **k: None)
    lc._send("a-r-6", "conv-receipt", PAYLOAD["text"], PAYLOAD)
    assert order == ["receipt", "deliver"], order


def test_the_signer_is_the_governance_one_not_a_local_copy():
    import inspect
    src = inspect.getsource(lc.sign_receipt)
    assert "action_gate" in src and "_receipt" in src, (
        "use the signed decision BOM, not a private log file")

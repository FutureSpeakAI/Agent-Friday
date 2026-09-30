"""A private workflow result is offered scrubbed, not simply lost.

Voice can fire a workflow whose steps run on a local seat — that is how a
spoken request reaches private work. But the result has to come back, and the
egress gate's only move on TIER_3 material is to withhold it. So the owner
asked for a summary of his own mail, the work ran on his own machine, and what
arrived was "content withheld". The privacy promise held and the answer was
gone.

The work ran here, so there is another move: hand the text through the same
path an `ask_local_for_context` answer takes — scrubbed to placeholders, the
never-send floor refusing it outright if it touches that, receipted, and the
owner reading the exact words before any of it leaves.

Nothing is relaxed by this. The tests below pin both halves: the model still
never receives the raw text, and the floor still refuses outright.
"""
import pytest

vr = pytest.importorskip("agent_friday.routes.voice")
lc = pytest.importorskip("agent_friday.services.local_context")

PHONE = "(512) 555-0147"
RESULT = ('Background task "Summarise my private mail" finished.\n\n'
          "Her custody hearing is on the 14th; her lawyer wants the medical "
          "records from the clinic before then. Reach her on " + PHONE + ".")


@pytest.fixture
def offered(monkeypatch):
    """Capture what would be put on a card, without touching the store."""
    seen = {}

    def fake_offer(text, **kw):
        seen.update(kw)
        seen["text"] = text
        return {"status": seen.pop("_status", "pending"), "approval_id": "a-wf"}

    monkeypatch.setattr(lc, "offer", fake_offer)
    return seen


# ── The model never gets the raw text ──────────────────────────────────────

def test_a_withheld_task_result_becomes_a_card_instead_of_a_dead_end(offered):
    handed = vr._injection_or_card(RESULT, "task_result", "conv-wf")
    assert offered, "a private result must be offered, not dropped"
    assert PHONE not in handed, "the raw result must never reach the model"
    assert "waiting on his screen" in handed, (
        "the model has to be told there is something to mention")


def test_the_card_is_handed_the_raw_text_so_it_can_scrub_it(offered):
    vr._injection_or_card(RESULT, "task_result", "conv-wf")
    assert PHONE in offered["text"], (
        "offer() does the scrubbing; it needs the original to scrub")


def test_the_card_names_the_cloud_model_it_would_go_to(offered):
    vr._injection_or_card(RESULT, "task_result", "conv-wf")
    assert offered.get("cloud_model"), "a card must say where the text would go"
    assert offered.get("title")


def test_the_real_path_puts_a_scrubbed_summary_on_the_card():
    """End to end through the real offer(), with the store."""
    from agent_friday.services import approvals
    handed = vr._injection_or_card(RESULT, "task_result", "conv-wf-real")
    assert PHONE not in handed
    cards = [a for a in (approvals.list_approvals() or [])
             if a.get("kind") == lc.KIND]
    assert cards, "a card should be waiting"
    text = ((cards[-1].get("payload") or {}).get("text")
            or cards[-1].get("description") or "")
    assert text, "the card must carry something to read"
    assert PHONE not in text, "the card's own text must be scrubbed"
    assert "custody hearing" in text, (
        "scrubbing replaces identifiers; it does not delete the answer")


# ── Nothing is relaxed ─────────────────────────────────────────────────────

def test_a_result_the_floor_refuses_stays_withheld(offered, monkeypatch):
    """The never-send floor outranks the offer. No card, no share."""
    offered["_status"] = "withheld"
    handed = vr._injection_or_card(RESULT, "task_result", "conv-wf")
    assert "EGRESS-GATE" in handed, (
        "when nothing can be shared the gate's own sentence is the honest "
        "thing to hand over")
    assert PHONE not in handed


def test_an_ordinary_result_is_untouched(offered):
    """Only a WITHHELD result is offered. A harmless one just passes."""
    plain = ('Background task "Add two numbers" finished.\n\n17 times 23 is 391.')
    handed = vr._injection_or_card(plain, "task_result", "conv-wf")
    assert "391" in handed, "a harmless result must reach the model as usual"
    assert not offered, "nothing to offer when nothing was withheld"


def test_a_tool_result_mid_turn_is_not_offered(offered):
    """The model is waiting on a tool result; a card cannot be read in that
    gap, so the gate's refusal stands."""
    handed = vr._injection_or_card(RESULT, "result", "conv-wf")
    assert not offered
    assert PHONE not in handed


def test_no_conversation_means_no_card(offered):
    """Without a conversation there is nowhere to raise a card or deliver to."""
    handed = vr._injection_or_card(RESULT, "task_result", None)
    assert not offered
    assert PHONE not in handed


def test_approved_context_is_still_never_re_gated(offered):
    """What the card showed is what the model receives, unchanged."""
    approved = "She enjoys hiking; reach her on [phone number 1]."
    handed = vr._injection_or_card(approved, "context", "conv-wf")
    assert approved in handed
    assert not offered


def test_an_offer_that_raises_falls_back_to_the_gates_own_answer(monkeypatch):
    """A failure here must not become a leak, or a silence."""
    monkeypatch.setattr(lc, "offer",
                        lambda t, **k: (_ for _ in ()).throw(RuntimeError("store down")))
    handed = vr._injection_or_card(RESULT, "task_result", "conv-wf")
    assert "EGRESS-GATE" in handed
    assert PHONE not in handed


def test_offer_reuses_the_request_path_rather_than_a_second_one():
    """One scrub, one floor, one receipt — not a parallel implementation."""
    import inspect
    src = inspect.getsource(lc.request)
    assert "offer(" in src, "request() should delegate to offer()"
    offer_src = inspect.getsource(lc.offer)
    assert "prepare(" in offer_src and "_send(" in offer_src

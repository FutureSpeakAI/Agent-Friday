"""Live state is never answerable from memory.

2026-09-09. Friday's settings page showed two expired Google accounts as
"connected". Stephen read the page and told Friday what he saw. Friday stored
his sentence as a user-authored fact -- its highest-trust source -- and from
then on answered "are my Google accounts connected?" by retrieving his own
sentence and citing him, without consulting anything live.

Nothing else built that day caught it. The claim-verifier could not: nothing
was fabricated. The tool layer could not: no tool was called. Fixing the
settings page could not: the memory predated the fix by nine days.

These tests pin the structural defence -- a live-state question is answered
from a live source and recall's licence to answer it is explicitly withdrawn --
and the supersession primitive, which must strip authority WITHOUT destroying
the record.
"""

import json

import pytest

from agent_friday.services import google_accounts as ga
from agent_friday.services import live_state


def _write_accounts(*records):
    ga.ACCOUNTS_DIR.mkdir(parents=True, exist_ok=True)
    ga.ACCOUNTS_INDEX.write_text(
        json.dumps({"version": 1, "accounts": list(records)}, indent=2),
        encoding="utf-8")
    ga._MIGRATION_DONE = True


def _rec(**kw):
    base = {"id": "a1", "email": "stephen@example.com", "label": "Work",
            "status": "connected", "services": {}, "color": "#fff",
            "created": "2026-08-26T00:00:00+00:00",
            "last_sync": "2026-09-09T09:00:00+00:00", "scopes": [],
            "enc_method": "vault"}
    base.update(kw)
    return base


def _days_ago(n):
    from datetime import datetime, timedelta, timezone
    return (datetime.now(timezone.utc) - timedelta(days=n)).isoformat()


@pytest.fixture(autouse=True)
def clean():
    import shutil
    if ga.ACCOUNTS_DIR.exists():
        shutil.rmtree(ga.ACCOUNTS_DIR, ignore_errors=True)
    ga._MIGRATION_DONE = False
    yield
    if ga.ACCOUNTS_DIR.exists():
        shutil.rmtree(ga.ACCOUNTS_DIR, ignore_errors=True)


THE_PRODUCTION_STATE = (
    _rec(id="a1", email="primary@example.com", label="Personal",
         status="needs_reauth", last_sync=_days_ago(9)),
    _rec(id="a2", email="stephen@futurespeak.ai", label="Work",
         status="needs_reauth", last_sync=_days_ago(9)),
)


class TestTheTwoQuestionClasses:
    """The distinction the module exists to draw."""

    LIVE_STATE = [
        "Are my Google accounts connected?",
        "are my google accounts still connected",
        "is my gmail connected?",
        "is the calendar working",
        "are both google accounts authorized",
        "Is my Google account linked?",
    ]

    RECALLED_FACT = [
        "What did we decide about the launch date?",
        "What's my sister's name?",
        "What did I say I wanted the tone to be?",
        "Remind me what we called that project",
        "What was the plan for the deck?",
    ]

    @pytest.mark.parametrize("q", LIVE_STATE)
    def test_live_state_questions_are_diverted(self, q):
        assert live_state.classify(q) is not None, q

    @pytest.mark.parametrize("q", RECALLED_FACT)
    def test_recalled_fact_questions_are_left_to_memory(self, q):
        assert live_state.classify(q) is None, q


class TestTheBlockContradictsThePoisonedMemory:

    def test_says_not_connected_when_the_store_says_so(self):
        _write_accounts(*THE_PRODUCTION_STATE)
        block = live_state.live_state_block("Are my Google accounts connected?")
        assert block
        assert "0 of 2" in block
        assert "primary@example.com" in block
        assert "stephen@futurespeak.ai" in block
        assert "Answer NO" in block

    def test_withdraws_recall_authority_explicitly(self):
        """Supplying the truth is only half the job.

        Without this half the model holds a live reading AND a remembered one,
        and the remembered one arrives with a citation and the user's own voice
        behind it. That is precisely what happened.
        """
        _write_accounts(*THE_PRODUCTION_STATE)
        block = live_state.live_state_block("Are my Google accounts connected?")
        low = block.lower()
        assert "cannot answer this" in low
        assert "out of date" in low
        assert "do not cite a conversation" in low
        # names the exact trap: the user having said it themselves
        assert "the user themselves" in low

    def test_partial_failure_is_not_a_yes(self):
        _write_accounts(_rec(id="a1", email="ok@x.com", status="connected"),
                        _rec(id="a2", email="dead@x.com", status="needs_reauth"))
        block = live_state.live_state_block("Are my Google accounts connected?")
        assert "PARTIALLY" in block
        assert "do not say yes" in block.lower()
        assert "dead@x.com" in block

    def test_all_healthy_says_yes(self):
        _write_accounts(_rec(id="a1", status="connected"))
        assert "connected and" in live_state.live_state_block(
            "Are my Google accounts connected?")

    def test_a_recalled_fact_question_gets_no_block(self):
        _write_accounts(*THE_PRODUCTION_STATE)
        assert live_state.live_state_block("What did we decide yesterday?") == ""

    def test_unreadable_store_admits_ignorance_rather_than_guessing(self, monkeypatch):
        def boom():
            raise RuntimeError("disk gone")
        monkeypatch.setattr(ga, "accounts_summary", boom)
        block = live_state.live_state_block("Are my Google accounts connected?")
        assert "could not read" in block.lower()
        assert "do not guess" in block.lower()


class TestProbeRegistryIsTheDefaultPath:
    """A new status question should fall into the right path by construction."""

    def test_every_probe_declares_a_live_answer(self):
        assert live_state.PROBES
        for p in live_state.PROBES:
            assert p.key and p.subject and p.patterns
            assert callable(p.answer)

    def test_the_rule_is_stated_in_the_module(self):
        doc = live_state.__doc__ or ""
        assert "LIVE STATE IS NEVER ANSWERABLE FROM MEMORY" in doc
        assert "RECALLED-FACT" in doc and "LIVE-STATE" in doc


class TestTheLiveReadingSitsNextToTheQuestion:
    """The system-prompt block alone was not enough.

    The replayed transcript is memory too, and it carried Friday's OWN earlier
    "yep, they're connected" turns -- closer to the question than any system
    text. A small local model continues its own recent voice. Verified live on
    2026-09-09: with the block in the system prompt but the transcript
    contradicting it, the answer was still "Yes, boss. They are connected."
    """

    def test_a_live_state_turn_carries_its_reading(self):
        _write_accounts(*THE_PRODUCTION_STATE)
        out = live_state.annotate_user_turn("Are my Google accounts connected?")
        assert out.startswith("Are my Google accounts connected?")
        assert "LIVE STATE" in out
        assert "Answer NO" in out

    def test_an_ordinary_turn_is_returned_untouched(self):
        _write_accounts(*THE_PRODUCTION_STATE)
        msg = "What did we decide about the launch date?"
        assert live_state.annotate_user_turn(msg) == msg

    def test_it_never_raises_into_the_chat_path(self, monkeypatch):
        def boom():
            raise RuntimeError("store gone")
        monkeypatch.setattr(ga, "accounts_summary", boom)
        out = live_state.annotate_user_turn("Are my Google accounts connected?")
        assert out.startswith("Are my Google accounts connected?")

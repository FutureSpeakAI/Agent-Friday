"""The phone's pick is bound through the REAL conversations route, with a chat id the PC no longer has.

tests/unit/test_owner_turn_cloud_pick_over_phone.py stands in for the desktop's conversations API; the
fake hid the live defect for a week (a 404 read as an empty record). This runs owner_turn against the
desktop app's own /api/conversations routes: the only fakes are the model call and the slate.
"""
import pytest

from agent_friday.services import conversations as _conv
from agent_friday.tv_gateway import owner_turn
from tests.unit.test_owner_turn_cloud_pick_over_phone import OPUS, STALE, LiveDesk, cloud_answer
from tests.unit.test_owner_turn_model_truth import _clean, ask, block, phone  # noqa: F401  (the autouse fixture)
from agent_friday.tv_gateway import app as gateway


@pytest.fixture
def gw():
    """The gateway's client; `client` is the desktop app's in tests/api."""
    return gateway.create_app().test_client()


@pytest.fixture
def desk(monkeypatch):
    d = LiveDesk()
    monkeypatch.setattr(owner_turn, "_slate", lambda dev: [
        {"id": OPUS, "name": "Claude Opus 5.5", "available": True, "reason": None, "kind": "cloud",
         "provider": "anthropic", "provider_name": "Anthropic"}])
    monkeypatch.setattr(owner_turn, "_post", d.post)
    reset = getattr(owner_turn, "_reset_for_tests", lambda: None)   # absent on the code before the memo: red by assertion, not by setup
    reset()
    yield d
    reset()


def test_a_pick_in_a_chat_the_pc_lost_is_bound_in_a_fresh_chat_on_this_pcs_own_route(gw, desk):
    assert _conv.load(STALE) is None
    cloud_answer(desk)
    r = ask(gw, phone(), model=OPUS, conversation_id=STALE)
    assert r.status_code == 200, r.get_data(as_text=True)
    b = block(r)
    new = b["conversation_id"]
    assert new and new != STALE
    assert (_conv.load(new) or {}).get("seat") == {"model": OPUS}
    assert desk.turns[-1]["conversation_id"] == new and "new chat" in b["notice"].lower()

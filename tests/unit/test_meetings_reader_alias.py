"""A plainly named alias of a cloud model is a cloud reader for a meeting (H4's residual).

`ollama cp x:cloud myglm:latest` gives the daemon a row marked remote_host. The model's own name
says nothing, so the reader checks (`library.tools._meetings_reader_ok`, `trust.people.loop_is_local`)
must consult the alias set, and must fill it once when nothing has yet.
"""
import pytest

from agent_friday.services import agent, local_call
from agent_friday.services import local_only_guard as g
from agent_friday.services.library import tools
from agent_friday.trust import people

ALIAS = "fixture-myglm:latest"
ROWS = [{"name": ALIAS, "remote_host": "https://ollama.com"}, {"name": "fixture-local:7b"}]


@pytest.fixture(autouse=True)
def alias_state(monkeypatch):
    before = (g._REMOTE_ALIASES, g._ALIASES_FILLED, g._ALIAS_RETRY_AT)
    g._REMOTE_ALIASES, g._ALIASES_FILLED, g._ALIAS_RETRY_AT = frozenset(), False, 0.0
    monkeypatch.setattr(local_call, "seat_endpoint", lambda model: None)
    yield
    g._REMOTE_ALIASES, g._ALIASES_FILLED, g._ALIAS_RETRY_AT = before


def _loop(model):
    a, b = agent._CURRENT_PROVIDER.set("ollama"), agent._CURRENT_MODEL.set(model)
    return lambda: (agent._CURRENT_PROVIDER.reset(a), agent._CURRENT_MODEL.reset(b))


def _inventory(monkeypatch, calls):
    def read():
        calls.append(1)
        g.note_remote_aliases(ROWS)
        return {r["name"] for r in ROWS}
    monkeypatch.setattr(local_call, "_daemon_tags", read)


def test_an_alias_is_not_local_for_the_loop_check_and_the_meetings_reader(monkeypatch):
    calls = []
    _inventory(monkeypatch, calls)
    undo = _loop(ALIAS)
    try:
        assert people.loop_is_local("ollama") is False
        assert tools._model_is_cloud_tagged() is True
        assert tools._meetings_reader_ok() is False
    finally:
        undo()
    assert calls, "the unfilled alias set was filled from the daemon inventory"


def test_a_plain_local_model_still_hears_the_meetings_after_the_inventory_is_read(monkeypatch):
    calls = []
    _inventory(monkeypatch, calls)
    undo = _loop("fixture-local:7b")
    try:
        assert people.loop_is_local("ollama") is True
        assert tools._model_is_cloud_tagged() is False
    finally:
        undo()


def test_the_inventory_is_read_once_when_it_is_filled_and_not_every_ask(monkeypatch):
    calls = []
    _inventory(monkeypatch, calls)
    for _ in range(3):
        assert g.reader_model_is_cloud(ALIAS) is True
    assert len(calls) == 1


def test_an_unreadable_daemon_is_retried_no_more_than_every_half_minute(monkeypatch):
    calls = []
    monkeypatch.setattr(local_call, "_daemon_tags", lambda: calls.append(1) or set())
    for _ in range(3):
        assert g.reader_model_is_cloud("fixture-local:7b") is False
    assert len(calls) == 1


def test_a_seat_of_this_pc_is_never_looked_up_in_the_daemon(monkeypatch):
    calls = []
    _inventory(monkeypatch, calls)
    monkeypatch.setattr(local_call, "seat_endpoint", lambda model: "http://127.0.0.1:1/v1")
    assert g.reader_model_is_cloud("fixture-seat") is False and not calls


def test_the_tag_check_is_unchanged():
    for tag in ("glm-4.6:cloud", "gpt-oss:120b-cloud"):
        assert g.reader_model_is_cloud(tag) is True
    assert g.reader_model_is_cloud("") is False

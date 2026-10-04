"""cost_calls gains key_profile and codebase (salon spec §4.7 Metering): a
call under a guest key writes both, and Costs can split by them."""
from __future__ import annotations

import pytest

from agent_friday.services import cost_meter as cm


@pytest.fixture(autouse=True)
def _isolated_db(monkeypatch, tmp_path):
    monkeypatch.setattr(cm, "DB_PATH", tmp_path / "costs.db")
    monkeypatch.setattr(cm, "FRIDAY_DIR", tmp_path)
    monkeypatch.setattr(cm, "_CONN", None)
    monkeypatch.setattr(cm, "_BUFFER", [])
    yield
    try:
        if cm._CONN is not None:
            cm._CONN.close()
    except Exception:
        pass


def _cols():
    return {r[1] for r in cm._conn().execute("PRAGMA table_info(cost_calls)")}


def test_the_table_gains_the_two_columns_by_migration():
    assert {"key_profile", "codebase"} <= _cols()


def test_a_call_carries_the_key_profile_and_the_codebase_and_costs_split_by_them():
    cm.record("anthropic", "claude-opus-5-5", 1000, 100, cost_usd=0.5, key_profile="alex", codebase="cb-1")
    cm.record("anthropic", "claude-opus-5-5", 1000, 100, cost_usd=0.25, key_profile="mine", codebase="cb-1")
    cm.record("anthropic", "claude-opus-5-5", 1000, 100, cost_usd=0.1, key_profile="mine", codebase="cb-2")
    cm.record("anthropic", "claude-opus-5-5", 1000, 100, cost_usd=0.05)     # an ordinary chat call
    cm.flush()
    s = cm.summary("all")
    assert s["by_key_profile"]["alex"]["usd"] == 0.5 and s["by_key_profile"]["mine"]["usd"] == 0.35
    assert s["by_codebase"]["cb-1"]["usd"] == 0.75 and s["by_codebase"]["cb-2"]["usd"] == 0.1
    assert cm.codebase_total("cb-1") == 0.75 and cm.codebase_total("cb-none") == 0.0
    split = cm.codebase_costs("cb-1")
    assert split["total_usd"] == 0.75 and split["by_key_profile"] == {"alex": 0.5, "mine": 0.25} and split["calls"] == 2


def test_attribution_reaches_the_row_from_the_session_context_and_the_thread():
    cm.record("anthropic", "claude-sonnet-5", 10, 10, cost_usd=0.01,
              session_ctx={"codebase": "cb-ctx", "key_profile": "alex", "workspace": "salon"})
    cm.push_attribution(codebase="cb-thread", key_profile="mine")
    try:
        cm.record("anthropic", "claude-sonnet-5", 10, 10, cost_usd=0.02)
    finally:
        cm.pop_attribution()
    cm.flush()
    s = cm.summary("all")
    assert s["by_codebase"]["cb-ctx"]["usd"] == 0.01 and s["by_codebase"]["cb-thread"]["usd"] == 0.02
    assert s["by_key_profile"]["alex"]["calls"] == 1

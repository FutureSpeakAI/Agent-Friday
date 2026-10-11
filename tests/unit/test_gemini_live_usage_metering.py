"""Gemini Live metering (LV-0): one row per usage_metadata message, the prompt
composition stored beside it, and a ledger audit that never writes.

Fixtures are synthetic. The session below is shaped like a real Live session:
the prompt re-read each turn grows as context accrues, the response count rises
and falls.
"""
from __future__ import annotations

import importlib.util
import sqlite3
import types
from pathlib import Path

import pytest

import agent_friday.routes.voice as vr
from agent_friday.services import cost_meter as cm

_ROOT = Path(__file__).resolve().parents[2]


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


def _detail(modality, n):
    return types.SimpleNamespace(modality=modality, token_count=n)


def _chunk(prompt, response, *, details=None, cached=None, tool_use=None):
    um = types.SimpleNamespace(
        prompt_token_count=prompt, response_token_count=response,
        prompt_tokens_details=details, cached_content_token_count=cached,
        tool_use_prompt_token_count=tool_use)
    return types.SimpleNamespace(usage_metadata=um)


def _rows(cols="input_tokens, output_tokens"):
    cm.flush()
    return cm._conn().execute(
        f"SELECT {cols} FROM cost_calls WHERE kind='voice' ORDER BY id").fetchall()


# A five-turn session: prompt grows with the context, response varies.
_SESSION = [(12000, 140), (12900, 310), (14100, 40), (15500, 220), (16000, 95)]


def test_every_message_of_a_session_is_one_row_and_the_rows_sum_to_the_session():
    for prompt, response in _SESSION:
        assert vr._meter_gemini_live_chunk(_chunk(prompt, response), "gemini-3.8-live")
    rows = _rows()
    assert rows == list(_SESSION)
    assert sum(r[0] for r in rows) == sum(p for p, _ in _SESSION)
    assert sum(r[1] for r in rows) == sum(r for _, r in _SESSION)


def test_modality_split_cached_and_tool_use_are_stored_without_changing_the_price():
    plain = _chunk(20000, 100)
    rich = _chunk(20000, 100,
                  details=[_detail("AUDIO", 3000), _detail("TEXT", 17000)],
                  cached=15000, tool_use=800)
    assert vr._meter_gemini_live_chunk(plain, "gemini-3.8-live")
    assert vr._meter_gemini_live_chunk(rich, "gemini-3.8-live")
    plain_row, rich_row = _rows(
        "prompt_audio_tokens, prompt_text_tokens, cached_prompt_tokens, "
        "tool_use_prompt_tokens")
    assert plain_row == (None, None, None, None)     # not reported is NULL, never 0
    assert rich_row == (3000, 17000, 15000, 800)
    costs = [c for (c,) in _rows("cost_usd")]
    assert costs[0] == costs[1]


def test_a_reported_zero_stays_zero_and_enum_modalities_are_read():
    enum_like = types.SimpleNamespace(value="AUDIO")
    chunk = _chunk(5000, 10, details=[_detail(enum_like, 0), _detail("TEXT", 5000)],
                   cached=0, tool_use=0)
    assert vr._meter_gemini_live_chunk(chunk, "gemini-3.8-live")
    assert _rows("prompt_audio_tokens, prompt_text_tokens, cached_prompt_tokens, "
                 "tool_use_prompt_tokens") == [(0, 5000, 0, 0)]


def test_modality_split_reads_dicts_and_survives_junk():
    assert cm.modality_split([{"modality": "AUDIO", "token_count": 7},
                              {"modality": "TEXT", "token_count": 3},
                              {"modality": "AUDIO", "token_count": 1}]) == (8, 3)
    assert cm.modality_split(None) == (None, None)
    assert cm.modality_split([{"modality": "IMAGE", "token_count": 9}]) == (None, None)
    assert cm.modality_split(5) == (None, None)


def test_the_migration_is_additive_and_old_rows_keep_null():
    old = sqlite3.connect(str(cm.DB_PATH))
    old.execute("""CREATE TABLE cost_calls (
        id INTEGER PRIMARY KEY, ts REAL, provider TEXT, model TEXT,
        input_tokens INT, output_tokens INT, cost_usd REAL, duration_ms INT,
        workspace TEXT, kind TEXT, schedule_id TEXT, run_id TEXT)""")
    old.execute("INSERT INTO cost_calls (ts, provider, model, input_tokens, "
                "output_tokens, cost_usd, kind) VALUES (1, 'gemini', 'm', 10, 2, 0.5, 'voice')")
    old.commit()
    old.close()
    cols = {r[1] for r in cm._conn().execute("PRAGMA table_info(cost_calls)")}
    assert {"prompt_audio_tokens", "prompt_text_tokens", "cached_prompt_tokens",
            "tool_use_prompt_tokens"} <= cols
    assert cm._conn().execute(
        "SELECT input_tokens, cost_usd, prompt_audio_tokens, tool_use_prompt_tokens "
        "FROM cost_calls").fetchall() == [(10, 0.5, None, None)]
    cm.record("gemini", "m", 4, 1, kind="voice", prompt_audio_tokens=2)
    cm.flush()                                       # the widened INSERT works on a migrated file


def _audit_module():
    spec = importlib.util.spec_from_file_location(
        "voice_cost_audit_under_test", _ROOT / "tools" / "voice_cost_audit.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _ledger(path, rows):
    conn = sqlite3.connect(str(path))
    conn.execute("""CREATE TABLE cost_calls (
        id INTEGER PRIMARY KEY, ts REAL, provider TEXT, model TEXT,
        input_tokens INT, output_tokens INT, cost_usd REAL, duration_ms INT,
        workspace TEXT, kind TEXT, schedule_id TEXT, run_id TEXT)""")
    conn.executemany("INSERT INTO cost_calls (ts, provider, model, input_tokens, "
                     "output_tokens, cost_usd, kind) VALUES (?,?,?,?,?,?,?)", rows)
    conn.commit()
    conn.close()


def test_the_audit_groups_sessions_restates_the_spend_and_reads_the_signature(tmp_path):
    audit = _audit_module()
    t0 = 1_000_000.0
    rows = []
    for s in range(2):                                # two sessions, an hour apart
        for i, (p, r) in enumerate(_SESSION):
            rows.append((t0 + s * 3600 + i * 10, "gemini", "live-model", p, r, 1.0, "voice"))
    rows.append((t0 + 5, "anthropic", "text-model", 100, 10, 8.0, "chat"))   # other spend
    db = tmp_path / "ledger.db"
    _ledger(db, rows)
    rep = audit.audit(db, gap_s=300)
    assert rep["voice_rows"] == 10 and rep["voice_sessions"] == 2
    assert rep["voice_spend_as_stored_usd"] == 10.0
    assert rep["voice_spend_cumulative_last_row_usd"] == 2.0     # one row per session
    assert rep["share_of_total_as_stored"] == round(10 / 18, 3)
    assert rep["share_of_total_cumulative_last_row"] == round(2 / 10, 3)
    assert rep["within_session_steps_prompt_tokens"]["down"] == 0
    assert rep["within_session_steps_response_tokens"]["down"] > 0
    assert rep["reading"].startswith("per-turn")


def test_the_audit_calls_a_true_running_total_cumulative_and_never_writes(tmp_path):
    audit = _audit_module()
    rows = [(1000.0 + i * 5, "gemini", "m", 100 * (i + 1), 10 * (i + 1), 1.0, "voice")
            for i in range(6)]
    db = tmp_path / "ledger.db"
    _ledger(db, rows)
    before = db.read_bytes()
    rep = audit.audit(db)
    assert rep["reading"].startswith("cumulative signature")
    assert db.read_bytes() == before                  # read-only: not one byte changed
    assert "prompt_audio_tokens" not in rep["modality_columns"]["columns_in_ledger"]


def test_the_audit_prints_aggregates_only(tmp_path, capsys):
    audit = _audit_module()
    db = tmp_path / "ledger.db"
    _ledger(db, [(1000.0 + i, "gemini", "secret-model-name", 5, 5, 0.1, "voice")
                 for i in range(3)])
    assert audit.main(["--db", str(db)]) == 0
    out = capsys.readouterr().out
    assert "secret-model-name" not in out and "1000.0" not in out
    assert audit.main(["--db", str(tmp_path / "missing.db")]) == 2

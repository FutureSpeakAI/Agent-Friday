"""An Idempotency-Key outlives a restart (NS-31.1-4, NS-31.4-1).

Before: keys lived only in memory, so a request retried after Friday restarted ran a second time.
Now a small file keeps, per key, digests and a state (never an answer body): the same key for the
same request is not run again after a restart (409), a request that was still running when Friday
stopped is reported as one that may have run, and a different request under the key is still 422.
The restart is simulated by reloading the module (its memory is gone; the file is not).
"""
from __future__ import annotations

import importlib

import pytest


@pytest.fixture
def idem(tmp_path):
    from agent_friday.services import idempotency as mod
    mod = importlib.reload(mod)
    mod._PATH_OVERRIDE = tmp_path / "idempotency_keys.json"
    mod.reset_for_tests()
    yield mod, tmp_path
    importlib.reload(mod)


def _restart(mod, tmp_path):
    mod = importlib.reload(mod)
    mod._PATH_OVERRIDE = tmp_path / "idempotency_keys.json"
    return mod


def _fp(mod, body=b'{"sender": "news@example.test"}'):
    return mod.fingerprint("POST", "/api/messages/learn", body)


def test_a_finished_request_is_not_run_again_after_a_restart(idem):
    mod, tmp = idem
    decision, scope = mod.begin("retry-restart-0001", "user", "", _fp(mod))
    assert decision == "run"
    mod.finish(scope, 200, b'{"ok": true}', "application/json", False)
    mod = _restart(mod, tmp)
    decision, _ = mod.begin("retry-restart-0001", "user", "", _fp(mod))
    assert decision != "run", "the retried request ran again after the restart"
    assert decision == "handled"


def test_a_request_running_when_friday_stopped_is_not_run_again(idem):
    mod, tmp = idem
    assert mod.begin("retry-restart-0002", "user", "", _fp(mod))[0] == "run"
    mod = _restart(mod, tmp)                 # stopped before it answered
    assert mod.begin("retry-restart-0002", "user", "", _fp(mod))[0] == "restarted"


def test_a_different_request_under_the_key_is_still_refused_after_a_restart(idem):
    mod, tmp = idem
    _d, scope = mod.begin("retry-restart-0003", "user", "", _fp(mod))
    mod.finish(scope, 200, b"{}", "application/json", False)
    mod = _restart(mod, tmp)
    assert mod.begin("retry-restart-0003", "user", "", _fp(mod, b'{"other": 1}'))[0] == "mismatch"


def test_a_failed_request_is_forgotten_so_a_retry_runs_after_a_restart(idem):
    mod, tmp = idem
    _d, scope = mod.begin("retry-restart-0004", "user", "", _fp(mod))
    mod.finish(scope, 400, b'{"error": 1}', "application/json", False)
    mod = _restart(mod, tmp)
    assert mod.begin("retry-restart-0004", "user", "", _fp(mod))[0] == "run"


def test_no_answer_body_is_kept_on_disk(idem):
    mod, tmp = idem
    _d, scope = mod.begin("retry-restart-0005", "user", "", _fp(mod))
    mod.finish(scope, 200, b'{"secret": "SYNTHETIC-ANSWER-WORDS"}', "application/json", False)
    text = (tmp / "idempotency_keys.json").read_text(encoding="utf-8")
    assert "SYNTHETIC-ANSWER-WORDS" not in text and "retry-restart-0005" not in text


def test_another_caller_is_not_answered_by_a_kept_key(idem):
    mod, tmp = idem
    _d, scope = mod.begin("retry-restart-0006", "user", "token-a", _fp(mod))
    mod.finish(scope, 200, b"{}", "application/json", False)
    mod = _restart(mod, tmp)
    assert mod.begin("retry-restart-0006", "user", "token-b", _fp(mod))[0] == "run"

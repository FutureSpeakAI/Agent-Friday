"""Worker scripts must not inherit the server's secrets.

2026-09-06: services/worker_adapters/python_script_adapter.py spawned the
caller-supplied script with `{**os.environ, "FRIDAY_WORKER": "1"}` -- the
full server environment. It is reached from POST /api/orchestrator/delegate
(adapter_type=PYTHON_SCRIPT) and from the federation compute route's
`analysis.run` capability. The fix builds the child's environment FROM
extension_security.SANDBOXED_ENV_ALLOWLIST (the allowlist the MCP launcher
already switched to after three denylist recurrences) plus a tiny
WORKER_ENV_EXTRA -- never "inherit everything then strip names".

The first test drives the real adapter end to end (a real subprocess), the
way the leak actually happened; the rest pin the environment builder.
"""
from __future__ import annotations

import json
import os
import time

import pytest

from agent_friday.services.worker_adapters import python_script_adapter as psa
from agent_friday.services.worker_adapters.base import WorkerStatus
from agent_friday.services.orchestrator import WorkerTask, TaskType, AdapterType, ResultStatus

SECRETS = {
    "ANTHROPIC_API_KEY": "sk-ant-leak-test",       # pragma: allowlist secret
    "FRIDAY_PASSWORD": "vault-passphrase-leak-test",  # pragma: allowlist secret
    "KIE_API_KEY": "kie-leak-test",                  # pragma: allowlist secret
    "SOME_FUTURE_PROVIDER_TOKEN": "not-yet-invented",  # pragma: allowlist secret
}


def _task(prompt: str) -> WorkerTask:
    return WorkerTask(prompt=prompt, task_type=TaskType.CUSTOM, context={},
                      budget_mψ=1000, budget_tokens=512, deadline_seconds=20,
                      adapter_type=AdapterType.PYTHON_SCRIPT)


def _run(adapter, prompt: str):
    aid = adapter.start(_task(prompt))
    deadline = time.time() + 15
    while time.time() < deadline:
        if adapter.poll(aid) in (WorkerStatus.COMPLETED, WorkerStatus.FAILED):
            break
        time.sleep(0.1)
    return adapter.result(aid)


def test_worker_subprocess_does_not_see_server_secrets(monkeypatch):
    for k, v in SECRETS.items():
        monkeypatch.setenv(k, v)
    res = _run(psa.PythonScriptAdapter(),
               "import os, json; print(json.dumps(sorted(os.environ)))")
    assert res.status == ResultStatus.COMPLETED, res.error
    seen = set(json.loads((res.output or "").strip().splitlines()[0]))
    leaked = {k for k in SECRETS if k in seen}
    assert not leaked, f"worker inherited server secrets: {sorted(leaked)}"
    assert "FRIDAY_WORKER" in seen
    assert any(k.upper() == "PATH" for k in seen), "worker lost PATH -- allowlist broke the child"


def test_worker_env_is_built_from_the_sandboxed_allowlist(monkeypatch):
    from agent_friday.services.extension_security import SANDBOXED_ENV_ALLOWLIST
    for k, v in SECRETS.items():
        monkeypatch.setenv(k, v)
    env = psa.worker_env()
    allowed = {n.upper() for n in SANDBOXED_ENV_ALLOWLIST} | {n.upper() for n in psa.WORKER_ENV_EXTRA}
    stray = {k for k in env if k.upper() not in allowed}
    assert not stray, f"worker env carries names outside the allowlist: {sorted(stray)}"
    assert env["FRIDAY_WORKER"] == "1"
    assert not (set(env) & set(SECRETS))


def test_extra_allowlist_is_the_one_extension_point(monkeypatch):
    monkeypatch.setenv("FRIDAY_WORKER_TEST_EXTRA", "visible")
    monkeypatch.setenv("FRIDAY_WORKER_TEST_NOT_EXTRA", "hidden")
    monkeypatch.setattr(psa, "WORKER_ENV_EXTRA",
                        psa.WORKER_ENV_EXTRA | {"FRIDAY_WORKER_TEST_EXTRA"})
    env = psa.worker_env()
    assert env.get("FRIDAY_WORKER_TEST_EXTRA") == "visible"
    assert "FRIDAY_WORKER_TEST_NOT_EXTRA" not in env


def test_no_worker_adapter_spawns_with_the_raw_environment():
    """Structural: the exact expression that leaked must not come back in
    any adapter under worker_adapters/."""
    import pathlib
    d = pathlib.Path(psa.__file__).parent
    offenders = [p.name for p in d.glob("*.py")
                 if "**os.environ" in p.read_text(encoding="utf-8", errors="replace")]
    assert not offenders, offenders

"""A Claude Code session launched from Friday runs under Friday's approval gates.

One card per task; approval mints a scoped grant and starts the session;
denial runs nothing; every action inside the session asks Friday's gate,
which consumes the grant through action_gate (cLaws check + signed receipt)
after Friday's own refusals. The launcher never skips permissions.
"""
from __future__ import annotations

import importlib.util
import io
import json
import re
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[2] / "src" / "agent_friday"


@pytest.fixture
def home(tmp_path, monkeypatch):
    """Everything the gate writes (approvals, grants, receipts, task files)
    lands under a scratch Friday home."""
    monkeypatch.setenv("FRIDAY_HOME", str(tmp_path))
    from agent_friday.services import approvals as ap
    monkeypatch.setattr(ap, "APPROVALS_FILE", tmp_path / "approvals.json")
    monkeypatch.setattr(ap, "FRIDAY_DIR", tmp_path)
    import agent_friday.core as core
    monkeypatch.setattr(core, "VIBE_TERMINALS", {})
    from agent_friday.services import claude_code_tasks as cct
    monkeypatch.setattr(cct, "VIBE_TERMINALS", core.VIBE_TERMINALS)
    # code_engine imports VIBE_TERMINALS from core by name: one registry for all.
    from agent_friday.services import code_engine as ce
    monkeypatch.setattr(ce, "VIBE_TERMINALS", core.VIBE_TERMINALS)
    monkeypatch.setattr(core, "_persist_vibe_terminals", lambda: None)
    return tmp_path


@pytest.fixture
def launched(monkeypatch):
    from agent_friday.services import code_engine as ce
    calls = []
    monkeypatch.setattr(ce, "_run_claude_terminal", lambda tid, task, cwd: calls.append((tid, task, cwd)))
    # The decision hook starts a thread; run it inline so the test can see it.
    import threading

    class _Inline:
        def __init__(self, target=None, args=(), kwargs=None, daemon=None, name=None):
            self._t, self._a, self._k = target, args, kwargs or {}

        def start(self):
            self._t(*self._a, **self._k)
    from agent_friday.services import claude_code_tasks as cct
    monkeypatch.setattr(cct.threading, "Thread", _Inline)
    return calls


# ── the launcher never skips permissions ────────────────────────────────────

def test_no_launch_path_skips_permissions():
    for rel in ("services/code_engine.py", "routes/code.py", "routes/futurespeak.py",
                "services/claude_code_tasks.py"):
        src = (SRC / rel).read_text(encoding="utf-8")
        assert "dangerously-skip-permissions" not in src, rel
        assert "bypassPermissions" not in src, rel


def test_the_launcher_refuses_without_a_grant(home, monkeypatch):
    import subprocess
    from agent_friday.services import code_engine as ce
    import agent_friday.core as core
    popen = []
    monkeypatch.setattr(subprocess, "Popen", lambda *a, **k: popen.append(a) or type("P", (), {"pid": 1})())
    core.VIBE_TERMINALS["t1"] = {"id": "t1", "task": "do it", "status": "awaiting_approval",
                                 "cwd": str(home), "token": "tok"}
    ce._run_claude_terminal("t1", "do it", str(home))
    assert popen == [], "Claude Code started without an approved grant"
    assert core.VIBE_TERMINALS["t1"]["status"] == "error"
    assert "no approval" in core.VIBE_TERMINALS["t1"]["error"]


def test_the_launcher_runs_under_the_gate_when_granted(home, monkeypatch):
    import subprocess
    from agent_friday.services import code_engine as ce
    from agent_friday.services import claude_code_tasks as cct
    import agent_friday.core as core
    monkeypatch.setattr(core, "_safe_under_home", lambda p: p)
    monkeypatch.setattr(ce, "_safe_under_home", lambda p: p)   # its own binding
    popen = []
    monkeypatch.setattr(subprocess, "Popen", lambda *a, **k: popen.append((a, k)) or type("P", (), {"pid": 4242})())
    core.VIBE_TERMINALS["t2"] = {"id": "t2", "task": "write tests", "status": "awaiting_approval",
                                 "cwd": str(home), "token": "tok"}
    cct._mint("t2", "write tests")
    ce._run_claude_terminal("t2", "write tests", str(home))
    assert len(popen) == 1
    cmd = popen[0][0][0][2]
    assert "--dangerously-skip-permissions" not in cmd
    assert "--settings" in cmd
    settings = json.loads((cct.task_dir("t2") / "settings.json").read_text(encoding="utf-8"))
    hook = settings["hooks"]["PreToolUse"][0]
    assert hook["matcher"] == ""
    assert "claude_code_gate_hook.py" in hook["hooks"][0]["command"]
    task = json.loads((cct.task_dir("t2") / "task.json").read_text(encoding="utf-8"))
    assert task["task_id"] == "t2" and task["token"] == "tok" and "/api/vibe-code/gate" in task["gate_url"]
    assert core.VIBE_TERMINALS["t2"]["status"] == "running"


# ── one card per task ───────────────────────────────────────────────────────

def test_a_request_raises_one_card_and_starts_nothing(home, launched):
    from agent_friday.services import approvals as ap
    from agent_friday.services import claude_code_tasks as cct
    rec = cct.request("t3", "refactor the parser", str(home))
    assert rec["status"] == "pending" and rec["kind"] == cct.KIND
    assert launched == []
    assert cct.VIBE_TERMINALS["t3"]["status"] == "awaiting_approval"
    again = cct.request("t3", "refactor the parser", str(home))
    assert again["approval_id"] == rec["approval_id"], "a second request must not raise a second card"
    assert len([r for r in ap.list_approvals() if r.get("kind") == cct.KIND]) == 1


def test_approving_the_card_mints_the_grant_and_starts_the_session_once(home, launched):
    from agent_friday.services import approvals as ap
    from agent_friday.governance import action_gate as gate
    from agent_friday.services import claude_code_tasks as cct
    rec = cct.request("t4", "add a test", str(home))
    ap.decide(rec["approval_id"], "approve")
    assert launched == [("t4", "add a test", str(home))]
    grants = [g for g in gate.list_grants() if g["scope"] == cct.scope("t4")]
    assert len(grants) == 1
    assert set(grants[0]["tools"]) == {cct.ACTION_TOOL, cct.LAUNCH_TOOL}
    assert grants[0]["uses_left"] == cct.GRANT_USES
    ap.decide(rec["approval_id"], "approve")   # already decided: no second start
    assert len(launched) == 1


def test_denying_the_card_runs_nothing(home, launched):
    from agent_friday.services import approvals as ap
    from agent_friday.governance import action_gate as gate
    from agent_friday.services import claude_code_tasks as cct
    rec = cct.request("t5", "delete everything", str(home))
    ap.decide(rec["approval_id"], "deny")
    assert launched == []
    assert cct.VIBE_TERMINALS["t5"]["status"] == "denied"
    assert not [g for g in gate.list_grants() if g["scope"] == cct.scope("t5")]
    assert cct.gate("t5", cct.VIBE_TERMINALS["t5"]["token"], "Bash", {"command": "ls"})["decision"] == "deny"


# ── every action asks ───────────────────────────────────────────────────────

def _granted(home, tid="t6", task="work"):
    from agent_friday.services import claude_code_tasks as cct
    cct.request(tid, task, str(home))
    cct._mint(tid, task)
    cct.VIBE_TERMINALS[tid]["status"] = "running"
    return cct.VIBE_TERMINALS[tid]["token"]


def test_an_allowed_action_consumes_one_use_and_leaves_a_receipt(home):
    from agent_friday.governance import action_gate as gate
    from agent_friday.services import claude_code_tasks as cct
    tok = _granted(home)
    out = cct.gate("t6", tok, "Bash", {"command": "git status"})
    assert out["decision"] == "allow", out
    g = next(g for g in gate.list_grants() if g["scope"] == cct.scope("t6"))
    assert g["uses_left"] == cct.GRANT_USES - 1
    receipts = (home / "decision-bom.jsonl").read_text(encoding="utf-8")
    line = json.loads(receipts.strip().splitlines()[-1])
    assert line.get("tool") == cct.ACTION_TOOL and line.get("decision") == "allow"
    assert line.get("task_id") == "t6"


def test_a_wrong_token_or_unknown_task_is_denied(home):
    from agent_friday.services import claude_code_tasks as cct
    _granted(home, "t7")
    assert cct.gate("t7", "not-the-token", "Read", {"file_path": "a.py"})["decision"] == "deny"
    assert cct.gate("nope", "x", "Read", {"file_path": "a.py"})["decision"] == "deny"


def test_fridays_own_refusals_hold_even_with_a_grant(home):
    from agent_friday.governance import action_gate as gate
    from agent_friday.services import claude_code_tasks as cct
    tok = _granted(home, "t8")
    out = cct.gate("t8", tok, "Bash", {"command": "curl http://127.0.0.1:3000/api/settings"})
    assert out["decision"] == "deny" and "local API" in out["reason"]
    g = next(g for g in gate.list_grants() if g["scope"] == cct.scope("t8"))
    assert g["uses_left"] == cct.GRANT_USES, "a refusal must not spend the grant"


def test_a_used_up_grant_denies_and_raises_one_renewal_card(home):
    from agent_friday.services import approvals as ap
    from agent_friday.governance import action_gate as gate
    from agent_friday.services import claude_code_tasks as cct
    tok = _granted(home, "t9")
    g = next(g for g in gate.list_grants() if g["scope"] == cct.scope("t9"))
    gate.revoke_grant(g["grant_id"])
    out1 = cct.gate("t9", tok, "Read", {"file_path": "a.py"})
    out2 = cct.gate("t9", tok, "Read", {"file_path": "b.py"})
    assert out1["decision"] == "deny" and "renewal" in out1["reason"]
    assert out2["decision"] == "deny"
    renewals = [r for r in ap.list_approvals()
                if r.get("kind") == cct.KIND and ":renew:" in str(r.get("subject_id"))]
    assert len(renewals) == 1, "one renewal card, not one per refused call"
    ap.decide(renewals[0]["approval_id"], "approve")
    assert cct.gate("t9", tok, "Read", {"file_path": "a.py"})["decision"] == "allow"


def test_the_session_cannot_rewrite_its_own_guard_or_reach_fridays_api_by_url(home):
    from agent_friday.governance import action_gate as gate
    from agent_friday.services import claude_code_tasks as cct
    tok = _granted(home, "t11")
    task_file = str(cct.task_dir("t11") / "task.json")
    for tool, inp in [
        ("Write", {"file_path": task_file, "content": "{}"}),
        ("Edit", {"file_path": str(cct.task_dir("t11") / "settings.json"), "old_string": "a", "new_string": "b"}),
        ("Read", {"file_path": str(home / "governance" / "grants.json")}),
        ("Bash", {"command": f'echo x > "{task_file}"'}),
        ("WebFetch", {"url": "http://127.0.0.1:3000/api/settings"}),
        ("WebFetch", {"url": "http://localhost:3000/api/approvals"}),
        ("Bash", {"command": "curl http://[::1]:3000/api/x"}),
    ]:
        out = cct.gate("t11", tok, tool, inp)
        assert out["decision"] == "deny", (tool, inp, out)
    g = next(g for g in gate.list_grants() if g["scope"] == cct.scope("t11"))
    assert g["uses_left"] == cct.GRANT_USES, "a refusal must not spend the grant"
    assert cct.gate("t11", tok, "Read", {"file_path": str(home.parent / "proj" / "a.py")})["decision"] == "allow"


def test_a_task_waiting_on_its_card_survives_a_restart(home, monkeypatch):
    from agent_friday.services import code_engine as ce
    import agent_friday.core as core
    saved = {"id": "t12", "task": "later", "status": "awaiting_approval", "cwd": str(home),
             "token": "tok", "approval_id": "ap_1"}
    monkeypatch.setattr(core, "_read_vibe_state", lambda: {"version": 2, "terminals": {"t12": saved}})
    monkeypatch.setattr(ce, "_vibe_terminal_processes", lambda: {})
    monkeypatch.setattr(ce, "_code_log", lambda *a, **k: None, raising=False)
    core.VIBE_TERMINALS.clear()
    ce.adopt_or_reap_vibe_terminals()
    assert core.VIBE_TERMINALS.get("t12", {}).get("status") == "awaiting_approval"
    assert core.VIBE_TERMINALS["t12"]["token"] == "tok"


def test_the_decision_hook_is_registered_when_the_routes_load():
    from agent_friday.services import approvals as ap
    from agent_friday.services import claude_code_tasks as cct
    import agent_friday.routes.code  # noqa: F401  (imports claude_code_tasks at module level)
    assert cct._on_decision in ap._HOOKS.get(cct.KIND, [])
    src = (SRC / "routes" / "code.py").read_text(encoding="utf-8")
    assert re.search(r"^from agent_friday\.services import claude_code_tasks", src, re.M)


def test_the_claws_check_holds_the_gate(home, monkeypatch):
    from agent_friday.governance import action_gate as gate
    from agent_friday.services import claude_code_tasks as cct
    tok = _granted(home, "t10")
    monkeypatch.setattr(gate, "verify_claws", lambda: (False, "pin mismatch"))
    out = cct.gate("t10", tok, "Read", {"file_path": "a.py"})
    assert out["decision"] == "deny" and "cLaws" in out["reason"]


# ── the hook script ─────────────────────────────────────────────────────────

def _hook_module():
    spec = importlib.util.spec_from_file_location(
        "claude_code_gate_hook", SRC / "services" / "claude_code_gate_hook.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def test_the_hook_imports_nothing_from_the_application():
    src = (SRC / "services" / "claude_code_gate_hook.py").read_text(encoding="utf-8")
    assert not re.search(r"^\s*(from|import)\s+agent_friday", src, re.M)


def test_the_hook_allows_only_on_an_allow_and_fails_closed(tmp_path):
    m = _hook_module()
    task = tmp_path / "task.json"
    task.write_text(json.dumps({"task_id": "t", "token": "x", "gate_url": "http://127.0.0.1:1/x"}),
                    encoding="utf-8")
    payload = json.dumps({"tool_name": "Bash", "tool_input": {"command": "ls"}})

    out, err = io.StringIO(), io.StringIO()
    m._post = lambda url, body: {"decision": "allow", "reason": "granted"}
    rc = m.main([str(task)], stdin=io.StringIO(payload), stdout=out, stderr=err)
    assert rc == 0
    assert json.loads(out.getvalue())["hookSpecificOutput"]["permissionDecision"] == "allow"

    out, err = io.StringIO(), io.StringIO()
    m._post = lambda url, body: {"decision": "deny", "reason": "no grant"}
    rc = m.main([str(task)], stdin=io.StringIO(payload), stdout=out, stderr=err)
    assert rc == 2 and "no grant" in err.getvalue() and out.getvalue() == ""

    out, err = io.StringIO(), io.StringIO()

    def down(url, body):
        raise OSError("connection refused")
    m._post = down
    rc = m.main([str(task)], stdin=io.StringIO(payload), stdout=out, stderr=err)
    assert rc == 2 and "could not be reached" in err.getvalue()

    rc = m.main([str(tmp_path / "missing.json")], stdin=io.StringIO(payload),
                stdout=io.StringIO(), stderr=io.StringIO())
    assert rc == 2

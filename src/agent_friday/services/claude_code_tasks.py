"""A Claude Code session launched from Friday runs under Friday's approval gates.

A Claude Code session started from the Code workspace is governed like
Friday's own tools: the approval cards, the cLaws check and the receipts
apply to every action inside it. One card per task, and nothing in the
session runs without it:

1. A launch request raises ONE approval card for the task ("Run Claude Code
   in <folder> for this task"). Nothing starts until the owner approves it;
   a denial runs nothing.
2. Approval mints a scoped grant (``vibe:<task id>``, bounded uses and
   expiry) and starts the session with a per-task Claude Code settings file
   whose PreToolUse hook (``claude_code_gate_hook.py``) asks Friday about
   every tool call. The session never starts with permission checks skipped.
3. The gate route answers each call from the grant through
   ``action_gate.authorize`` (which checks the cLaws and writes the signed
   receipt), after Friday's own refusals: a command aimed at Friday's local
   API, a blocklisted token, a read of key material. A used-up or expired
   grant raises a renewal card and denies until it is approved.

Batched approval is deliberate ("no pestering"): the owner decides once per
task, and the gate consumes one use per action so the receipt trail shows
what the task actually did.
"""
from __future__ import annotations

import hmac
import json
import re
import secrets
import sys
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

import agent_friday.core as core
from agent_friday.core import VIBE_TERMINALS

KIND = "claude_code_task"
SUBJECT_TYPE = "claude_code_task"
ACTION_TOOL = "claude_code_action"
LAUNCH_TOOL = "claude_code_launch"
GRANT_SECONDS = 2 * 3600
GRANT_USES = 400
MAX_TASK_CHARS = 2000


def scope(task_id: str) -> str:
    return f"vibe:{task_id}"


def task_dir(task_id: str) -> Path:
    from agent_friday.paths import friday_home
    return friday_home() / "vibe-code" / "tasks" / str(task_id)


# ── 1. One card per task ─────────────────────────────────────────────────────

def request(task_id: str, task: str, cwd: str, *, requested_by: str = "owner",
            extra: Optional[dict] = None) -> Dict[str, Any]:
    """Record the task and raise its approval card. Nothing runs here."""
    from agent_friday.services import approvals as _ap
    task = str(task or "")[:MAX_TASK_CHARS]
    entry = {
        'id': task_id, 'task': task, 'status': 'awaiting_approval', 'cwd': str(cwd),
        'pid': None, 'started': datetime.now().isoformat(), 'stopped': None,
        'log_file': None, 'token': secrets.token_urlsafe(24),
    }
    entry.update(extra or {})
    VIBE_TERMINALS[task_id] = entry
    folder = Path(cwd).name or str(cwd)
    rec = _ap.create_approval(
        kind=KIND, subject_type=SUBJECT_TYPE, subject_id=str(task_id),
        title=f"Run Claude Code in {folder} for this task",
        description=(f"Task: {task[:600]}\n\nFolder: {cwd}\n\nApproving starts one Claude Code "
                     f"session for this task. Every action it takes (commands, file edits, "
                     f"web fetches) is checked against this approval by Friday, up to "
                     f"{GRANT_USES} actions or {GRANT_SECONDS // 3600} hours, whichever "
                     f"comes first; after that a renewal card is raised. Denying runs nothing."),
        action_description="Start a Claude Code session with a per-task grant",
        payload={"task_id": task_id, "launch": True}, requested_by=requested_by,
        force_gate=True)
    entry['approval_id'] = rec.get('approval_id')
    core._persist_vibe_terminals()
    return rec


def _mint(task_id: str, task: str) -> dict:
    from agent_friday.governance import action_gate as _gate
    return _gate.create_grant(tools=[ACTION_TOOL, LAUNCH_TOOL], scope=scope(task_id),
                              expires_in_seconds=GRANT_SECONDS, max_uses=GRANT_USES,
                              created_by="owner", note=f"Claude Code task: {task[:150]}")


def _on_decision(record: dict) -> None:
    """The approval card's decision hook: approve mints the grant and starts
    the session (or renews a running one); deny or expiry runs nothing."""
    payload = record.get("payload") or {}
    task_id = str(payload.get("task_id") or record.get("subject_id") or "").split(":")[0]
    entry = VIBE_TERMINALS.get(task_id)
    if not entry:
        return
    status = record.get("status")
    if status != "approved":
        if payload.get("launch", True) and entry.get("status") == "awaiting_approval":
            entry['status'] = 'denied' if status == "denied" else 'expired'
            entry['stopped'] = datetime.now().isoformat()
            core._persist_vibe_terminals()
        return
    grant = _mint(task_id, entry.get('task') or '')
    entry['grant_id'] = grant.get('grant_id')
    core._persist_vibe_terminals()
    if payload.get("launch", True):
        from agent_friday.services.code_engine import _run_claude_terminal
        threading.Thread(target=_run_claude_terminal,
                         args=(task_id, entry.get('task') or '', entry.get('cwd') or ''),
                         daemon=True, name=f"vibe-launch-{task_id}").start()


def _register() -> None:
    from agent_friday.services import approvals as _ap
    hooks = _ap._HOOKS.get(KIND) or []
    if _on_decision not in hooks:
        _ap.register_decision_hook(KIND, _on_decision)


_register()


# ── 2. The launch itself ─────────────────────────────────────────────────────

def launch_allowed(task_id: str) -> Tuple[bool, str]:
    """A launch needs the task's own grant; without one nothing starts."""
    from agent_friday.governance import action_gate as _gate
    g = _gate.consume_grant(LAUNCH_TOOL, scope(task_id))
    if g is None:
        return False, ("no approval: this task has no live grant, so Claude Code "
                       "was not started. Approve the task's card in Friday first.")
    return True, g.get("grant_id") or ""


def hook_script() -> Path:
    return Path(__file__).resolve().parent / "claude_code_gate_hook.py"


def gate_url() -> str:
    return f"http://127.0.0.1:{int(getattr(core, 'SERVER_PORT', 3000) or 3000)}/api/vibe-code/gate"


def write_session_files(task_id: str) -> Path:
    """The per-task Claude Code settings file (hook on every tool) and the
    task file the hook reads. Returns the settings path."""
    entry = VIBE_TERMINALS.get(task_id) or {}
    d = task_dir(task_id)
    d.mkdir(parents=True, exist_ok=True)
    task_file = d / "task.json"
    task_file.write_text(json.dumps({
        "task_id": task_id, "token": entry.get("token") or "",
        "gate_url": gate_url(),
    }), encoding="utf-8")
    cmd = '"%s" "%s" "%s"' % (
        Path(sys.executable).as_posix(), hook_script().as_posix(), task_file.as_posix())
    settings = {
        "hooks": {
            "PreToolUse": [{
                "matcher": "",
                "hooks": [{"type": "command", "command": cmd, "timeout": 30}],
            }],
        },
    }
    settings_file = d / "settings.json"
    settings_file.write_text(json.dumps(settings, indent=1), encoding="utf-8")
    return settings_file


# ── 3. Every action asks ─────────────────────────────────────────────────────

_LOOPBACK_URL = re.compile(r"https?://(127\.0\.0\.1|localhost|\[::1\]|0\.0\.0\.0)(:\d+)?(/|$)", re.I)


def _in_friday_state(path: str) -> bool:
    """A path inside Friday's own data folder: the session's task file and
    settings, the vault, approvals, grants. The session never touches them."""
    if not path:
        return False
    try:
        from agent_friday.paths import friday_home
        p = Path(path).expanduser().resolve()
        home = Path(friday_home()).resolve()
        return p == home or home in p.parents
    except Exception:
        return True


def _own_refusal(tool_name: str, tool_input: dict) -> Optional[str]:
    """Friday's own hard refusals, before the grant is even consulted.

    They hold whatever the grant says: the session cannot rewrite its own
    guard (the task file, the settings file, anything under Friday's data
    folder), cannot reach Friday's loopback-trusted API by command or by
    URL, and cannot read key material.
    """
    try:
        from agent_friday.services import credential_paths as _cred
    except Exception:
        _cred = None
    tool_input = tool_input or {}
    for key in ("url", "command", "file_path", "path", "notebook_path", "pattern"):
        val = str(tool_input.get(key) or "")
        if val and _LOOPBACK_URL.search(val):
            return ("this call addresses Friday's own local API, which trusts this "
                    "machine as the owner; Friday does not run it for a session")
    try:
        from agent_friday.paths import friday_home
        # Both sides in one spelling (forward slashes, lower case): a POSIX
        # home is written with "/" and a Windows one with "\\", and a command
        # may use either separator on either platform.
        home_str = str(Path(friday_home()).resolve()).replace("\\", "/").lower()
        for key in ("command", "file_path", "path", "notebook_path", "url"):
            val = str(tool_input.get(key) or "").replace("\\", "/").lower()
            if val and (home_str in val or ".friday" in val):
                return ("refused: this touches Friday's own data folder (its state, "
                        "its approvals, this session's own gate); nothing there is "
                        "the session's to read or change")
    except Exception:
        return "Friday could not resolve its data folder, so this call did not run"
    if tool_name in ("Bash", "PowerShell"):
        cmd = str((tool_input or {}).get("command") or "")
        try:
            from agent_friday.governance.action_gate import classify_command
            if classify_command(cmd)[0] == "forbidden":
                return ("this command addresses Friday's own local API, which trusts this "
                        "machine as the owner; Friday does not run it for a session")
        except Exception:
            return "Friday could not classify this command, so it did not run"
        try:
            from agent_friday.services.agent import blocked_command_token
            bad = blocked_command_token(cmd)
            if bad is not None:
                return f"blocked by cLaws safety: command matches blocklist token {bad!r}"
        except Exception:
            pass
        if _cred is not None:
            why = _cred.scan_command(cmd)
            if why:
                return f"refused: {why}"
    path = str((tool_input or {}).get("file_path") or (tool_input or {}).get("path")
               or (tool_input or {}).get("notebook_path") or "")
    if path and _in_friday_state(path):
        return ("refused: this touches Friday's own data folder (its state, its "
                "approvals, this session's own gate); nothing there is the session's "
                "to read or change")
    if path and _cred is not None:
        try:
            if _cred.check(Path(path).expanduser()):
                return "refused: Friday does not read or write key material, even for a session"
        except Exception:
            pass
    return None


def _renewal(task_id: str, entry: dict) -> None:
    """A used-up grant raises one renewal card (not one per refused call)."""
    from agent_friday.services import approvals as _ap
    n = int(entry.get('renewals') or 0)
    if n:
        # The card raised last time is still the one to answer while it is
        # pending; a decided one (approved and used up again, or denied)
        # earns the next number.
        last = _ap.find_for_subject(SUBJECT_TYPE, f"{task_id}:renew:{n}", KIND)
        if last is not None and last.get("status") == "pending":
            return
    n += 1
    subject = f"{task_id}:renew:{n}"
    entry['renewals'] = n
    core._persist_vibe_terminals()
    folder = Path(entry.get('cwd') or '').name
    _ap.create_approval(
        kind=KIND, subject_type=SUBJECT_TYPE, subject_id=subject,
        title=f"Renew Claude Code's approval in {folder}",
        description=(f"The session for this task has used its {GRANT_USES} approved actions "
                     f"or its {GRANT_SECONDS // 3600}-hour window. Task: "
                     f"{(entry.get('task') or '')[:400]}\n\nApproving lets it continue for "
                     f"another {GRANT_USES} actions or {GRANT_SECONDS // 3600} hours. "
                     f"Denying leaves it refused."),
        action_description="Renew the per-task grant for a running Claude Code session",
        payload={"task_id": task_id, "launch": False}, requested_by="claude_code",
        force_gate=True)


def gate(task_id: str, token: str, tool_name: str, tool_input: Any) -> Dict[str, Any]:
    """Answer one tool call from a session: {"decision": "allow"|"deny", "reason"}."""
    entry = VIBE_TERMINALS.get(str(task_id or ""))
    if not entry or not entry.get('token') or not token:
        return {"decision": "deny", "reason": "unknown task; nothing runs"}
    if not hmac.compare_digest(str(entry['token']), str(token)):
        return {"decision": "deny", "reason": "the task token does not match; nothing runs"}
    if entry.get('status') in ('stopped', 'denied', 'expired'):
        return {"decision": "deny", "reason": "this task was stopped; nothing runs"}
    tool_input = tool_input if isinstance(tool_input, dict) else {}
    why = _own_refusal(str(tool_name or ""), tool_input)
    if why:
        _receipt_refusal(task_id, tool_name, why)
        return {"decision": "deny", "reason": why}
    from agent_friday.governance import action_gate as _gate
    summary = _summarise(tool_name, tool_input)
    v = _gate.authorize(ACTION_TOOL, {"tool": tool_name, "summary": summary},
                        session_ctx={"grant_scope": scope(task_id), "task_id": task_id,
                                     "is_background_task": True, "surface": "claude_code"},
                        tainted=False)
    if v.action == "allow":
        entry['actions'] = int(entry.get('actions') or 0) + 1
        entry['last_action'] = {"tool": tool_name, "summary": summary[:200],
                                "at": datetime.now().isoformat()}
        return {"decision": "allow", "reason": v.reason}
    if v.action == "deny":
        return {"decision": "deny", "reason": v.reason}
    _renewal(task_id, entry)
    return {"decision": "deny",
            "reason": ("Friday's approval for this task is used up or has expired; a "
                       "renewal card is waiting in Friday. Nothing runs until the owner "
                       "approves it. Tell the user, then retry after approval.")}


def _summarise(tool_name: str, tool_input: dict) -> str:
    for key in ("command", "file_path", "path", "url", "pattern", "query", "prompt"):
        if tool_input.get(key):
            return f"{tool_name}: {str(tool_input[key])[:300]}"
    return f"{tool_name}: {json.dumps(tool_input, default=str)[:300]}"


def _receipt_refusal(task_id: str, tool_name: str, why: str) -> None:
    try:
        from agent_friday.governance import action_gate as _gate
        _gate._receipt({"tool": ACTION_TOOL, "class": _gate.OUTWARD, "decision": "deny",
                        "reason": why, "surface": "claude_code", "task_id": task_id,
                        "target": str(tool_name)[:60]})
    except Exception:
        pass


def status_line(task_id: str) -> str:
    e = VIBE_TERMINALS.get(task_id) or {}
    return f"{e.get('status')} ({e.get('actions') or 0} actions approved through Friday)"


__all__ = ["KIND", "ACTION_TOOL", "LAUNCH_TOOL", "GRANT_SECONDS", "GRANT_USES", "scope",
           "request", "gate", "launch_allowed", "write_session_files", "status_line"]

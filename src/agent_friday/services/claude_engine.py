"""Claude's agent as a salon engine (docs/design/active/vibe-coding-salon.md
§4.7 ``engine: claude_agent``, §10 row 3).

The user's own Claude Code CLI runs one task inside the codebase's folder, as
a process on this PC (tier B1, with the disclosure that it could read this
PC's files while it worked), pointed at the salon proxy with a dummy key. The
proxy injects the real key, owner's or guest's, on requests to the provider
only, and records every host the agent reached. When the process ends,
everything it changed in the folder becomes ONE step whose receipt names the
engine, the hosts contacted and the disclosure.

Nothing here spends a token by itself: the engine runs only when the user
asks for it on a codebase whose engine they set to Claude's agent.
"""
from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import sys
from typing import Optional

from agent_friday.services import codebases as cb
from agent_friday.services.salon_proxy import DUMMY_KEY, KeyProxy

_log = logging.getLogger(__name__)

UPSTREAM = "https://api.anthropic.com"
TIER = "B1"
DISCLOSURE = ("Claude's agent ran as an ordinary process on this PC (B1): it could read this PC's files while it "
              "worked. The key never entered its environment; the salon proxy injected it on requests to the "
              "provider only.")
#: The parts of the environment a coding agent needs to start on Windows and
#: to find its own settings. Nothing else crosses: no provider keys, no proxies.
_ENV_KEEP = ("PATH", "PATHEXT", "SYSTEMROOT", "SystemRoot", "COMSPEC", "ComSpec", "TEMP", "TMP", "USERPROFILE",
             "APPDATA", "LOCALAPPDATA", "HOMEDRIVE", "HOMEPATH", "HOME", "PROGRAMDATA", "ProgramData")


def _engine_command() -> Optional[list]:
    """The command that runs Claude's agent, or None when it is not installed."""
    exe = shutil.which("claude")
    return [exe] if exe else None


def _owner_key() -> Optional[str]:
    try:
        from agent_friday.services import credential_store as _cs
        key = _cs.get_provider_key("anthropic")
        if key:
            return key
    except Exception:
        pass
    key = os.environ.get("ANTHROPIC_API_KEY", "")
    if key:
        return key
    try:
        from agent_friday import core
        return getattr(core, "ANTHROPIC_API_KEY", "") or None
    except Exception:
        return None


def _key_provider(cid: str, key_profile: str):
    if key_profile and key_profile != "mine":
        return lambda: cb.guest_key_secret(cid, key_profile)
    return _owner_key


def _scrubbed_env(proxy_url: str) -> dict:
    env = {k: os.environ[k] for k in _ENV_KEEP if k in os.environ}
    env["ANTHROPIC_BASE_URL"] = proxy_url
    env["ANTHROPIC_API_KEY"] = DUMMY_KEY
    env["PYTHONIOENCODING"] = "utf-8"
    return env


def _parse_result(stdout: str) -> dict:
    for line in reversed((stdout or "").strip().splitlines()):
        line = line.strip()
        if line.startswith("{"):
            try:
                d = json.loads(line)
                if isinstance(d, dict):
                    return d
            except Exception:
                continue
    return {"result": (stdout or "").strip()[-2000:]}


def run_task(cid: str, task: str, *, key_profile: str = "mine", timeout_s: int = 600, extra_args: Optional[list] = None) -> dict:
    """Run one task with Claude's agent in the codebase's folder and make its
    changes one step. Returns the step and the evidence, or a typed refusal."""
    rec = cb.load(cid)
    if rec is None:
        raise KeyError(cid)
    task = " ".join(str(task or "").split())
    if not task:
        return {"status": "refused", "blocker": "needs_user_input", "say": "Say what the agent should do."}
    cmd = _engine_command()
    if not cmd:
        return {"status": "refused", "blocker": "missing_dependency",
                "say": "Claude's agent is not installed on this PC (no `claude` command). Install Claude Code, or keep Friday as this codebase's engine."}
    key_profile = key_profile or rec.get("key_profile") or "mine"
    proxy = KeyProxy(upstream=UPSTREAM, key_provider=_key_provider(cid, key_profile))
    proxy.start()
    repo = cb.repo_path(cid)
    args = list(cmd) + ["-p", task, "--output-format", "json"] + list(extra_args or [])
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if sys.platform == "win32" else 0
    stdout, stderr, code, timed_out = "", "", None, False
    try:
        cp = subprocess.run(args, cwd=str(repo), env=_scrubbed_env(proxy.url), capture_output=True, text=True,
                            encoding="utf-8", errors="replace", timeout=timeout_s, creationflags=flags)
        stdout, stderr, code = cp.stdout, cp.stderr, cp.returncode
    except subprocess.TimeoutExpired as e:
        timed_out = True
        stdout = (e.stdout or b"").decode("utf-8", "replace") if isinstance(e.stdout, bytes) else (e.stdout or "")
        stderr = "timed out after %ds" % timeout_s
    finally:
        proxy.stop()
    summary = proxy.summary()
    engine = _parse_result(stdout)
    base = {"engine": engine, "disclosure": DISCLOSURE, "tier": TIER, "hosts": summary["hosts"],
            "proxy": {"stopped": True, "requests": summary["requests"], "refused": summary["refused"]},
            "returncode": code, "stderr_tail": (stderr or "")[-600:]}
    if timed_out or (code not in (0, None)):
        base.update(status="failed", blocker="run_failed", step=None,
                    say="Claude's agent %s; nothing was committed. Its changes, if any, are in the folder for you to look at."
                        % ("timed out" if timed_out else "exited with code %s" % code))
        return base
    st = cb.commit_working_tree(cid, "Claude's agent: %s" % task[:150], author="claude-agent", model="claude-agent",
                                key_profile=key_profile,
                                extra={"engine": "claude_agent", "tier": TIER, "hosts_contacted": summary["hosts"],
                                       "disclosure": DISCLOSURE, "engine_result": str(engine.get("result") or "")[:2000],
                                       "proxy_refused": summary["refused"]})
    if st is None:
        base.update(status="no_change", step=None, say="Claude's agent finished and changed nothing in the folder.")
        return base
    base.update(status="ok", step=st,
                say="Claude's agent made one step: %s. It reached %d host path%s through the proxy%s. %s"
                    % (st["summary"], len(summary["hosts"]), "" if len(summary["hosts"]) == 1 else "s",
                       (", and %d request%s was refused" % (summary["refused"], "" if summary["refused"] == 1 else "s")) if summary["refused"] else "",
                       DISCLOSURE))
    return base

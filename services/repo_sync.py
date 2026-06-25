"""Deterministic git-pull sync across the user's configured repos (Part A).

A tiny built-in scheduler task — deterministic and cheap, so it shouldn't burn
agent tokens (hence a plain function, not an ``agent_prompt``). The repo list is
read from settings (``repo_sync.repos``); each entry is an absolute path to a
git working tree. Reports a delta only — clean pulls are silent, conflicts /
failures surface a notification (the scheduler treats this as ``on_change``).
"""

import subprocess
from pathlib import Path

from core import _load_settings, _POPEN_FLAGS


def _git_pull(repo_path: Path) -> dict:
    try:
        proc = subprocess.run(
            ["git", "-C", str(repo_path), "pull", "--ff-only"],
            capture_output=True, text=True, timeout=120,
            creationflags=_POPEN_FLAGS,
        )
        out = (proc.stdout or "") + (proc.stderr or "")
        if proc.returncode != 0:
            return {"repo": repo_path.name, "ok": False, "changed": False,
                    "detail": out.strip()[:300]}
        changed = "Already up to date" not in out
        return {"repo": repo_path.name, "ok": True, "changed": changed,
                "detail": out.strip()[:300]}
    except Exception as e:  # noqa: BLE001
        return {"repo": repo_path.name, "ok": False, "changed": False,
                "detail": f"{type(e).__name__}: {e}"}


def run_repo_sync() -> dict:
    """Pull every configured repo. Returns a result dict the scheduler reads:
    ``changed`` is True when any repo updated or any pull failed."""
    cfg = (_load_settings().get("repo_sync") or {})
    repos = cfg.get("repos") or []
    if not repos:
        return {"changed": False, "summary": "No repos configured for sync.",
                "results": []}
    results = []
    for raw in repos:
        p = Path(raw).expanduser()
        if not (p / ".git").exists():
            results.append({"repo": p.name, "ok": False, "changed": False,
                            "detail": "not a git working tree"})
            continue
        results.append(_git_pull(p))

    failed = [r for r in results if not r["ok"]]
    updated = [r for r in results if r["ok"] and r["changed"]]
    if failed:
        summary = (f"{len(failed)} repo(s) failed to sync: "
                   + ", ".join(r["repo"] for r in failed))
    elif updated:
        summary = "Updated: " + ", ".join(r["repo"] for r in updated)
    else:
        summary = f"All {len(results)} repo(s) already up to date."
    return {"changed": bool(failed or updated), "summary": summary,
            "results": results}

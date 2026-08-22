"""Execute a model plan, and report only what was verified.

Separated from `model_plan` so the decision stays a pure function and the side
effects live in one place. This module is where the installer's honesty rule
gets enforced:

    Nothing here reports success it has not verified.

Concretely, a model is "installed" only when it appears in the daemon's own
inventory *after* the pull, resolved by its full tag. Not when the pull command
exits zero — a pull can exit zero having fetched a manifest and no weights, and
`ollama pull` on a name that resolves to a nonexistent tag is one of the ways
this has already gone wrong tonight. Not when the name looks right. When the
daemon lists it.

The verification step exists because every failure this codebase has produced
in the last month shares one shape: something reported done that was not done.
An installer is the worst possible place to repeat that, because the user has
no baseline yet and nothing to compare against.
"""
from __future__ import annotations

import subprocess
import time
from typing import Callable


class Result:
    """What actually happened, per model. Never optimistic."""

    def __init__(self, model_id: str):
        self.model_id = model_id
        self.ok = False
        self.detail = "not attempted"
        self.seconds = 0.0

    def __repr__(self):
        return f"<{self.model_id}: {'OK' if self.ok else 'FAILED'} — {self.detail}>"


def _installed_tags(list_fn: Callable[[], list]) -> set:
    try:
        return {str(m.get("name") or m.get("model") or "") for m in (list_fn() or [])}
    except Exception:
        return set()


def _resolves(tags: set, model_id: str) -> bool:
    """Is this EXACT tag present?

    A bare family name matches any tag of the family; a specific tag must match
    exactly, allowing only quantisation suffixes. This is the same rule as
    cli._has_model, and it is written out again here rather than imported
    because the CLI is not a dependency of the services layer — but if you
    change one, change both. The version that compared only the family prefix
    reported `gemma4:e2b` installed when `gemma4:12b` was present, and the next
    call 404'd.
    """
    if ":" not in model_id:
        return any(t.split(":")[0] == model_id for t in tags)
    return any(t == model_id or t.startswith(model_id + "-") for t in tags)


def install(plan: dict,
            *,
            pull_fn: Callable[[str], tuple] | None = None,
            list_fn: Callable[[], list] | None = None,
            say: Callable[[str], None] = print) -> dict:
    """Download everything the plan calls for. Returns a verified report.

    `pull_fn(model_id) -> (returncode, output)` and `list_fn() -> [{"name": ...}]`
    are injected so this is testable without a daemon. Defaults shell out to
    `ollama`.
    """
    if pull_fn is None:
        def pull_fn(model_id):
            p = subprocess.run(["ollama", "pull", model_id],
                               capture_output=True, text=True, timeout=3600)
            return p.returncode, (p.stdout or "") + (p.stderr or "")

    if list_fn is None:
        def list_fn():
            from agent_friday.routing.ollama_manager import get_manager
            return get_manager().list_models()

    results, wanted = [], plan.get("download") or []
    if not wanted:
        return {"results": [], "ok": True, "installed": 0, "failed": 0,
                "summary": "Nothing to install."}

    before = _installed_tags(list_fn)

    for m in wanted:
        mid = m["id"]
        r = Result(mid)
        results.append(r)

        if _resolves(before, mid):
            r.ok, r.detail = True, "already installed"
            say(f"  [ok]  {mid} — already installed")
            continue

        say(f"  ...   {mid} ({m['gib']:.2f} GiB) — {m['why']}")
        t0 = time.time()
        try:
            code, out = pull_fn(mid)
        except Exception as e:
            r.detail = f"pull raised: {type(e).__name__}: {e}"
            say(f"  [!!]  {mid} — {r.detail}")
            continue
        r.seconds = round(time.time() - t0, 1)

        # The pull's exit code is EVIDENCE, not proof. Verify against the
        # daemon's own inventory before claiming anything.
        after = _installed_tags(list_fn)
        if _resolves(after, mid):
            r.ok = True
            r.detail = f"verified present after pull ({r.seconds}s)"
            say(f"  [ok]  {mid} — installed and verified ({r.seconds}s)")
        elif code == 0:
            r.detail = ("pull reported success but the model is NOT in the "
                        "daemon's inventory afterwards — treating as failed")
            say(f"  [!!]  {mid} — {r.detail}")
        else:
            tail = " ".join((out or "").split())[-160:]
            r.detail = f"pull failed (exit {code}): {tail or 'no output'}"
            say(f"  [!!]  {mid} — {r.detail}")

    ok = [r for r in results if r.ok]
    bad = [r for r in results if not r.ok]
    return {
        "results": results,
        "ok": not bad,
        "installed": len(ok),
        "failed": len(bad),
        "summary": (f"{len(ok)} of {len(results)} models installed and verified."
                    + ("" if not bad else
                       "  FAILED: " + ", ".join(f"{r.model_id} ({r.detail})"
                                                for r in bad))),
    }


def vault_status(report: dict, plan: dict) -> tuple[bool, str]:
    """Can the vault actually be used after this install?

    The vault is the minimum requirement, so it gets its own verdict rather
    than being averaged into a total. A run that installs a conversational
    brain and fails the embedder has not succeeded, whatever the count says.
    """
    need = {m["id"] for t in plan.get("tiers", [])
            if t["id"] == "vault" for m in t.get("models", [])}
    if not need:
        return False, ("The vault tier was refused, so vault memory and tools "
                       "are not available on this machine.")
    got = {r.model_id for r in report.get("results", []) if r.ok}
    missing = need - got
    if missing:
        return False, ("Vault memory is NOT working: " + ", ".join(sorted(missing))
                       + " did not install. Friday will run, but she cannot "
                         "index or search your vault until this succeeds. "
                         "Re-run `friday models` to retry.")
    return True, "Vault memory and tools are installed and verified."

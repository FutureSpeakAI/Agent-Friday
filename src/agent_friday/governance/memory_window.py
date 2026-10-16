"""The memory-write window (SENSITIVE: governance).

The owner's standing "No Pestering" rule: when Friday saves to its memory
while working on one conversation or one task, the owner decides once, not
once per write. Approving a provenance card for a memory write opens a
window: the same tool, in the same conversation (or the same background
task), may write to Friday's memory folders without a new card until the
window expires (WINDOW_S) or is used up (WINDOW_USES). The card says so before
the owner decides.

What a window can never cover:
  * any path outside MEMORY_DIRS under Friday's home: governance state, the
    approvals file, the decision receipts, settings, credentials and keys all
    live elsewhere, and a protected name inside a memory folder is refused too;
  * a delete, an empty write, or a learn_skill read/list (which writes nothing);
  * a call carrying any flag other than a memory-write flag (a recipient, a
    URL, a command, a payment detail);
  * another conversation, another task, or a chat session with no
    conversation id (a date-keyed session is shared across conversations);
  * any tool other than the one approved.

Only the owner's own decision opens a window: a verified click on Friday's
page ("owner:ui") or the server's voice path ("owner:/voice/"). Neither label
can be claimed in a request body (services/screen_click.decided_by).

Every window is receipted when it opens and every time it is used, in the
signed decision receipts, naming the window (grant) and the card that opened
it. A window is an ordinary scoped grant: listed and revocable with the other
governance grants.

Batching a write whose detail came from outside content (a "warn" flag) is the
owner's product decision: the provenance card is the decision point, and the
window extends that one decision to the conversation's next memory writes.
"""
from __future__ import annotations

import time
from pathlib import Path
from typing import Any, Dict, Iterable, Optional

#: How long a window stays open, and how many writes it covers.
WINDOW_S = 2 * 3600
WINDOW_USES = 25

#: The folders under Friday's home a window may write into.
MEMORY_DIRS = ("wiki", "skills", "notes")

#: The tools a window can cover. Each window covers exactly one of them.
TOOLS = ("learn_skill", "correct_wiki", "propose_wiki_update", "write_file")

#: Who can open a window: a verified click, or the server's voice path.
APPROVERS = frozenset({"owner:ui", "owner:/voice/"})

#: The flag roles a covered call may carry; any other role means a card.
ROLES = frozenset({"memory_write", "write_path"})

#: Names never written through a window, even inside a memory folder.
_PROTECTED_STEMS = ("settings", "approvals", "decision-bom", "grants", "credential",
                    "secret", "token", "passphrase", "vault", "keystore")
_PROTECTED_SUFFIXES = (".key", ".pem", ".p12", ".pfx", ".kdbx")

SCOPE_PREFIX = "memwin:"


def _home() -> Path:
    from agent_friday.paths import friday_home
    return Path(friday_home()).resolve()


def _rule_files() -> set:
    try:
        from agent_friday.services import taint
        return {str(n).lower() for n in taint._RULE_FILES}
    except Exception:
        return {"soul.md", "self.md", "agents.md", ".friday-context.md"}


def memory_path(path: Any) -> bool:
    """A path a window may write: inside one of Friday's memory folders, and not
    a protected or rule-file name."""
    raw = str(path or "").strip()
    if not raw:
        return False
    try:
        import os
        p = Path(os.path.expanduser(raw)).resolve()
        home = _home()
    except Exception:
        return False
    if not any((home / d) in p.parents for d in MEMORY_DIRS):
        return False
    name = p.name.lower()
    if name in _rule_files() or name.endswith(_PROTECTED_SUFFIXES):
        return False
    return not any(stem in name for stem in _PROTECTED_STEMS)


def batchable(tool: str, args: Optional[dict]) -> bool:
    """Whether a window could cover this call at all."""
    a = args or {}
    if tool == "learn_skill":
        return (str(a.get("action") or "").strip().lower() in ("create", "modify")
                and bool(str(a.get("content") or "").strip()))
    if tool == "correct_wiki":
        return bool(str(a.get("new_text") or "").strip())
    if tool == "propose_wiki_update":
        return bool(str(a.get("new_value") or "").strip())
    if tool == "write_file":
        return bool(str(a.get("content") or "").strip()) and memory_path(a.get("path"))
    return False


def scope_of(ctx: Optional[dict]) -> Optional[str]:
    """The window's scope, from server-set fields only: a conversation, else a
    background task. A chat session with neither has no window."""
    c = ctx or {}
    cid = str(c.get("conversation_id") or "").strip()
    if cid:
        return SCOPE_PREFIX + "conversation:" + cid
    tid = str(c.get("task_id") or "").strip()
    if tid:
        return SCOPE_PREFIX + "task:" + tid
    return None


def card_note(tool: str, args: Optional[dict], ctx: Optional[dict]) -> str:
    """The sentence the card carries when approving it opens a window ('' when
    it does not), so the owner knows what the approval covers."""
    if not batchable(tool, args) or not scope_of(ctx):
        return ""
    where = ("this conversation" if str((ctx or {}).get("conversation_id") or "").strip()
             else "this task")
    return (f"Approving also lets Friday make up to {WINDOW_USES} more saves of this kind "
            f"to its memory in {where} for the next {WINDOW_S // 3600} hours, without asking "
            f"again. It is listed with Friday's other grants in Settings (\"Scheduled jobs: what "
            f"they may do on their own\"), where you can revoke it.")


def open_window(record: Dict[str, Any]) -> Optional[dict]:
    """Open a window for an approved provenance card, or return None. Receipted."""
    from agent_friday.governance import action_gate as gate
    if not isinstance(record, dict) or record.get("kind") != "tainted_action":
        return None
    if record.get("status") != "approved" or record.get("decided_by") not in APPROVERS:
        return None
    payload = record.get("payload") or {}
    tool, args = payload.get("tool"), payload.get("input")
    scope = scope_of(payload)
    if tool not in TOOLS or not scope or not batchable(tool, args):
        return None
    if any(g.get("scope") == scope and tool in (g.get("tools") or [])
           for g in gate.list_grants()):
        return None                       # one open window per scope and tool
    card = str(record.get("approval_id") or "")
    g = gate.create_grant(tools=[tool], scope=scope, expires_in_seconds=WINDOW_S,
                          max_uses=WINDOW_USES, created_by=str(record.get("decided_by")),
                          note="Memory-write window opened by card " + card, source=card)
    gate.receipt_window("opened", g)
    return g


def spend(tool: str, args: Optional[dict], ctx: Optional[dict],
          roles: Iterable[str] = ()) -> Optional[dict]:
    """One use of the window that covers this call, or None (then it gets its card)."""
    if tool not in TOOLS or not batchable(tool, args):
        return None
    if not set(roles or ()) <= ROLES:
        return None
    scope = scope_of(ctx)
    if not scope:
        return None
    from agent_friday.governance import action_gate as gate
    return gate.consume_grant(tool, scope)


def is_window(grant: Optional[dict]) -> bool:
    return bool(grant) and str(grant.get("scope") or "").startswith(SCOPE_PREFIX)


def expires_in(grant: Optional[dict]) -> int:
    return max(0, int(float((grant or {}).get("expires_at") or 0) - time.time()))

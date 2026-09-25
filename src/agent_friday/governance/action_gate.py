"""The one governance checkpoint every action passes before it runs.

`authorize(tool_name, args, session_ctx)` is called from `agent._execute_tool`
(and from the few executors that do not go through it, see `authorize_external`)
BEFORE a handler runs. It is enforced in code; the prompt only describes it.

Per call it:

  1. verifies the cLaws are intact -- the HMAC of the canonical cLaws text
     under the governance key must equal the value pinned in
     ~/.friday/governance/claws.pin.json (pinned on first use);
  2. classifies the action: INTERNAL (reading, drafting, local and reversible
     work) or OUTWARD (reaches other people, money, accounts, runs code, or
     cannot be undone);
  3. lets an internal action through, and holds an outward one until the owner
     has decided it: a chat yes in an interactive conversation (the existing
     confirmation flow), an approval card, or -- for scheduled and background
     work -- a pre-approved grant that names the tool, is scoped to that job
     and expires;
  4. writes a signed receipt of the decision to ~/.friday/decision-bom.jsonl.

It fails closed. If the cLaws check fails, the classifier errors, the
configured Laya classifier is unavailable, the receipt cannot be signed or
written, or anything in here raises, an outward action is HELD, never run.
Internal actions (reads) keep working so Friday can still say what happened.

What stays outside it on purpose, and why, is listed in
docs/decisions/2026-09-24-injection-provenance-gate.md.
"""
from __future__ import annotations

import hashlib
import hmac as _hmac
import json
import logging
import re
import threading
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from agent_friday.paths import friday_home

_log = logging.getLogger("friday.governance")
_LOCK = threading.RLock()

INTERNAL = "internal"
OUTWARD = "outward"


def _gov_dir() -> Path:
    d = Path(friday_home()) / "governance"
    d.mkdir(parents=True, exist_ok=True)
    return d


# ── 1. cLaws integrity ──────────────────────────────────────────────────────

def _governance_key() -> bytes:
    from agent_friday.governance.proof_of_integrity import get_governance_key
    key = get_governance_key()
    if not key:
        raise RuntimeError("governance key unavailable")
    return key


def claws_hmac(key: Optional[bytes] = None) -> str:
    from agent_friday.governance.proof_of_integrity import CLAWS_TEXT
    k = key or _governance_key()
    return _hmac.new(k, CLAWS_TEXT.encode("utf-8"), hashlib.sha256).hexdigest()


def verify_claws() -> tuple:
    """(ok, reason). The pin is written the first time and compared after.

    A mismatch means the cLaws text or the governance key changed since the
    pin: either is a reason to stop taking outward actions until the owner
    re-pins (`repin_claws`), not something to paper over.
    """
    try:
        mac = claws_hmac()
    except Exception as e:
        return False, f"cannot compute the cLaws signature: {e}"
    pin = _gov_dir() / "claws.pin.json"
    try:
        with _LOCK:
            if not pin.exists():
                pin.write_text(json.dumps({"claws_hmac": mac, "pinned_at": time.time()}),
                               encoding="utf-8")
                return True, "pinned on first use"
            want = json.loads(pin.read_text(encoding="utf-8")).get("claws_hmac")
    except Exception as e:
        return False, f"cannot read the cLaws pin: {e}"
    if not want or not _hmac.compare_digest(str(want), mac):
        return False, "the cLaws signature does not match the pinned one"
    return True, "intact"


def repin_claws() -> str:
    """Owner action after an intended cLaws or key change."""
    mac = claws_hmac()
    (_gov_dir() / "claws.pin.json").write_text(
        json.dumps({"claws_hmac": mac, "pinned_at": time.time()}), encoding="utf-8")
    return mac


# ── 2. Classification ──────────────────────────────────────────────────────

#: Friday's own tools that act outside the conversation: other people, money,
#: accounts, publishing, code execution, or no undo.
OUTWARD_TOOLS = frozenset({
    "draft_email",                  # its own card is the gate (SELF_GATED)
    "create_calendar_event", "update_calendar_event", "annotate_calendar_events",
    # Scheduling (services/scheduling.py). book_slot sends invitations to
    # other people. hold_slots writes only to the owner's own calendar and
    # invites nobody, but a hold shows as busy to everyone who can see that
    # calendar's free/busy (colleagues on a work account), so it changes what
    # other people see: it asks, once per batch of holds. A standing,
    # expiring grant for holds is a later refinement, not a default.
    "hold_slots", "book_slot",
    "delete_task",
    "install_package",
    "spawn_interactive_session", "send_to_session",
    "content_schedule_post",
    # Phone (agent_friday/phone): a text reaches a person the moment it is
    # sent; a call only ever raises its own card (SELF_GATED below).
    "text_by_phone", "call_by_phone",
    # A signature binds the owner. The tool only raises a card naming the
    # file, page and placement; services/pdf_signing signs on approval.
    "sign_pdf",
    # Changes the owner's Google Contacts, which sync to every device.
    "save_google_contact",
})

#: Tools whose handler raises its own approval card and cannot complete the
#: action itself (draft_email only queues; gmail_send sends on approval).
SELF_GATED = frozenset({"draft_email", "call_by_phone", "sign_pdf"})

#: Friday's own tools that stay inside: reading, searching, drafting, local
#: files the confirmation gate already asks about, memory writes the taint
#: gate already judges, and delegation (a spawned task's own actions come
#: back through this checkpoint one by one).
INTERNAL_TOOLS = frozenset({
    "search_web", "browse_web", "read_file", "search_files",
    "write_clipboard", "query_trust_graph", "query_calendar", "revert_workspace",
    "list_workspace_history", "find_calendar_events", "search_email",
    "search_drive", "read_doc", "list_tasks", "complete_task", "create_task",
    "update_task", "search_contacts", "read_wiki", "search_wiki", "search_news",
    "open_url", "open_path", "navigate", "switch_model", "list_sending_accounts",
    "get_career_pipeline", "get_briefing", "spawn_task", "propose_wiki_update",
    # Background research reads the web and runs local models; its report
    # lands in the conversation. Nothing it does reaches another person.
    "deep_research",
    "correct_wiki", "learn_skill", "epistemic_score", "personality_show",
    "personality_check_sycophancy", "generate_image", "generate_video",
    "generate_music", "compose_timeline", "create_presentation", "create_website",
    "office_check",                 # validates; renders a preview PNG beside it
    "create_workflow", "run_workflow", "workflow_status", "creative_project",
    "start_creative_pipeline", "compare_image_takes", "content_post_status",
    "content_repurpose", "knowledge_query", "knowledge_related",
    "knowledge_communities", "inspect_image", "inspect_audio", "save_output",
    "speak_text", "list_voices", "read_session_output", "load_tools",
    # Voice-only helpers routed through the checkpoint.
    "check_email", "get_source_trust", "get_article_deep_dive", "ask_friday",
    # find_free_slots reads free/busy only. release_holds deletes nothing but
    # Friday's own holds: each event is re-read and must carry Friday's active
    # hold marker for that series and no attendees, and nobody is notified,
    # so it undoes Friday's own earlier work and reaches no one.
    "find_free_slots", "release_holds",
    "list_pdf_fields",              # reads a form's fields
    # Relationship memory: reads of the local timeline, and a local reminder
    # that is never sent to the person it is about.
    "person_timeline", "people_at", "set_follow_up",
})

#: Classified by argument: run_command by its command, content_create_post by
#: whether it schedules, write_file and fill_pdf_form by where they write.
BY_ARGUMENT = frozenset({"run_command", "content_create_post", "office",
                         "write_file", "fill_pdf_form",
                         # Desktop control (ring 3), by the app it lands on:
                         # services/desktop_grants.py. The Computer Control
                         # switch, grant and kill switch are checked before
                         # this, in agent._governance_check.
                         "move_mouse", "click", "type_text", "press_key",
                         "screenshot", "scroll"})

_READ_VERBS = ("get", "list", "search", "read", "fetch", "query", "find", "check",
               "lookup", "describe", "show", "view", "count", "status", "explore",
               "balance", "info")


def known(tool_name: str) -> bool:
    return (tool_name in OUTWARD_TOOLS or tool_name in INTERNAL_TOOLS
            or tool_name in BY_ARGUMENT)


# ── run_command: read-only allowlist, everything else outward ───────────────

#: Commands that only read. Anything not on this list -- or any command that
#: chains, redirects, calls out, or names this machine's own API -- is outward.
READ_ONLY_COMMANDS = frozenset({
    "get-childitem", "gci", "dir", "ls", "get-content", "gc", "cat", "type",
    "get-item", "gi", "test-path", "get-date", "get-process", "gps", "ps",
    "get-service", "gsv", "get-location", "pwd", "resolve-path", "split-path",
    "join-path", "get-filehash", "measure-object", "measure", "select-string",
    "sls", "select-object", "select", "where-object", "where", "sort-object",
    "sort", "format-table", "ft", "format-list", "fl", "out-string",
    "get-command", "gcm", "get-help", "whoami", "hostname", "get-psdrive",
    "get-volume", "get-computerinfo", "get-ciminstance", "echo", "write-output",
    "get-itemproperty", "gp", "get-acl", "get-host", "get-culture",
    "get-timezone", "get-uptime", "tree", "where.exe", "systeminfo",
    "git",  # read-only subcommands only, see _GIT_READ
})
_GIT_READ = frozenset({"status", "log", "diff", "show", "branch", "rev-parse",
                       "ls-files", "blame", "describe", "remote", "tag"})
#: Never allowed without a decision, whatever the first word is.
_DANGEROUS = re.compile(
    r"(?:[;&]|\|\||>|\$\(|`|\binvoke-|\biex\b|\biwr\b|\birm\b|\bcurl\b|\bwget\b|"
    r"\bstart-process\b|\bsaps\b|\bnew-object\b|\bset-|\bremove-|\bnew-|\bcopy-|"
    r"\bmove-|\brename-|\bstop-|\bclear-|\bout-file\b|\badd-content\b|\bstart\b)",
    re.I)
#: This machine's own API. Loopback is trusted by the web app as the owner, so
#: a command that can reach it can approve its own cards or switch gates off.
_SELF_API = re.compile(r"\b(?:localhost|127\.\d+\.\d+\.\d+|0\.0\.0\.0|\[?::1\]?|agent\.friday)\b"
                       r"|/api/", re.I)


def classify_command(cmd: str) -> tuple:
    """(class, why) for one PowerShell command."""
    c = (cmd or "").strip()
    if not c:
        return INTERNAL, "empty"
    if _SELF_API.search(c):
        return "forbidden", "it addresses Friday's own local API"
    if _DANGEROUS.search(c):
        return OUTWARD, "it chains, redirects, changes or reaches outside"
    for stage in c.split("|"):
        words = stage.strip().split()
        if not words:
            continue
        head = words[0].lower()
        if head not in READ_ONLY_COMMANDS:
            return OUTWARD, f"'{words[0]}' is not on the read-only list"
        if head == "git" and (len(words) < 2 or words[1].lower() not in _GIT_READ):
            return OUTWARD, "a git command that is not read-only"
    return INTERNAL, "read-only"


def _resolve(path) -> Optional[Path]:
    import os
    try:
        return Path(os.path.expanduser(str(path))).resolve()
    except Exception:
        return None


def _output_dirs() -> list:
    """Folders that hold Friday's own output: the creations folders and the
    office documents folder. What lands here is Friday's work, not the
    owner's files or Friday's configuration."""
    dirs = []
    try:
        from agent_friday import core as _core
        dirs += [getattr(_core, "CREATIONS_DIR", None), getattr(_core, "DAILY_CREATIONS_DIR", None)]
    except Exception:
        pass
    try:
        from agent_friday.services import office_engine as _oe
        dirs.append(_oe.DOCUMENTS_DIR)
    except Exception:
        pass
    out = []
    for d in dirs:
        if d:
            try:
                out.append(Path(d).resolve())
            except Exception:
                pass
    return out


def _in_output_dir(p: Path) -> bool:
    for d in _output_dirs():
        if p == d or d in p.parents:
            return True
    return False


def _writes_friday_state(path) -> bool:
    """A file write that lands in Friday's own state (settings, approvals,
    schedules, skills, wiki) or in a file it loads as instructions. The
    output folders are ordinary output and are not state."""
    if not path:
        return False
    p = _resolve(path)
    if p is None:
        return True
    try:
        from agent_friday.services.taint import _RULE_FILES
        if p.name.lower() in _RULE_FILES:
            return True
    except Exception:
        return True
    if _in_output_dir(p):
        return False
    try:
        p.relative_to(Path(friday_home()).resolve())
        return True
    except ValueError:
        return False


def classify_write(path) -> tuple:
    """(class, why) for a file write.

    Only Friday's output folders are internal. Anywhere else is the owner's
    disk: a write there can replace a document, drop a script into a folder
    that runs at sign-in, or change what another program does. That waits for
    a decision -- a yes in chat, or a card when nobody is in the chat.
    """
    if not path:
        return OUTWARD, "a file write with no path"
    if _writes_friday_state(path):
        return OUTWARD, "it rewrites Friday's own settings, memory or rules"
    p = _resolve(path)
    if p is not None and _in_output_dir(p):
        return INTERNAL, "it writes into Friday's own output folder"
    return OUTWARD, "it writes a file outside Friday's output folders"


def _laya_down() -> bool:
    """True when settings ask for a Laya backend that is not available."""
    try:
        import os
        want = (os.environ.get("FRIDAY_DECISION_BACKEND") or "").strip()
        if not want:
            from agent_friday.core import _load_settings
            want = str((_load_settings() or {}).get("decision_backend") or "")
        if "laya" not in want:
            return False
        from agent_friday.services import decisions as _dec
        from agent_friday.services import laya_backend as _lb
        # `decisions.active_backend()` quietly answers "keyword" for a backend
        # that is not registered, and the union answers with the keyword half
        # while the model loads. Neither is Laya answering.
        return want not in _dec.available_backends() or not _lb.is_ready()
    except Exception:
        return True


def classify(tool_name: str, args: Optional[dict]) -> tuple:
    """(INTERNAL|OUTWARD|"forbidden", why). Unknown tools are OUTWARD."""
    a = args or {}
    if tool_name == "run_command":
        return classify_command(str(a.get("command") or ""))
    from agent_friday.services import desktop_grants as _dg
    if _dg.is_desktop_tool(tool_name):
        # Before the generic connector rule below: a desktop connector tool
        # is judged by the app it lands on, like Friday's own `click`.
        return _dg.classify(tool_name, a)
    if tool_name == "office":
        # By argument, like run_command: the verb and the target decide.
        # Reading a document and building a new one inside Friday's own
        # documents folder are internal; overwriting a file that already
        # exists is not reversible, so it waits for a decision.
        try:
            from agent_friday.services import office_engine as _oe
            return _oe.classify(a)
        except Exception as e:
            return OUTWARD, f"the office command could not be classified ({e})"
    if tool_name == "write_file":
        return classify_write(a.get("path"))
    if tool_name == "fill_pdf_form":
        # A new file in Friday's output folder is internal; replacing a file
        # or writing anywhere else is outward; over the source is refused.
        try:
            from agent_friday.services import pdf_forms as _pf
            return _pf.classify(a)
        except Exception as e:
            return OUTWARD, f"the form fill could not be classified ({e})"
    if tool_name == "content_create_post":
        if a.get("publish_at") or a.get("optimal_time"):
            return OUTWARD, "it schedules a post to go out"
        return INTERNAL, "it only saves a draft"
    if tool_name in OUTWARD_TOOLS:
        return OUTWARD, "it acts outside this conversation"
    if tool_name in INTERNAL_TOOLS:
        return INTERNAL, "internal"
    low = (tool_name or "").lower()
    if low.startswith("mcp_"):
        # Connector tools: a read verb in the name AND the union gate agreeing
        # it is internal. Either one saying otherwise makes it outward, and so
        # does the union gate being unable to answer.
        # The tool's own first word after the server name must be the read
        # verb. Anywhere in the name is not enough: "update_user_info" ends
        # in a read-sounding word and writes your bank profile.
        parts = low[4:].split("_")
        verb_read = len(parts) > 1 and parts[1] in _READ_VERBS
        if not verb_read:
            return OUTWARD, "a connector tool that is not a read"
        if _laya_down():
            return OUTWARD, "the Laya classifier is configured but unavailable"
        try:
            from agent_friday.services import approvals as _ap
            desc = f"{tool_name} {json.dumps(a, default=str)[:300]}"
            if _ap.classify(desc).get("gated"):
                return OUTWARD, "the action classifier judged it outward"
        except Exception as e:
            return OUTWARD, f"the action classifier failed ({e})"
        return INTERNAL, "a connector read"
    return OUTWARD, "an unknown tool is treated as outward"


# ── 3. Grants for work nobody is watching ───────────────────────────────────

def _grants_file() -> Path:
    return _gov_dir() / "grants.json"


def _read_grants() -> list:
    try:
        return json.loads(_grants_file().read_text(encoding="utf-8"))
    except Exception:
        return []


def create_grant(*, tools, scope: str, expires_in_seconds: float,
                 max_uses: int = 1, created_by: str = "owner", note: str = "") -> dict:
    """Pre-approve named outward tools for one scheduled job or task.

    `scope` is the schedule id or task id the grant belongs to. Nothing else
    can use it, and it stops working at `expires_in_seconds` or after
    `max_uses`, whichever comes first. Owner-only: callers are the owner's
    authenticated routes, never a tool.
    """
    if not tools or not scope or expires_in_seconds <= 0 or max_uses < 1:
        raise ValueError("a grant needs tools, a scope, a positive expiry and uses")
    g = {"grant_id": "grant_" + uuid.uuid4().hex[:10], "tools": sorted(set(tools)),
         "scope": str(scope), "expires_at": time.time() + float(expires_in_seconds),
         "uses_left": int(max_uses), "created_by": created_by, "note": note[:200],
         "created_at": time.time()}
    with _LOCK:
        gs = [x for x in _read_grants() if x.get("expires_at", 0) > time.time()]
        gs.append(g)
        _grants_file().write_text(json.dumps(gs, indent=1), encoding="utf-8")
    return g


def list_grants() -> list:
    return [g for g in _read_grants() if g.get("expires_at", 0) > time.time()]


def revoke_grant(grant_id: str) -> bool:
    with _LOCK:
        gs = _read_grants()
        keep = [g for g in gs if g.get("grant_id") != grant_id]
        _grants_file().write_text(json.dumps(keep, indent=1), encoding="utf-8")
    return len(keep) != len(gs)


def _scopes(ctx: dict) -> set:
    return {str(ctx[k]) for k in ("schedule_id", "task_id", "run_id", "grant_scope")
            if ctx.get(k)}


def _use_grant(tool_name: str, ctx: dict) -> Optional[dict]:
    scopes = _scopes(ctx)
    if not scopes:
        return None
    with _LOCK:
        gs = _read_grants()
        now = time.time()
        for g in gs:
            if (g.get("scope") in scopes and tool_name in (g.get("tools") or [])
                    and g.get("expires_at", 0) > now and int(g.get("uses_left") or 0) > 0):
                g["uses_left"] = int(g["uses_left"]) - 1
                _grants_file().write_text(json.dumps(gs, indent=1), encoding="utf-8")
                return dict(g)
    return None


# ── 4. Receipts ─────────────────────────────────────────────────────────────

def _receipt(entry: dict) -> None:
    """Sign and append. Raises on failure, so the caller can hold the action."""
    entry = dict(entry, timestamp=datetime.utcnow().isoformat() + "Z")
    canonical = json.dumps(entry, sort_keys=True, default=str).encode("utf-8")
    entry["hmac"] = _hmac.new(_governance_key(), canonical, hashlib.sha256).hexdigest()
    path = receipts_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, default=str) + "\n")


def receipts_path() -> Path:
    return Path(friday_home()) / "decision-bom.jsonl"


def verify_receipt(entry: dict, key: Optional[bytes] = None) -> bool:
    """True when `entry` carries an HMAC that matches its own content under
    the governance key -- the inverse of `_receipt`. Read-only; a receipt
    with no signature, a changed field, or a key that cannot be loaded is
    reported as not verified, never as verified."""
    if not isinstance(entry, dict):
        return False
    sig = entry.get("hmac")
    if not isinstance(sig, str) or not sig:
        return False
    body = {k: v for k, v in entry.items() if k != "hmac"}
    try:
        k = key or _governance_key()
        canonical = json.dumps(body, sort_keys=True, default=str).encode("utf-8")
        want = _hmac.new(k, canonical, hashlib.sha256).hexdigest()
    except Exception:
        return False
    return _hmac.compare_digest(want, sig)


# ── The checkpoint ──────────────────────────────────────────────────────────

@dataclass
class Verdict:
    action: str                  # allow | confirm | card | deny
    klass: str
    reason: str
    grant: Optional[dict] = None
    detail: dict = field(default_factory=dict)


def _interactive(ctx: dict) -> bool:
    return bool(ctx.get("session_id")) and not (
        ctx.get("is_background_task") or ctx.get("scheduled") or ctx.get("confirm_bypass"))


def authorize(tool_name: str, args: Optional[dict], session_ctx: Optional[dict] = None,
              *, tainted: bool = False) -> Verdict:
    """Decide whether this action may run now. Never raises.

    allow    run it
    confirm  outward, in an interactive chat, nothing from outside content:
             the confirmation gate asks the user yes/no in chat
    card     outward and nobody can be asked in chat, or a detail came from
             outside content: an approval card decides it
    deny     never run (forbidden, or the checkpoint itself failed)
    """
    ctx = session_ctx or {}
    failed = None
    try:
        klass, why = classify(tool_name, args)
    except Exception as e:
        klass, why, failed = OUTWARD, "", f"classification failed ({e})"
    try:
        # A check that could not run holds the action; not even a grant
        # overrides that, because nobody knows what the action is.
        v = (Verdict("deny", klass, f"held: {failed}") if failed
             else _decide(tool_name, klass, why, ctx, tainted))
    except Exception as e:
        v = Verdict("deny" if klass != INTERNAL else "allow", klass,
                    f"governance check failed ({e})")
    try:
        _receipt({"tool": tool_name, "class": v.klass, "decision": v.action,
                  "reason": v.reason, "tainted": tainted,
                  "args_hash": hashlib.sha256(json.dumps(args or {}, sort_keys=True,
                                                         default=str).encode()).hexdigest(),
                  "surface": ctx.get("surface") or ("chat" if ctx.get("session_id") else
                                                     "background" if ctx.get("is_background_task") else "other"),
                  "grant": (v.grant or {}).get("grant_id"),
                  # Which background task took the action, so the morning
                  # receipt can link a decision to the work it belonged to.
                  "task_id": ctx.get("task_id")})
    except Exception as e:
        _log.error("governance receipt failed: %s", e)
        if v.klass != INTERNAL and v.action != "deny":
            return Verdict("deny", v.klass, f"the signed receipt could not be written ({e}); "
                                            f"outward actions are held")
    return v


def _decide(tool_name, klass, why, ctx, tainted) -> Verdict:
    if klass == "forbidden":
        return Verdict("deny", klass, f"not allowed: {why}")
    if klass == INTERNAL:
        return Verdict("card" if tainted else "allow", klass, why)
    ok, integrity = verify_claws()
    if not ok:
        return Verdict("deny", klass, f"held: cLaws integrity check failed -- {integrity}")
    if tool_name in SELF_GATED and not tainted:
        return Verdict("allow", klass, "its own approval card is the gate")
    if tool_name in SELF_GATED:
        return Verdict("allow", klass, "its own approval card is the gate (with provenance)")
    if not tainted and not _interactive(ctx):
        g = _use_grant(tool_name, ctx)
        if g is not None:
            return Verdict("allow", klass, f"pre-approved grant {g['grant_id']}", grant=g)
    if _interactive(ctx) and not tainted:
        return Verdict("confirm", klass, why)
    return Verdict("card", klass, why)


class Held(RuntimeError):
    """An outward action the checkpoint would not let through."""


def record_external(action: str, *, surface: str, approval_id: Optional[str] = None,
                    target: str = "") -> None:
    """The checkpoint's integrity check and signed receipt, for an executor
    that carries out an already-decided action outside a tool call (an
    approved email, an approved text).

    The decision itself (the card) is the caller's to verify. This adds the
    two steps every tool call gets in `authorize`: the cLaws are intact, and
    a signed receipt of the action is written. Raises `Held` if either fails,
    so the caller stops before anything leaves the machine.
    """
    try:
        ok, why = verify_claws()
    except Exception as e:
        raise Held(f"the cLaws integrity check could not run ({e})")
    if not ok:
        raise Held(f"the cLaws integrity check failed ({why})")
    try:
        _receipt({"tool": action, "class": OUTWARD, "decision": "allow",
                  "surface": surface,
                  "reason": "approved card" if approval_id else surface,
                  "approval": approval_id, "target": target})
    except Exception as e:
        raise Held(f"the signed receipt could not be written ({e})")


def authorize_external(action: str, detail: dict, *, requested_by: str,
                       title: Optional[str] = None, description: Optional[str] = None,
                       action_description: Optional[str] = None,
                       approval_id: Optional[str] = None) -> Verdict:
    """For executors that do not run as a tool call (federated compute jobs,
    mailbox changes Friday proposes on its own).

    Outward by definition. Returns allow only for an approved, unused card
    for exactly this action; otherwise raises (or re-reads) the card and
    returns card.

    `title`, `description` and `action_description` say on the card, in
    words, what will happen (default: the action's name and its detail).
    `approval_id` is for a decision hook acting on the card it was just told
    about: that exact card is used if it was raised for this very action and
    detail, so a card raised again after an earlier one was used is still the
    one that authorises. Nothing about the decision itself is relaxed by it.
    """
    from agent_friday.services import approvals as _ap
    try:
        ok, integrity = verify_claws()
        if not ok:
            return Verdict("deny", OUTWARD, f"held: {integrity}")
        fp = hashlib.sha256(json.dumps({"a": action, "d": detail}, sort_keys=True,
                                       default=str).encode()).hexdigest()[:16]
        rec = None
        if approval_id:
            named = _ap.get_approval(approval_id)
            sid = str((named or {}).get("subject_id") or "")
            if (named and named.get("kind") == "governed_action"
                    and named.get("subject_type") == "external_action"
                    and (sid == f"{action}:{fp}" or sid.startswith(f"{action}:{fp}:"))):
                rec = named
        if rec is None:
            rec = _ap.find_for_subject("external_action", f"{action}:{fp}", "governed_action")
        if rec and rec.get("status") == "approved" and not rec.get("consumed"):
            _ap.mark_used(rec["approval_id"], requested_by)
            v = Verdict("allow", OUTWARD, "approved on a card")
        elif rec and rec.get("status") in ("denied", "blocked"):
            v = Verdict("deny", OUTWARD, "declined on a card")
        else:
            if rec is None or rec.get("status") == "expired" or rec.get("consumed"):
                _ap.create_approval(
                    kind="governed_action", subject_type="external_action",
                    subject_id=f"{action}:{fp}" + (f":{uuid.uuid4().hex[:6]}" if rec else ""),
                    title=title or f"Allow {action}",
                    description=description or json.dumps(detail, default=str)[:600],
                    action_description=action_description or action, force_gate=True,
                    payload=detail, requested_by=requested_by)
            v = Verdict("card", OUTWARD, "waiting for the owner's decision")
        _receipt({"tool": action, "class": OUTWARD, "decision": v.action,
                  "reason": v.reason, "surface": requested_by})
        return v
    except Exception as e:
        return Verdict("deny", OUTWARD, f"governance check failed ({e})")

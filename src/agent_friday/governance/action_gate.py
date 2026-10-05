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
from contextvars import ContextVar
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from agent_friday.paths import friday_home

_log = logging.getLogger("friday.governance")
_LOCK = threading.RLock()

INTERNAL = "internal"
OUTWARD = "outward"
#: North star §18.2 Class 0: a read at a service the owner connected. It
#: reaches outside and is receipted as such, and runs without a card unless a
#: detail came from outside content or its arguments would carry private data
#: out (see _connector_read).
OBSERVE = "observe"


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
    # Decided, not yet reviewed for internal: each waits for a card, as an
    # unclassified tool does. notifications can also mute and clear the
    # owner's cards, so a turn steered by something it read could silence an
    # alarm; local_model_status only reads, and its case for internal is its
    # author's to make with a review.
    "notifications", "local_model_status",
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
    # A Claude Code session launched from the Code workspace: the launch and
    # every action inside it are governed through a per-task grant minted by
    # one approval card (services/claude_code_tasks.py).
    "claude_code_launch", "claude_code_action",
    "content_schedule_post",
    # Phone (agent_friday/phone): a text reaches a person the moment it is
    # sent; a call only ever raises its own card (SELF_GATED below).
    "text_by_phone", "call_by_phone",
    # A signature binds the owner. The tool only raises a card naming the
    # file, page and placement; services/pdf_signing signs on approval.
    "sign_pdf",
    # Changes the owner's Google Contacts, which sync to every device.
    "save_google_contact",
    # The career-ops tracker is the owner's record. The tool only raises a
    # card listing every field; services/career_ops writes on approval.
    "career_update_tracker",
    # Changes to the owner's Gmail, which reach every device. The tool only
    # raises one card for the whole batch; services/item_actions changes
    # exactly the conversations it lists, on approval.
    "organize_email",
    # Publishing a page to the web (services/publish_web). The tool only
    # raises the card with the files, scan and licence check; the card's
    # decision hook publishes on approval (SELF_GATED below).
    "publish_artifact",
    # A command, or Claude's agent, run as a process on this PC inside a
    # codebase's folder (services/codebase_tasks). Both reach past the browser
    # frame, and the agent reaches the provider's API, so both are outward.
    # They are also SELF_GATED: the first of a task raises one card, and its
    # approval mints a grant scoped to that codebase that the rest of the task
    # spends (consume_grant). Nothing runs on a denied or unanswered card.
    "codebase_run", "codebase_agent",
})

#: Tools whose handler raises its own approval card and cannot complete the
#: action itself (draft_email only queues; gmail_send sends on approval).
SELF_GATED = frozenset({"draft_email", "call_by_phone", "sign_pdf",
                        "career_update_tracker", "publish_artifact", "workspace_swap",
                        # Friday's browser (services/browser_session.py): when
                        # classified outward, the handler submits or fills
                        # only on an approved card for exactly what the page
                        # holds at that moment, and raises that card otherwise.
                        "browser_click", "browser_type",
                        # services/item_actions: a batch, or a change that
                        # reaches past this PC, raises ONE card and runs on
                        # approval; one local change runs with an undo.
                        "organize_email", "organize_files", "organize_wiki",
                        "undo_action",
                        # A command or Claude's agent in a codebase's own folder
                        # (services/codebase_tasks): the first of a task raises
                        # ONE card; its approval mints a grant scoped to that
                        # codebase, which the handlers spend with consume_grant.
                        "codebase_run", "codebase_agent"})

#: Friday's own tools that stay inside: reading, searching, drafting, local
#: files the confirmation gate already asks about, memory writes the taint
#: gate already judges, and delegation (a spawned task's own actions come
#: back through this checkpoint one by one).
LIBRARY_SCREEN_ACTIONS = frozenset({"library_add", "library_remove", "library_forget", "library_shelf",
                                    "library_reindex"})

INTERNAL_TOOLS = frozenset({
    # Reads the machine and the model catalogue; downloads nothing and
    # reaches no one (services/local_models_tools).
    "local_models_advise",
    "search_web", "browse_web", "read_file", "search_files",
    # The Library: reads the owner's own index (search_library, library_status)
    # or moves the owner's own screen (library_show). Adding, removing and
    # forgetting are file_access cards, decided on screen.
    "search_library", "library_status", "library_show",
    # The one 3D file browser by voice: moves the owner's own screen only.
    "show_files_3d",
    "write_clipboard", "query_trust_graph", "query_calendar", "revert_workspace",
    "list_workspace_history", "find_calendar_events", "search_email",
    "search_drive", "read_doc", "list_tasks", "complete_task", "create_task",
    "update_task", "search_contacts", "read_wiki", "search_wiki", "search_news",
    "open_url", "navigate", "switch_model", "list_sending_accounts",
    # The owner's own desktop: navigate_to opens an item in Friday's UI,
    # set_workspace_layout lays a workspace out (fullscreen with chat),
    # show_my_day shows the start screen's cluster or sets when it shows,
    # set_chat_tray shows, hides or docks the chat tray, and check_situation
    # reads state the server already holds. None reaches anyone else.
    "navigate_to", "check_situation", "set_workspace_layout", "show_my_day",
    "set_chat_tray",
    # The Chat Hub by voice (chat-hub.md M3c): open a project's chat, show the
    # preview beside it, enter or leave build mode. The owner's own screen and
    # Friday's own records; nothing reaches anyone else.
    "open_project", "show_preview", "build_mode",
    "get_career_pipeline", "get_briefing", "spawn_task", "propose_wiki_update",
    # Voice's hand-over to the full agent: a background task like spawn_task,
    # whose own actions come back through this checkpoint one by one.
    "delegate_to_friday",
    # Sharing local context with the cloud voice model has its own gate: the
    # payload card (services/local_context), decided once, by the owner.
    "ask_local_for_context", "answer_share_request", "revise_share_request",
    # Reads the local conversation store; what a cloud call may hear of it is
    # decided by provenance (services/conversation_recall).
    "search_past_conversations",
    # The voice model's note on the conversation: state in memory, nothing more.
    "note_conversation_state",
    # Decides one of Friday's organize cards, and only from the owner's own
    # words said after the card was raised (services/item_actions).
    "answer_card",
    # Background research reads the web and runs local models; its report
    # lands in the conversation. Nothing it does reaches another person.
    "deep_research",
    # The artifact panel's one tool writes only to Friday's own artifact store
    # under the Friday home, versioned and never sent anywhere
    # (services/artifacts). Off the record it writes nothing at all.
    "artifact_put",
    # A plan is an artifact in that same store; approving one is the user's
    # decision, which the model only reports (services/plans).
    "plan_first", "plan_approve", "plan_milestone",
    # A plain-project zip of a codebase, handed to the user; reads only.
    "codebase_export",
    # Improving a bundle workspace opens a codebase chat; the swap raises ONE
    # card and installs only on approval (services/workspace_bundles).
    "improve_workspace", "workspace_swap",
    # A codebase's own seats, key profile and cost total: the user's choice,
    # disclosed in the header line (services/codebases, spec §4.7).
    "codebase_seat", "codebase_key", "codebase_costs",
    # Which engine edits a codebase: the user's choice, disclosed. (Running
    # that engine, codebase_agent, and a command, codebase_run, are outward
    # and self-gated: one card per task, services/codebase_tasks.)
    "codebase_engine",
    "correct_wiki", "learn_skill", "epistemic_score", "personality_show",
    "personality_check_sycophancy", "generate_image", "compose_timeline", "create_presentation", "create_website",
    "office_check",                 # validates; renders a preview PNG beside it
    "create_workflow", "run_workflow", "workflow_status", "creative_project",
    "start_creative_pipeline", "compare_image_takes", "content_post_status",
    "content_repurpose", "knowledge_query", "knowledge_related",
    "knowledge_communities", "inspect_image", "inspect_audio", "save_output",
    "speak_text", "list_voices", "read_session_output", "load_tools",
    # Podcasts: an episode is written and spoken on this computer and saved in
    # Friday's own folder; playing it steers the owner's own screen; the
    # format is the owner's own podcast setting on this computer.
    "make_podcast", "podcast_list", "podcast_play", "podcast_source", "podcast_format",
    "media_show", "media_cards", "media_turn",
    # media_play finds one audio or video card in the owner's own Media index
    # and tells the owner's own screen to open it in the quick look and play
    # it, from where a searched word was said when the local transcript knows
    # it. It reads this PC's files and index and steers this PC's screen (ring
    # 1, like podcast_play and navigate_to, so a phone-origin turn cannot drive
    # the screen at home); it writes nothing and sends nothing out.
    "media_play",
    # A media diet note only proposes: an approval card in the owner's own
    # approvals; the rule is applied by the approved card, with a receipt.
    "media_diet_note",
    # Discuss reads the web through the guarded fetcher and the local model;
    # it writes only the owner's own files (follows, notes, a queued episode).
    "discuss_story",
    # Friday's own look: it changes only the owner's own desktop and history.
    # A step authored by a cloud model sends numbers only, through the spend
    # guard and the egress gate like any model call; it reaches no one.
    "avatar_evolution",
    # The hologram window's dials: the owner's own settings and own screen.
    "hologram_window",
    # Standing back for a call: the owner's own machine and own setting.
    "call_mode",
    # File permissions: lists them, removes one (only ever narrowing what
    # leaves), or raises an approval card. It creates no grant: that happens
    # only when the owner approves the card on screen.
    "file_access",
    # Big mode and the hand cursor: the owner's own screen and own setting;
    # select never fires a guarded action.
    "big_mode",
    "hand_cursor",
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
    # career-ops (services/career_ops.py). career_status and career_inbox
    # only read (the inbox searches Gmail, like search_email). career_tailor
    # writes a NEW .docx in Friday's documents folder and only reads cv.md.
    # career_evaluate writes a NEW report into career-ops' reports/ folder,
    # the folder its own evaluate mode writes to: never over an existing
    # report, nothing reads it as instructions, and deleting it undoes it.
    "career_status", "career_inbox", "career_tailor", "career_evaluate",
    # Friday's browser: opening and reading a page is reading, like
    # browse_web, with the same address rules (web_safety). Choosing an option
    # changes nothing until the form is submitted, which browser_click and
    # browser_type judge; sensitive questions are refused in the handler.
    "browser_open", "browser_read", "browser_select", "browser_scroll",
    "browser_close",
})

#: Classified by argument: run_command by its command, content_create_post by
#: whether it schedules, write_file and fill_pdf_form by where they write,
#: career_run_script by whether the script rewrites the tracker, career_scan
#: by whether it adds to the pipeline file.
BY_ARGUMENT = frozenset({"run_command", "content_create_post", "office",
                         "write_file", "fill_pdf_form", "run_sandboxed",
                         "career_run_script", "career_scan",
                         # Desktop control (ring 3), by the app it lands on:
                         # services/desktop_grants.py. The Computer Control
                         # switch, grant and kill switch are checked before
                         # this, in agent._governance_check.
                         "move_mouse", "click", "type_text", "press_key",
                         "screenshot", "scroll",
                         # Friday's browser, by the element acted on:
                         # services/browser_session.classify.
                         "browser_click", "browser_type",
                         # By what it opens: services/open_safety.py.
                         "open_path",
                         # By the seed image they upload: services/seed_images.py.
                         "generate_video", "generate_music",
                         # By how many items and where: services/item_actions.
                         "organize_files", "organize_wiki", "undo_action",
                         # By which codebase (classify, below): reading one is
                         # internal; changing a codebase Friday made, under her
                         # own folder, is internal and every change is an
                         # undoable step; changing a folder the user pointed at
                         # is judged as any write outside Friday's output is;
                         # with no codebase in scope, outward.
                         "codebase_edit", "codebase_undo", "codebase_read"})

#: Tools whose outward case is decided on a card even in an interactive chat,
#: never by a yes/no question. generate_video and generate_music are outward
#: only when a seed image outside Friday's creations, not named by the owner,
#: would be uploaded to a cloud service; a card shows the owner which file.
CARD_ONLY_WHEN_OUTWARD = frozenset({"generate_video", "generate_music"})

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


def _literal_search_patterns(cmd: str) -> str:
    """Hide inert search patterns from checks of executable shell text.

    This recognizes only flat, static Get-Content/Select-String pipelines,
    optionally followed by Select-Object. Only quoted -Pattern values are
    hidden; paths and other arguments still face the local-API check. Any
    interpolation, scriptblock, chaining or unfamiliar syntax keeps the
    original conservative classification.
    """
    # PowerShell also treats typographic quotes as delimiters. They are
    # outside this small literal grammar, including inside ASCII quotes.
    if any(c in cmd for c in "\n\r\u2018\u2019\u201a\u201b\u201c\u201d\u201e\u201f"):
        return cmd
    stages = [[]]
    i = 0
    while i < len(cmd):
        if cmd[i].isspace():
            i += 1
            continue
        if cmd[i] == "|":
            if not stages[-1]:
                return cmd
            stages.append([])
            i += 1
            continue
        start = i
        quoted = cmd[i] in "\"'"
        if quoted:
            quote = cmd[i]
            i += 1
            while i < len(cmd):
                if quote == '"' and cmd[i] in "$`":
                    return cmd
                if cmd[i] == quote:
                    if quote == "'" and i + 1 < len(cmd) and cmd[i + 1] == quote:
                        i += 2
                        continue
                    i += 1
                    break
                i += 1
            else:
                return cmd
            if i < len(cmd) and not cmd[i].isspace() and cmd[i] not in ",|":
                return cmd
        elif cmd[i] == ",":
            i += 1
        else:
            while i < len(cmd) and not cmd[i].isspace() and cmd[i] not in ",|":
                i += 1
            if not re.fullmatch(r"[\w./:\\*?=-]+", cmd[start:i]):
                return cmd
        stages[-1].append((start, i, quoted))

    spans = []
    for position, stage in enumerate(stages):
        if not stage or stage[0][2]:
            return cmd
        head = cmd[stage[0][0]:stage[0][1]].lower()
        allowed = {"select-string", "sls", "select-object", "select"}
        if position == 0:
            allowed = {"get-content", "gc", "cat", "type", "select-string", "sls"}
        if head not in allowed:
            return cmd
        if head not in {"select-string", "sls"}:
            continue
        for index, (start, end, quoted) in enumerate(stage):
            if quoted or cmd[start:end].lower() != "-pattern":
                continue
            j = index + 1
            if j == len(stage) or not stage[j][2]:
                return cmd
            while True:
                spans.append(stage[j][:2])
                j += 1
                if j == len(stage) or cmd[stage[j][0]:stage[j][1]] != ",":
                    break
                j += 1
                if j == len(stage) or not stage[j][2]:
                    return cmd
    for start, end in reversed(spans):
        cmd = cmd[:start] + "'search-pattern'" + cmd[end:]
    return cmd


def classify_command(cmd: str) -> tuple:
    """(class, why) for one PowerShell command."""
    c = (cmd or "").strip()
    if not c:
        return INTERNAL, "empty"
    c = _literal_search_patterns(c)
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


def output_dirs() -> list:
    """Friday's output folders (see `_output_dirs`)."""
    return _output_dirs()


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


def classify(tool_name: str, args: Optional[dict], ctx: Optional[dict] = None) -> tuple:
    """(INTERNAL|OUTWARD|"forbidden", why). Unknown tools are OUTWARD.

    `ctx` is the call's session context, when there is one: a seed image the
    owner named in this conversation is theirs to send.
    """
    a = args or {}
    if tool_name == "open_path":
        # An allow-list, not a deny-list: documents, pictures, recordings and
        # folders open; anything that could run code waits for a decision.
        from agent_friday.services import open_safety as _os
        return _os.classify_open(a)
    if tool_name in ("generate_video", "generate_music"):
        # A seed image outside Friday's creations that the owner did not name
        # would be uploaded to a cloud service.
        from agent_friday.services import seed_images as _si
        return _si.classify(tool_name, a, ctx)
    if tool_name == "run_command":
        return classify_command(str(a.get("command") or ""))
    if tool_name == "run_sandboxed":
        from agent_friday.services import code_sandbox as _sbx
        return _sbx.classify(a)
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
    if tool_name in ("codebase_edit", "codebase_undo", "codebase_read"):
        # A codebase Friday made lives under ~/.friday/codebases and is hers to
        # change; an existing folder the user pointed at is their files, so a
        # change there is judged as any write outside Friday's output is.
        try:
            from agent_friday.services import codebases as _cb
            cbid = str(a.get("codebase_id") or "").strip()
            if not cbid:
                from agent_friday.services.agent import _CURRENT_CONVERSATION
                rec = _cb.for_conversation(_CURRENT_CONVERSATION.get())
                cbid = rec["id"] if rec else ""
            if not cbid or _cb.load(cbid) is None:
                return OUTWARD, "no codebase is in scope for this call"
            if tool_name == "codebase_read":
                return INTERNAL, "it only reads a codebase file"
            if _cb.is_managed(cbid):
                return INTERNAL, "it changes a codebase Friday made, under her own folder"
            return classify_write(str(_cb.repo_path(cbid) / "x"))
        except Exception as e:
            return OUTWARD, f"the codebase action could not be classified ({e})"
    if tool_name in ("browser_click", "browser_type"):
        # A click that submits, sends, pays or confirms, typing into a payment
        # field, and Enter in a form are outward; a password field is
        # forbidden; an element Friday has not read is outward.
        try:
            from agent_friday.services import browser_session as _bs
            return _bs.classify(tool_name, a)
        except Exception as e:
            return OUTWARD, f"the browser action could not be classified ({e})"
    if tool_name == "fill_pdf_form":
        # A new file in Friday's output folder is internal; replacing a file
        # or writing anywhere else is outward; over the source is refused.
        try:
            from agent_friday.services import pdf_forms as _pf
            return _pf.classify(a)
        except Exception as e:
            return OUTWARD, f"the form fill could not be classified ({e})"
    if tool_name in ("career_run_script", "career_scan"):
        # A read-only check or a scan that writes nothing is internal; a
        # script that rewrites the tracker, or adding offers to the pipeline
        # file, is a change to the owner's records and waits.
        try:
            from agent_friday.services import career_ops as _co
            return (_co.classify_script(a) if tool_name == "career_run_script"
                    else _co.classify_scan(a))
        except Exception as e:
            return OUTWARD, f"the career-ops action could not be classified ({e})"
    if tool_name in ("organize_files", "organize_wiki", "undo_action"):
        # One local change Friday can undo is internal. A batch, anything in
        # the code projects, a move into a folder a cloud client syncs, and
        # putting mail back wait for one card (the handler raises it).
        try:
            from agent_friday.services import item_actions as _ia
            if tool_name == "organize_files":
                return _ia.classify_files(a)
            if tool_name == "organize_wiki":
                return _ia.classify_wiki(a)
            return _ia.classify_undo(a, (ctx or {}).get("conversation_id"))
        except Exception as e:
            return OUTWARD, f"the change could not be classified ({e})"
    if tool_name == "content_create_post":
        if a.get("publish_at") or a.get("optimal_time"):
            return OUTWARD, "it schedules a post to go out"
        return INTERNAL, "it only saves a draft"
    if tool_name in LIBRARY_SCREEN_ACTIONS:
        # The owner's own change to their Library, from its workspace page. Only a verified
        # click there counts; the same action named anywhere else is held for a decision.
        if (ctx or {}).get("screen_click") is True:
            return INTERNAL, "the owner's own change, made on the Library page"
        return OUTWARD, "a change to the Library that did not come from the Library page"
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
        return _connector_read(tool_name, a)
    return OUTWARD, "an unknown tool is treated as outward"


def _outward_reads_policy() -> str:
    """"observe" (the default) or "card". The owner's switch, in settings."""
    try:
        from agent_friday.core import _load_settings
        v = str((_load_settings() or {}).get("outward_reads") or "observe").lower()
    except Exception:
        v = "observe"
    return v if v in ("observe", "card") else "card"


def _args_private(args: dict) -> bool:
    """Would these arguments carry private data out? The PII check's fast,
    local layers (structured PII and keywords) plus an email address.

    Deliberately not the embedding layer: its first use loads a model (30 s
    measured), and on 10 private and 10 clean queries it changed no verdict.
    A check that cannot run counts as private.
    """
    text = json.dumps(args or {}, default=str, ensure_ascii=False)
    if _EMAIL_IN_ARGS.search(text):
        return True
    try:
        from agent_friday.services import sensitivity_classifier as _sc
        return _sc.classify(text, egress=True, use_presidio=False,
                            use_embeddings=False) >= _sc.Tier.PRIVATE
    except Exception:
        return True


def _has_free_text(args) -> bool:
    """Is any argument value prose rather than an identifier?

    Laya's carries_private is counted only then. Measured on the live engine,
    it called {"owner": "octo", "repo": "hello", "pull_number": 3} and
    {"limit": 10} personal. An identifier without spaces cannot carry "my son
    failed his math test"; the structured leaks it can carry (an email, a
    phone number, an account or ID number) are the PII check's, which runs on
    every argument regardless.
    """
    def walk(v):
        if isinstance(v, str):
            return bool(re.search(r"\s", v.strip())) or len(v) > 64
        if isinstance(v, dict):
            return any(walk(x) for x in v.values())
        if isinstance(v, (list, tuple)):
            return any(walk(x) for x in v)
        return False
    return walk(args or {})


_EMAIL_IN_ARGS = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")


def _connector_read(tool_name: str, a: dict) -> tuple:
    """A connector tool whose name leads with a read verb.

    OBSERVE (North star §18.2 Class 0) when nothing says otherwise: it reaches
    a service the owner connected, is receipted as outward, and runs without a
    card. The owner's direction (2026-09-29): "I think it should also be a high
    priority that we don't constantly pester the user with approval cards for
    every single command." His own labels had made every GitHub and Higgsfield
    lookup card under the union.

    It is OUTWARD, and cards exactly as before, when:
      * the keyword scan flags it (its words say send, pay, delete ...);
      * Laya says it changes something outside;
      * its arguments would carry private data out: the PII check OR Laya's
        carries_private. Each catches what the other misses; this is the
        "exfiltrate through a search query" path;
      * the owner set outward_reads="card", in which case the union's own
        verdict decides as it did before this policy.
    A detail from outside content cards through the taint ledger regardless.

    When Laya cannot answer (loading, busy, too slow), the read still runs if
    the PII check finds its arguments clean; the miss is counted by the union
    and logged. It waits for the owner only when they look private.
    """
    policy = _outward_reads_policy()
    try:
        from agent_friday.services import approvals as _ap
        from agent_friday.services import decisions as _dec
        desc = f"{tool_name} {json.dumps(a, default=str)[:300]}"
        with _dec.about_tool(tool_name):
            verdict = _ap.classify(desc)
    except Exception as e:
        return OUTWARD, f"the action classifier failed ({e})"
    union = verdict.get("union") or {}
    laya_down = _laya_down()
    missing = laya_down or verdict.get("second_opinion") == "missing"

    if policy == "card":
        if laya_down:
            return OUTWARD, "the Laya classifier is configured but unavailable"
        if verdict.get("gated"):
            return OUTWARD, "the action classifier judged it outward"
        if missing:
            return OUTWARD, "the Laya classifier could not check it in time"
        return INTERNAL, "a connector read"

    # The keyword scan's own verdict, not Laya's severity: its words decide.
    kw = union.get("keyword") or verdict.get("policy_class")
    if kw and kw != INTERNAL:
        return OUTWARD, "the keyword scan judged it outward"
    private = _args_private(a)
    if missing:
        if private:
            return OUTWARD, ("Laya could not check it in time and its arguments "
                             "look private")
        return OBSERVE, ("a read at a connected service; Laya could not check it "
                         "in time and the PII check found nothing private in it")
    also = union.get("also") or {}
    if also.get("changes_outside") == "yes":
        return OUTWARD, "Laya judged that it changes something outside"
    if private or (also.get("carries_private") == "personal" and _has_free_text(a)):
        return OUTWARD, "private data would leave in its arguments"
    return OBSERVE, ("a read at a connected service: it reaches outside, "
                     "nothing private leaves")


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


def consume_grant(tool_name: str, scope: str) -> Optional[dict]:
    """Spend one use of a scoped grant for an executor that is not a tool
    call (a Claude Code launch, a codebase task). None when nothing is live."""
    if not tool_name or not scope:
        return None
    return _use_grant(tool_name, {"grant_scope": str(scope)})


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


def consume_grant(tool_name: str, scope: str) -> Optional[dict]:
    """One use of a grant for `scope`, spent by a self-gated tool that checks
    its own grant (a codebase task: scope "codebase:<id>"). None when no grant
    covers the tool, has uses left, or is still in time."""
    if not tool_name or not scope:
        return None
    return _use_grant(tool_name, {"grant_scope": str(scope)})


# ── 4. Receipts ─────────────────────────────────────────────────────────────

#: What a receipt keeps while off the record: the tool, its class, the decision,
#: the time and the ids that link it to a card, grant or task. No reason text,
#: target, or hash of the arguments (a hash of a short message can be guessed).
_OFF_RECORD_RECEIPT_KEYS = ("tool", "class", "decision", "surface", "approval",
                            "grant", "task_id", "tainted")


def _receipt(entry: dict) -> None:
    """Sign and append. Raises on failure, so the caller can hold the action."""
    try:
        from agent_friday.services import off_record as _off
        if _off.active():
            entry = {k: entry.get(k) for k in _OFF_RECORD_RECEIPT_KEYS if k in entry}
            entry["off_record"] = True
    except ImportError:
        pass
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


#: The owner's decision behind the tool call running now: an approved card's
#: id, a scoped grant's id, or the chat confirmation the owner answered yes.
#: `agent._execute_tool` sets it around the handler from what the hooks
#: established; the model has no way to set it. A handler whose action needs
#: a decision (opening a non-allow-listed file, uploading a seed image from
#: outside Friday's creations) checks it as a second line behind this gate.
DECIDED: ContextVar = ContextVar("friday_owner_decision", default=None)


def owner_decision() -> Optional[str]:
    """The id of the owner's decision behind the running call, or None."""
    return DECIDED.get()


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
        klass, why = classify(tool_name, args, ctx)
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
    if klass == OBSERVE:
        # A read at a connected service: runs unasked, receipted as observe.
        # A detail from outside content could be data being smuggled out
        # through the query, so that still goes to a card.
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
    if _interactive(ctx) and not tainted and tool_name not in CARD_ONLY_WHEN_OUTWARD:
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


def external_subject(action: str, detail: dict) -> str:
    """The approval subject `authorize_external` files a card under for this
    exact action and detail. A caller may look the card up by it (to raise a
    card early without spending an approved one); it decides nothing."""
    fp = hashlib.sha256(json.dumps({"a": action, "d": detail}, sort_keys=True,
                                   default=str).encode()).hexdigest()[:16]
    return f"{action}:{fp}"


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
        subject = external_subject(action, detail)
        rec = None
        if approval_id:
            named = _ap.get_approval(approval_id)
            sid = str((named or {}).get("subject_id") or "")
            if (named and named.get("kind") == "governed_action"
                    and named.get("subject_type") == "external_action"
                    and (sid == subject or sid.startswith(f"{subject}:"))):
                rec = named
        if rec is None:
            rec = _ap.find_for_subject("external_action", subject, "governed_action")
        if rec and rec.get("status") == "approved" and not rec.get("consumed"):
            _ap.mark_used(rec["approval_id"], requested_by)
            v = Verdict("allow", OUTWARD, "approved on a card")
        elif rec and rec.get("status") in ("denied", "blocked"):
            v = Verdict("deny", OUTWARD, "declined on a card")
        else:
            if rec is None or rec.get("status") == "expired" or rec.get("consumed"):
                _ap.create_approval(
                    kind="governed_action", subject_type="external_action",
                    subject_id=subject + (f":{uuid.uuid4().hex[:6]}" if rec else ""),
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

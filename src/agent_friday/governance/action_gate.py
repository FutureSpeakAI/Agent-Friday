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
     and expires; and, for a send, the owner's standing pre-approval of its
     recipients (governance/send_grants.py: named recipients, optionally
     one job or one skill, logged, revocable);
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
from dataclasses import asdict, dataclass, field, fields
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
    # services/web_tasks: proposing a website task is outward (it can lead to
    # acting on a site as the owner); its plan card is the gate (SELF_GATED),
    # and every browser step then passes this checkpoint itself.
    "web_task_start",
    # draft_email is judged by argument (BY_ARGUMENT, classify): saving a
    # draft is an own-mailbox write; send=true is outward and its own card
    # is the gate (SELF_GATED).
    # Decided, not yet reviewed for internal: each waits for a card, as an
    # unclassified tool does. notifications can also mute and clear the
    # owner's cards, so a turn steered by something it read could silence an
    # alarm; local_model_status only reads, and its case for internal is its
    # author's to make with a review.
    "local_model_status",
    "create_calendar_event", "update_calendar_event", "annotate_calendar_events",
    # Deleting an event removes it from a calendar other people may see, and Google keeps no undo the
    # API reaches: it asks every time, by the event's title and time (agent._taint_title).
    "delete_calendar_event",
    # Scheduling (services/scheduling.py). book_slot sends invitations to
    # other people. hold_slots writes only to the owner's own calendar and
    # invites nobody, but a hold shows as busy to everyone who can see that
    # calendar's free/busy (colleagues on a work account), so it changes what
    # other people see: it asks, once per batch of holds. A standing,
    # expiring grant for holds is a later refinement, not a default.
    "hold_slots", "book_slot",
    "delete_task",
    # A24 (owner ruling 2026-10-22): completing, creating and editing Google Tasks ask first.
    # In a run nobody watches, one card approves the run's task changes (services/run_grants);
    # each change still writes its own receipt.
    "complete_task", "create_task", "update_task",
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
    # Builds, publishing and registrar changes raise their own exact cards.
    "site_action", "domain_action",
    # A command, or Claude's agent, run as a process on this PC inside a
    # codebase's folder (services/codebase_tasks). Both reach past the browser
    # frame, and the agent reaches the provider's API, so both are outward.
    # They are also SELF_GATED: the first of a task raises one card, and its
    # approval mints a grant scoped to that codebase that the rest of the task
    # spends (consume_grant). Nothing runs on a denied or unanswered card.
    "codebase_run", "codebase_agent",
})

#: Tools whose handler raises its own approval card and cannot complete the
#: action itself (draft_email with send=true only queues; gmail_send sends on
#: approval).
SELF_GATED = frozenset({"draft_email", "call_by_phone", "sign_pdf",
                        "career_update_tracker", "publish_artifact", "workspace_swap",
                        "site_action", "domain_action",
                        # Friday's browser (services/browser_session.py): when
                        # classified outward, the handler submits or fills
                        # only on an approved card for exactly what the page
                        # holds at that moment, and raises that card otherwise.
                        "browser_click", "browser_type",
                        # services/web_tasks: proposes a website task; an acting
                        # task starts only on the owner's yes on its plan card,
                        # and each browser step then passes this gate itself.
                        "web_task_start",
                        # services/item_actions: a batch, or a change that
                        # reaches past this PC, raises ONE card and runs on
                        # approval; one local change runs with an undo.
                        "organize_email", "organize_files", "organize_wiki", "organize_media", "organize_calendar",
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
                                    "library_reindex", "library_tracked", "library_draft_review"})

INTERNAL_TOOLS = frozenset({
    # Reads the machine and the model catalogue; downloads nothing and
    # reaches no one (services/local_models_tools).
    "local_models_advise",
    "search_web", "browse_web", "read_file", "search_files",
    # The Library: reads the owner's own index (search_library, library_status)
    # or moves the owner's own screen (library_show). Adding, removing and
    # forgetting are file_access cards, decided on screen.
    "search_library", "library_status", "library_show",
    # Recorded meetings (services/meeting_tool): finds a meeting, runs the local notes model, raises memory
    # proposals that wait for the owner's yes, adds a bullet to the owner's own pad. Nothing leaves this PC,
    # and it never starts a recording.
    "meeting",
    # The one 3D file browser by voice: moves the owner's own screen only.
    "show_files_3d",
    # "Add my Obsidian vault": opens the owner's own folder window and speaks what
    # was found. It adds nothing; the picks return as a Library card decided on screen.
    "add_knowledge",
    "write_clipboard", "query_trust_graph", "query_calendar", "revert_workspace",
    "list_workspace_history", "find_calendar_events", "search_email",
    "search_drive", "read_doc", "list_tasks", "search_contacts", "read_wiki", "search_wiki", "search_news",
    "open_url", "navigate", "switch_model", "list_sending_accounts",
    # The owner's own desktop: navigate_to opens an item in Friday's UI,
    # set_workspace_layout lays a workspace out (fullscreen with chat),
    # show_my_day shows the start screen's cluster or sets when it shows,
    # set_chat_tray shows, hides or docks the chat tray, and check_situation
    # reads state the server already holds. None reaches anyone else.
    "navigate_to", "check_situation", "set_workspace_layout", "show_my_day",
    # Reads the Local AI queue (labels and reasons only). local_work says what
    # the local models are working on and takes a waiting job out of the line:
    # Friday's own queue, nothing reaches anyone else.
    "queue_status", "local_work",
    # A goal draft that waits in System > Goals for the owner's confirming
    # click (services/goal_tools). It creates and starts nothing.
    "draft_goal",
    # What Laya learned from the owner's own corrections (services/laya_learning_tools):
    # a summary, and forget / reset / roll back / wipe of her own personal layer. It runs
    # only on the owner's own words in the turn, changes nothing outside Friday's learning
    # state, and reaches no one else. It can only make her shipped self the one in force.
    "laya_learning",
    "set_chat_tray",
    # See & Touch (services/screen_stage): ticks rows on the owner's own screen. It shows
    # and changes nothing; every change to the mail still goes through organize_email.
    "screen_select",
    # Stopping a workflow or task after its step, or steering one (the steer's words are checked for where they
    # came from, like any instruction): it only ends or redirects Friday's own background work.
    "task_control",
    # Can voice run locally, and queue the measurement for an idle moment
    # (services/local_voice_tools). It moves the voice engine only to carry out the owner's own
    # answer to "just this call, or always?" / the switch-back offer (their words, after the question).
    "local_voice_readiness",
    # a setting change holds itself for the owner's Yes on its own card (services/setting_proposals)
    "set_setting",
    # This chat's seat, or one task on the local model: raises the owner's card
    # and changes nothing; the card's hook acts on the Yes (services/seat_handoff).
    "switch_seat",
    # The Chat Hub by voice (chat-hub.md M3c): open a project's chat, show the
    # preview beside it, enter or leave build mode. The owner's own screen and
    # Friday's own records; nothing reaches anyone else.
    "open_project", "show_preview", "build_mode",
    "get_career_pipeline", "get_briefing", "spawn_task", "propose_wiki_update",
    # Voice's hand-over to the full agent: a background task like spawn_task,
    # whose own actions come back through this checkpoint one by one.
    "delegate_to_friday",
    # Crew dispatch binds a saved profile; its individual actions return here.
    # Profile proposals open an unsaved editor and grant no capabilities.
    "list_crew", "ask_crew", "propose_crew_agent",
    # Steering queues guidance; each worker action remains governed. Talking
    # uses the bound profile's model and privacy policy with no tool authority.
    "steer_crew", "talk_crew",
    # Sharing local context with the cloud voice model has its own gate: the
    # payload card (services/local_context), decided once, by the owner.
    "ask_local_for_context", "answer_share_request", "revise_share_request",
    # The text chat's counterpart of ask_local_for_context: the same local
    # answer, scrub, floor and card (services/slice_head -> local_context). A
    # turn that has read untrusted content cannot use a standing grant.
    "request_context",
    # Reads the turn receipt back; names, sizes and tiers, never content.
    "why_that_plan",
    # A cloud model's plan, queued for local models. It validates and fences the
    # order, fixes the privacy class itself and writes nothing: write intents
    # become proposals later, each behind its own gate.
    "submit_work_order", "work_order_status",
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
    # The core memory card (services/core_memory): local, undoable, shown to
    # the owner line by line. A tainted call is a card (taint memory_write).
    "update_core_memory",
    "personality_check_sycophancy", "generate_image", "compose_timeline", "create_presentation", "create_website",
    "office_check",                 # validates; renders a preview PNG beside it
    "create_workflow", "run_workflow", "workflow_status", "creative_project",
    # Local definition/discovery and initiation only; workflow execution keeps
    # every constituent action on the existing governed task/tool path.
    "workflow_action", "discover_capabilities", "read_skill",
    "voice_preferences",  # local spoken style preferences only; no permissions
    "start_creative_pipeline", "compare_image_takes", "content_post_status",
    "content_repurpose", "knowledge_query", "knowledge_related",
    "knowledge_communities", "inspect_image", "inspect_audio", "save_output",
    "speak_text", "list_voices", "read_session_output", "load_tools",
    # Podcasts: an episode is written and spoken on this computer and saved in
    # Friday's own folder; playing it steers the owner's own screen; the
    # format and the length are the owner's own podcast settings on this computer.
    "make_podcast", "podcast_list", "podcast_play", "podcast_source", "podcast_format",
    "podcast_length",
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
    # Laya beside a cloud call: reads the call's own receipt; the reflex setting
    # only narrows without a card, widening goes through setting_proposals.
    "live_laya",
    # File permissions: lists them, removes one (only ever narrowing what
    # leaves), or raises an approval card. It creates no grant: that happens
    # only when the owner approves the card on screen.
    "file_access",
    # Big mode and the hand cursor: the owner's own screen and own setting;
    # select never fires a guarded action.
    "big_mode",
    "hand_cursor",
    # Declarative local UI only: no executable card actions or codebase writes.
    "home_cards", "customize_workspace",
    # Voice-only helpers routed through the checkpoint.
    "check_email", "get_source_trust", "get_article_deep_dive", "ask_friday",
    # Cloud-voice session helpers: more_tools only changes which of Friday's own
    # tools the NEXT leg declares (each still passes its own gate when called);
    # call_cost reads this call's metered total.
    "more_tools", "call_cost",
    # find_free_slots reads free/busy only. release_holds deletes nothing but
    # Friday's own holds: each event is re-read and must carry Friday's active
    # hold marker for that series and no attendees, and nobody is notified,
    # so it undoes Friday's own earlier work and reaches no one.
    "find_free_slots", "release_holds",
    # check_plan_travel is arithmetic on the stops it is given: no network, no write.
    "check_plan_travel",
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
                         # By action: only turning quiet ON is internal (it can only hold back
                         # interruptions); every other action keeps the outward class it had.
                         "notifications",
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
                         "organize_files", "organize_wiki", "organize_media", "organize_calendar", "undo_action",
                         # By which codebase (classify, below): reading one is
                         # internal; changing a codebase Friday made, under her
                         # own folder, is internal and every change is an
                         # undoable step; changing a folder the user pointed at
                         # is judged as any write outside Friday's output is;
                         # with no codebase in scope, outward.
                         "codebase_edit", "codebase_undo", "codebase_read", "codebase_understand",
                         # By what the scheduled job will do when it fires: creating
                         # or resuming a schedule for a job that sends, posts, buys or
                         # deletes is outward; the rest is a timetable (services/schedule_tools).
                         "schedule_job",
                         # By whether it sends: saving a draft into the owner's
                         # own Gmail is an own-mailbox write (no card, receipted);
                         # send=true is outward and raises its own card.
                         "draft_email"})

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


#: Brackets that run what they hold: (…), @(…), {…}.
_GROUPING = re.compile(r"[({]")


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
    if _GROUPING.search(c):
        # (…), @(…) and {…} run whatever they hold. Quoted or not: where a
        # quote ends is PowerShell's to decide, so any bracket left after the
        # literal search patterns are hidden goes to a card
        return OUTWARD, "a bracketed expression or script block can run any command"
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
    if tool_name in ("workflow_action", "create_workflow", "schedule_job"):
        # Changing a job the owner pre-approved sends for is the owner's
        # decision, not the model's: a card (governance/send_grants). Any
        # other workflow or schedule change is judged below as before.
        try:
            from agent_friday.governance import send_grants as _sg
            touched = _sg.edit_touches_granted_job(tool_name, a)
        except Exception as e:
            touched = "the pre-approved job check could not run (%s)" % e
        if touched:
            return OUTWARD, touched
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
    if tool_name == "notifications":
        # Narrowing only. "Quiet until morning" holds back interruptions and never hides, approves or
        # drops anything (services/interruption_policy). Ending quiet, muting, clearing and every
        # other action, and a call that names no action, are judged outward exactly as before.
        # Only when the owner's own words did it: the router rule on their typed or spoken line sets
        # ctx["owner_utterance"]; a model's tool call never carries it, so that call is a card.
        if (str(a.get("action") or "").strip().lower() == "quiet"
                and str(a.get("group") or a.get("kind") or "").strip().lower() not in ("off", "end", "stop")
                and (ctx or {}).get("owner_utterance") is True):
            return INTERNAL, "the owner asked for quiet until morning, which only holds back interruptions"
        return OUTWARD, "it can clear, mute or change what reaches the owner"
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
    if tool_name in ("codebase_edit", "codebase_undo", "codebase_read", "codebase_understand"):
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
            if tool_name in ("codebase_read", "codebase_understand"):
                return INTERNAL, "it only reads the scoped codebase"
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
    if tool_name in ("organize_files", "organize_wiki", "organize_media", "organize_calendar", "undo_action"):
        # One local change Friday can undo is internal. A batch, anything in
        # the code projects, a move into a folder a cloud client syncs, and
        # putting mail back wait for one card (the handler raises it).
        try:
            from agent_friday.services import item_actions as _ia
            if tool_name == "organize_files":
                return _ia.classify_files(a)
            if tool_name == "organize_wiki":
                return _ia.classify_wiki(a)
            if tool_name == "organize_media":
                return _ia.classify_media(a)
            if tool_name == "organize_calendar":
                return _ia.classify_calendar(a)
            return _ia.classify_undo(a, (ctx or {}).get("conversation_id"))
        except Exception as e:
            return OUTWARD, f"the change could not be classified ({e})"
    if tool_name == "schedule_job":
        try:
            from agent_friday.services import schedule_tools as _st
            return _st.classify(a)
        except Exception as e:
            return OUTWARD, f"the schedule could not be classified ({e})"
    if tool_name == "draft_email":
        if _own_mailbox_draft(tool_name, a):
            return INTERNAL, OWN_MAILBOX_WHY
        return OUTWARD, "it asks to send an email to other people"
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


#: Why a draft save is internal, in the receipt.
OWN_MAILBOX_WHY = ("an own-mailbox write: it saves a draft in the owner's own Gmail "
                   "and sends nothing to anyone")


def _own_mailbox_draft(tool_name: str, args) -> bool:
    """draft_email that saves (no send=true): a write to the owner's own
    mailbox. A draft reaches nobody, so it needs no card, whatever the
    recipients' provenance; sending it is a separate, carded request."""
    if tool_name != "draft_email":
        return False
    try:
        from agent_friday.services.gmail_send import wants_send
        return not wants_send(args or {})
    except Exception:
        return False


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


_EMAIL_IN_ARGS = re.compile(r"(?<![A-Za-z0-9._%+-])[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")


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
         "created_at": time.time(),
         # the governing policy version it was issued under (NS-30.19-1)
         "policy_version": _policy_version_id()}
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


def _grant_live(g: dict, tool_name: str, scopes: set, now: float) -> bool:
    return bool(g.get("scope") in scopes and tool_name in (g.get("tools") or [])
                and g.get("expires_at", 0) > now and int(g.get("uses_left") or 0) > 0)


def _grant_covers(tool_name: str, ctx: dict) -> bool:
    """Whether `_use_grant` would find a live grant for this call. Spends nothing."""
    scopes = _scopes(ctx)
    if not scopes:
        return False
    now = time.time()
    with _LOCK:
        return any(_grant_live(g, tool_name, scopes, now) for g in _read_grants())


def _use_grant(tool_name: str, ctx: dict) -> Optional[dict]:
    scopes = _scopes(ctx)
    if not scopes:
        return None
    with _LOCK:
        gs = _read_grants()
        now = time.time()
        for g in gs:
            if _grant_live(g, tool_name, scopes, now):
                g["uses_left"] = int(g["uses_left"]) - 1
                # the policy version this use was made under (NS-30.19-1); the
                # decision receipt that names the grant carries it too
                g["last_used_policy_version"] = _policy_version_id()
                _grants_file().write_text(json.dumps(gs, indent=1), encoding="utf-8")
                return dict(g, used_policy_version=g["last_used_policy_version"])
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
                            "grant", "task_id", "tainted", "chosen_by",
                            "plan_version", "action_id", "policy_version", "envelope",
                            "outcome")


def _policy_version_id() -> Optional[str]:
    """The governing policy version now (services/policy_version). Records
    only: nothing here reads it to decide. Never raises."""
    try:
        from agent_friday.services import policy_version as _pv
        return _pv.current_id()
    except Exception:  # noqa: BLE001
        return None


@dataclass(frozen=True)
class ActionEnvelope:
    """The single action envelope (NS-36.3-1, BD-11a): built once per decision
    on the gate path (`authorize`; `_receipt` builds it for any other decision
    receipt) and carried to the receipts. Ids, labels and hashes only.

    action_id          ``act_`` + 20 hex: the action every receipt names
    principal          who the action is for (action_ledger.PRINCIPAL)
    tool               the tool or executor action
    args_hash          SHA-256 of the canonical arguments (None when there are none)
    idempotency_key    ``idk_`` + 24 hex: the same request (same tool, arguments
                       and turn or task) gets the same key; a caller-supplied
                       ``idempotency_key`` in the session context is hashed in
                       instead. Never the arguments themselves.
    verification_plan  what checks the decision and what record it must leave
    policy_version     the governing policy version (services/policy_version)
    claws_state        {"hash", "status"}: the cLaws as this action found them
    created_at         unix seconds
    origin             where the request came from (ORIGINS): chat, voice, task,
                       schedule, channel, card, owner_route, route, other; derived
                       from the session context the gate already reads
    risk_class         the gate's own classification (observe, internal,
                       outward, forbidden); None when it is not known
    provenance_summary counts of where the call's arguments came from
                       ({"owner", "content", "sender", "model"}: services/taint
                       origins); never a value. None when no provenance check ran
    """
    action_id: str
    principal: str
    tool: str
    args_hash: Optional[str]
    idempotency_key: Optional[str]
    verification_plan: dict
    policy_version: Optional[str]
    claws_state: dict
    created_at: float
    origin: Optional[str] = None
    risk_class: Optional[str] = None
    provenance_summary: Optional[dict] = None

    def to_dict(self) -> dict:
        return asdict(self)


ENVELOPE_FIELDS = tuple(f.name for f in fields(ActionEnvelope))

#: Where a request came from, as the envelope names it (NS-18.1-1). Labels only.
#: owner_route: a route whose request is verified as the owner's (check_auth);
#: route: any other request to a route.
ORIGINS = ("chat", "voice", "task", "schedule", "channel", "card", "owner_route", "route", "other")
#: The provenance buckets an envelope counts (services/taint Origin kinds:
#: "user" and "own" are the owner's; "content", "sender" and "model" as they are).
PROVENANCE_KEYS = ("owner", "content", "sender", "model")
_PROVENANCE_OF = {"user": "owner", "own": "owner", "content": "content", "sender": "sender",
                  "model": "model"}


def derive_origin(ctx: Optional[dict] = None, *, surface: Optional[str] = None,
                  approval: Optional[str] = None) -> str:
    """The envelope's `origin` from what the gate already reads: the session
    context (origin, approved_card, scheduled/schedule_id, is_voice/surface,
    is_background_task/task_id, session_id) or, for an executor outside a tool
    call, its surface and card. Records only; never raises."""
    try:
        c = ctx if isinstance(ctx, dict) else {}
        o = str(c.get("origin") or "").strip().lower()
        s = str(c.get("surface") or surface or "").strip().lower()
        if approval or c.get("approved_card") or c.get("approved_card_decision") \
                or s == "approval_card":
            return "card"
        if o in ("channel", "phone") or s.startswith(("channel", "phone")):
            return "channel"
        if c.get("scheduled") or c.get("schedule_id") or s.startswith("schedul"):
            return "schedule"
        if c.get("is_voice") or o == "voice" or s.startswith("voice"):
            return "voice"
        if c.get("is_background_task") or c.get("task_id") or s in (
                "background", "web_task", "work_order", "claude_code"):
            return "task"
        if c.get("session_id") or s in ("chat", "typed"):
            return "chat"
        try:
            from flask import has_request_context
            if has_request_context():
                # a route: the owner's only when the request is verified as theirs
                return "owner_route" if _owner_verified_request() else "route"
        except Exception:  # noqa: BLE001
            pass
        return "other"
    except Exception:  # noqa: BLE001
        return "other"


def _owner_verified_request() -> bool:
    """Whether the current request passed core.check_auth as the owner: its
    principal is the user (not the observer credential) and it is signed in
    (the session check_auth marks, or this launch's page token). A label for
    the envelope; decides nothing. Never raises."""
    try:
        from flask import g, request, session
        if getattr(g, "friday_principal", None) != "user":
            return False
        if session.get("authenticated"):
            return True
        from agent_friday import core as _core
        return bool(_core._api_token_valid(request.headers.get("X-Friday-Token")))
    except Exception:  # noqa: BLE001
        return False


def provenance_summary(origins: Any) -> Optional[dict]:
    """Counts per PROVENANCE_KEYS from a taint origin tally ({kind: n}); None
    when there is no tally. Never a value."""
    if not isinstance(origins, dict):
        return None
    out = {k: 0 for k in PROVENANCE_KEYS}
    for kind, n in origins.items():
        b = _PROVENANCE_OF.get(str(kind))
        if b is not None and isinstance(n, int) and n > 0:
            out[b] += n
    return out


def _verification_plan(klass, decision, via=None) -> dict:
    """Labels: the checks the decision passes and the record it must leave."""
    checks = ["signed_receipt"]
    if klass == OUTWARD:
        checks += ["claws_pin", "outward_hold"]
    if decision == "card":
        checks.append("approval_card")
    elif decision == "confirm":
        checks.append("chat_confirmation")
    expects = klass in (OUTWARD, OBSERVE) and decision not in ("deny", None)
    return {"checks": checks, "outcome": "outcome_receipt" if expects else "none",
            "via": str(via)[:40] if via else None}


def _idempotency_key(tool, args_hash, ctx, principal) -> Optional[str]:
    c = ctx if isinstance(ctx, dict) else {}
    supplied = str(c.get("idempotency_key") or "").strip()
    if supplied:
        basis = "client|%s|%s" % (principal, supplied)
    elif args_hash:
        scope = next((str(c[k]) for k in ("turn_id", "task_id", "run_id", "schedule_id",
                                          "session_id") if c.get(k)), "") or (_turn_id() or "")
        basis = "derived|%s|%s|%s|%s" % (principal, tool, args_hash, scope)
    else:
        return None
    return "idk_" + hashlib.sha256(basis.encode("utf-8")).hexdigest()[:24]


#: The idempotency scopes that name one request (NS-36.3-5): a caller's own
#: key, one turn, one task, one run. A session or a schedule is not one
#: request (the same message may rightly go out again tomorrow), so a key
#: scoped by them never refuses anything.
_ONE_REQUEST_SCOPES = frozenset({"client", "turn_id", "task_id", "run_id", "turn"})


def _idempotency_scope(ctx) -> str:
    """Which scope `_idempotency_key` derived its key from (same order)."""
    c = ctx if isinstance(ctx, dict) else {}
    if str(c.get("idempotency_key") or "").strip():
        return "client"
    named = next((k for k in ("turn_id", "task_id", "run_id", "schedule_id", "session_id")
                  if c.get(k)), "")
    if named:
        return named
    return "turn" if _turn_id() else ""


def idempotency_scope_refuses(scope: Optional[str]) -> bool:
    """Whether a key derived under `scope` (`_idempotency_scope`) names one
    request: the gate refuses a repeat of it, and the outward audit counts
    it run twice as duplicate_key. The one rule both read."""
    return str(scope or "") in _ONE_REQUEST_SCOPES


#: The decision record's field naming the scope its idempotency key was
#: derived under (`_idempotency_scope`; "client" for a card's own key).
IDEMPOTENCY_SCOPE_FIELD = "idempotency_scope"


#: The outward tool calls a repeat of which is refused as already done
#: (NS-36.3-5): one-shot sends and writes whose second run is a second
#: message, post, booking or record at another person's or service's end.
#: Texts, scheduled posts, bookings and holds, calendar writes, Google Tasks
#: writes, a contact saved; plus connector writes (ONE_SHOT_CONNECTOR_PREFIX,
#: is_one_shot). Deliberately NOT here: SELF_GATED tools (their handler
#: raises its own card, and a codebase command, a browser click or an
#: organize/undo is rightly repeated within one task), commands
#: (run_command, run_sandboxed, install_package), the browser and desktop
#: control. Email is sent from its card (draft_email send=true is SELF_GATED;
#: gmail_send uses record_external(once=True), which is keyed by the card).
ONE_SHOT_ACTIONS = frozenset({
    "text_by_phone",
    "content_schedule_post", "content_create_post",
    "book_slot", "hold_slots",
    "create_calendar_event", "update_calendar_event", "delete_calendar_event",
    "annotate_calendar_events",
    "create_task", "update_task", "complete_task", "delete_task",
    "save_google_contact",
})
#: A connector (MCP) tool; an outward one is a write at the connected service.
ONE_SHOT_CONNECTOR_PREFIX = "mcp_"
#: The verbs (a word of the tool name) that make a connector tool one-shot:
#: its second run is a second message, post, invite, booking or file at the
#: other end. A connector tool without one of these is not refused on repeat.
ONE_SHOT_CONNECTOR_VERBS = frozenset({
    "send", "create", "post", "publish", "reply", "forward", "schedule", "book",
    "invite", "upload", "write", "submit", "share", "comment", "tweet",
})
#: Words naming browser or UI automation in a connector tool's name: such a
#: tool (a click, a keypress, a page load) is rightly repeated within one
#: task and is never one-shot, whatever else its name says.
CONNECTOR_AUTOMATION_WORDS = frozenset({
    "browser", "playwright", "puppeteer", "selenium", "computer",
    "click", "dblclick", "type", "press", "keyboard", "mouse", "navigate", "goto",
    "fill", "scroll", "hover", "drag", "select", "screenshot", "snapshot",
    "wait", "evaluate", "resize",
})


def _name_words(t: str) -> list[str]:
    """The words of a tool name: split on non-alphanumerics and camelCase."""
    t = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", t)
    return [w for w in re.split(r"[^a-z0-9]+", t.lower()) if w]


def _is_connector_write_name(t: str) -> bool:
    words = _name_words(t[len(ONE_SHOT_CONNECTOR_PREFIX):])
    if any(w in CONNECTOR_AUTOMATION_WORDS for w in words):
        return False
    forms = {v + s for v in ONE_SHOT_CONNECTOR_VERBS for s in ("", "s", "d", "ed", "ing")}
    return any(w in forms for w in words)


def is_one_shot(tool_name: Any) -> bool:
    """Whether a repeat of this outward tool call is refused as already done:
    ONE_SHOT_ACTIONS, or a connector write: an mcp_ tool whose name carries
    a send/create/post-type verb (ONE_SHOT_CONNECTOR_VERBS) and no browser or
    UI-automation word (CONNECTOR_AUTOMATION_WORDS), and is not a
    desktop-control connector (judged per click like Friday's own). Never
    raises."""
    t = str(tool_name or "")
    if t in ONE_SHOT_ACTIONS:
        return True
    if t in SELF_GATED or not t.lower().startswith(ONE_SHOT_CONNECTOR_PREFIX):
        return False
    try:
        if not _is_connector_write_name(t):
            return False
        from agent_friday.services import desktop_grants as _dg
        return not _dg.is_desktop_tool(t)
    except Exception:  # noqa: BLE001 - not known to be a connector write: refuses nothing
        return False


def recorded_key_names_one_request(rec: Any) -> bool:
    """`idempotency_scope_refuses` for a decision already on record (its
    IDEMPOTENCY_SCOPE_FIELD), and, for a tool call's own derived key, only a
    one-shot tool (is_one_shot; a card's own key, scope "client", names one
    card). A decision written before the scope was recorded counts, as it
    always did. Never raises."""
    if not isinstance(rec, dict) or IDEMPOTENCY_SCOPE_FIELD not in rec:
        return True
    scope = rec.get(IDEMPOTENCY_SCOPE_FIELD)
    if not idempotency_scope_refuses(scope):
        return False
    return scope == "client" or is_one_shot(rec.get("tool"))


def _args_hash_of(args) -> str:
    return hashlib.sha256(json.dumps(args or {}, sort_keys=True, default=str).encode()).hexdigest()


def _principal_of(ctx) -> str:
    try:
        from agent_friday.services.action_ledger import PRINCIPAL as _principal
    except Exception:  # noqa: BLE001
        _principal = "owner"
    return str((ctx or {}).get("principal") or _principal)[:40]


def _duplicate_of(tool_name, args, ctx) -> Optional[str]:
    """The action that already carried out this exact request (the key
    `build_envelope` would give it), when the tool is one-shot (is_one_shot)
    and its key names one request; else None. Never raises."""
    try:
        if not is_one_shot(tool_name) or not idempotency_scope_refuses(_idempotency_scope(ctx)):
            return None
        key = _idempotency_key(str(tool_name or "")[:120], _args_hash_of(args), ctx,
                               _principal_of(ctx))
    except Exception:  # noqa: BLE001 - a lookup that cannot run refuses nothing
        return None
    return _already_done(key)


def _already_done_verdict(klass, done: str) -> "Verdict":
    return Verdict("deny", klass, ALREADY_DONE_REASON, outcome=DENIED,
                   detail={"duplicate_key": True, "already_done_action": done})


def _already_done(idem_key: Optional[str]) -> Optional[str]:
    """The action id that already carried out this idempotency key (executed
    or completed_verified in the last 30 days: services/idempotency_index),
    else None. A lookup that cannot run refuses nothing (as before). Never raises."""
    if not idem_key:
        return None
    try:
        from agent_friday.services import idempotency_index
        return idempotency_index.done_action(idem_key)
    except Exception as e:  # noqa: BLE001
        _log.warning("idempotency lookup unavailable: %s", type(e).__name__)
        return None


#: The verdict reason for a refused repeat (detail code `duplicate_key`).
ALREADY_DONE_REASON = "already done: this exact request was already carried out, so it was not run again"


def _current_pv() -> Optional[dict]:
    """The governing policy version now ({"id", "parts"}), or None when the
    module cannot be used. Read once per decision and passed along. Never raises."""
    try:
        from agent_friday.services import policy_version as _pv
        v = _pv.current()
        return v if isinstance(v, dict) and v.get("id") else None
    except Exception:  # noqa: BLE001
        return None


def _given_pv(pv: Any) -> Optional[dict]:
    return pv if isinstance(pv, dict) and pv.get("id") else None


#: The policy version `authorize` read for the decision it is recording, so
#: `_receipt` does not read it again (set only around that one call).
_DECISION_PV: ContextVar = ContextVar("friday_decision_policy_version", default=None)


def build_envelope(tool, *, args_hash: Optional[str] = None, ctx: Optional[dict] = None,
                   klass=None, decision=None, via=None, action_id: Optional[str] = None,
                   key: Optional[bytes] = None, pv: Optional[dict] = None,
                   origin: Optional[str] = None,
                   provenance: Optional[dict] = None) -> ActionEnvelope:
    """The envelope for one action; records only. `pv`: the policy version
    the caller already read (else it is read here). `origin`: else derived
    from `ctx` (derive_origin); `provenance`: a taint origin tally, counted
    (provenance_summary)."""
    from agent_friday.services import policy_version as _pv
    pvd = _given_pv(pv) or _current_pv()
    principal = _principal_of(ctx)
    aid = action_id if _pv.valid_action_id(action_id) else _pv.new_action_id()
    tool = str(tool or "")[:120]
    return ActionEnvelope(
        action_id=aid, principal=principal, tool=tool,
        args_hash=str(args_hash) if args_hash else None,
        idempotency_key=_idempotency_key(tool, args_hash, ctx, principal),
        verification_plan=_verification_plan(klass, decision, via),
        policy_version=(pvd or {}).get("id"), claws_state=_pv.claws_state(key, pv=pvd),
        created_at=time.time(),
        origin=origin if origin in ORIGINS else derive_origin(ctx, surface=via),
        risk_class=str(klass)[:20] if klass else None,
        provenance_summary=provenance_summary(provenance))


def _origin_of_entry(entry: dict) -> str:
    """The origin a receipt entry names (its surface and card), for an
    envelope built at the receipt. Never raises."""
    try:
        return derive_origin({"task_id": entry.get("task_id")} if entry.get("task_id") else None,
                             surface=entry.get("surface"), approval=entry.get("approval"))
    except Exception:  # noqa: BLE001
        return "other"


def _envelope_of(entry: dict, key: Optional[bytes], pv: Optional[dict] = None) -> dict:
    """The envelope a receipt carries: the one the gate built (`authorize`
    puts it in the entry), else one built now, with the cLaws status read
    under the signing key and the policy version as of this receipt (`pv`)."""
    from agent_friday.services import policy_version as _pv
    pvd = _given_pv(pv) or _current_pv()
    env = entry.get("envelope") if isinstance(entry.get("envelope"), dict) else None
    if env is None or not _pv.valid_action_id(env.get("action_id")):
        env = build_envelope(entry.get("tool"), args_hash=entry.get("args_hash"),
                             klass=entry.get("class"), decision=entry.get("decision"),
                             via=entry.get("via"), action_id=entry.get("action_id"),
                             key=key, pv=pvd, origin=_origin_of_entry(entry)).to_dict()
    else:
        env = {k: env.get(k) for k in ENVELOPE_FIELDS}
        env["claws_state"] = _pv.claws_state(key, pv=pvd)
        if env.get("origin") not in ORIGINS:
            env["origin"] = _origin_of_entry(entry)
        if not env.get("risk_class") and entry.get("class"):
            env["risk_class"] = str(entry.get("class"))[:20]
    env["policy_version"] = (pvd or {}).get("id") or env.get("policy_version")
    return env


#: The policy version a receipt names when the version could not be read.
PV_UNAVAILABLE = "pv_unavailable"
_LOCAL_ACTION_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{3,79}")


def _fallback_envelope(entry: dict) -> dict:
    """The envelope when the policy version module cannot be used: the
    action id the entry already names (else a local ``act_`` id) and
    PV_UNAVAILABLE. Still recorded; never holds a decision. Never raises."""
    given = entry.get("envelope") if isinstance(entry.get("envelope"), dict) else {}
    aid = next((a for a in (given.get("action_id"), entry.get("action_id"))
                if isinstance(a, str) and _LOCAL_ACTION_ID.fullmatch(a)), None)
    try:
        plan = _verification_plan(entry.get("class"), entry.get("decision"), entry.get("via"))
    except Exception:  # noqa: BLE001
        plan = {"checks": ["signed_receipt"], "outcome": "none", "via": None}
    try:
        from agent_friday.services.action_ledger import PRINCIPAL as _principal
    except Exception:  # noqa: BLE001
        _principal = "owner"
    idk = given.get("idempotency_key")
    ah = entry.get("args_hash")
    return {"action_id": aid or "act_" + uuid.uuid4().hex[:20],
            "principal": str(given.get("principal") or _principal)[:40],
            "tool": str(entry.get("tool") or "")[:120],
            "args_hash": str(ah) if ah else None,
            "idempotency_key": idk if isinstance(idk, str) else None,
            "verification_plan": plan, "policy_version": PV_UNAVAILABLE,
            "claws_state": {"hash": None, "status": "unavailable"},
            "created_at": time.time(),
            "origin": given.get("origin") if given.get("origin") in ORIGINS else _origin_of_entry(entry),
            "risk_class": str(entry.get("class"))[:20] if entry.get("class") else None,
            "provenance_summary": given.get("provenance_summary")
            if isinstance(given.get("provenance_summary"), dict) else None}


def _receipt(entry: dict, *, pv: Optional[dict] = None) -> dict:
    """Sign and append; returns the ledger entry. Raises on failure, so the
    caller can hold the action.

    Every decision receipt carries its action envelope (`envelope`, and its
    `action_id` and `policy_version` at the top), and the policy version it
    names is on record in the ledger first (policy_version.ensure_recorded).
    The ledger refuses a receipt without an action id.

    `pv`: the policy version the caller already read for this decision
    (`authorize` passes it through _DECISION_PV); else it is read once here.
    The envelope never holds a decision: if it cannot be built, a fallback
    with a local action id and PV_UNAVAILABLE is recorded instead."""
    key = _governance_key()
    pvd = _given_pv(pv) or _given_pv(_DECISION_PV.get()) or _current_pv()
    try:
        env = _envelope_of(entry, key, pvd)
        if not (isinstance(env, dict) and isinstance(env.get("action_id"), str)):
            raise ValueError("no action id")
    except Exception as e:  # noqa: BLE001 - the envelope records; it never holds
        _log.warning("action envelope fell back: %s", type(e).__name__)
        env = _fallback_envelope(entry)
    if not env.get("policy_version"):
        env["policy_version"] = PV_UNAVAILABLE
    entry = dict(entry, envelope=env, action_id=env["action_id"],
                 policy_version=env["policy_version"])
    if pvd is not None and env["policy_version"] == pvd["id"]:
        try:
            from agent_friday.services import policy_version as _pvm
            _pvm.ensure_recorded(pvd)
        except Exception:  # noqa: BLE001 - recording the version never holds a decision
            pass
    try:
        from agent_friday.services import off_record as _off
        if _off.active():
            entry = {k: entry.get(k) for k in _OFF_RECORD_RECEIPT_KEYS if k in entry}
            # no hash of the arguments off the record (a short message can be guessed)
            entry["envelope"] = dict(env, args_hash=None, idempotency_key=None,
                                     provenance_summary=None)
            entry["off_record"] = True
    except ImportError:
        pass
    entry = dict(entry, timestamp=datetime.utcnow().isoformat() + "Z")
    try:
        # The request this decision belongs to (services/correlation): one id across friday.log
        # and these receipts. Signed with the rest; absent outside a request.
        from agent_friday.services import correlation as _corr
        if _corr.current() and "correlation_id" not in entry and not entry.get("off_record"):
            entry["correlation_id"] = _corr.current()
    except ImportError:
        pass
    canonical = json.dumps(entry, sort_keys=True, default=str).encode("utf-8")
    entry["hmac"] = _hmac.new(key, canonical, hashlib.sha256).hexdigest()
    # One store for what Friday did (services/action_ledger.py): the decision
    # is an entry in the hash-chained ledger, written to disk (fsync) before
    # the action runs. The entry keeps its own HMAC as well, so a decision can
    # still be checked on its own (verify_receipt).
    from agent_friday.services import action_ledger
    return action_ledger.append("decision", entry, durable=True)


#: The checkpoint's last decision in this context (set by authorize only):
#: the governance hook stores it on the call, so the call's outcome names the
#: exact decision it carried out (north star §18.9, NS-34.17-7).
LAST_RECEIPT: ContextVar = ContextVar("friday_last_decision_receipt", default=None)

#: Statuses an outcome entry may carry: how the call ended, as its caller saw it.
OUTCOME_STATUSES = ("ok", "error", "refused", "held")

# ── The outcome vocabulary (NS-31.5-2, BD-11a) ──────────────────────────────
#
# One word for what became of an action, on every verdict, decision receipt
# and outcome receipt. Labels only: no decision reads them.
#
#   executed            it runs (a verdict), or it ran (an outcome)
#   approval_required   it waits for the owner's yes (a card or a chat question)
#   denied              the owner said no, or the handler refused it
#   forbidden           a rule or a hold stopped it (forbidden class, safe mode,
#                       a revocation, a ledger break, a later check)
#   waiting             a card for it is already open, or everything is stopped
#   failed              it was tried and did not work, or the check itself failed
#   completed_verified  it ran and the service confirmed it (an id came back)
#   unconfirmed         nobody knows yet: the request that carried it out
#                       answered, but its executor never said what became of it
#                       (settle_request_externals), or a send the gateway could
#                       not confirm. Not terminal: a later outcome may close it.

EXECUTED = "executed"
APPROVAL_REQUIRED = "approval_required"
DENIED = "denied"
FORBIDDEN = "forbidden"
WAITING = "waiting"
FAILED = "failed"
COMPLETED_VERIFIED = "completed_verified"
UNCONFIRMED = "unconfirmed"
OUTCOMES = (EXECUTED, APPROVAL_REQUIRED, DENIED, FORBIDDEN, WAITING, FAILED, COMPLETED_VERIFIED,
            UNCONFIRMED)
#: After one of these nothing more happens to the action: its terminal receipt.
TERMINAL_OUTCOMES = frozenset({EXECUTED, FAILED, COMPLETED_VERIFIED, DENIED, FORBIDDEN})
#: An outcome entry's status as an outcome word.
STATUS_OUTCOME = {"ok": EXECUTED, "error": FAILED, "refused": DENIED, "held": FORBIDDEN}
#: A task's state (services/task_states.derive) as an outcome word.
TASK_STATE_OUTCOME = {
    "completed_verified": COMPLETED_VERIFIED, "completed_unverified": EXECUTED,
    "failed": FAILED, "recovered_after_crash": FAILED, "cancelled": DENIED,
    "waiting_for_approval": WAITING, "waiting_for_input": WAITING,
    "waiting_for_external_event": WAITING,
    "outcome_unknown": UNCONFIRMED,     # it may or may not have happened: never executed
}


def verdict_outcome(action: str, klass: str = "", reason: str = "") -> str:
    """The outcome word for a checkpoint verdict (allow/confirm/card/deny)."""
    if action == "allow":
        return EXECUTED
    if action in ("confirm", "card"):
        return APPROVAL_REQUIRED
    why = str(reason or "").strip().lower()
    if klass == "forbidden" or why.startswith(("not allowed", "held")):
        return FORBIDDEN
    if why.startswith("declined"):
        return DENIED
    return FAILED                       # the check itself failed: nothing ran


def outcome_of_status(status: str) -> str:
    """An outcome entry's status (ok/error/refused/held), or a task status, as
    an outcome word. Unknown words are `failed`, never `executed`."""
    s = str(status or "").strip().lower()
    if s in STATUS_OUTCOME:
        return STATUS_OUTCOME[s]
    if s in OUTCOMES:
        return s
    try:
        from agent_friday.services import task_states
        return TASK_STATE_OUTCOME.get(task_states.derive({"status": s}), FAILED)
    except Exception:  # noqa: BLE001
        return FAILED


def record_outcome(tool: str, decision: Optional[dict], status: str, *,
                   approval: Optional[str] = None, duration_ms: Optional[float] = None,
                   detail: str = "", outcome: Optional[str] = None) -> None:
    """The terminal receipt of an outward or reading action: what happened
    after the decision (it ran: ok or error; the handler refused; a later
    check held it), linked to the decision's seq and hash. `detail` is a
    fixed code, never free text, and is left out off the record. Never raises.

    `outcome`: the outcome word (OUTCOMES), else the status's (STATUS_OUTCOME).
    `completed_verified` stands only on a status of ok. A `decision` dict
    passed in is marked closed (`_closed`), so it is not settled twice."""
    try:
        st = status if status in OUTCOME_STATUSES else "error"
        oc = outcome if outcome in OUTCOMES else STATUS_OUTCOME[st]
        if oc in (EXECUTED, COMPLETED_VERIFIED) and st != "ok":
            oc = STATUS_OUTCOME[st]
        from agent_friday.services import action_ledger
        from agent_friday.services import policy_version as _pv
        try:
            from agent_friday.services import off_record as _off
            if _off.active():
                detail = ""
        except Exception:
            pass
        # The action this outcome belongs to: the one its decision names. An
        # outcome with no decision to name (a call that ran on an approved
        # card before the checkpoint) gets an action id of its own, marked so.
        aid = (decision or {}).get("action_id")
        minted = not _pv.valid_action_id(aid)
        if minted:
            aid = _pv.new_action_id()
        action_ledger.try_append("outcome", {
            "action_id": aid,
            **({"action_id_origin": "outcome"} if minted else {}),
            "policy_version": _policy_version_id(),
            "decision_policy_version": (decision or {}).get("policy_version"),
            "tool": tool, "status": st, "outcome": oc, "terminal": oc in TERMINAL_OUTCOMES,
            "decision_seq": (decision or {}).get("seq"), "decision_hash": (decision or {}).get("hash"),
            "approval": approval or (decision or {}).get("approval"),
            "duration_ms": round(float(duration_ms), 1) if duration_ms is not None else None,
            "detail": str(detail or "")[:120]})
        if isinstance(decision, dict) and oc in TERMINAL_OUTCOMES:
            decision["_closed"] = True
        # §4.7 (G9): an outward action that closed checked or failed inside a
        # task is remembered as an outcome (services/outcome_memory: nothing
        # off the record, nothing without a task).
        if oc in (COMPLETED_VERIFIED, FAILED) and (decision or {}).get("class") == OUTWARD:
            try:
                from agent_friday.services import outcome_memory as _om
                _om.on_action_closed(tool=tool, action_id=aid, outcome_word=oc,
                                     approval=approval or (decision or {}).get("approval"))
            except Exception:
                pass
    except Exception:
        pass


# ── Outcomes for executors outside a tool call ──────────────────────────────

def _external_ref(out: Any, tool: str, approval_id: Optional[str]) -> dict:
    """The decision as an outcome names it (seq, hash, action id), returned
    for `close_external` and kept in a request's list for
    `settle_request_externals`."""
    out = out if isinstance(out, dict) else {}
    rec = out.get("rec") if isinstance(out.get("rec"), dict) else {}
    ref = {"seq": out.get("seq"), "hash": out.get("hash"), "tool": tool, "class": OUTWARD,
           "decision": "allow", "action_id": rec.get("action_id"),
           "policy_version": rec.get("policy_version"), "approval": approval_id,
           "t": time.time()}
    try:
        from flask import g, has_request_context
        if has_request_context():
            g._friday_externals = (getattr(g, "_friday_externals", None) or []) + [ref]
    except Exception:  # noqa: BLE001 - outside Flask: the executor closes it itself
        pass
    return ref


def close_external(ref: Optional[dict], ok: bool = True, *, detail: str = "",
                   verified: bool = False, outcome: Optional[str] = None) -> bool:
    """Write the terminal outcome of an action decided by `record_external`
    (or an allowing `authorize_external`), given the decision it returned:
    `ok` executed, else failed; `verified` (the service confirmed it, an id
    came back) completed_verified; `outcome` names another word. Once per
    decision; anything but a decision it returned is ignored. True when an
    outcome was written. Never raises.

    `outcome=UNCONFIRMED` is not terminal: it is written at most once and the
    decision stays open for the real outcome."""
    try:
        r = ref
        # a decision the ledger did not keep (no action id) has nothing to close
        if not (isinstance(r, dict) and r.get("class") == OUTWARD and r.get("action_id")) \
                or r.get("_closed"):
            return False
        oc = outcome if outcome in OUTCOMES else (
            COMPLETED_VERIFIED if ok and verified else EXECUTED if ok else FAILED)
        if oc == UNCONFIRMED and r.get("_unconfirmed"):
            return False
        record_outcome(str(r.get("tool") or ""), r, "ok" if ok else "error",
                       approval=r.get("approval"), detail=detail, outcome=oc)
        if oc in TERMINAL_OUTCOMES:
            r["_closed"] = True
        else:
            r["_unconfirmed"] = oc == UNCONFIRMED or bool(r.get("_unconfirmed"))
        return True
    except Exception:  # noqa: BLE001
        return False


def answer_outcome(resp) -> Optional[str]:
    """The outcome word a route's own answer gives for the change it made
    itself (the route is the executor, and builds its answer from the change's
    result): failed for an error status or an error body, unconfirmed for a
    partial or streamed answer, else executed. None when it cannot be read."""
    try:
        if resp.status_code >= 400:
            return FAILED
        if resp.is_streamed or resp.direct_passthrough or not resp.is_json:
            return UNCONFIRMED
        body = resp.get_json(silent=True)
        if not isinstance(body, dict):
            return UNCONFIRMED
        st = str(body.get("status") or "").lower()
        if st in ("error", "conflict", "denied", "failed") or body.get("ok") is False:
            return FAILED
        if st in ("partial", "unsure", "pending", "queued") or resp.status_code == 202:
            return UNCONFIRMED
        return EXECUTED
    except Exception:  # noqa: BLE001
        return None


def close_on_answer(ref: Optional[dict], detail: str = "route_answer") -> Optional[dict]:
    """For a route that carries out the change itself, inside this request:
    close `ref` from the route's own answer (`answer_outcome`) as the answer
    leaves. Outside a request this does nothing (the caller closes it).
    Returns `ref`."""
    if not isinstance(ref, dict):
        return ref
    try:
        from flask import after_this_request, has_request_context
        if not has_request_context():
            return ref

        @after_this_request
        def _close(resp):
            oc = answer_outcome(resp)
            if oc is not None:
                close_external(ref, oc in (EXECUTED, UNCONFIRMED), detail=detail, outcome=oc)
            return resp
    except Exception:  # noqa: BLE001
        pass
    return ref


def defer_external(ref: Optional[dict]) -> Optional[dict]:
    """Mark a decision as one its executor closes later, outside this request
    (a scheduled publish closes when the publish finishes): the request's
    settlement leaves it alone. Returns `ref`."""
    if isinstance(ref, dict):
        ref["_deferred"] = True
    return ref


def decision_ref_of(verdict: Any) -> Optional[dict]:
    """The decision an allowing `authorize_external` verdict carries
    (`detail['decision_ref']`), for its executor to close with the action's
    own result. None for any other verdict. Never raises."""
    try:
        if getattr(verdict, "action", None) != "allow":
            return None
        ref = (getattr(verdict, "detail", None) or {}).get("decision_ref")
        return ref if isinstance(ref, dict) else None
    except Exception:  # noqa: BLE001
        return None


#: What of a decision a record keeps so a later reconcile can close it.
_PORTABLE_KEYS = ("seq", "hash", "tool", "class", "decision", "action_id", "policy_version",
                  "approval", "t", "_unconfirmed", "_closed")


def portable_ref(ref: Optional[dict]) -> Optional[dict]:
    """A decision as an operation record keeps it (JSON-safe), for an action
    whose final state is known only later (a site publish the provider is
    still building): `close_persisted` closes it from the reconcile. None when
    there is nothing to keep. Never raises."""
    try:
        if not (isinstance(ref, dict) and ref.get("class") == OUTWARD and ref.get("action_id")):
            return None
        return {k: ref[k] for k in _PORTABLE_KEYS if k in ref}
    except Exception:  # noqa: BLE001
        return None


def _has_terminal_outcome(action_id: str) -> bool:
    from agent_friday.services import action_ledger
    for e in action_ledger.entries(["outcome"], newest_first=True,
                                   where=lambda e: isinstance(e.get("rec"), dict)
                                   and e["rec"].get("action_id") == action_id):
        rec = e.get("rec") or {}
        if rec.get("outcome") in TERMINAL_OUTCOMES or rec.get("terminal") is True:
            return True
    return False


def _ledger_decision_matches(ref: dict, expected_tool: Optional[str]) -> bool:
    """Is `ref` exactly a decision the ledger holds? The decision entry with
    its action id must carry the same seq and hash, be outward, and name the
    tool the caller expects (`expected_tool`, when given; the ref's own tool
    must agree). A ref read back from a record is data, not proof: a forged
    or swapped one (another action's id, a wrong hash, another kind) matches
    nothing and closes nothing."""
    from agent_friday.services import action_ledger
    aid = str(ref.get("action_id") or "")
    if not aid:
        return False
    if expected_tool is not None and str(ref.get("tool") or "") != str(expected_tool):
        return False
    # verified: only a line whose hash and signature hold; archive: a decision kept long enough to
    # have been archived still counts
    for e in action_ledger.entries(["decision"], newest_first=True, verified=True, archive=True,
                                   where=lambda e: isinstance(e.get("rec"), dict)
                                   and e["rec"].get("action_id") == aid):
        rec = e.get("rec") or {}
        if (e.get("seq") == ref.get("seq") and isinstance(e.get("hash"), str)
                and e.get("hash") == ref.get("hash")
                and rec.get("class") == OUTWARD
                and str(rec.get("tool") or "") == str(ref.get("tool") or "")
                and (expected_tool is None or rec.get("tool") == expected_tool)):
            return True
    return False


def close_persisted(ref: Optional[dict], ok: bool = True, *, detail: str = "",
                    verified: bool = False, outcome: Optional[str] = None,
                    expected_tool: Optional[str] = None) -> bool:
    """`close_external` for a decision kept on a record (`portable_ref`) and
    read back, possibly after a restart. The ref is checked against the
    ledger first (`_ledger_decision_matches`: the decision with its action id,
    the same seq and hash, outward, and the `expected_tool` the caller names);
    one that does not match is refused (logged, nothing written). Then the
    ledger is asked whether the action already has its terminal outcome, so
    it never gets a second one. True when an outcome was written. Never
    raises."""
    try:
        if not (isinstance(ref, dict) and ref.get("action_id")) or ref.get("_closed"):
            return False
        if not _ledger_decision_matches(ref, expected_tool):
            _log.warning("close_persisted refused a decision ref that matches no ledger decision "
                         "(expected tool %s)", expected_tool or "any")
            return False
        if _has_terminal_outcome(str(ref["action_id"])):
            ref["_closed"] = True
            return False
        return close_external(ref, ok, detail=detail, verified=verified, outcome=outcome)
    except Exception:  # noqa: BLE001
        return False


def settle_request_externals(ok: bool, detail: str = "") -> int:
    """At the end of a request: every decision `record_external` wrote in it
    that its executor neither closed nor deferred is marked `unconfirmed` (not
    terminal), with a fixed code saying how the request ended (`ok`: it answered
    without an error). The request's answer is not evidence of what became of
    the action, so settlement never writes executed or failed. Returns how
    many. Never raises."""
    try:
        from flask import g, has_request_context
        if not has_request_context():
            return 0
        refs = getattr(g, "_friday_externals", None) or []
        g._friday_externals = []
    except Exception:  # noqa: BLE001
        return 0
    n = 0
    for r in refs:
        if isinstance(r, dict) and not r.get("_closed") and not r.get("_deferred"):
            n += bool(close_external(r, ok, outcome=UNCONFIRMED,
                                     detail=detail or ("request_ok" if ok else "request_error")))
    return n


def receipts_path() -> Path:
    """Where decisions were kept before the action ledger. Read only by the
    one-time migration (action_ledger.migrate_legacy); nothing writes it."""
    return Path(friday_home()) / "decision-bom.jsonl"


def receipts(*, newest_first: bool = False, limit: Optional[int] = None,
             where=None, with_seq: bool = False) -> list:
    """Every decision receipt, from the action ledger: the signed entry as
    `_receipt` wrote it (`with_seq` adds `_seq`, its place in the ledger)."""
    from agent_friday.services import action_ledger
    out = []
    for e in action_ledger.entries(["decision"], newest_first=newest_first, limit=limit,
                                   where=where):
        r = e.get("rec")
        if isinstance(r, dict):
            out.append(dict(r, _seq=e.get("seq")) if with_seq else dict(r))
    return out


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
    # `_seq` (its place in the action ledger) and `_migrated` (where an old
    # receipt came from) are added after signing and are not part of it.
    body = {k: v for k, v in entry.items() if k not in ("hmac", "_seq", "_migrated")}
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
    #: The outcome word (OUTCOMES); derived from action, class and reason
    #: (verdict_outcome) unless the checkpoint names it.
    outcome: str = ""

    def __post_init__(self):
        if self.outcome not in OUTCOMES:
            self.outcome = verdict_outcome(self.action, self.klass, self.reason)


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
              *, tainted: bool = False, clean: bool = False,
              grant_tainted: Optional[bool] = None, via: str = "",
              provenance: Optional[dict] = None) -> Verdict:
    """Decide whether this action may run now. Never raises.

    `provenance`: the provenance check's origin tally for the call's
    arguments (services/taint Decision.origins), counted into the envelope.
    Records only.

    `tainted`: a detail came from outside content badly enough to need a card.
    `clean`: the provenance check found nothing at all from outside content in
    the call, and nothing was read from outside recently in the turn or task.
    Only a clean call may use a pre-approved send (governance/send_grants);
    it defaults to False, so a caller that does not know never unlocks one.
    `grant_tainted`: `tainted`, for the pre-approved send only; the checkpoint
    clears it when every flag came from a skill procedure the owner
    pre-approved, unchanged. None means `tainted`.

    allow    run it
    confirm  outward, in an interactive chat, nothing from outside content:
             the confirmation gate asks the user yes/no in chat
    card     outward and nobody can be asked in chat, or a detail came from
             outside content: an approval card decides it
    deny     never run (forbidden, the checkpoint itself failed, or an outward
             request already carried out: ALREADY_DONE_REASON, detail
             duplicate_key)
    """
    ctx = session_ctx or {}
    failed = None
    try:
        klass, why = classify(tool_name, args, ctx)
    except Exception as e:
        klass, why, failed = OUTWARD, "", f"classification failed ({e})"
    try:
        # Stop everything (services/global_stop): every authorization fails closed while
        # stopped, whoever asks (an approval hook, a Claude Code session, a held send).
        import agent_friday.core as _core_gs
        if _core_gs.GLOBAL_STOP.is_set() and not (
                tool_name == "task_control"
                and str((args or {}).get("op") if isinstance(args, dict) else "").lower() in ("stop", "stop_all")):
            failed = failed or "everything is stopped (the owner pressed Stop)"
    except Exception:
        pass
    try:
        # A check that could not run holds the action; not even a grant
        # overrides that, because nobody knows what the action is.
        v = (Verdict("deny", klass, f"held: {failed}") if failed
             else _decide(tool_name, klass, why, ctx, tainted, args, clean,
                          tainted if grant_tainted is None else grant_tainted))
    except Exception as e:
        v = Verdict("deny" if klass != INTERNAL else "allow", klass,
                    f"governance check failed ({e})")
    try:
        _args_hash = _args_hash_of(args)
        _scope = _idempotency_scope(ctx)
        # The single action envelope (NS-36.3-1), built once here and carried
        # to the decision receipt and, through LAST_RECEIPT, to the outcome.
        # Records only: nothing reads it to decide. The policy version is read
        # once and passed on to the receipt.
        _pvd = _current_pv()
        try:
            _env = build_envelope(tool_name, args_hash=_args_hash, ctx=ctx, klass=v.klass,
                                  decision=v.action, via=via or None, pv=_pvd,
                                  provenance=provenance).to_dict()
        except Exception as e:  # noqa: BLE001 - _receipt builds one instead
            _log.warning("action envelope not built here: %s", type(e).__name__)
            _env = None
        # Idempotent execute (NS-36.3-5): an outward action about to run whose
        # request (its idempotency key, scoped to one turn, task or run, or the
        # caller's own key) was already carried out is refused, not run again.
        # The only thing here that changes a verdict, and only toward refusing.
        # (`_decide` checks first, before it spends a grant use: a refused
        # repeat never uses one up.) Only a one-shot send or write
        # (ONE_SHOT_ACTIONS, is_one_shot): a self-gated tool, a command or a
        # browser step is rightly repeated within one task, run or turn.
        # Known limit: two identical calls at the same moment both pass (the
        # first has no outcome yet when the second is checked); this refuses
        # a repeat after the first one ran, it is not a lock.
        _dup = None
        if v.action == "deny" and (v.detail or {}).get("duplicate_key"):
            _dup = v.detail.get("already_done_action")
        elif (v.action == "allow" and v.klass == OUTWARD and _env
                and is_one_shot(tool_name) and idempotency_scope_refuses(_scope)):
            _dup = _already_done(_env.get("idempotency_key"))
            if _dup:
                v = _already_done_verdict(v.klass, _dup)
                _env["verification_plan"] = _verification_plan(v.klass, "deny", via or None)
        # (through a context variable, not an argument: callers and tests
        # replace _receipt with one-argument callables)
        _pv_token = _DECISION_PV.set(_pvd)
        try:
            _out = _receipt({"tool": tool_name, "class": v.klass, "decision": v.action,
                  "reason": v.reason, "tainted": tainted, "clean": clean,
                  # what became of it as the checkpoint sees it (OUTCOMES); a
                  # deny is its own terminal receipt
                  "outcome": v.outcome,
                  # "tool_call": the governance hook of _execute_tool, whose
                  # call writes an outcome (services/receipt_audit expects one)
                  "via": via or None,
                  "args_hash": _args_hash,
                  **({"envelope": _env, "action_id": _env["action_id"]} if _env else {}),
                  **({"detail": "duplicate_key", "already_done_action": _dup} if _dup else {}),
                  # the scope its idempotency key was derived under, so the
                  # outward audit applies the gate's own rule to it
                  IDEMPOTENCY_SCOPE_FIELD: _scope or None,
                  "surface": ctx.get("surface") or ("chat" if ctx.get("session_id") else
                                                     "background" if ctx.get("is_background_task") else "other"),
                  "grant": (v.grant or {}).get("grant_id"),
                  # Which background task took the action, so the morning
                  # receipt can link a decision to the work it belonged to.
                  "task_id": ctx.get("task_id"),
                  # Which layer chose the action, and under which plan.
                  "chosen_by": _chosen_by(ctx),
                  "plan_version": _plan_version(),
                  "turn_id": _turn_id()})
        finally:
            _DECISION_PV.reset(_pv_token)
        _out = _out if isinstance(_out, dict) else {}
        _orec = _out.get("rec") if isinstance(_out.get("rec"), dict) else {}
        LAST_RECEIPT.set({"seq": _out.get("seq"), "hash": _out.get("hash"), "tool": tool_name,
                          "class": v.klass, "decision": v.action,
                          "action_id": _orec.get("action_id") or (_env or {}).get("action_id"),
                          "policy_version": _orec.get("policy_version")
                          or (_env or {}).get("policy_version")})
    except Exception as e:
        _log.error("governance receipt failed: %s", e)
        if v.klass != INTERNAL and v.action != "deny":
            return Verdict("deny", v.klass, f"the signed receipt could not be written ({e}); "
                                            f"outward actions are held")
    return v


def _chosen_by(ctx: dict) -> str:
    """Which layer proposed the action: an explicit `chosen_by` in the session
    context, else the model that is running (`brain:` local, `cloud:` remote),
    else "unknown". Informational; no verdict reads it."""
    try:
        named = str(ctx.get("chosen_by") or "").strip()
        if named:
            return named[:80]
        model = str(ctx.get("model") or "").strip()
        if model:
            provider = str(ctx.get("provider") or "").strip()
            if provider:
                from agent_friday.services.egress_gate import is_local_provider
                return ("brain:" if is_local_provider(provider) else "cloud:") + model[:60]
            return "brain:" + model[:60]
    except Exception:
        pass
    return "unknown"


def _plan_version():
    try:
        from agent_friday.services import turn_plan
        return turn_plan.current_plan_version()
    except Exception:
        return None


def _turn_id():
    try:
        from agent_friday.services import turn_plan
        return turn_plan.current_turn_id()
    except Exception:
        return None


def _send_grant(tool_name, args, ctx) -> Optional[dict]:
    """The owner's standing pre-approval covering this send (every recipient,
    this job or skill), or None (governance/send_grants.py). Never raises."""
    try:
        from agent_friday.governance import send_grants as _sg
        return _sg.covering_tool(tool_name, args or {}, ctx)
    except Exception as e:
        _log.warning("send-grant check failed, so the send asks: %s", e)
        return None


#: In safe mode these run nothing at all: a command or program can reach the
#: network or another machine in ways no socket guard in this process sees
#: (git remotes, \\host paths, -ComputerName).
SAFE_MODE_HELD_TOOLS = frozenset({"run_command", "run_sandboxed", "open_path", "open_url",
                                  "browser_open", "browse_web", "install_package",
                                  "codebase_run", "codebase_agent"})

#: Built-in tools that reach a connected service (most judged internal; the
#: Google Tasks changes are outward since A24) (the
#: owner's Google account): mail (also the voice check and the career inbox),
#: calendar (also free/busy and releasing holds), Drive and Docs, contacts,
#: Tasks, Gmail drafts, the sending-account list and selecting mail on
#: screen (a Gmail search). Safe mode and a
#: revocation of authority hold them like any read at a connected service.
CONNECTED_SERVICE_TOOLS = frozenset({
    "search_email", "check_email", "career_inbox", "query_calendar", "find_calendar_events",
    "find_free_slots", "release_holds", "search_drive", "read_doc", "search_contacts",
    "list_tasks", "create_task", "update_task", "complete_task", "draft_email",
    "list_sending_accounts", "screen_select",
})
#: The connected-service tools that change something in the owner's account
#: (tasks, drafts, free/busy). These also wait on every outward hold: after a
#: restore until the owner resumes, and on a ledger break.
CONNECTED_SERVICE_WRITES = frozenset({
    "release_holds", "create_task", "update_task", "complete_task", "draft_email",
})


def _decide(tool_name, klass, why, ctx, tainted, args=None, clean=False,
            grant_tainted=None) -> Verdict:
    if klass == "forbidden":
        return Verdict("deny", klass, f"not allowed: {why}")
    if tool_name in SAFE_MODE_HELD_TOOLS:
        try:
            from agent_friday.services import safe_mode as _sm
            _why = _sm.refusal("connectors") if _sm.active() else _authority_hold()
        except Exception as e:  # noqa: BLE001 - a check that cannot run holds
            _why = "safe mode could not be checked (%s)" % type(e).__name__
        if _why:
            return Verdict("deny", klass, f"held: {_why}")
    if tool_name in CONNECTED_SERVICE_TOOLS:
        # a built-in tool that reaches the owner's Google account: off in
        # safe mode (connectors off) and while authority is revoked, whatever
        # its class (A24 made the Google Tasks changes outward; they are held
        # here first, exactly as before)
        try:
            from agent_friday.services import safe_mode as _sm
            _why = _sm.refusal("connector_reads") or _authority_hold()
            if not _why and tool_name in CONNECTED_SERVICE_WRITES:
                _why = outward_hold()
        except Exception as e:  # noqa: BLE001 - a check that cannot run holds
            _why = "safe mode could not be checked (%s)" % type(e).__name__
        if _why:
            return Verdict("deny", klass, f"held: {_why}")
    if klass == INTERNAL:
        if str(tool_name).lower().startswith("mcp_"):
            # a connector tool judged internal (a clean read under the card
            # setting) still reaches a connected service
            _why = _authority_hold()
            if _why:
                return Verdict("deny", klass, f"held: {_why}")
        if tainted and _own_mailbox_draft(tool_name, args):
            # A recipient read in an email is the normal case for a reply. The
            # draft reaches nobody; the send that could is asked separately.
            return Verdict("allow", klass, why + " (some details came from content read)")
        return Verdict("card" if tainted else "allow", klass, why)
    if klass == OBSERVE:
        # Safe mode (services/safe_mode.py): connectors are off, so a read at
        # a connected service is held too.
        try:
            from agent_friday.services import safe_mode as _sm
            _why = _sm.refusal("connector_reads") or _authority_hold()
        except Exception as e:  # noqa: BLE001 - a check that cannot run holds
            _why = "safe mode could not be checked (%s)" % type(e).__name__
        if _why:
            return Verdict("deny", klass, f"held: {_why}")
        # A read at a connected service: runs unasked, receipted as observe.
        # A detail from outside content could be data being smuggled out
        # through the query, so that still goes to a card.
        return Verdict("card" if tainted else "allow", klass, why)
    ok, integrity = verify_claws()
    if not ok:
        return Verdict("deny", klass, f"held: cLaws integrity check failed -- {integrity}")
    hold = outward_hold()
    if hold:
        return Verdict("deny", klass, f"held: {hold}")
    # A send the owner pre-approved (named recipients; a job's or skill's
    # sends to them). Its id becomes the owner's decision behind the call
    # (DECIDED), which the sending handler re-verifies for the exact message
    # before it skips the card. Only for a CLEAN call: nothing in it came from
    # outside content and nothing from outside content was read recently in
    # this turn or task. "Not tainted" is not enough: a body or subject
    # written from an email only ever reaches "note", never "ask".
    # Then two more conditions on the send itself (send_grants.use_refusal):
    # a spoken request uses no pre-approval unless the owner turned that on,
    # and the subject and body must screen as TIER_1 (nothing personal or
    # sensitive) by the sensitivity classifier the egress gate uses.
    withheld = {}
    sg = _send_grant(tool_name, args, ctx)
    if sg is not None:
        try:
            from agent_friday.governance import send_grants as _sg
            if not (clean and not (tainted if grant_tainted is None else grant_tainted)):
                # Named by kind, in plain words: "This chat read an outside email, so ..."
                refusal = _sg.outside_content_why(ctx)
            else:
                refusal = _sg.use_refusal(tool_name, args or {}, ctx)
            if not refusal:
                return Verdict("allow", klass, f"pre-approved send {sg['grant_id']}", grant=sg)
            _sg.note_withheld(sg, tool_name, ctx, why=refusal)
            withheld = {"grant_withheld": sg["grant_id"], "withheld_why": refusal}
        except Exception as e:
            _log.warning("could not record a withheld pre-approval: %s", e)
            withheld = {"grant_withheld": sg.get("grant_id"),
                        "withheld_why": "the pre-approval check could not finish (%s)" % e}
    if tool_name in SELF_GATED and not tainted:
        return Verdict("allow", klass, "its own approval card is the gate", detail=withheld)
    if tool_name in SELF_GATED:
        return Verdict("allow", klass, "its own approval card is the gate (with provenance)",
                       detail=withheld)
    if not tainted and not _interactive(ctx):
        # Idempotent execute (NS-36.3-5) before a grant use is spent: a request
        # the grant would let run, already carried out, is refused here (as
        # `authorize` would refuse it after) and the grant keeps its use.
        # One-shot sends and writes only (is_one_shot), as in `authorize`.
        if is_one_shot(tool_name) and _grant_covers(tool_name, ctx):
            done = _duplicate_of(tool_name, args, ctx)
            if done:
                return _already_done_verdict(klass, done)
        g = _use_grant(tool_name, ctx)
        if g is not None:
            return Verdict("allow", klass, f"pre-approved grant {g['grant_id']}", grant=g)
    if _interactive(ctx) and not tainted and tool_name not in CARD_ONLY_WHEN_OUTWARD:
        return Verdict("confirm", klass, why)
    return Verdict("card", klass, why)


def outward_hold() -> Optional[str]:
    """Why every outward action waits right now, or None. Read on every
    outward decision, after the cLaws check:

    * Friday is in safe mode (services/safe_mode.py);
    * Friday was restored from a Friday Bundle and the owner has not yet
      resumed outward actions on this device (services/friday_bundle.py,
      north star §27.6);
    * the action ledger has a break the owner has not looked at
      (services/action_ledger.hold_reason): a receipt changed, removed or
      reordered on disk;
    * the owner revoked all action authority (services/authority.py) and
      has not restored it.

    A check that cannot run holds: the reason names what failed."""
    try:
        from agent_friday.services import safe_mode as _sm
        if _sm.active():
            return _sm.refusal("connectors")
    except Exception as e:  # noqa: BLE001 - fail closed
        return "safe mode could not be checked (%s)" % type(e).__name__
    try:
        from agent_friday.services import friday_bundle
        why = friday_bundle.restore_hold()
        if why:
            return why
    except Exception as e:  # noqa: BLE001 - fail closed
        return "the restore state could not be checked (%s)" % type(e).__name__
    try:
        from agent_friday.services import action_ledger
        why = action_ledger.hold_reason()
    except Exception as e:  # noqa: BLE001 - fail closed
        why = "the action ledger could not be checked (%s)" % type(e).__name__
    if why:
        return why                       # a break is named first: it is the true reason
    return _authority_hold() or None


def _authority_hold() -> Optional[str]:
    """The owner revoked all action authority (services/authority.py)."""
    try:
        from agent_friday.services import authority
        return authority.hold_reason()
    except Exception as e:  # noqa: BLE001 - fail closed
        return "the owner's revocation of authority could not be checked (%s)" % type(e).__name__


class Held(RuntimeError):
    """An outward action the checkpoint would not let through."""


class AlreadyDone(Held):
    """`record_external(once=True)`: the card's action was already carried
    out (its idempotency key has an executed or completed_verified outcome),
    so it is refused, not run again (NS-36.3-5). `action_id`: the one that
    ran. A new card is a new key: the owner re-does it with a new card.

    One-shot by construction, whatever the tool: the key names one approval
    card (its id, action and target), and one approved card is carried out
    once. It does not read ONE_SHOT_ACTIONS (that set is for a tool call's
    key derived from its turn, task or run, which a legitimate repeat can share)."""

    def __init__(self, msg: str, action_id: Optional[str] = None):
        super().__init__(msg)
        self.action_id = action_id


def check_external() -> None:
    """The checks `record_external` makes before its receipt (Stop is not
    pressed, the cLaws are intact, no outward hold). Raises `Held`."""
    import agent_friday.core as _core_gs
    if _core_gs.GLOBAL_STOP.is_set():
        # Stop everything (services/global_stop): held; the decision is not spent.
        raise Held("everything is stopped (the owner pressed Stop); nothing was sent")
    try:
        ok, why = verify_claws()
    except Exception as e:
        raise Held(f"the cLaws integrity check could not run ({e})")
    if not ok:
        raise Held(f"the cLaws integrity check failed ({why})")
    hold = outward_hold()
    if hold:
        raise Held(hold)


def record_external(action: str, *, surface: str, approval_id: Optional[str] = None,
                    target: str = "", task_id: Optional[str] = None, once: bool = False) -> dict:
    """The checkpoint's integrity check and signed receipt, for an executor
    that carries out an already-decided action outside a tool call (an
    approved email, an approved text).

    The decision itself (the card) is the caller's to verify. This adds the
    two steps every tool call gets in `authorize`: the cLaws are intact, and
    a signed receipt of the action is written. Raises `Held` if either fails,
    so the caller stops before anything leaves the machine.

    Returns the decision as an outcome names it; the executor writes the
    action's terminal outcome with `close_external` (inside a request, one it
    leaves open is marked unconfirmed: settle_request_externals). The
    decision receipt carries no outcome word: nothing has run yet, and what
    became of the action is its outcome receipt's to say.

    `once` (with `approval_id`), for an executor whose card buys exactly one
    action (the approval executor, a mail send): the decision's idempotency
    key is the card's (with the action and target), so the outward audit sees
    that card carried out twice (`duplicate_key`), across restarts too. Not
    the default: some cards rightly carry more than one decision of the same
    action and target (a site build's operation and its process launch, a
    publisher series).
    `task_id`: the task the card was raised for, so the audit can tell a task
    marked done whose approved action never ended executed (false_done).
    """
    check_external()
    entry = {"tool": action, "class": OUTWARD, "decision": "allow",
             "surface": surface,
             "reason": "approved card" if approval_id else surface,
             "approval": approval_id, "target": target}
    if task_id:
        entry["task_id"] = str(task_id)[:120]
    if approval_id and once:
        entry[IDEMPOTENCY_SCOPE_FIELD] = "client"       # the card's own key: one request
        try:
            entry["envelope"] = build_envelope(
                action, klass=OUTWARD, decision="allow", via=surface,
                ctx={"idempotency_key": "approval|%s|%s|%s" % (approval_id, action, target)},
                origin=derive_origin(None, surface=surface, approval=approval_id)).to_dict()
        except Exception:  # noqa: BLE001 - the envelope records; _receipt builds one without a key
            pass
        # Idempotent execute (NS-36.3-5): this card's action already ran (the
        # key names the card, so only this card): refused, with its own
        # receipt, and nothing is carried out again.
        done = _already_done((entry.get("envelope") or {}).get("idempotency_key"))
        if done:
            env = dict(entry["envelope"], verification_plan=_verification_plan(OUTWARD, "deny", surface))
            refusal = dict(entry, decision="deny", reason=ALREADY_DONE_REASON, outcome=DENIED,
                           detail="duplicate_key", already_done_action=done, envelope=env)
            try:
                _receipt(refusal)
            except Exception as e:  # noqa: BLE001 - refused either way
                _log.warning("the already-done refusal could not be receipted: %s", type(e).__name__)
            raise AlreadyDone("%s (approval %s)" % (ALREADY_DONE_REASON, approval_id), done)
    try:
        out = _receipt(entry)
    except Exception as e:
        raise Held(f"the signed receipt could not be written ({e})")
    return _external_ref(out, action, approval_id)


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
        import agent_friday.core as _core_gs
        if _core_gs.GLOBAL_STOP.is_set():
            # Stop everything (services/global_stop): held, and the card is not used up.
            # "card", not "deny": callers wait (as for a card) instead of recording a decline.
            return Verdict("card", OUTWARD, "held: everything is stopped (the owner pressed Stop)",
                           outcome=WAITING)
    except Exception:
        pass
    try:
        ok, integrity = verify_claws()
        if not ok:
            return Verdict("deny", OUTWARD, f"held: {integrity}")
        # the same hold every outward action takes (safe mode, a restore, the
        # owner's revocation of authority, a ledger break): before any card
        # is looked up or raised, so an approved card cannot slip past it
        hold = outward_hold()
        if hold:
            return Verdict("deny", OUTWARD, f"held: {hold}")
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
        card_id = (rec or {}).get("approval_id")
        if rec and rec.get("status") == "approved" and not rec.get("consumed"):
            _ap.mark_used(rec["approval_id"], requested_by)
            v = Verdict("allow", OUTWARD, "approved on a card")
        elif rec and rec.get("status") in ("denied", "blocked"):
            v = Verdict("deny", OUTWARD, "declined on a card", outcome=DENIED)
        else:
            if rec is None or rec.get("status") == "expired" or rec.get("consumed"):
                made = _ap.create_approval(
                    kind="governed_action", subject_type="external_action",
                    subject_id=subject + (f":{uuid.uuid4().hex[:6]}" if rec else ""),
                    title=title or f"Allow {action}",
                    description=description or json.dumps(detail, default=str)[:600],
                    action_description=action_description or action, force_gate=True,
                    payload=detail, requested_by=requested_by)
                card_id = (made or {}).get("approval_id") if isinstance(made, dict) else None
                v = Verdict("card", OUTWARD, "waiting for the owner's decision",
                            outcome=APPROVAL_REQUIRED)
            else:
                # its card is already open: the action waits on it
                v = Verdict("card", OUTWARD, "waiting for the owner's decision", outcome=WAITING)
        out = _receipt({"tool": action, "class": OUTWARD, "decision": v.action,
                        "reason": v.reason, "surface": requested_by, "outcome": v.outcome,
                        **({"approval": card_id} if card_id else {})})
        if v.action == "allow":
            # the caller carries it out next: its outcome closes this decision
            v.detail["decision_ref"] = _external_ref(out, action, card_id)
        return v
    except Exception as e:
        return Verdict("deny", OUTWARD, f"governance check failed ({e})")

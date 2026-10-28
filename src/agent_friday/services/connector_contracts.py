"""What each connector lets Friday do, declared in one place (BD-06, NS-23.1-2).

A connector's contract says, per operation Friday can perform through it: the tool name,
the service, whether it only reads or changes something at the provider, the provider
permission (OAuth scope) it needs (`scopes` lists the permissions any one of which lets it
run, or the ones it needs for the kind of file it reads), and its action class at Friday's
checkpoint (governance/action_gate). GET /api/google/accounts/contract serves it (no screen reads
it yet); answer_card, which carries out cards the operations below raised, is not an operation of
its own;
tests/unit/test_google_connector_contract.py fails when the code and the declaration drift
apart (a Google tool with no declaration, a change declared as a read, an action class the
checkpoint does not give, a change permission asked for on an ordinary connect).

This file declares; it decides nothing. The checkpoint's classification stays in
governance/action_gate.py, and a disagreement here is a failing test, not a second opinion.
"""
from __future__ import annotations

from typing import Any, Dict, List

# OAuth scopes (services/google_accounts.py holds the same strings; repeated here as data so
# the declaration can be read without importing the Google client libraries).
_G = "https://www.googleapis.com/auth/"
GMAIL_READ = _G + "gmail.readonly"
GMAIL_SEND = _G + "gmail.send"
GMAIL_MODIFY = _G + "gmail.modify"
CALENDAR_READ = _G + "calendar.readonly"
CALENDAR_RW = _G + "calendar"
CALENDAR_EVENTS = _G + "calendar.events"
DRIVE_READ = _G + "drive.readonly"
DOCS_READ = _G + "documents.readonly"
SHEETS_READ = _G + "spreadsheets.readonly"
TASKS_READ = _G + "tasks.readonly"
TASKS_RW = _G + "tasks"
CONTACTS_READ = _G + "contacts.readonly"
CONTACTS_RW = _G + "contacts"

_CAL_WRITE = [CALENDAR_RW, CALENDAR_EVENTS]      # either one allows the change
#: A24: asks first; in a run nobody watches one card approves the run's task changes
#: (services/run_grants), each change with its own receipt. Needs Allow task changes.
_RUN_GRANT_NOTE = ("asks first (A24); in a background or workflow run one card covers the run's "
                   "task changes, each with its own receipt; needs Allow task changes")


def _op(tool: str, service: str, access: str, scopes: List[str], action_class: str,
        note: str = "", *, by_argument: bool = False, send_scopes: List[str] = ()) -> Dict[str, Any]:
    out = {"tool": tool, "service": service, "access": access, "scopes": list(scopes),
           "action_class": action_class, "note": note, "by_argument": bool(by_argument)}
    if send_scopes:
        out["send_scopes"] = list(send_scopes)
    return out


GOOGLE_OPERATIONS: List[Dict[str, Any]] = [
    # Calendar
    _op("query_calendar", "calendar", "read", [CALENDAR_READ], "internal"),
    _op("find_calendar_events", "calendar", "read", [CALENDAR_READ], "internal"),
    _op("find_free_slots", "calendar", "read", [CALENDAR_READ], "internal"),
    _op("create_calendar_event", "calendar", "write", _CAL_WRITE, "outward"),
    _op("update_calendar_event", "calendar", "write", _CAL_WRITE, "outward"),
    _op("annotate_calendar_events", "calendar", "write", _CAL_WRITE, "outward"),
    _op("delete_calendar_event", "calendar", "write", _CAL_WRITE, "outward"),
    _op("hold_slots", "calendar", "write", _CAL_WRITE, "outward"),
    _op("book_slot", "calendar", "write", _CAL_WRITE, "outward", "sends the invitations"),
    _op("release_holds", "calendar", "write", _CAL_WRITE, "internal",
        "deletes only the tentative holds Friday itself placed (action_gate's own reasoning)"),
    _op("organize_calendar", "calendar", "write", _CAL_WRITE, "outward",
        "classified by its arguments; always outward"),
    # Gmail
    _op("search_email", "gmail", "read", [GMAIL_READ], "internal"),
    _op("career_inbox", "gmail", "read", [GMAIL_READ], "internal"),
    _op("draft_email", "gmail", "write", [GMAIL_MODIFY], "internal",
        "saves a draft in the owner's own mailbox; with send=true it sends (gmail.send) and "
        "is outward, by its arguments", send_scopes=[GMAIL_SEND]),
    _op("organize_email", "gmail", "write", [GMAIL_MODIFY], "outward",
        "read/unread, star and label changes run without a card (item_actions EMAIL_NO_CARD); "
        "archive, trash, spam, mute and unsubscribe wait for one"),
    _op("check_email", "gmail", "read", [GMAIL_READ], "internal", "the voice path's mail read"),
    _op("undo_action", "gmail", "write", [GMAIL_MODIFY] + _CAL_WRITE, "outward",
        "classified by the receipt it undoes: outward for mail and calendar changes", by_argument=True),
    _op("list_sending_accounts", "gmail", "read", [], "internal",
        "reads which accounts may send from Friday's own record; no call to Google"),
    # Drive, Docs, Sheets
    _op("search_drive", "drive", "read", [DRIVE_READ], "internal"),
    _op("read_doc", "drive", "read", [DRIVE_READ, DOCS_READ, SHEETS_READ], "internal"),
    # Tasks
    _op("list_tasks", "tasks", "read", [TASKS_READ], "internal"),
    _op("complete_task", "tasks", "write", [TASKS_RW], "outward", _RUN_GRANT_NOTE),
    _op("create_task", "tasks", "write", [TASKS_RW], "outward", _RUN_GRANT_NOTE),
    _op("update_task", "tasks", "write", [TASKS_RW], "outward", _RUN_GRANT_NOTE),
    _op("delete_task", "tasks", "write", [TASKS_RW], "outward"),
    # Contacts
    _op("search_contacts", "contacts", "read", [CONTACTS_READ], "internal"),
    _op("save_google_contact", "contacts", "write", [CONTACTS_RW], "outward"),
]

#: Asked for only by their own buttons ("Reconnect with sending", "Allow calendar changes",
#: "Allow task changes", "Allow saving to Google Contacts"), never on an ordinary connect.
GOOGLE_CHANGE_SCOPES = [GMAIL_SEND, GMAIL_MODIFY, CALENDAR_RW, CALENDAR_EVENTS, TASKS_RW, CONTACTS_RW]


def google_contract() -> Dict[str, Any]:
    """The Google connector's declaration, as GET /api/google/accounts/contract serves it."""
    reads = [o for o in GOOGLE_OPERATIONS if o["access"] == "read"]
    writes = [o for o in GOOGLE_OPERATIONS if o["access"] == "write"]
    from agent_friday.services import google_accounts as ga
    connect_scopes = list(ga.GOOGLE_MULTI_SCOPES)
    return {
        "connector": "google",
        "connect_scopes": connect_scopes,
        "change_scopes": list(GOOGLE_CHANGE_SCOPES),
        "reads": [dict(o) for o in reads],
        "changes": [dict(o) for o in writes],
        "outward_changes": sorted(o["tool"] for o in writes if o["action_class"] == "outward"),
        "changes_without_a_card": sorted(o["tool"] for o in writes if o["action_class"] != "outward"),
        "rate_limits": GOOGLE_RATE_LIMITS,
        "destinations": [dict(d) for d in GOOGLE_DESTINATIONS],
        "receives": list(GOOGLE_RECEIVES),
        "retention": [dict(r) for r in GOOGLE_RETENTION],
        "revocation": dict(GOOGLE_REVOCATION),
        "audit": list(GOOGLE_AUDIT),
        "limitations": list(GOOGLE_LIMITATIONS),
        "card": google_card(),
    }


def operation(tool: str) -> Dict[str, Any]:
    """The declared operation for a tool name, or {}."""
    return next((dict(o) for o in GOOGLE_OPERATIONS if o["tool"] == tool), {})


# ── the rest of the contract (BD-06: NS-23.1-3, -4, -6, -7; the card, NS-8.9-2) ──────────
#
# Declared as data, like the operations above. tests/unit/test_connector_contract_complete.py
# ties each number to the constant the code runs on, each host to the code that calls it and
# each kept file to the module that writes it, so the card cannot say something the code does
# not do.

#: What each Google service lets Friday do, in the owner's words (one line per service).
GOOGLE_SERVICE_SUMMARY: Dict[str, str] = {
    "calendar": ("Calendar: reads your events and free/busy. Changes (create, move, delete, holds, "
                 "bookings) need \"Allow calendar changes\" and wait for your yes on a card."),
    "gmail": ("Gmail: searches and reads your mail. Drafts are saved in your mailbox; sending, "
              "archiving, trash, spam, mute and unsubscribe wait for a card; read/unread, star and "
              "labels change without one. Needs \"Reconnect with sending\" for any change."),
    "drive": ("Drive: searches your files and reads Google Docs and Sheets. Never changes them."),
    "tasks": ("Tasks: reads your open tasks. Changes need \"Allow task changes\" and wait for a card."),
    "contacts": ("Contacts: looks up names, email addresses and phone numbers. Saving a contact needs "
                 "\"Allow saving to Google Contacts\" and waits for a card."),
}

#: NS-23.1-3. How fast Friday calls Google, and what it does when Google says slow down.
GOOGLE_RATE_LIMITS: Dict[str, Any] = {
    "gmail_retry": {"tries": 4, "first_delay_s": 0.8, "max_delay_s": 8.0, "batch_size": 50,
                    "on": "Gmail reads and saving a draft, on a busy or rate-limit answer (429, "
                          "5xx, rateLimitExceeded, userRateLimitExceeded): waits longer each time "
                          "(or as Google asks), then says it could not. A draft save retried after "
                          "Google failed to answer can, rarely, leave two drafts"},
    "other_services": ("Calendar, Drive, Docs, Sheets, Tasks and Contacts are not retried: a busy "
                       "answer is reported as busy and nothing is tried again, so a change is never "
                       "made twice"),
    "sending": "a send is never retried automatically (a second try could send twice)",
    "friday_caps": {
        "calendar_holds_per_request": 10,
        "scheduling_window_days": 31,
        "items_per_organize_card": 500,
        "tasks_inbox_posts_per_day": 30,
        "tasks_inbox_reposts_per_hour": 5,
        "connect_attempts_per_5_minutes": 10,
    },
}

#: NS-23.1-4. Where Friday's Google calls go. Reached directly; none goes through a third party.
GOOGLE_DESTINATIONS: List[Dict[str, str]] = [
    {"host": "accounts.google.com", "for": "signing in and the consent screen (in your browser)"},
    {"host": "oauth2.googleapis.com", "for": "getting and refreshing the sign-in token, and "
                                             "revoking it when you remove the account"},
    {"host": "www.googleapis.com", "for": "Calendar, Drive and the account's email address"},
    {"host": "gmail.googleapis.com", "for": "Gmail"},
    {"host": "docs.googleapis.com", "for": "reading Google Docs"},
    {"host": "sheets.googleapis.com", "for": "reading Google Sheets"},
    {"host": "tasks.googleapis.com", "for": "Google Tasks"},
    {"host": "people.googleapis.com", "for": "Google Contacts"},
]

#: The Google API clients Friday builds (googleapiclient.discovery.build), by name and version.
GOOGLE_API_CLIENTS: Dict[str, str] = {"gmail": "v1", "calendar": "v3", "drive": "v3", "docs": "v1",
                                      "sheets": "v4", "tasks": "v1", "people": "v1", "oauth2": "v2"}

#: NS-23.1-4. What Google receives: only what each call needs.
GOOGLE_RECEIVES: List[str] = [
    "for a read: what is asked for (a search, a date range, a file or message id)",
    "for a change: the change itself (an event's details, a task's text, an email you approved)",
    "the sign-in token with every call; never your other accounts' tokens or any other key",
]

#: NS-23.1-4. What Friday keeps on this PC from the Google connector, and for how long.
GOOGLE_RETENTION: List[Dict[str, str]] = [
    {"what": "the sign-in token for each account", "where": "google_accounts/tokens/<id>.token.enc",
     "kept": "encrypted, until you remove the account", "module": "services/google_accounts.py"},
    {"what": "the list of connected accounts and when each last synced",
     "where": "google_accounts/accounts.json", "kept": "until you remove the account",
     "module": "services/google_accounts.py"},
    {"what": "which Google services answered recently", "where": "google_accounts/service_state.json",
     "kept": "until replaced (shown as current for 10 minutes)", "module": "services/google_accounts.py"},
    {"what": "a copy of the recent mail list and calendar prep", "where": "gmail/, messages/, calendar/",
     "kept": "until the next sync replaces it, or you delete it in Settings > Privacy",
     "module": "services/calendar_engine.py"},
    {"what": "emails waiting to be sent (scheduled or held) and their attachments",
     "where": "mail_outbox/", "kept": "until sent or cancelled", "module": "services/gmail_send.py"},
    {"what": "a receipt for each change, so it can be undone", "where": "the action journal",
     "kept": "the last 500", "module": "services/action_journal.py"},
    {"what": "a signed record of each action Friday was allowed or refused", "where": "the action ledger",
     "kept": "kept; moved to an archive after 730 days", "module": "services/action_ledger.py"},
    {"what": "a record of what was sent out (who, when, a fingerprint; the text encrypted)",
     "where": "the egress log", "kept": "kept", "module": "services/egress_gate.py"},
]

#: NS-23.1-6. Removing access, and the record kept of what Friday did.
GOOGLE_REVOCATION: Dict[str, Any] = {
    "how": "Settings > Connections > Google > Remove (one account at a time)",
    "does": [
        "asks Google to revoke the sign-in token (if Google does not confirm it, Settings says so; "
        "you can also revoke Friday at myaccount.google.com > Security > Third-party access)",
        "deletes the encrypted token from this PC",
        "drops the account from Friday's list; with the last account gone, the record of which "
        "Google services answered goes too",
    ],
    "keeps": "the receipts, the action ledger and the egress log (they are the record of what was "
             "done); cached copies are replaced at the next sync",
    "if_google_revokes": ("a call that finds access revoked does nothing, says so (\"[NOT DONE]\") and "
                          "marks the account \"Reconnect needed\""),
}
GOOGLE_AUDIT: List[str] = [
    "every action Friday is allowed or refused is a signed entry in the action ledger",
    "every change at Google has a receipt (and Undo where Google allows it)",
    "every send and every change is in the egress log",
    "reads are not logged one by one (only the decision to run the tool is in the ledger)",
]

#: NS-23.1-7. What Google, or Friday's use of it, cannot do.
GOOGLE_LIMITATIONS: List[str] = [
    "Gmail: attachments up to 20 MB per message (Google's limit is 25 MB); scheduled sends up to "
    "30 days ahead; a send can be undone for 10 seconds; mail is never permanently deleted",
    "Calendar: a change to a repeating event applies to the whole series unless you say one "
    "occurrence; guests are told only if you choose to tell them",
    "Docs: the first 20,000 characters are read. Sheets: the first tab, cells A1 to Z200. Other "
    "Drive files (PDFs, images, Office files) are not read",
    "Tasks: only open tasks are read, notes up to 300 characters; Google does not say who changed a task",
    "Contacts: names, email addresses and phone numbers are read",
]


def google_card() -> List[Dict[str, Any]]:
    """The connector card (NS-8.9-2): what the connection enables, the permissions it asks for
    (reads and changes apart), what runs without a card, what Google receives, what is kept on
    this PC, the limits, and how to remove it. Plain lines for the Settings panel."""
    from agent_friday.services import google_accounts as ga
    services = []
    for o in GOOGLE_OPERATIONS:
        if o["service"] not in services:
            services.append(o["service"])
    writes = [o for o in GOOGLE_OPERATIONS if o["access"] == "write"]
    no_card = sorted(o["tool"] for o in writes if o["action_class"] != "outward")
    connect = [s.rsplit("/", 1)[-1] for s in ga.GOOGLE_MULTI_SCOPES]
    change = [s.rsplit("/", 1)[-1] for s in GOOGLE_CHANGE_SCOPES]
    rl = GOOGLE_RATE_LIMITS
    return [
        {"heading": "What it lets Friday do", "lines": [GOOGLE_SERVICE_SUMMARY[s] for s in services]},
        {"heading": "Permissions", "lines": [
            "Asked for when you connect (reading): " + ", ".join(connect),
            "Asked for only by their own buttons (changing): " + ", ".join(change)]},
        {"heading": "Runs without a card", "lines": [
            "Only these changes: " + ", ".join(no_card) + "; and in Gmail, read/unread, star and "
            "labels. Everything else that changes something at Google waits for your yes."]},
        {"heading": "What Google receives", "lines": list(GOOGLE_RECEIVES) + [
            "Friday talks to: " + ", ".join(d["host"] for d in GOOGLE_DESTINATIONS)]},
        {"heading": "Kept on this PC", "lines": ["%s (%s)" % (r["what"], r["kept"]) for r in GOOGLE_RETENTION]},
        {"heading": "Limits", "lines": [
            "Gmail reads and draft saves: tried up to %d times when Google is busy, waiting longer "
            "each time" % rl["gmail_retry"]["tries"], rl["other_services"], rl["sending"]] + list(GOOGLE_LIMITATIONS)},
        {"heading": "Removing it", "lines": [GOOGLE_REVOCATION["how"]] + list(GOOGLE_REVOCATION["does"])
            + ["Kept after removal: " + GOOGLE_REVOCATION["keeps"],
               "If Google revokes access: " + GOOGLE_REVOCATION["if_google_revokes"]]},
    ]

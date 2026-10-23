"""What each connector lets Friday do, declared in one place (BD-06, NS-23.1-2).

A connector's contract says, per operation Friday can perform through it: the tool name,
the service, whether it only reads or changes something at the provider, the provider
permission (OAuth scope) it needs, and its action class at Friday's checkpoint
(governance/action_gate). The connect card and GET /api/google/accounts/contract read it;
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


def _op(tool: str, service: str, access: str, scopes: List[str], action_class: str,
        note: str = "") -> Dict[str, Any]:
    return {"tool": tool, "service": service, "access": access, "scopes": list(scopes),
            "action_class": action_class, "note": note}


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
        "is outward, by its arguments"),
    _op("organize_email", "gmail", "write", [GMAIL_MODIFY], "outward"),
    _op("list_sending_accounts", "gmail", "read", [], "internal",
        "reads which accounts may send from Friday's own record; no call to Google"),
    # Drive, Docs, Sheets
    _op("search_drive", "drive", "read", [DRIVE_READ], "internal"),
    _op("read_doc", "drive", "read", [DRIVE_READ, DOCS_READ, SHEETS_READ], "internal"),
    # Tasks
    _op("list_tasks", "tasks", "read", [TASKS_READ], "internal"),
    _op("complete_task", "tasks", "write", [TASKS_RW], "internal",
        "changes the owner's own task list; needs Allow task changes"),
    _op("create_task", "tasks", "write", [TASKS_RW], "internal",
        "changes the owner's own task list; needs Allow task changes"),
    _op("update_task", "tasks", "write", [TASKS_RW], "internal",
        "changes the owner's own task list; needs Allow task changes"),
    _op("delete_task", "tasks", "write", [TASKS_RW], "outward"),
    # Contacts
    _op("search_contacts", "contacts", "read", [CONTACTS_READ], "internal"),
    _op("save_google_contact", "contacts", "write", [CONTACTS_RW], "outward"),
]

#: Asked for only by their own buttons ("Reconnect with sending", "Allow calendar changes",
#: "Allow task changes", "Allow saving to Google Contacts"), never on an ordinary connect.
GOOGLE_CHANGE_SCOPES = [GMAIL_SEND, GMAIL_MODIFY, CALENDAR_RW, CALENDAR_EVENTS, TASKS_RW, CONTACTS_RW]


def google_contract() -> Dict[str, Any]:
    """The Google connector's declaration, as the connect card and the API show it."""
    reads = [o for o in GOOGLE_OPERATIONS if o["access"] == "read"]
    writes = [o for o in GOOGLE_OPERATIONS if o["access"] == "write"]
    try:
        from agent_friday.services import google_accounts as ga
        connect_scopes = list(ga.GOOGLE_MULTI_SCOPES)
    except Exception:
        connect_scopes = []
    return {
        "connector": "google",
        "connect_scopes": connect_scopes,
        "change_scopes": list(GOOGLE_CHANGE_SCOPES),
        "reads": [dict(o) for o in reads],
        "changes": [dict(o) for o in writes],
        "outward_changes": sorted(o["tool"] for o in writes if o["action_class"] == "outward"),
        "changes_without_a_card": sorted(o["tool"] for o in writes if o["action_class"] != "outward"),
    }


def operation(tool: str) -> Dict[str, Any]:
    """The declared operation for a tool name, or {}."""
    return next((dict(o) for o in GOOGLE_OPERATIONS if o["tool"] == tool), {})

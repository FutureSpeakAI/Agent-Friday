"""Saving a contact to the owner's Google Contacts.

Outward: it changes the owner's Google account, which syncs to their phone and
to every app that reads it. It runs only as the `save_google_contact` tool,
which the governance checkpoint classifies as outward, so nothing here runs
until the owner has decided it (a chat yes, an approval card, or a scoped
grant for background work).

It also needs a permission ordinary connections do not have. Accounts are
connected with contacts.readonly; saving needs the full contacts scope, which
the owner grants per account from the Contacts workspace. `writable_accounts`
reads the scopes Google actually granted, and a save on an account without
it is refused with that reason, never attempted.
"""
from __future__ import annotations

import re

_EMAIL = re.compile(r"^[^@\s,;<>]+@[^@\s,;<>]+\.[^@\s,;<>]+$")
_PHONE = re.compile(r"^[+()0-9 .\-]{5,32}$")
_CTRL = re.compile(r"[\x00-\x1f\x7f]")


def writable_accounts() -> list:
    """Accounts whose granted scopes allow saving contacts."""
    try:
        from agent_friday.services import google_accounts as ga
        return [{"id": r.get("id"), "email": r.get("email"), "label": r.get("label")}
                for r in ga.list_accounts() if ga.CONTACTS_RW in (r.get("scopes") or [])]
    except Exception:
        return []


def _clean(s, cap=120) -> str:
    return _CTRL.sub("", str(s or "")).strip()[:cap]


def _pick_account(account_id: str) -> tuple:
    """(account, error). Refuses plainly when no account may save contacts."""
    from agent_friday.services import google_accounts as ga
    accounts = ga.list_accounts()
    if not accounts:
        return None, "No Google account is connected."
    ok = writable_accounts()
    if account_id:
        rec = next((a for a in accounts if a.get("id") == account_id), None)
        if not rec:
            return None, "There is no connected account with id %r." % account_id
        if not any(a["id"] == account_id for a in ok):
            return None, ("%s was connected with read-only access to contacts, so Friday "
                          "cannot save to it. The owner can allow it in Contacts → "
                          "Allow saving to Google Contacts." % (rec.get("email") or account_id))
        return rec, ""
    if not ok:
        return None, ("None of the connected Google accounts allows saving contacts; they "
                      "were connected with read-only access. The owner can allow it in "
                      "Contacts → Allow saving to Google Contacts.")
    if len(ok) > 1:
        return None, ("More than one account can save contacts (%s); say which account_id."
                      % ", ".join("%s=%s" % (a["id"], a["email"]) for a in ok))
    return ok[0], ""


def _service(account_id: str):
    from agent_friday.services import google_accounts as ga
    creds = ga.credentials_for(account_id)
    if not creds:
        return None, "The account needs reconnecting before Friday can save to it."
    from googleapiclient.discovery import build
    return build("people", "v1", credentials=creds, cache_discovery=False), ""


def build_person(*, name="", email="", phone="", company="", job_title="") -> tuple:
    """(People API body, error). Rejects rather than repairs bad values."""
    name, email, phone = _clean(name), _clean(email).lower(), _clean(phone, 40)
    company, job_title = _clean(company), _clean(job_title)
    if not name and not email:
        return None, "A contact needs at least a name or an email address."
    if email and not _EMAIL.match(email):
        return None, "%r is not a valid email address." % email
    if phone and not _PHONE.match(phone):
        return None, "%r is not a phone number." % phone
    body = {}
    if name:
        body["names"] = [{"unstructuredName": name}]
    if email:
        body["emailAddresses"] = [{"value": email}]
    if phone:
        body["phoneNumbers"] = [{"value": phone}]
    if company or job_title:
        body["organizations"] = [{k: v for k, v in (("name", company), ("title", job_title)) if v}]
    return body, ""


def save_contact(*, account_id: str = "", resource_name: str = "", **fields) -> dict:
    """Create a contact, or update `resource_name` (people/c123...) in place.

    Only reached through the outward `save_google_contact` tool. An update
    changes only the fields given.
    """
    rec, err = _pick_account((account_id or "").strip())
    if err:
        return {"ok": False, "error": err, "needs_permission": "read-only" in err}
    body, err = build_person(**fields)
    if err:
        return {"ok": False, "error": err}
    svc, err = _service(rec["id"])
    if svc is None:
        return {"ok": False, "error": err}
    resource_name = (resource_name or "").strip()
    try:
        if resource_name:
            if not re.match(r"^people/[A-Za-z0-9_\-]+$", resource_name):
                return {"ok": False, "error": "resource_name must look like people/c123."}
            current = svc.people().get(resourceName=resource_name,
                                       personFields="metadata").execute()
            body["etag"] = current.get("etag")
            fields_mask = ",".join(k for k in ("names", "emailAddresses", "phoneNumbers",
                                               "organizations") if k in body)
            out = svc.people().updateContact(resourceName=resource_name,
                                             updatePersonFields=fields_mask,
                                             body=body).execute()
            action = "updated"
        else:
            out = svc.people().createContact(body=body).execute()
            action = "created"
    except Exception as e:
        return {"ok": False, "error": "Google refused the save: %s" % str(e)[:300]}
    return {"ok": True, "action": action, "account": rec.get("email"),
            "resource_name": out.get("resourceName")}

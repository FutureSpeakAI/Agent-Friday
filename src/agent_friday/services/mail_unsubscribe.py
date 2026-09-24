"""Unsubscribing from a mailing list, the way the list itself asks for it.

A list says how to leave it in the List-Unsubscribe header (RFC 2369), and
whether a single POST is enough in List-Unsubscribe-Post (RFC 8058,
"one-click"). The header is read from Gmail here, never taken from the
browser, so a page cannot make Friday post to an address of its choosing.

* One-click https: Friday sends the one POST the RFC defines, and nothing
  else (no cookies, no redirects followed), only to a public address.
* mailto: the owner gets a message addressed to the list in the composer;
  sending it goes through the approval card like any other mail.
* A plain web link: the owner is given the link to open themselves.

Unsubscribing tells the sender the address is live, so the UI confirms
first, and Friday never does it on its own initiative without approval
(see mail_proposals).
"""
from __future__ import annotations

import ipaddress
import re
import socket
from urllib.parse import parse_qs, unquote, urlsplit

from agent_friday.services import gmail_api

_ENTRY = re.compile(r"<\s*([^>]+?)\s*>")


def parse_header(value: str) -> dict:
    """-> {"https": [...], "mailto": [...]} in the order the list gives them."""
    out = {"https": [], "mailto": []}
    for u in _ENTRY.findall(value or ""):
        u = u.strip()
        if u.lower().startswith("https://"):
            out["https"].append(u)
        elif u.lower().startswith("mailto:"):
            out["mailto"].append(u)
    return out


def _public_host(url: str) -> bool:
    """Only an https URL whose host resolves to public addresses: a mail
    header must not steer Friday into this machine or its network."""
    try:
        parts = urlsplit(url)
        if parts.scheme.lower() != "https" or not parts.hostname:
            return False
        port = parts.port or 443
        infos = socket.getaddrinfo(parts.hostname, port, proto=socket.IPPROTO_TCP)
    except Exception:
        return False
    if not infos:
        return False
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if not ip.is_global:
            return False
    return True


def _mailto(u: str) -> dict:
    parts = urlsplit(u)
    q = parse_qs(parts.query)
    return {"to": unquote(parts.path or ""),
            "subject": (q.get("subject") or ["unsubscribe"])[0],
            "body": (q.get("body") or ["unsubscribe"])[0]}


def _headers(account_id: str, message_id: str) -> dict:
    from agent_friday.services import gmail_mailbox as gm
    svc = gm._read_svc(account_id)
    msg = gmail_api.execute(svc.users().messages().get(
        userId="me", id=message_id, format="metadata",
        metadataHeaders=["From", "List-Unsubscribe", "List-Unsubscribe-Post"]))
    return {h["name"].lower(): h["value"] for h in (msg.get("payload") or {}).get("headers") or []}


def options(account_id: str, message_id: str) -> dict:
    """What leaving this list would involve, for the confirm dialog."""
    h = _headers(account_id, message_id)
    ways = parse_header(h.get("list-unsubscribe", ""))
    one_click = bool(ways["https"]) and "list-unsubscribe=one-click" in (h.get("list-unsubscribe-post") or "").lower()
    method = "one_click" if one_click else "mailto" if ways["mailto"] else "link" if ways["https"] else None
    return {"status": "ok" if method else "none", "method": method, "sender": h.get("from", ""),
            "mailto": _mailto(ways["mailto"][0]) if ways["mailto"] else None,
            "url": ways["https"][0] if ways["https"] and not one_click else None,
            "host": urlsplit(ways["https"][0]).hostname if ways["https"] else None}


def unsubscribe(account_id: str, message_id: str) -> dict:
    """Do the part Friday can do itself. -> {"status": "done" | "next" | "none" | "error", ...}"""
    opt = options(account_id, message_id)
    if opt["method"] == "one_click":
        h = _headers(account_id, message_id)
        url = parse_header(h.get("list-unsubscribe", ""))["https"][0]
        if not _public_host(url):
            return {"status": "error", "method": "one_click",
                    "message": "The list's unsubscribe address is not a public web address, so Friday did not contact it."}
        import requests
        try:
            r = requests.post(url, data="List-Unsubscribe=One-Click", timeout=12, allow_redirects=False,
                              headers={"Content-Type": "application/x-www-form-urlencoded",
                                       "User-Agent": "Friday (List-Unsubscribe one-click)"})
        except Exception as e:
            return {"status": "error", "method": "one_click", "message": "The list did not answer: %s" % e}
        if 200 <= r.status_code < 400:
            return {"status": "done", "method": "one_click", "host": opt["host"],
                    "message": "Unsubscribed: %s accepted the request." % opt["host"]}
        return {"status": "error", "method": "one_click",
                "message": "The list refused the request (HTTP %d)." % r.status_code}
    if opt["method"] == "mailto":
        return {"status": "next", "method": "mailto", "mailto": opt["mailto"],
                "message": "This list unsubscribes by email. Friday has written the message; sending it asks for your approval."}
    if opt["method"] == "link":
        return {"status": "next", "method": "link", "url": opt["url"], "host": opt["host"],
                "message": "This list unsubscribes on its own web page."}
    return {"status": "none", "message": "This message does not say how to unsubscribe."}

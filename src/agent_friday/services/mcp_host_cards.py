"""The card that asks the owner to let Friday reach a remote add-on (MCP) server's host.

governance/mcp_hosts.py refuses any remote server host the owner has not allowed. This
module raises the card that asks, once per host, and applies the answer. The card says
what the server is called, where it lives, and what reaching it means. Only a press on
screen approves it (approvals.SCREEN_ONLY_KINDS); approving adds the host to the signed
list and starts the server.

A declined card is not raised again by itself at every start; restarting the server from
Settings asks again (``ask(..., again=True)``).
"""
from __future__ import annotations

import logging
import sys
import threading
import time
from urllib.parse import urlsplit, urlunsplit

KIND = "mcp_host"
SUBJECT_TYPE = "mcp_host"

_log = logging.getLogger("friday.mcp_host_cards")
_HOOKED = {"done": False}
_LOCK = threading.Lock()


def _ap():
    from agent_friday.services import approvals
    return approvals


def _shown_url(url: str) -> str:
    """Scheme and host only: a path or query can carry a key (some services put the
    secret in the path), so neither is shown or stored on the card."""
    try:
        p = urlsplit(str(url or ""))
        netloc = p.hostname or ""
        if ":" in netloc:
            netloc = "[%s]" % netloc
        if p.port:
            netloc += ":%d" % p.port
        return urlunsplit((p.scheme, netloc, "", "", ""))[:300]
    except Exception:
        return ""


def _latest(key: str):
    cards = [r for r in _ap().list_approvals(kind=KIND)
             if (r.get("payload") or {}).get("host") == key]
    return cards[0] if cards else None          # newest first


def ask(url: str, server: str, *, again: bool = False) -> dict:
    """Raise (or return) the card for this URL's host. Changes nothing by itself.

    Returns {"state": "pending"|"declined"|"approved"|"none", "approval_id"}."""
    from agent_friday.governance import mcp_hosts
    key = mcp_hosts.host_key(url)
    if not key:
        return {"state": "none", "approval_id": ""}
    ensure_hook()
    with _LOCK:
        last = _latest(key)
        status = (last or {}).get("status")
        if last and status == "pending":
            return {"state": "pending", "approval_id": last["approval_id"]}
        if last and status in ("denied", "declined") and not again:
            return {"state": "declined", "approval_id": last["approval_id"]}
        if last and status == "blocked" and not again:
            return {"state": "blocked", "approval_id": last["approval_id"]}
        # An expired card was never answered: the next start asks again.
        where = mcp_hosts.display(key)
        name = str(server or "an add-on")[:80]
        card = _ap().create_approval(
            kind=KIND, subject_type=SUBJECT_TYPE,
            subject_id="%s@%d" % (key, time.time_ns()),
            title="Let Friday reach the add-on server at %s?" % where,
            description=(
                "The add-on \"%s\" lives at %s. If you approve, Friday may connect to %s, "
                "list the tools it offers, and send it what each call made with those tools "
                "contains, such as text you ask Friday to work on, along with the add-on's "
                "saved headers and its sign-in token. Each call still follows your approval "
                "rules. Friday reaches no add-on server at a host you have not allowed. "
                "Removing the add-on stops Friday reaching it."
                % (name, _shown_url(url) or where, where)),
            action_description="Allow Friday to reach add-on server host %s" % where,
            payload={"host": key, "server": str(server or "")[:200], "url": _shown_url(url)},
            requested_by="friday:mcp", action_class="outward", force_gate=True)
        return {"state": "pending" if card.get("status") == "pending" else str(card.get("status")),
                "approval_id": card.get("approval_id", "")}


def _restart_servers(key: str) -> None:
    """Start every configured server at this host now that it is allowed (only if Friday's
    agent is loaded)."""
    from agent_friday.governance import mcp_hosts
    agent = sys.modules.get("agent_friday.services.agent")
    mgr = getattr(agent, "_MCP_MANAGER", None) if agent else None
    if mgr is None:
        return
    for name, sp in list(getattr(mgr, "servers", {}).items()):
        if mcp_hosts.host_key(getattr(sp, "url", "") or "") != key:
            continue
        try:
            threading.Thread(target=mgr.restart, args=(name,),
                             kwargs={"on_ready": getattr(agent, "_mcp_register_server_tools", None)},
                             name="mcp-%s-allowed" % name, daemon=True).start()
        except Exception as e:
            _log.info("add-on server restart after approval failed: %s", type(e).__name__)


def apply_approved(record: dict) -> dict:
    """Add the card's host to the owner's list. Called by the decision hook; only an approved
    card of this kind, decided by a press on screen, gets through."""
    ap = _ap()
    if record.get("kind") != KIND or record.get("status") != "approved":
        return {"ok": False}
    if record.get("decided_by") not in ap.SCREEN_ONLY_KINDS.get(KIND, frozenset()):
        return {"ok": False}
    if not ap.claim_for_execution(record["approval_id"]):
        return {"ok": False}
    from agent_friday.governance import mcp_hosts
    payload = record.get("payload") or {}
    try:
        row = mcp_hosts.approve(payload.get("host", ""), made_by=record["decided_by"],
                                server=payload.get("server", ""),
                                approval_id=record["approval_id"])
    except Exception as e:
        ap.mark_used(record["approval_id"], "mcp_hosts", {"ok": False, "error": str(e)[:200]})
        return {"ok": False, "error": str(e)}
    ap.mark_used(record["approval_id"], "mcp_hosts", {"ok": True, "host": row.get("host")})
    _restart_servers(str(row.get("host") or ""))
    return {"ok": True, "host": row.get("host")}


def _on_decision(record: dict) -> None:
    if record.get("status") == "approved":
        apply_approved(record)


def ensure_hook() -> None:
    """Register the decision hook once per process (also called at MCP start-up, so a card
    raised before a restart is still applied when it is approved after it)."""
    hooks = getattr(_ap(), "_HOOKS", {})
    if _HOOKED["done"] and _on_decision in hooks.get(KIND, []):
        return
    _ap().register_decision_hook(KIND, _on_decision)
    _HOOKED["done"] = True

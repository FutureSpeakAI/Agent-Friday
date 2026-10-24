"""The card that asks the owner to review a connector's tools (BD-07, A30).

governance/tool_manifests withholds a connector tool that has no manifest (new installs, and
connectors added from now on), and lets a connector from before A30 keep working until the
owner answers. This module raises one card per connector and applies the answer, in the A25
pattern:

* The card lists each tool with its action class, the domains it may reach and its sensitive
  arguments. Only a press in Friday's window on this PC answers it
  (approvals.SCREEN_ONLY_KINDS).
* "Add a manifest" (approve) signs the reviewed manifests and restarts the connector so its
  tools are offered. Only tools whose description is still the one shown on the card count.
* "Turn it off" (deny) turns a connector from before A30 off; the receipt is written before
  the config changes. For a new connector, its tools simply stay unavailable.
* A card already waiting is not raised twice; an expired card is raised again on a new card;
  an answered card is not raised again at every start (``ask(..., again=True)`` asks anew,
  e.g. when the connector is restarted from Settings).
"""
from __future__ import annotations

import logging
import sys
import threading
import time
from typing import Any, Dict, List

KIND = "tool_manifest"
SUBJECT_TYPE = "mcp_server"

_log = logging.getLogger("friday.tool_manifest_cards")
_HOOKED = {"done": False}
_LOCK = threading.Lock()
_APPLIED: set = set()                            # declines applied in this process


def _ap():
    from agent_friday.services import approvals
    return approvals


def _latest(server: str):
    cards = [r for r in _ap().list_approvals(kind=KIND)
             if (r.get("payload") or {}).get("server") == server]
    return cards[0] if cards else None          # newest first


#: The most tools one card asks about; the rest get the next card once these are answered.
MAX_PER_CARD = 40


def _plain(text: Any, n: int) -> str:
    """One line of the server's own words, shown as data: no line breaks or control marks."""
    t = "".join(ch if ch.isprintable() else " " for ch in str(text or ""))
    return " ".join(t.split())[:n]


def _line(m: Dict[str, Any]) -> str:
    bits = ["\"%s\" (Friday's guess: %s)" % (_plain(m.get("tool"), 80),
                                            str(m.get("action_class") or "outward").lower())]
    if m.get("description"):
        bits.append("it says: \"%s\"" % _plain(m["description"], 300))
    if m.get("domains"):
        bits.append("reaches " + ", ".join(m["domains"]))
    if m.get("sensitive_args"):
        bits.append("takes " + ", ".join(_plain(a, 40) for a in m["sensitive_args"]))
    return "; ".join(bits)


def ask(server: str, manifests: List[Dict[str, Any]], *, grandfathered: bool,
        again: bool = False) -> dict:
    """Raise (or return) the card for this connector. Changes nothing by itself.
    Returns {"state": "pending"|"declined"|"approved"|"blocked"|"none", "approval_id"}."""
    if not server or not manifests:
        return {"state": "none", "approval_id": ""}
    ensure_hook()
    manifests = manifests[:MAX_PER_CARD]          # approve only what the card shows
    shas = sorted(str(m.get("schema_sha")) for m in manifests)
    with _LOCK:
        last = _latest(server)
        if last and status_changed(last, shas):
            # Something the owner has not seen (a new or changed tool): ask about it, but only
            # once per change, not at every restart.
            again = True
        status = (last or {}).get("status")
        if last and status == "pending":
            return {"state": "pending", "approval_id": last["approval_id"]}
        pressed = (last or {}).get("decided_by") in _ap().SCREEN_ONLY_KINDS.get(KIND, frozenset())
        if last and status in ("denied", "declined") and not pressed:
            last = None                  # not answered by a press on this PC: ask again
            status = None
        if last and status in ("denied", "declined", "blocked") and not again:
            return {"state": "declined" if status != "blocked" else "blocked",
                    "approval_id": last["approval_id"]}
        if last and status in ("approved", "used") and not again:
            # Answered: tools that changed since are shown as unavailable; restarting the
            # connector from Settings asks again.
            return {"state": "approved", "approval_id": last["approval_id"]}
        name = _plain(server, 80)
        lines = "\n".join("- " + _line(m) for m in manifests)
        more = ""
        if grandfathered:
            title = "Review the add-on \"%s\": add a manifest, or turn it off?" % name
            what = ("The add-on \"%s\" was set up before Friday asked for tool manifests. It "
                    "keeps working until you answer. Add a manifest to keep it, after checking "
                    "what each tool does; or turn it off." % name)
        else:
            title = "Review the add-on \"%s\" before Friday uses its tools?" % name
            what = ("The add-on \"%s\" has no manifest yet, so Friday does not offer its tools. "
                    "Approve to add one, after checking what each tool does." % name)
        card = _ap().create_approval(
            kind=KIND, subject_type=SUBJECT_TYPE, subject_id="%s@%d" % (server, time.time_ns()),
            title=title,
            description=(what + "\n\nIts tools:\n" + lines + more +
                         "\n\nEach call still follows your approval rules. If a tool's "
                         "description changes later, Friday stops offering it until you "
                         "review it again."),
            action_description=("Add manifests for the tools of add-on %s" % name),
            payload=_sealed_payload({"server": str(server)[:200], "grandfathered": bool(grandfathered),
                                     "manifests": manifests, "shas": shas}),
            requested_by="friday:mcp", action_class="outward", force_gate=True)
        return {"state": "pending" if card.get("status") == "pending" else str(card.get("status")),
                "approval_id": card.get("approval_id", "")}


def status_changed(last: dict, shas: List[str]) -> bool:
    """The tools now are not the ones the last card asked about (and it was answered)."""
    if (last or {}).get("status") == "pending":
        return False
    return sorted((last.get("payload") or {}).get("shas") or []) != shas


def _sealed_payload(payload: Dict[str, Any]) -> Dict[str, Any]:
    from agent_friday.governance import tool_manifests
    payload["seal"] = tool_manifests.seal(payload)
    return payload


def _restart(server: str) -> None:
    agent = sys.modules.get("agent_friday.services.agent")
    mgr = getattr(agent, "_MCP_MANAGER", None) if agent else None
    if mgr is None or server not in getattr(mgr, "servers", {}):
        return
    try:
        threading.Thread(target=mgr.restart, args=(server,),
                         kwargs={"on_ready": getattr(agent, "_mcp_register_server_tools", None)},
                         name="mcp-%s-reviewed" % server, daemon=True).start()
    except Exception as e:
        _log.info("add-on restart after review failed: %s", type(e).__name__)


def _turn_off(server: str) -> None:
    agent = sys.modules.get("agent_friday.services.agent")
    if agent is None:
        from agent_friday.services import agent  # noqa: F811
    cfg = agent._load_mcp_servers()
    servers = cfg.get("servers", cfg)
    spec = servers.get(server)
    if isinstance(spec, dict):
        spec["enabled"] = False
        agent._save_mcp_servers(cfg)
    mgr = getattr(agent, "_MCP_MANAGER", None)
    try:
        agent._mcp_unregister_server_tools(server)
        sp = (getattr(mgr, "servers", {}) or {}).get(server) if mgr is not None else None
        if sp is not None:
            sp.stop()                                  # the program or session ends now
            try:
                sp.status = "disabled"
            except Exception:
                pass
    except Exception as e:
        _log.info("add-on stop after review failed: %s", type(e).__name__)


def apply(record: dict) -> dict:
    """Apply an answered card. Only a press on screen counts; the receipt is written before
    anything changes."""
    ap = _ap()
    if record.get("kind") != KIND or record.get("status") not in ("approved", "denied"):
        return {"ok": False}
    if record.get("decided_by") not in ap.SCREEN_ONLY_KINDS.get(KIND, frozenset()):
        return {"ok": False}
    approved = record.get("status") == "approved"
    if approved:
        if not ap.claim_for_execution(record["approval_id"]):
            return {"ok": False}
    else:
        # A decline is applied once: an approval's claim does not exist for it.
        with _LOCK:
            if record["approval_id"] in _APPLIED or record.get("used_by"):
                return {"ok": False}
            _APPLIED.add(record["approval_id"])
    from agent_friday.governance import action_gate, tool_manifests
    payload = record.get("payload") or {}
    server = str(payload.get("server") or "")
    if not tool_manifests.sealed(payload):
        # The card's contents changed after it was raised: nothing is signed or turned off.
        ap.mark_used(record["approval_id"], "tool_manifests", {"ok": False, "error": "card altered"})
        return {"ok": False, "error": "the card was altered after it was raised"}
    try:
        action_gate._receipt({"tool": "tool_manifest", "class": action_gate.INTERNAL,
                              "decision": "add_manifest" if approved else
                              ("turn_off" if payload.get("grandfathered") else "keep_unavailable"),
                              "surface": "screen", "chosen_by": record["decided_by"],
                              "target": server[:120], "approval_id": record["approval_id"]})
        if approved:
            rows = [m for m in (payload.get("manifests") or [])
                    if isinstance(m, dict) and m.get("server") == server and m.get("tool")]
            n = tool_manifests.write(rows, made_by=record["decided_by"],
                                     approval_id=record["approval_id"])
            tool_manifests.release_grandfather(server, why="manifest added")
            ap.mark_used(record["approval_id"], "tool_manifests", {"ok": True, "added": n})
            _restart(server)
            return {"ok": True, "added": n}
        if payload.get("grandfathered"):
            tool_manifests.release_grandfather(server, why="turned off")
            _turn_off(server)
        ap.mark_used(record["approval_id"], "tool_manifests", {"ok": True, "turned_off": True})
        return {"ok": True, "turned_off": bool(payload.get("grandfathered"))}
    except Exception as e:  # noqa: BLE001
        ap.mark_used(record["approval_id"], "tool_manifests", {"ok": False, "error": str(e)[:200]})
        return {"ok": False, "error": str(e)}


def _on_decision(record: dict) -> None:
    if record.get("status") in ("approved", "denied"):
        apply(record)


def ensure_hook() -> None:
    hooks = getattr(_ap(), "_HOOKS", {})
    if _HOOKED["done"] and _on_decision in hooks.get(KIND, []):
        return
    _ap().register_decision_hook(KIND, _on_decision)
    _HOOKED["done"] = True

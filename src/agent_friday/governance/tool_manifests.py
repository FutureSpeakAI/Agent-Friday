"""governance/tool_manifests.py -- every tool Friday offers has a manifest (BD-07, A30).

North Star 23.4-1: every tool has a typed manifest (version, provider, schemas, ring, action
class, idempotency, verification, domains, sensitive arguments). 23.5-1: a tool that cannot
run is shown as an unavailable capability with the reason. 23.11-2: tool descriptions and
manifests are trusted only after installation review and signature validation.

Two kinds of tool:

* Friday's own tools. Their manifest is built from the release itself (the tool's schema in
  services/agent.CLAUDE_TOOLS, its ring, the action checkpoint's class): ``builtin(name)``.
  A test fails the build if one has none.
* Connector (MCP) tools. A connector's tools get their manifest at the owner's review: the
  card (services/tool_manifest_cards) lists each tool with its class, domains and sensitive
  arguments, and a press on this PC approves it. The rows are signed here with the
  governance key. A row whose signature does not verify is no manifest at all, and a tool
  whose description or schema changed since its review is unavailable until reviewed again.

The owner's ruling (A30): on a new install, and for any connector added from now on, a tool
without a manifest is not offered to the model; it is listed as unavailable with the reason.
A connector configured before A30 on an existing install keeps working unchanged until the
owner answers its one card ("Add a manifest" or "Turn it off"), as in A25: those connectors
are recorded once, at the first boot with this list (``carry_over_at_boot``), in a signed
row, and the list is never refilled from the config afterwards.
"""
from __future__ import annotations

import hashlib
import hmac as _hmac
import json
import os
import re
import threading
import time
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlsplit

#: Who may write a manifest row: a press on Friday's own window on this PC.
MAKERS = ("owner:ui",)
GRANDFATHERED = "__grandfathered__"          # the signed list of connectors from before A30
_LOCK = threading.RLock()
#: server -> {raw tool name: reason}, for the status page and the Doctor (this process only).
_UNAVAILABLE: Dict[str, Dict[str, str]] = {}

_SENSITIVE = re.compile(r"pass(word|phrase)?|secret|token|api[_-]?key|private[_-]?key|card|cvv|"
                        r"ssn|iban|account[_-]?number|credential", re.I)


class ManifestRefused(ValueError):
    pass


def _dir():
    from agent_friday.governance.action_gate import _gov_dir
    return _gov_dir()


def _path():
    return _dir() / "tool_manifests.json"


def _history_path():
    return _dir() / "tool_manifests_history.jsonl"


def _key() -> bytes:
    from agent_friday.governance.action_gate import _governance_key
    return _governance_key()


def _sign(row: Dict[str, Any]) -> str:
    body = {k: v for k, v in row.items() if k != "sig"}
    canonical = json.dumps(body, sort_keys=True, default=str).encode("utf-8")
    return _hmac.new(_key(), canonical, hashlib.sha256).hexdigest()


def _verified(row: Any) -> bool:
    if not isinstance(row, dict) or not isinstance(row.get("sig"), str):
        return False
    try:
        return _hmac.compare_digest(row["sig"], _sign(row))
    except Exception:
        return False


def _read_raw() -> Optional[Dict[str, Any]]:
    """{} when there is no list yet; None when it cannot be read (then nothing verifies)."""
    p = _path()
    if not p.exists():
        return {}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else None
    except Exception:
        return None


def _write_raw(raw: Dict[str, Any]) -> None:
    p = _path()
    tmp = p.with_name(p.name + ".tmp")
    tmp.write_text(json.dumps(raw, indent=1, sort_keys=True, default=str), encoding="utf-8")
    os.replace(tmp, p)


def _history(event: Dict[str, Any]) -> None:
    entry = dict(event, at=time.time())
    entry["sig"] = _sign(entry)
    with open(_history_path(), "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, default=str) + "\n")


# ── what a tool is ───────────────────────────────────────────────────────────

def schema_sha(tool: Dict[str, Any]) -> str:
    """The fingerprint the owner reviewed: the tool's name, description and input schema."""
    body = {"name": tool.get("name"), "description": tool.get("description") or "",
            "schema": tool.get("inputSchema") or tool.get("input_schema") or {}}
    return hashlib.sha256(json.dumps(body, sort_keys=True, default=str).encode("utf-8")).hexdigest()


def sensitive_args(tool: Dict[str, Any]) -> List[str]:
    schema = tool.get("inputSchema") or tool.get("input_schema") or {}
    props = schema.get("properties") if isinstance(schema, dict) else None
    return sorted(str(k) for k in (props or {}) if _SENSITIVE.search(str(k)))[:20]


def _domains(spec: Dict[str, Any]) -> List[str]:
    url = str((spec or {}).get("url") or "")
    if not url:
        return []                                  # a program on this PC
    try:
        from agent_friday.governance import mcp_hosts
        k = mcp_hosts.host_key(url)
        return [mcp_hosts.display(k)] if k else []
    except Exception:
        return [urlsplit(url).hostname or ""]


def _classify(full_name: str) -> str:
    try:
        from agent_friday.governance import action_gate
        return str(action_gate.classify(full_name, {})[0])
    except Exception:
        return "outward"                           # action_gate.OUTWARD: unknown is outward


def describe(server: str, tool: Dict[str, Any], *, full_name: str = "", spec: Optional[dict] = None,
             ring: int = 2, version: str = "") -> Dict[str, Any]:
    """The typed manifest (23.4-1) for one connector tool, unsigned: what the card shows."""
    spec = spec or {}
    return {"kind": "connector", "server": str(server)[:120], "tool": str(tool.get("name") or "")[:200],
            "description": " ".join(str(tool.get("description") or "").split())[:400],
            "version": str(version or "")[:60],
            "provider": ("%s (%s)" % (server, (_domains(spec) or ["this PC"])[0]))[:200],
            "schema_sha": schema_sha(tool), "ring": int(ring),
            "action_class": _classify(full_name) if full_name else "outward",
            "idempotency": "unknown", "verification": "owner review on this PC",
            "domains": _domains(spec), "sensitive_args": sensitive_args(tool)}


def builtin(name: str) -> Optional[Dict[str, Any]]:
    """The manifest of one of Friday's own tools, built from the release; None if the name is
    not one of hers."""
    import sys
    agent = sys.modules.get("agent_friday.services.agent")
    if agent is None or str(name).startswith("mcp_"):
        return None
    entry = next((t for t in getattr(agent, "CLAUDE_TOOLS", []) if t.get("name") == name), None)
    if entry is None:
        return None
    try:
        from agent_friday import __version__ as version
    except Exception:
        version = ""
    rings = getattr(agent, "TOOL_RINGS", {})
    return {"kind": "builtin", "server": "", "tool": name, "version": str(version),
            "provider": "friday", "schema_sha": schema_sha(entry),
            "ring": int(rings.get(name, 1)), "action_class": _classify(name),
            "idempotency": "declared by the tool's code", "verification": "shipped in the release",
            "domains": [], "sensitive_args": sensitive_args(entry)}


# ── the signed list ─────────────────────────────────────────────────────────

def config_fingerprint(spec: Any) -> str:
    """What a connector runs or reaches (command, arguments, address; never its secrets): a
    connector from before A30 stays trusted only while this is unchanged."""
    spec = spec if isinstance(spec, dict) else {}
    body = {"command": spec.get("command") or "", "args": spec.get("args") or [],
            "url": spec.get("url") or "", "cwd": spec.get("cwd") or ""}
    return hashlib.sha256(json.dumps(body, sort_keys=True, default=str).encode("utf-8")).hexdigest()


def _row_key(server: str, tool: str) -> str:
    return "%s\x1f%s" % (server, tool)


def manifest(server: str, tool_name: str) -> Optional[Dict[str, Any]]:
    """The signed manifest row for a connector tool, or None (none, unreadable, or forged)."""
    raw = _read_raw()
    if not raw:
        return None
    row = raw.get(_row_key(server, tool_name))
    if not _verified(row) or row.get("server") != server or row.get("tool") != tool_name:
        return None
    return row


def write(rows: List[Dict[str, Any]], *, made_by: str, approval_id: str = "") -> int:
    """Sign and store reviewed manifests. Only a press on this PC may (MAKERS)."""
    if made_by not in MAKERS:
        raise ManifestRefused("a tool manifest is added only by your press in Friday's window on this PC")
    with _LOCK:
        raw = _read_raw()
        if raw is None:
            raise ManifestRefused("the manifest list could not be read; nothing was changed")
        n = 0
        for r in rows:
            row = dict(r, made_by=made_by, made_at=time.time(), approval_id=str(approval_id)[:80])
            row.pop("sig", None)
            row["sig"] = _sign(row)
            raw[_row_key(row["server"], row["tool"])] = row
            _history({"event": "manifest_added", "server": row["server"], "tool": row["tool"],
                      "approval_id": row["approval_id"]})
            n += 1
        _write_raw(raw)
    return n


def _grandfathered_row(raw: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    row = (raw or {}).get(GRANDFATHERED)
    return row if _verified(row) and row.get("tool") == GRANDFATHERED else None


def grandfathered(server: str, spec: Any = None) -> bool:
    """A connector configured before A30 on an existing install, not yet answered, and still
    running or reaching what it did then (``spec``: its current config; None skips that check,
    for display only)."""
    row = _grandfathered_row(_read_raw() or {})
    servers = (row or {}).get("servers") or {}
    if not isinstance(servers, dict) or server not in servers:
        return False
    return spec is None or servers[server] == config_fingerprint(spec)


def carry_over_at_boot(config: Any = None) -> int:
    """Once ever, at the first boot with this list: on an existing install, the connectors
    configured now are recorded as from before A30 (they keep working until their card is
    answered). On a new install nothing is. Never refilled from the config afterwards."""
    with _LOCK:
        raw = _read_raw()
        if raw is None or GRANDFATHERED in raw or _history_path().exists():
            return 0
        from agent_friday import core
        try:
            if core.install_choices_state().get("tool_manifests_carried_over"):
                return 0                           # it ran once already: never again
            # The marker goes first: a carry-over that fails part-way is not retried at a later
            # boot with whatever is configured then (it fails closed: those connectors ask).
            core.update_install_choices_state(tool_manifests_carried_over=time.time())
        except Exception:
            return 0
        servers: Dict[str, str] = {}
        try:
            existing = core.install_origin() == core.EXISTING_INSTALL
        except Exception:
            existing = True                        # as core: an unreadable record is EXISTING
        if existing:
            cfg = config if isinstance(config, dict) else {}
            conf = cfg.get("servers", cfg) if isinstance(cfg, dict) else {}
            servers = {str(n): config_fingerprint(s) for n, s in sorted((conf or {}).items())
                       if isinstance(s, dict) and s.get("enabled") is not False}
        row = {"tool": GRANDFATHERED, "server": "", "servers": servers, "made_by": "carried_over",
               "made_at": time.time(), "existing_install": bool(existing)}
        row["sig"] = _sign(row)
        raw[GRANDFATHERED] = row
        _write_raw(raw)
        _history({"event": "carried_over", "servers": servers, "existing_install": bool(existing)})
        return len(servers)


def release_grandfather(server: str, *, why: str) -> None:
    """The owner answered the connector's card: it is no longer 'from before A30'."""
    with _LOCK:
        raw = _read_raw()
        row = _grandfathered_row(raw or {})
        if raw is None or not row or server not in (row.get("servers") or {}):
            return
        row = dict(row, servers={k: v for k, v in row["servers"].items() if k != server})
        row.pop("sig", None)
        row["sig"] = _sign(row)
        raw[GRANDFATHERED] = row
        _write_raw(raw)
        _history({"event": "answered", "server": server, "why": why[:80]})


# ── the decision ─────────────────────────────────────────────────────────────

NO_MANIFEST = ("it has no manifest yet: review its tools on the card in Needs you to add one "
               "(BD-07, A30)")
CHANGED = ("its description or inputs changed since you reviewed it: review it again on the "
           "card in Needs you")


def check(server: str, tool: Dict[str, Any], spec: Any = None) -> Tuple[bool, str]:
    """(may be offered, reason). Never raises: a check that cannot run withholds the tool
    (for a connector from before A30 too: a list that cannot be read or verified trusts
    nothing). ``spec`` is the connector's current config."""
    name = str(tool.get("name") or "")
    try:
        row = manifest(server, name)
        if row is not None:
            if row.get("schema_sha") == schema_sha(tool):
                return True, ""
            return False, CHANGED
        if grandfathered(server, spec if spec is not None else {}):
            return True, "from before A30: works until you answer its card"
        return False, NO_MANIFEST
    except Exception as e:  # noqa: BLE001
        return False, "its manifest could not be checked (%s)" % type(e).__name__


def note_unavailable(server: str, withheld: Dict[str, str]) -> None:
    with _LOCK:
        if withheld:
            _UNAVAILABLE[server] = dict(withheld)
        else:
            _UNAVAILABLE.pop(server, None)


def unavailable(server: Optional[str] = None) -> Dict[str, Dict[str, str]]:
    with _LOCK:
        if server is not None:
            return {server: dict(_UNAVAILABLE.get(server, {}))}
        return {k: dict(v) for k, v in _UNAVAILABLE.items()}


def seal(payload: Dict[str, Any]) -> str:
    """A signature over a card's payload, so what the owner approves is what gets signed."""
    return _sign({"card": {k: v for k, v in payload.items() if k != "seal"}})


def sealed(payload: Dict[str, Any]) -> bool:
    try:
        return _hmac.compare_digest(str(payload.get("seal") or ""), seal(payload))
    except Exception:
        return False


def _reset_for_tests() -> None:
    with _LOCK:
        _UNAVAILABLE.clear()

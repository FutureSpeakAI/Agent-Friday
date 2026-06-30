"""
services/connectors.py — Friday's one-click connector registry.

A single, unified abstraction over every external integration Friday can speak
to, so the UI gets ONE consistent surface (icon, name, status badge, Connect
button) regardless of whether the integration underneath is a Google OAuth
grant or a standard MCP server.

Two connector *kinds* are supported today:

  • "oauth"  — Google (Gmail + Calendar, read-only). Status comes from the live
               OAuth token; one-click Connect drives the existing loopback
               consent flow (scripts/friday_google_connect.py) or returns the
               web auth URL. This is the WORKING TEMPLATE every other connector
               aspires to.

  • "mcp"    — Slack / GitHub / Linear / Notion / Discord. Each ships a config
               stub (command + args + the env tokens it needs). Connect writes
               the user's token into ~/.friday/mcp_servers.json, enables the
               server, and hot-reloads the MCP manager; status then reflects the
               live MCP handshake. These are real Model-Context-Protocol servers
               — the same protocol Claude Desktop uses — so once connected their
               tools register into Friday's agent automatically.

Everything here fails soft: a missing dependency, an unreachable MCP manager, or
an absent token degrades to a clear status string, never an exception that could
wedge a request or the boot sequence.

Public surface (consumed by routes/connectors.py + briefings + ambient health):
  list_connectors()            -> [status dict, ...]
  get_connector(key)           -> status dict | None
  connect_connector(key, data) -> {ok, status, message, ...}
  disconnect_connector(key)    -> {ok, status, message}
  connectors_health()          -> {summary, connectors:[...], degraded:[...]}
  connector_intelligence()     -> {sections:[...], markdown, signals:{...}}
  monitor_connector_health()   -> fires notifications on connected->down edges
"""

from __future__ import annotations

import os
import json
import sys
import subprocess
import threading
import time as _time
import logging
from pathlib import Path

_log = logging.getLogger("friday.connectors")

HOME = Path(os.path.expanduser("~"))
FRIDAY_DIR = HOME / ".friday"
REPO_DIR = Path(__file__).resolve().parents[3]  # connectors.py is src/agent_friday/services/ → repo root

# Spawn child processes without flashing a console window on Windows.
_POPEN_FLAGS = getattr(subprocess, "CREATE_NO_WINDOW", 0) if sys.platform == "win32" else 0


# ══════════════════════════════════════════════════════════════════════════
#  REGISTRY  —  the declarative source of truth
# ══════════════════════════════════════════════════════════════════════════
# Each entry is purely declarative: it names the connector, what it powers, and
# (for MCP connectors) the server template + the credential fields the user must
# supply. The status/connect/disconnect logic below reads these defs — adding a
# new connector is a matter of appending one dict here.
#
# `workspaces` lists the Friday workspace ids this connector feeds, which drives
# the "connector-aware workspace" badges in the UI and lets a workspace show
# whether its data source is live.

CONNECTOR_DEFS: dict[str, dict] = {
    # ── Google — the working OAuth template ───────────────────────────────
    "google": {
        "name": "Google",
        "icon": "📧",
        "category": "Productivity",
        "kind": "oauth",
        "blurb": "Gmail + Calendar, read-only. Powers your morning briefing, "
                 "calendar, and unread-email signals.",
        "capabilities": ["gmail", "calendar"],
        "workspaces": ["home", "messages", "calendar", "news"],
        "setup_hint": "One-click — opens your browser to approve read-only "
                      "access. No tokens to copy/paste.",
        "docs_url": "https://console.cloud.google.com/apis/credentials",
        "fields": [],   # OAuth — nothing to type
    },

    # ── MCP connector stubs ───────────────────────────────────────────────
    "slack": {
        "name": "Slack",
        "icon": "💬",
        "category": "Communication",
        "kind": "mcp",
        "blurb": "Read channels, search messages, and post updates from your "
                 "workspace via the official Slack MCP server.",
        "capabilities": ["channels", "messages", "search"],
        "workspaces": ["messages", "home"],
        "mcp_server": "slack",
        "mcp_template": {
            "command": "npx",
            "args": ["-y", "@modelcontextprotocol/server-slack"],
            "env": {},
        },
        "fields": [
            {"key": "SLACK_BOT_TOKEN", "label": "Bot Token", "secret": True,
             "placeholder": "xoxb-…", "required": True},
            {"key": "SLACK_TEAM_ID", "label": "Team ID", "secret": False,
             "placeholder": "T01234567", "required": True},
        ],
        "docs_url": "https://api.slack.com/apps",
        "setup_hint": "Create a Slack app, add the bot scopes, and paste its "
                      "Bot Token + your Team ID.",
    },
    "github": {
        "name": "GitHub",
        "icon": "🐙",
        "category": "Development",
        "kind": "mcp",
        "blurb": "Issues, pull requests, and repo search through the official "
                 "GitHub MCP server. Surfaces PRs awaiting your review.",
        "capabilities": ["issues", "pull_requests", "repos", "search"],
        "workspaces": ["code", "career", "home"],
        "mcp_server": "github",
        "mcp_template": {
            "command": "npx",
            "args": ["-y", "@modelcontextprotocol/server-github"],
            "env": {},
        },
        "fields": [
            {"key": "GITHUB_PERSONAL_ACCESS_TOKEN", "label": "Personal Access Token",
             "secret": True, "placeholder": "ghp_…", "required": True},
        ],
        "docs_url": "https://github.com/settings/tokens",
        "setup_hint": "Create a fine-grained PAT with repo + read:org scopes "
                      "and paste it here.",
    },
    "linear": {
        "name": "Linear",
        "icon": "📐",
        "category": "Development",
        "kind": "mcp",
        "blurb": "Issues, projects, and cycles from Linear. Pulls your assigned "
                 "issues into the morning briefing.",
        "capabilities": ["issues", "projects", "cycles"],
        "workspaces": ["code", "career", "home"],
        "mcp_server": "linear",
        "mcp_template": {
            "command": "npx",
            "args": ["-y", "@tacticlaunch/mcp-linear"],
            "env": {},
        },
        "fields": [
            {"key": "LINEAR_API_KEY", "label": "API Key", "secret": True,
             "placeholder": "lin_api_…", "required": True},
        ],
        "docs_url": "https://linear.app/settings/api",
        "setup_hint": "Generate a personal API key in Linear settings and paste "
                      "it here.",
    },
    "notion": {
        "name": "Notion",
        "icon": "📔",
        "category": "Productivity",
        "kind": "mcp",
        "blurb": "Search and read your Notion workspace — notes, docs, and "
                 "databases — through the official Notion MCP server.",
        "capabilities": ["pages", "databases", "search"],
        "workspaces": ["wiki", "content", "home"],
        "mcp_server": "notion",
        "mcp_template": {
            "command": "npx",
            "args": ["-y", "@notionhq/notion-mcp-server"],
            "env": {},
        },
        "fields": [
            {"key": "NOTION_TOKEN", "label": "Integration Token", "secret": True,
             "placeholder": "ntn_… / secret_…", "required": True},
        ],
        "docs_url": "https://www.notion.so/my-integrations",
        "setup_hint": "Create an internal integration, share the pages you want "
                      "Friday to see with it, and paste its token.",
    },
    "discord": {
        "name": "Discord",
        "icon": "🎮",
        "category": "Communication",
        "kind": "mcp",
        "blurb": "Read and post to your Discord servers via a Discord MCP "
                 "server. Useful for community and team channels.",
        "capabilities": ["channels", "messages"],
        "workspaces": ["messages", "home"],
        "mcp_server": "discord",
        "mcp_template": {
            "command": "npx",
            "args": ["-y", "mcp-discord"],
            "env": {},
        },
        "fields": [
            {"key": "DISCORD_TOKEN", "label": "Bot Token", "secret": True,
             "placeholder": "Bot token…", "required": True},
        ],
        "docs_url": "https://discord.com/developers/applications",
        "setup_hint": "Create a Discord application + bot, invite it to your "
                      "server, and paste its token.",
    },
}

# Connectors are ordered for the UI: the working one first, then the stubs.
CONNECTOR_ORDER = ["google", "slack", "github", "linear", "notion", "discord"]


# ══════════════════════════════════════════════════════════════════════════
#  Defensive bridges into the rest of Friday
# ══════════════════════════════════════════════════════════════════════════
# All cross-module access is lazy + wrapped so connectors.py can be imported in
# any order (and under the test suite) without circular-import or boot hazards.

def _google_helpers():
    """Return (credentials_fn, client_config_fn, token_path) or (None, None, None)."""
    try:
        from agent_friday.services.calendar_engine import (
            _google_credentials, _google_client_config, GOOGLE_TOKEN_PATH,
        )
        return _google_credentials, _google_client_config, GOOGLE_TOKEN_PATH
    except Exception:
        return None, None, FRIDAY_DIR / "google_token.json"


def _mcp_bridge():
    """Return the services.agent module (for live MCP manager + config helpers)."""
    try:
        import agent_friday.services.agent as agent_svc
        return agent_svc
    except Exception:
        return None


def _notif_push(**kwargs):
    """Best-effort notification push (no-op if the engine isn't up)."""
    try:
        from agent_friday.services.voice_engine import _notif_engine
        if _notif_engine:
            _notif_engine.push(**kwargs)
    except Exception:
        pass


# ══════════════════════════════════════════════════════════════════════════
#  STATUS
# ══════════════════════════════════════════════════════════════════════════

def _mcp_server_config(server_name: str):
    """The raw mcp_servers.json entry for *server_name*, or None."""
    agent_svc = _mcp_bridge()
    if not agent_svc:
        return None
    try:
        cfg = agent_svc._load_mcp_servers()
        return (cfg.get("servers") or {}).get(server_name)
    except Exception:
        return None


def _mcp_live_status(server_name: str):
    """Live MCP manager state for *server_name*, or None if unavailable."""
    agent_svc = _mcp_bridge()
    if not agent_svc:
        return None
    try:
        mgr = getattr(agent_svc, "_MCP_MANAGER", None)
        if mgr is None:
            return None
        return mgr.status().get(server_name)
    except Exception:
        return None


def _has_required_tokens(defn: dict, server_cfg: dict | None) -> bool:
    """True if every required credential field is populated in the server env."""
    fields = [f for f in (defn.get("fields") or []) if f.get("required")]
    if not fields:
        return True
    env = (server_cfg or {}).get("env") or {}
    return all(str(env.get(f["key"], "")).strip() for f in fields)


# Status vocabulary (drives the badge color in the UI):
#   connected   — live + healthy            (green)
#   connecting  — handshake in progress     (amber, pulsing)
#   error       — configured but failing    (red)
#   disconnected— configured + token, off   (grey)
#   needs_setup — missing token/OAuth client (blue — "Connect")
_STATUS_COLORS = {
    "connected": "#00ff80",
    "connecting": "#f59e0b",
    "error": "#ff5470",
    "disconnected": "#7a8699",
    "needs_setup": "#00d4ff",
    "unknown": "#888888",
}


def _status_for_google(defn: dict) -> dict:
    creds_fn, client_cfg_fn, token_path = _google_helpers()
    connected = False
    client_ok = False
    try:
        if creds_fn:
            connected = creds_fn() is not None
        if client_cfg_fn:
            cfg, _src = client_cfg_fn()
            client_ok = cfg is not None
    except Exception:
        pass
    if connected:
        status, detail = "connected", "Live — Gmail + Calendar read-only"
    elif client_ok:
        status, detail = "needs_setup", "OAuth client ready — click Connect to approve access"
    else:
        status, detail = "needs_setup", "Add a Google OAuth client, then connect"
    return {
        "status": status,
        "detail": detail,
        "token_present": connected,
        "client_configured": client_ok,
    }


def _status_for_mcp(defn: dict) -> dict:
    server_name = defn.get("mcp_server") or defn["__key__"]
    cfg = _mcp_server_config(server_name)
    live = _mcp_live_status(server_name)
    has_token = _has_required_tokens(defn, cfg)  # pragma: allowlist secret

    if cfg is None or not has_token:
        return {"status": "needs_setup",
                "detail": "Paste your credentials to connect",
                "token_present": False, "tool_count": 0}

    enabled = bool(cfg.get("enabled"))
    if not enabled:
        return {"status": "disconnected",
                "detail": "Configured — toggle Connect to activate",
                "token_present": True, "tool_count": 0}

    # Enabled + token present → reflect the live MCP handshake.
    live = live or {}
    raw = (live.get("status") or "").lower()
    tool_count = live.get("tool_count", 0)
    if raw == "ready":
        return {"status": "connected",
                "detail": f"Live — {tool_count} tool(s) registered",
                "token_present": True, "tool_count": tool_count,
                "tools": live.get("tools", [])}
    if raw in ("starting", "", "stopped"):
        return {"status": "connecting",
                "detail": "Starting MCP server…",
                "token_present": True, "tool_count": tool_count}
    # error / crashed / anything else
    return {"status": "error",
            "detail": (live.get("error") or "MCP server failed to start")[:200],
            "token_present": True, "tool_count": tool_count}


def _build_status(key: str) -> dict | None:
    defn = CONNECTOR_DEFS.get(key)
    if not defn:
        return None
    defn = {**defn, "__key__": key}
    if defn["kind"] == "oauth":
        dyn = _status_for_google(defn)
    elif defn["kind"] == "mcp":
        dyn = _status_for_mcp(defn)
    else:
        dyn = {"status": "unknown", "detail": "", "token_present": False}

    status = dyn.get("status", "unknown")
    # The credential fields, minus any secret VALUES — we report whether each is
    # set, never echo the secret back to the browser.
    cfg = _mcp_server_config(defn.get("mcp_server")) if defn["kind"] == "mcp" else None
    env = (cfg or {}).get("env") or {}
    fields = []
    for f in (defn.get("fields") or []):
        fields.append({
            "key": f["key"], "label": f.get("label", f["key"]),
            "secret": bool(f.get("secret")), "required": bool(f.get("required")),
            "placeholder": f.get("placeholder", ""),
            "set": bool(str(env.get(f["key"], "")).strip()),
        })

    return {
        "key": key,
        "name": defn["name"],
        "icon": defn["icon"],
        "category": defn["category"],
        "kind": defn["kind"],
        "blurb": defn["blurb"],
        "capabilities": defn.get("capabilities", []),
        "workspaces": defn.get("workspaces", []),
        "setup_hint": defn.get("setup_hint", ""),
        "docs_url": defn.get("docs_url", ""),
        "fields": fields,
        "status": status,
        "color": _STATUS_COLORS.get(status, _STATUS_COLORS["unknown"]),
        "detail": dyn.get("detail", ""),
        "connected": status == "connected",
        "tool_count": dyn.get("tool_count", 0),
        "tools": dyn.get("tools", []),
    }


def list_connectors() -> list[dict]:
    """Every connector with live status, in display order."""
    out = []
    for key in CONNECTOR_ORDER:
        s = _build_status(key)
        if s:
            out.append(s)
    # Any defs not in CONNECTOR_ORDER (future-proofing) get appended.
    for key in CONNECTOR_DEFS:
        if key not in CONNECTOR_ORDER:
            s = _build_status(key)
            if s:
                out.append(s)
    return out


def get_connector(key: str) -> dict | None:
    return _build_status(key)


# ══════════════════════════════════════════════════════════════════════════
#  CONNECT  /  DISCONNECT
# ══════════════════════════════════════════════════════════════════════════

# Tracks an in-flight Google consent subprocess so repeated clicks don't spawn
# a swarm of browser windows.
_GOOGLE_CONNECT_LOCK = threading.Lock()
_google_connect_proc: subprocess.Popen | None = None


def _google_connect_running() -> bool:
    with _GOOGLE_CONNECT_LOCK:
        return _google_connect_proc is not None and _google_connect_proc.poll() is None


def _launch_google_connect() -> dict:
    """Run scripts/friday_google_connect.py in the background — the SAME loopback
    consent flow as the CLI, wired into the one-click web button. It opens the
    browser, captures the token, writes ~/.friday/google_token.json, and exits.
    The running server reads that token per-request, so the connector flips to
    'connected' on the next status poll with no restart.
    """
    global _google_connect_proc
    script = REPO_DIR / "scripts" / "friday_google_connect.py"
    if not script.exists():
        return {"ok": False, "status": "error",
                "message": f"connector script missing: {script}"}
    with _GOOGLE_CONNECT_LOCK:
        if _google_connect_proc is not None and _google_connect_proc.poll() is None:
            return {"ok": True, "status": "connecting",
                    "message": "Google consent already in progress — check the "
                               "browser window that opened."}
        try:
            _google_connect_proc = subprocess.Popen(
                [sys.executable, str(script)],
                cwd=str(REPO_DIR),
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                creationflags=_POPEN_FLAGS,
            )
        except Exception as e:
            return {"ok": False, "status": "error",
                    "message": f"could not launch consent flow: {e}"}
    return {"ok": True, "status": "connecting",
            "message": "Opening your browser to approve Gmail + Calendar "
                       "(read-only). Approve there, then return — Friday "
                       "connects automatically."}


def _google_web_auth_url(host_url: str | None) -> dict | None:
    """Fallback: the hosted OAuth start URL (for remote/headless clients where a
    local browser can't be popped). Mirrors routes/google.py:/api/google/auth.
    """
    creds_fn, client_cfg_fn, _ = _google_helpers()
    if not client_cfg_fn:
        return None
    try:
        cfg, _src = client_cfg_fn()
        if not cfg:
            return None
        from agent_friday.services.calendar_engine import (
            GOOGLE_SCOPES, _google_client_type, _google_redirect_uri,
        )
        from google_auth_oauthlib.flow import Flow
        client_type = _google_client_type(cfg) or "installed"
        redirect_uri = _google_redirect_uri(cfg, client_type)
        flow = Flow.from_client_config(cfg, scopes=GOOGLE_SCOPES, redirect_uri=redirect_uri)
        auth_url, _state = flow.authorization_url(
            access_type="offline", include_granted_scopes="true", prompt="consent",
        )
        return {"auth_url": auth_url, "redirect_uri": redirect_uri}
    except Exception:
        return None


def _disconnect_google() -> dict:
    creds_fn, _client_cfg_fn, token_path = _google_helpers()
    try:
        if token_path and Path(token_path).exists():
            Path(token_path).unlink()
        return {"ok": True, "status": "needs_setup",
                "message": "Google disconnected — token removed."}
    except Exception as e:
        return {"ok": False, "status": "error",
                "message": f"could not remove token: {e}"}


def _connect_mcp(key: str, defn: dict, data: dict) -> dict:
    """Write the supplied tokens into mcp_servers.json, enable the server, and
    hot-reload. `data` carries {FIELD_KEY: value} for the connector's fields.
    """
    agent_svc = _mcp_bridge()
    if not agent_svc:
        return {"ok": False, "status": "error", "message": "MCP subsystem unavailable"}

    server_name = defn.get("mcp_server") or key
    # Validate required fields are present.
    env_update = {}
    missing = []
    for f in (defn.get("fields") or []):
        val = str((data or {}).get(f["key"], "")).strip()
        if val:
            env_update[f["key"]] = val
        elif f.get("required"):
            missing.append(f.get("label", f["key"]))
    if missing:
        return {"ok": False, "status": "error",
                "message": "Missing: " + ", ".join(missing)}

    try:
        full = agent_svc._load_mcp_servers()
        servers = full.setdefault("servers", {})
        existing = servers.get(server_name) or {}
        tmpl = defn.get("mcp_template") or {}
        merged_env = dict(tmpl.get("env") or {})
        merged_env.update(existing.get("env") or {})
        merged_env.update(env_update)   # new tokens win
        servers[server_name] = {
            "command": existing.get("command") or tmpl.get("command"),
            "args": existing.get("args") or list(tmpl.get("args") or []),
            "env": merged_env,
            "enabled": True,
            "note": existing.get("note")
            or f"{defn['name']} connector (managed by Friday's connector registry).",
        }
        agent_svc._save_mcp_servers(full)
        reload_result = agent_svc._mcp_reload()
    except Exception as e:
        return {"ok": False, "status": "error", "message": f"connect failed: {e}"}

    return {"ok": True, "status": "connecting",
            "message": f"{defn['name']} configured — starting its MCP server…",
            "reload": reload_result}


def _disconnect_mcp(key: str, defn: dict) -> dict:
    """Disable the server (keep the config + token so re-connecting is one click)
    and hot-reload so its tools are unregistered.
    """
    agent_svc = _mcp_bridge()
    if not agent_svc:
        return {"ok": False, "status": "error", "message": "MCP subsystem unavailable"}
    server_name = defn.get("mcp_server") or key
    try:
        full = agent_svc._load_mcp_servers()
        servers = full.get("servers") or {}
        if server_name in servers:
            servers[server_name]["enabled"] = False
            agent_svc._save_mcp_servers(full)
            agent_svc._mcp_reload()
        return {"ok": True, "status": "disconnected",
                "message": f"{defn['name']} disconnected (config kept)."}
    except Exception as e:
        return {"ok": False, "status": "error", "message": f"disconnect failed: {e}"}


def connect_connector(key: str, data: dict | None = None, *, host_url: str | None = None) -> dict:
    """Initiate a connection for *key*. Returns {ok, status, message, ...}."""
    defn = CONNECTOR_DEFS.get(key)
    if not defn:
        return {"ok": False, "status": "error", "message": f"unknown connector {key!r}"}
    data = data or {}
    if defn["kind"] == "oauth":
        # Prefer the one-click local loopback flow; expose the web URL too so a
        # remote client can fall back to it.
        result = _launch_google_connect()
        web = _google_web_auth_url(host_url)
        if web:
            result["auth_url"] = web["auth_url"]
            result["redirect_uri"] = web["redirect_uri"]
        return result
    if defn["kind"] == "mcp":
        return _connect_mcp(key, {**defn, "__key__": key}, data)
    return {"ok": False, "status": "error", "message": "connector kind not connectable"}


def disconnect_connector(key: str) -> dict:
    defn = CONNECTOR_DEFS.get(key)
    if not defn:
        return {"ok": False, "status": "error", "message": f"unknown connector {key!r}"}
    if defn["kind"] == "oauth":
        return _disconnect_google()
    if defn["kind"] == "mcp":
        return _disconnect_mcp(key, {**defn, "__key__": key})
    return {"ok": False, "status": "error", "message": "connector kind not disconnectable"}


# ══════════════════════════════════════════════════════════════════════════
#  AMBIENT HEALTH MONITORING
# ══════════════════════════════════════════════════════════════════════════
# A lightweight snapshot of every connector's health, cached with a short TTL so
# the UI can poll it cheaply, plus an edge-detector that fires a notification
# when a previously-connected connector drops.

_HEALTH_CACHE = {"ts": 0.0, "data": None}
_HEALTH_TTL = 8.0           # seconds
_HEALTH_LOCK = threading.Lock()
# Last-seen status per connector, for connected->down edge detection.
_HEALTH_LAST: dict[str, str] = {}


def connectors_health(*, use_cache: bool = True) -> dict:
    """Snapshot for ambient monitoring + the /api/connectors/health route.

    Returns {summary, connected, total, degraded:[...], connectors:[...]}.
    `degraded` lists connectors that are configured but erroring — the ones an
    ambient monitor should surface.
    """
    now = _time.time()
    if use_cache:
        with _HEALTH_LOCK:
            if _HEALTH_CACHE["data"] is not None and (now - _HEALTH_CACHE["ts"]) < _HEALTH_TTL:
                return _HEALTH_CACHE["data"]

    conns = list_connectors()
    connected = [c for c in conns if c["status"] == "connected"]
    degraded = [
        {"key": c["key"], "name": c["name"], "status": c["status"], "detail": c["detail"]}
        for c in conns if c["status"] == "error"
    ]
    connecting = [c["key"] for c in conns if c["status"] == "connecting"]
    snapshot = {
        "summary": f"{len(connected)}/{len(conns)} connected",
        "connected": len(connected),
        "total": len(conns),
        "degraded": degraded,
        "connecting": connecting,
        "connectors": [
            {"key": c["key"], "name": c["name"], "icon": c["icon"],
             "status": c["status"], "color": c["color"], "detail": c["detail"],
             "workspaces": c["workspaces"]}
            for c in conns
        ],
        "ts": now,
    }
    with _HEALTH_LOCK:
        _HEALTH_CACHE["ts"] = now
        _HEALTH_CACHE["data"] = snapshot
    return snapshot


def monitor_connector_health() -> dict:
    """One health tick: detect connected→down edges and push a notification.

    Designed to be called on a background cadence (see server.py daemon). Returns
    the health snapshot so a caller can log/inspect it.
    """
    snap = connectors_health(use_cache=False)
    by_key = {c["key"]: c for c in snap["connectors"]}
    edges = []
    for key, c in by_key.items():
        prev = _HEALTH_LAST.get(key)
        cur = c["status"]
        if prev == "connected" and cur in ("error", "disconnected", "needs_setup"):
            edges.append(c)
        _HEALTH_LAST[key] = cur
    for c in edges:
        _notif_push(
            title=f"🔌 {c['name']} disconnected",
            body=f"{c['name']} was connected and is now {c['status']}: {c['detail']}",
            priority="medium",
            source="connectors",
            kind="connector_down",
            meta={"connector": c["key"], "status": c["status"]},
            target={"workspace": "system"},
            dedupe_key=f"connector_down:{c['key']}:{c['status']}",
        )
    snap["edges"] = [c["key"] for c in edges]
    return snap


def connector_health_monitor_loop(interval: float = 120.0):
    """Background daemon: tick monitor_connector_health() forever."""
    _log.info("Connector health monitor started.")
    _time.sleep(12)   # let MCP servers finish their first handshake
    # Prime last-seen so the first real tick doesn't fire spurious "down" alerts.
    try:
        for c in connectors_health(use_cache=False)["connectors"]:
            _HEALTH_LAST[c["key"]] = c["status"]
    except Exception:
        pass
    while True:
        try:
            monitor_connector_health()
        except Exception as e:
            print(f"  [connector-health] tick failed: {e}")
        _time.sleep(interval)


# ══════════════════════════════════════════════════════════════════════════
#  CROSS-CONNECTOR INTELLIGENCE  (for briefings)
# ══════════════════════════════════════════════════════════════════════════
# Synthesizes a compact, connector-aware preamble for the morning briefing so
# Friday can say "GitHub has 2 PRs awaiting your review and Slack has 4 unread
# in #eng" instead of only knowing about Gmail + Calendar. Each source is best
# effort: a connector that's down contributes nothing (never an error).

def _google_signals() -> list[str]:
    """Live Gmail + Calendar one-liners, if connected."""
    out = []
    creds_fn, _cfg, _tok = _google_helpers()
    try:
        if not creds_fn or creds_fn() is None:
            return out
    except Exception:
        return out
    # Calendar — next event today.
    try:
        from agent_friday.services.calendar_engine import _fetch_calendar_today
        events = _fetch_calendar_today()
        if isinstance(events, list) and events:
            nxt = events[0]
            when = nxt.get("start_time") or ""
            title = nxt.get("title") or "an event"
            out.append(f"Calendar: {len(events)} event(s) today; next is {title} {when}".strip())
    except Exception:
        pass
    # Gmail — unread count proxy.
    try:
        from agent_friday.services.calendar_engine import _fetch_gmail_recent
        emails = _fetch_gmail_recent(limit=10)
        if isinstance(emails, list) and emails:
            out.append(f"Gmail: {len(emails)} recent message(s) needing a look")
    except Exception:
        pass
    return out


def _mcp_tool_signal(server_name: str, label: str) -> str | None:
    """Generic 'connected + N tools' signal for an MCP connector."""
    live = _mcp_live_status(server_name) or {}
    if (live.get("status") or "").lower() == "ready":
        n = live.get("tool_count", 0)
        return f"{label}: connected ({n} tool(s) available)"
    return None


def connector_intelligence() -> dict:
    """Cross-connector signals for the briefing.

    Returns {sections:[{connector,name,lines}], signals:{key:[...]}, markdown}.
    The markdown block is drop-in for the briefing context; signals is the
    structured form for any caller that wants to reason over it.
    """
    sections = []
    signals: dict[str, list[str]] = {}

    # Google (live data).
    g = _google_signals()
    if g:
        sections.append({"connector": "google", "name": "Google", "lines": g})
        signals["google"] = g

    # MCP connectors — surface that they're live + ready (the agent can then call
    # their tools to go deeper during the briefing itself).
    for key in ("slack", "github", "linear", "notion", "discord"):
        defn = CONNECTOR_DEFS.get(key)
        if not defn:
            continue
        sig = _mcp_tool_signal(defn.get("mcp_server") or key, defn["name"])
        if sig:
            sections.append({"connector": key, "name": defn["name"], "lines": [sig]})
            signals[key] = [sig]

    # Build the markdown block.
    if sections:
        lines = ["## Connected Sources"]
        for sec in sections:
            for ln in sec["lines"]:
                lines.append(f"- {ln}")
        markdown = "\n".join(lines)
    else:
        markdown = ""

    return {"sections": sections, "signals": signals, "markdown": markdown}


# ══════════════════════════════════════════════════════════════════════════
#  CONNECTOR-AWARE WORKSPACES
# ══════════════════════════════════════════════════════════════════════════
# Drives the "Connected Sources" indicator a workspace header renders: given a
# workspace id, which connectors feed it and what's their live status.

def workspace_connectors(workspace: str) -> list[dict]:
    """Connectors that feed *workspace*, each with its current status — for the
    per-workspace 'Connected Sources' badge."""
    out = []
    for c in list_connectors():
        if workspace in (c.get("workspaces") or []):
            out.append({
                "key": c["key"], "name": c["name"], "icon": c["icon"],
                "status": c["status"], "color": c["color"],
                "tool_count": c.get("tool_count", 0),
            })
    return out


def workspace_connector_map() -> dict[str, list[str]]:
    """{workspace id: [connector keys that feed it]} — declarative, no status."""
    mapping: dict[str, list[str]] = {}
    for key, defn in CONNECTOR_DEFS.items():
        for ws in defn.get("workspaces") or []:
            mapping.setdefault(ws, []).append(key)
    return mapping


def connected_keys() -> list[str]:
    """Keys of connectors currently in the 'connected' state."""
    return [c["key"] for c in list_connectors() if c["status"] == "connected"]


# ══════════════════════════════════════════════════════════════════════════
#  MEETING INTELLIGENCE  (cross-connector context for a calendar event)
# ══════════════════════════════════════════════════════════════════════════

def _gmail_search(query: str, limit: int = 3) -> list[dict]:
    """Best-effort Gmail metadata search. [] when Google isn't connected."""
    creds_fn, _cfg, _tok = _google_helpers()
    try:
        if not creds_fn or creds_fn() is None:
            return []
        from googleapiclient.discovery import build
        creds = creds_fn()
        svc = build("gmail", "v1", credentials=creds, cache_discovery=False)
        resp = svc.users().messages().list(userId="me", q=query, maxResults=limit).execute()
        out = []
        for ref in resp.get("messages", []):
            msg = svc.users().messages().get(
                userId="me", id=ref.get("id"), format="metadata",
                metadataHeaders=["From", "Subject"],
            ).execute()
            h = {x["name"].lower(): x["value"]
                 for x in msg.get("payload", {}).get("headers", [])}
            out.append({
                "sender": h.get("from", ""),
                "subject": h.get("subject", "(no subject)"),
                "snippet": (msg.get("snippet") or "").strip()[:200],
            })
        return out
    except Exception:
        return []


def meeting_context(event: dict) -> dict:
    """Gather cross-connector context for a calendar meeting.

    Given an event (title + attendees), pull relevant material from whichever
    connectors are connected: recent Gmail threads with the attendees, plus a
    note of which other connected sources (Slack/GitHub/Linear/Notion) could be
    queried for deeper context. Every source is best effort — one that isn't
    connected simply contributes nothing.
    """
    title = event.get("title") or event.get("summary") or "(untitled)"
    attendees_raw = event.get("attendees") or []
    emails: list[str] = []
    for a in attendees_raw:
        if isinstance(a, str):
            emails.append(a)
        elif isinstance(a, dict):
            em = a.get("email") or a.get("displayName") or a.get("name")
            if em:
                emails.append(em)

    connected = connected_keys()
    sources: list[dict] = []

    # Gmail — recent threads from/to the attendees.
    if "google" in connected and emails:
        hits: list[dict] = []
        for em in emails[:4]:
            addr = em.split("<")[-1].rstrip(">").strip() if "<" in em else em
            hits.extend(_gmail_search(f"from:{addr} OR to:{addr} newer_than:30d", limit=2))
        if hits:
            sources.append({"connector": "google", "icon": "📧", "label": "Gmail",
                            "items": hits[:5]})

    # Other connected project/chat sources — flagged for deeper, agent-driven pulls.
    available = []
    for key in ("slack", "github", "linear", "notion", "discord"):
        if key in connected:
            defn = CONNECTOR_DEFS.get(key, {})
            available.append({"connector": key, "icon": defn.get("icon", "🔌"),
                              "label": defn.get("name", key)})

    return {
        "event": title,
        "attendees": emails,
        "connected_sources": connected,
        "sources": sources,
        "available_sources": available,
    }

"""The actions behind "Connect your services": test a connection, choose local
AI only, add a local or custom model endpoint, add or remove an MCP server.

Statuses and the list itself come from `services/setup_connections`; the
words from `services/connections_guide`. This module DOES things, and each
thing is the owner's click on their own screen (the routes are local-user
only). Nothing here reads, returns or logs a key: a key is stored through the
existing credential endpoint (``POST /api/providers/<name>/key``) and a test
reads it from the store inside the provider's own probe.

A test answers {state, text, fix}:
  pass           it answered; `text` says what was proved
  fail           it answered no (bad key, no credit, not running); `fix` says
                 the one thing to do about it
  not_connected  nothing is set up to test
  unknown        could not tell (offline, a proxy, a timeout): not a verdict
"""
from __future__ import annotations

import json
import logging
import re
import time
from urllib.parse import urlparse

_log = logging.getLogger("friday.connections_setup")

PASS, FAIL, NOT_CONNECTED, UNKNOWN = "pass", "fail", "not_connected", "unknown"

#: Default addresses of the two local runtimes.
OLLAMA_DEFAULT = "http://localhost:11434"
LMSTUDIO_DEFAULT = "http://localhost:1234/v1"

#: Registry name for the LM Studio endpoint this screen adds.
LMSTUDIO_PROVIDER = "lmstudio"


def _out(state, text, fix=""):
    return {"state": state, "text": text, "fix": fix}


# ── "I'll just use local AI" ─────────────────────────────────────────────────

def _choice_path():
    from agent_friday.core import FRIDAY_DIR
    return FRIDAY_DIR / "connections_choice.json"


def local_only() -> bool:
    """Did the person say they'll just use local AI? Never asked again, never
    nagged: the screen and the setup conversation read this to stay quiet
    about cloud keys. It changes no privacy or routing setting."""
    try:
        d = json.loads(_choice_path().read_text(encoding="utf-8") or "{}")
        return bool(d.get("local_only"))
    except Exception:
        return False


def set_local_only(on: bool) -> bool:
    path = _choice_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps({"local_only": bool(on), "at": time.time()}),
                   encoding="utf-8")
    tmp.replace(path)
    return bool(on)


# ── Test connection ──────────────────────────────────────────────────────────

def _label(item_id: str) -> str:
    try:
        from agent_friday.services import setup_connections as scx
        for it in scx.checklist()["items"]:
            if it["id"] == item_id:
                return it["label"]
    except Exception:
        pass
    return item_id.split(":", 1)[-1]


def _verdict_out(verdict: str, label: str, signup: str = "") -> dict:
    from agent_friday.services import key_verdict as kv
    where = (" at " + signup) if signup else ""
    if verdict == kv.OK:
        return _out(PASS, "%s answered with this key. It works." % label)
    if verdict == kv.REJECTED:
        return _out(FAIL, "%s refused this key." % label,
                    "Copy a fresh key%s and paste it again: the old one is wrong, "
                    "revoked, or was copied incompletely." % where)
    if verdict == kv.NO_CREDIT:
        return _out(FAIL, "%s accepted the key, but the account has no credit."
                    % label, "Add credit or a payment method on your %s "
                             "account. The key itself is fine." % label)
    return _out(UNKNOWN, "Couldn't reach %s to check. That isn't a verdict on "
                         "the key." % label,
                "Check the internet connection and try again in a minute.")


def _models_list_verdict(prov: dict) -> str:
    """A free check for an OpenAI-compatible provider: list its models with the
    stored key. Proves the key; says nothing about credit."""
    from agent_friday.services import key_verdict as kv
    from agent_friday.routing.provider_descriptors import auth_headers, provider_api_key
    import requests
    base = (prov.get("base_url") or "").rstrip("/")
    if not base:
        return kv.UNKNOWN
    try:
        key = provider_api_key(prov)  # pragma: allowlist secret
        url = base + "/models"
        headers = auth_headers(prov, key)
        if prov.get("type") == "google":
            # The key travels in Google's header, never in the URL: a URL is
            # what an exception, a proxy log or an error page quotes back.
            url = base + "/v1beta/models"
            headers = {"x-goog-api-key": key or ""}
        r = requests.get(url, headers=headers, timeout=20)
        return kv.verdict_for(r.status_code, r.text)
    except Exception:
        return kv.UNKNOWN


def _test_provider(name: str) -> dict:
    from agent_friday.services import credential_store as cs
    from agent_friday.services.provider_registry import get_provider_registry
    from agent_friday.services import connections_guide as cg
    reg = get_provider_registry()
    prov = reg.get_provider(name)
    label = (prov or {}).get("label") or name
    signup = (cg.GUIDE.get("provider:" + name) or {}).get("signup", "")
    if cs.provider_key_status(name) == "present_but_unreadable":
        return _out(FAIL, "A %s key is stored but can't be opened." % label,
                    "Paste the key again; it will be stored fresh.")
    from agent_friday.routing.provider_descriptors import provider_api_key
    if not prov or not provider_api_key(prov):  # pragma: allowlist secret
        return _out(NOT_CONNECTED, "No %s key is saved yet." % label,
                    "Paste a key in the secure field and press Save securely.")
    from agent_friday.services import one_key
    if name in (one_key.ANTHROPIC, one_key.OPENROUTER):
        # One token through the same check as setup's key step: proves credit.
        return _verdict_out(one_key.verify(name)["verdict"], label, signup)
    if prov and prov.get("type") == "kie":
        from agent_friday.services import provider_health
        chk = provider_health.check_provider(name, deep=True, use_cache=False)
        ok = chk.get("status") == "ok"
        return (_out(PASS, "%s answered with this key." % label) if ok else
                _out(FAIL, "%s did not accept the key (%s)." % (label, chk.get("status")),
                     "Copy a fresh key%s and paste it again."
                     % ((" at " + signup) if signup else "")))
    if prov and prov.get("type") in ("openai-compatible", "google"):
        res = _verdict_out(_models_list_verdict(prov), label, signup)
        if res["state"] == PASS:
            res["text"] += " (Credit is checked the first time it thinks.)"
        return res
    return _out(UNKNOWN, "%s has no quick check yet; it is checked the first "
                         "time Friday uses it." % label)


def _test_brave() -> dict:
    from agent_friday.services import web_search
    if not web_search.brave_key():
        return _out(NOT_CONNECTED, "No Brave Search key is saved yet.",
                    "Paste a key in the secure field and press Save securely.")
    v = web_search.verify_key()
    web = (v.get("endpoints") or {}).get("web") or {}
    if web.get("ok"):
        return _out(PASS, "Brave Search answered a test search with this key.")
    err = str(web.get("error") or "")
    if "401" in err or "403" in err or "422" in err:
        return _out(FAIL, "Brave refused this key for web search.",
                    "Check the key is for the Search plan (not the AI plan) and "
                    "paste it again.")
    return _out(UNKNOWN, "Couldn't reach Brave to check.",
                "Check the internet connection and try again.")


def _test_firecrawl() -> dict:
    from agent_friday.services import firecrawl
    if not firecrawl.configured():
        return _out(NOT_CONNECTED, "No Firecrawl key is saved yet.",
                    "Paste a key in the secure field and press Save securely.")
    c = firecrawl.credits()
    if c.get("ok"):
        return _out(PASS, "Firecrawl answered with this key.")
    err = str(c.get("error") or "")
    if "401" in err or "403" in err:
        return _out(FAIL, "Firecrawl refused this key.",
                    "Copy a fresh key from your Firecrawl dashboard and paste it again.")
    return _out(UNKNOWN, "Couldn't reach Firecrawl to check.",
                "Check the internet connection and try again.")


def _test_elevenlabs() -> dict:
    from agent_friday.services import cloud_voice
    import requests
    key = cloud_voice._api_key("elevenlabs")  # pragma: allowlist secret
    if not key:
        return _out(NOT_CONNECTED, "No ElevenLabs key is saved yet.",
                    "Paste a key in the secure field and press Save securely.")
    try:
        r = requests.get("https://api.elevenlabs.io/v1/models",
                         headers={"xi-api-key": key}, timeout=20)
    except Exception:
        return _out(UNKNOWN, "Couldn't reach ElevenLabs to check.",
                    "Check the internet connection and try again.")
    from agent_friday.services import key_verdict as kv
    return _verdict_out(kv.verdict_for(r.status_code, r.text), "ElevenLabs",
                        "https://elevenlabs.io/app/settings/api-keys")


def _test_local() -> dict:
    from agent_friday.services import local_seats
    try:
        rows = local_seats.installed(force=True)
    except Exception:
        rows = []
    if rows:
        return _out(PASS, "%d local model%s ready on this computer."
                    % (len(rows), "" if len(rows) == 1 else "s"))
    return _out(NOT_CONNECTED, "No local model is installed yet.",
                "Install one from the hardware card during setup, or Settings > Models.")


def _ollama_url() -> str:
    from agent_friday.services.provider_registry import get_provider_registry
    p = get_provider_registry().get_provider("ollama-local") or {}
    return (p.get("base_url") or OLLAMA_DEFAULT).rstrip("/")


def _test_ollama() -> dict:
    from agent_friday.routing.ollama_manager import get_manager
    url = _ollama_url()
    try:
        mgr = get_manager(url)
        models = mgr.list_models() or []
        up = bool(models) or bool(mgr.is_available())
    except Exception:
        up, models = False, []
    if not up:
        return _out(FAIL, "Nothing answered at %s." % url,
                    "Start Ollama (or install it from ollama.com/download), then "
                    "test again.")
    if not models:
        return _out(FAIL, "Ollama is running but has no models.",
                    "Pull a model in Ollama, then test again.")
    return _out(PASS, "Ollama is running with %d model%s."
                % (len(models), "" if len(models) == 1 else "s"))


def _test_openai_endpoint(name: str, label: str) -> dict:
    from agent_friday.services.provider_registry import get_provider_registry
    prov = get_provider_registry().get_provider(name)
    if not prov:
        return _out(NOT_CONNECTED, "%s isn't set up yet." % label,
                    "Fill in the address and press Save.")
    label = prov.get("label") or label
    import requests
    from agent_friday.routing.provider_descriptors import auth_headers, provider_api_key
    base = (prov.get("base_url") or "").rstrip("/")
    try:
        r = requests.get(base + "/models",
                         headers=auth_headers(prov, provider_api_key(prov)),  # pragma: allowlist secret
                         timeout=10)
    except Exception:
        return _out(FAIL, "Nothing answered at %s." % base,
                    "Check the address, and that the server is running.")
    if r.status_code in (401, 403):
        return _out(FAIL, "%s refused the request." % label,
                    "It needs a key: paste it in the secure field and save.")
    if r.status_code >= 400:
        return _out(FAIL, "%s answered HTTP %d." % (label, r.status_code),
                    "Check the address ends in /v1.")
    try:
        n = len((r.json() or {}).get("data") or [])
    except Exception:
        n = 0
    return _out(PASS, "%s answered with %d model%s." % (label, n, "" if n == 1 else "s"))


def _test_google() -> dict:
    from agent_friday.services import google_accounts as ga
    s = ga.accounts_summary()
    if not s.get("total"):
        return _out(NOT_CONNECTED, "No Google account is connected yet.",
                    "Press Connect and approve on Google's screen.")
    if s.get("healthy"):
        bad = int(s["total"]) - int(s["healthy"])
        return (_out(PASS, "Google is connected.") if not bad else
                _out(FAIL, "%d Google account%s need%s reconnecting."
                     % (bad, "" if bad == 1 else "s", "s" if bad == 1 else ""),
                     "Press Connect again for that account."))
    return _out(FAIL, "Google's sign-in has expired or was withdrawn.",
                "Press Connect and approve on Google's screen again.")


def _test_connector(key: str) -> dict:
    from agent_friday.services import connectors as C
    st = C._build_status(key) or {}
    label = st.get("name") or key
    status = st.get("status")
    if status == "connected":
        n = int(st.get("tool_count") or 0)
        return _out(PASS, "%s is connected (%d tool%s)." % (label, n, "" if n == 1 else "s"))
    if status in ("error", "connecting"):
        return _out(FAIL, "%s is set up but not answering: %s"
                    % (label, (st.get("detail") or "no detail")[:160]),
                    "Check the token or sign in again, then test again.")
    return _out(NOT_CONNECTED, "%s isn't connected yet." % label,
                "Follow the steps above, then test again.")


def _manager():
    import sys
    agent = sys.modules.get("agent_friday.services.agent")
    return getattr(agent, "_MCP_MANAGER", None) if agent is not None else None


def _test_mcp(name: str) -> dict:
    mgr = _manager()
    info = ((mgr.status() if mgr else {}) or {}).get(name)
    if not info:
        return _out(NOT_CONNECTED, "No MCP server named %s is running." % name,
                    "Add it below, or restart Friday if you just did.")
    st = info.get("status")
    tools = info.get("tools") or []
    if st == "ready" or (tools and st not in ("error", "disabled")):
        return _out(PASS, "%s answered with %d tool%s." % (name, len(tools), "" if len(tools) == 1 else "s"))
    if st == "disabled":
        return _out(FAIL, "%s is switched off." % name, "Switch it on, then test again.")
    if st == "starting":
        return _out(UNKNOWN, "%s is still starting." % name, "Test again in a few seconds.")
    if st in ("needs_auth", "unauthorized"):
        return _out(FAIL, "%s needs you to sign in." % name, "Press Sign in for it.")
    return _out(FAIL, "%s is not answering (%s)." % (name, st or "unknown"),
                "Check the address or command, then test again.")


def test(item_id: str) -> dict:
    """Test one checklist item. Never raises; never returns a key."""
    try:
        if item_id == "local":
            return _test_local()
        if item_id == "local:ollama":
            return _test_ollama()
        if item_id == "local:lmstudio":
            return _test_openai_endpoint(LMSTUDIO_PROVIDER, "LM Studio")
        if item_id.startswith("custom:"):
            name = item_id.split(":", 1)[1]
            return _test_openai_endpoint(name, name)
        if item_id == "provider:brave":
            return _test_brave()
        if item_id == "provider:firecrawl":
            return _test_firecrawl()
        if item_id == "provider:elevenlabs":
            return _test_elevenlabs()
        if item_id.startswith("provider:"):
            return _test_provider(item_id.split(":", 1)[1])
        if item_id == "google":
            return _test_google()
        if item_id.startswith("connector:"):
            return _test_connector(item_id.split(":", 1)[1])
        if item_id.startswith("mcp:"):
            return _test_mcp(item_id.split(":", 1)[1])
    except Exception as e:  # noqa: BLE001
        _log.debug("connection test %s raised %s", item_id, type(e).__name__)
        return _out(UNKNOWN, "The test couldn't run.", "Try again in a moment.")
    return _out(UNKNOWN, "This connection has no test.")


#: Item ids `test()` can say something about.
def testable(item_id: str) -> bool:
    return (item_id in ("local", "local:ollama", "local:lmstudio", "google")
            or item_id.startswith(("custom:", "provider:", "connector:", "mcp:")))


# ── Local and custom endpoints ───────────────────────────────────────────────

#: Every refusal this screen can give, by code. The screen shows the sentence
#: for the code a `Refused` carries. A sentence names something only from a
#: fixed table (a connector card's own server name); what the person typed (a
#: label, a server name, an address) and any exception's text are never
#: echoed back.
REFUSALS = {
    "url_scheme": "That address should start with http:// or https://.",
    "url_password": "Leave any password out of the address; keys go in the secure field.",
    "url_local_only": "This one runs on this computer or your own network.",
    "url_needs_https": "A service on the internet needs an https:// address.",
    "endpoint_name": "Give it a short name, letters and numbers.",
    "endpoint_exists": "You already have one with that name; pick another name.",
    "endpoint_kind": "Unknown endpoint kind.",
    "endpoint_not_accepted": "That address was not accepted.",
    "command_quote": "That command has an unclosed quote.",
    "command_empty": "Paste the command that starts the server.",
    "mcp_name": "Give it a short name: lowercase letters, numbers, - or _.",
    "mcp_connector_name": "{connector} has its own card; connect it there, or pick another name.",
    "mcp_url_or_command": "Paste either an address or a command, not both.",
    "mcp_exists": "There is already a server with that name.",
    "mcp_url_keys": ("Leave keys out of the address. If the server needs a key, add it "
                     "with its own instructions in Settings > Advanced."),
    "mcp_connector_remove": "Disconnect that one from its own card.",
    "mcp_missing": "There is no server with that name.",
    "mcp_starting": "The MCP servers are still starting; try again in a moment.",
    "mcp_sign_in": "That server can't sign in.",
    "refused": "That was not accepted.",
}


def refusal_text(code: str, connector: str = "") -> str:
    """The sentence for `code`. `{connector}` is filled only with a name taken
    from `_CONNECTOR_SERVERS` itself (the one equal to `connector`)."""
    text = REFUSALS.get(code, REFUSALS["refused"])
    named = next((c for c in sorted(_CONNECTOR_SERVERS) if c == connector), "That name")
    return text.replace("{connector}", named)


class Refused(ValueError):
    """A refusal by code; `str()` is its fixed sentence, which the screen shows."""

    def __init__(self, code: str, connector: str = ""):
        self.code = code if code in REFUSALS else "refused"
        self.connector = connector if connector in _CONNECTOR_SERVERS else ""
        super().__init__(refusal_text(self.code, self.connector))


def _clean_url(url: str, *, local_only: bool) -> str:
    url = (url or "").strip().rstrip("/")
    p = urlparse(url)
    if p.scheme not in ("http", "https") or not p.netloc:
        raise Refused("url_scheme")
    if p.username or p.password:
        raise Refused("url_password")
    from agent_friday.routing.provider_descriptors import is_private_host
    if local_only and not is_private_host(url):
        raise Refused("url_local_only")
    if p.scheme == "http" and not is_private_host(url):
        raise Refused("url_needs_https")
    return url


def save_ollama(url: str) -> dict:
    url = _clean_url(url or OLLAMA_DEFAULT, local_only=True)
    from agent_friday.services.provider_registry import get_provider_registry
    get_provider_registry().update_provider("ollama-local", {"base_url": url, "enabled": True})
    try:
        from agent_friday.core import _load_settings, _save_settings
        block = dict((_load_settings() or {}).get("model_routing") or {})
        block["ollama_url"] = url
        _save_settings({"model_routing": block})
    except Exception as e:  # noqa: BLE001
        _log.debug("ollama_url not saved to settings: %s", type(e).__name__)
    return {"saved": True, "base_url": url}


_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,23}$")

#: Every custom endpoint's provider name starts with this. A provider's key is
#: stored and read under its name (credential_store, provider_api_key), and
#: the store also holds keys under names no descriptor declares (elevenlabs,
#: inworld, brave, firecrawl, ...). Without its own namespace a custom endpoint
#: labelled after one of those would read that service's real key, send it to
#: the address the person typed, and overwrite it when a key is saved on its
#: card. The free-text label is shown; it never becomes a credential name.
CUSTOM_PREFIX = "custom-"


def save_endpoint(kind: str, label: str, base_url: str) -> dict:
    """Add an OpenAI-compatible endpoint: LM Studio (added or updated), or a
    custom one (added once; a second one with the same name is refused). The
    descriptor carries no key; a key, if needed, goes through the secure field
    afterwards (POST /api/providers/<name>/key)."""
    from agent_friday.services.provider_registry import (
        ProviderExistsError, get_provider_registry)
    if kind == "lmstudio":
        name, label = LMSTUDIO_PROVIDER, "LM Studio"
        url = _clean_url(base_url or LMSTUDIO_DEFAULT, local_only=True)
        auth = {"type": "none"}
    elif kind == "custom":
        label = re.sub(r"\s+", " ", str(label or "")).strip()[:40]
        slug = re.sub(r"[^a-z0-9-]+", "-", label.lower()).strip("-")[:24].strip("-")
        if not label or not _NAME_RE.match(slug or ""):
            raise Refused("endpoint_name")
        name = CUSTOM_PREFIX + slug
        if get_provider_registry().get_provider(name) is not None:
            raise Refused("endpoint_exists")
        url = _clean_url(base_url, local_only=False)
        auth = {"type": "env_var",
                "key": "FRIDAY_%s_API_KEY" % name.upper().replace("-", "_")}
    else:
        raise Refused("endpoint_kind")
    from agent_friday.routing.provider_descriptors import is_private_host
    desc = {"name": name, "label": label, "type": "openai-compatible",
            "base_url": url, "auth": auth, "models": [],
            "classification": "local" if is_private_host(url) else "cloud",
            "enabled": True}
    try:
        get_provider_registry().add_provider(desc, replace=(kind == "lmstudio"))
    except ProviderExistsError:
        raise Refused("endpoint_exists")
    try:
        from agent_friday.services.model_discovery import invalidate_cache
        invalidate_cache(name)
    except Exception:
        pass
    return {"saved": True, "provider": name, "item": "custom:" + name
            if kind == "custom" else "local:lmstudio"}


def custom_endpoints() -> list:
    """The OpenAI-compatible endpoints this screen added, as (name, provider)."""
    from agent_friday.services.provider_registry import get_provider_registry
    reg = get_provider_registry()
    out = []
    for p in reg.list_providers():
        name = p.get("name") or ""
        if name in ("custom", LMSTUDIO_PROVIDER) or reg.is_builtin(name):
            continue
        if p.get("type") != "openai-compatible":
            continue
        out.append((name, p))
    return out


# ── MCP servers ──────────────────────────────────────────────────────────────

_MCP_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,31}$")
#: Servers that are first-class connectors with their own card: the
#: `mcp_server` of every MCP connector in services/connectors.CONNECTOR_DEFS.
#: Their names belong to the connector, so this screen neither adds, lists
#: nor removes a server under one of them.
_CONNECTOR_SERVERS = frozenset({"github", "slack", "discord", "linear", "notion",
                                "higgsfield", "windows_desktop"})


def _split_command(command: str) -> tuple[str, list]:
    import shlex
    try:
        parts = shlex.split(command, posix=True)
    except ValueError:
        raise Refused("command_quote")
    if not parts:
        raise Refused("command_empty")
    return parts[0], parts[1:]


def add_mcp(name: str, url: str = "", command: str = "") -> dict:
    """Add an MCP server by address (Streamable HTTP) or command (runs here).
    Refuses a name already in use or owned by a connector card; writes
    through the agent's own config writer (which encrypts any env values) and
    reloads the servers."""
    name = str(name or "").strip().lower()
    if not _MCP_NAME_RE.match(name):
        raise Refused("mcp_name")
    if name in _CONNECTOR_SERVERS:
        raise Refused("mcp_connector_name", connector=name)
    if bool(url) == bool(command):
        raise Refused("mcp_url_or_command")
    from agent_friday.services import agent
    cfg = agent._load_mcp_servers() or {}
    servers = dict(cfg.get("servers") or {})
    if name in servers:
        raise Refused("mcp_exists")
    if url:
        clean = _clean_url(url, local_only=False)
        if urlparse(clean).query:
            raise Refused("mcp_url_keys")
        spec = {"url": clean, "enabled": True, "note": "Added in Connect your services."}
    else:
        cmd, args = _split_command(command)
        spec = {"command": cmd, "args": args, "env": {}, "enabled": True,
                "note": "Added in Connect your services."}
    servers[name] = spec
    agent._save_mcp_servers({**cfg, "servers": servers})
    try:
        agent._mcp_reload()
    except Exception as e:  # noqa: BLE001
        _log.debug("mcp reload after add: %s", type(e).__name__)
    return {"added": name, "item": "mcp:" + name, "remote": bool(url)}


def remove_mcp(name: str) -> dict:
    name = str(name or "").strip().lower()
    if name in _CONNECTOR_SERVERS:
        raise Refused("mcp_connector_remove")
    from agent_friday.services import agent
    cfg = agent._load_mcp_servers() or {}
    servers = dict(cfg.get("servers") or {})
    if name not in servers:
        raise Refused("mcp_missing")
    servers.pop(name)
    agent._save_mcp_servers({**cfg, "servers": servers})
    try:
        agent._mcp_reload()
    except Exception as e:  # noqa: BLE001
        _log.debug("mcp reload after remove: %s", type(e).__name__)
    return {"removed": name}


def sign_in_mcp(name: str) -> dict:
    mgr = _manager()
    if mgr is None:
        raise Refused("mcp_starting")
    from agent_friday.services import agent
    res = mgr.authorize(name, open_browser=False,
                        on_ready=getattr(agent, "_mcp_register_server_tools", None))
    if not res.get("ok"):
        _log.debug("mcp sign-in refused for a server (%s)", "error" if res.get("error") else "no answer")
        raise Refused("mcp_sign_in")
    return {"auth_url": res.get("auth_url") or ""}


def mcp_servers() -> list:
    """The MCP servers the person added (not the built-in connector ones):
    [{name, remote, target, enabled}] with the address or the program name,
    never an env value."""
    try:
        from agent_friday.services import agent
        cfg = agent._load_mcp_servers() or {}
    except Exception:
        return []
    out = []
    for name, spec in sorted((cfg.get("servers") or {}).items()):
        if name in _CONNECTOR_SERVERS or not isinstance(spec, dict):
            continue
        remote = bool(spec.get("url"))
        target = str(spec.get("url") or spec.get("command") or "")
        out.append({"name": name, "remote": remote, "target": target[:200],
                    "enabled": spec.get("enabled") is not False})
    return out

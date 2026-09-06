"""
Agent Friday — Extension Security
Inspired by patterns in Goose (Apache-2.0). All code is original.

Env var allowlist for sandboxed MCP subprocess environments, audit logging,
Unicode sanitization, trust levels for MCP.
"""
import os, re, json, time, hashlib, sys, unicodedata
from pathlib import Path
from datetime import datetime

from agent_friday.paths import friday_home

AUDIT_DIR = friday_home() / "security"
AUDIT_DIR.mkdir(parents=True, exist_ok=True)
AUDIT_LOG = AUDIT_DIR / "mcp_audit.log"

# Persisted operator decisions + a dedicated audit trail for the static
# launch-command scanner (kept separate from the per-call AUDIT_LOG above).
ALLOWLIST_FILE = AUDIT_DIR / "extension_allowlist.json"
AUDIT_FILE = AUDIT_DIR / "extension_audit.jsonl"

# CORRECTION (F67, external review commissioned by Stephen, ruled on
# 2026-09-04): this used to be ENV_BLOCKLIST, a denylist of named secrets
# stripped from the FULL inherited environment before spawning a
# sandboxed/untrusted stdio MCP server. That shape failed three times in
# one audit cycle for the same structural reason each time -- F32 built
# it, F44 "fixed" it by naming two env vars (FRIDAY_VAULT_KEY,
# FRIDAY_HMAC_SECRET) that don't exist anywhere in this codebase while the
# real one (FRIDAY_VAULT_PASSPHRASE) stayed unlisted, and F67 then found
# six more live, real provider-key env vars (MISTRAL_API_KEY,
# DEEPSEEK_API_KEY, XAI_API_KEY, FIREWORKS_API_KEY, PERPLEXITY_API_KEY,
# COHERE_API_KEY) that had never been added at all
# (docs/audits/gauntlet-2026-09-03/findings.jsonl). A denylist has to name
# every secret that will ever exist; every new provider this codebase
# adds is a fresh chance to forget one. Stephen's ruling: invert it.
#
# SANDBOXED_ENV_ALLOWLIST names every environment variable a sandboxed
# subprocess is given -- everything else in the parent process's
# environment, named or not yet invented, simply never reaches it. A
# forgotten name here breaks a connector loudly (it fails to start,
# visible immediately) rather than leaking a secret silently. None of
# these carry a secret by construction -- a provider API key was never
# going to be named PATH -- so there is no list of provider names left to
# keep in sync going forward.
#
# NOT to be confused with get_allowlist()/is_allowlisted() further below
# in this file, which governs a different question entirely (which MCP
# SERVER launch commands an operator has approved), not which environment
# variables a server's own subprocess receives.
_ENV_ALLOWLIST_COMMON = {
    "PATH", "LANG", "LC_ALL", "LC_CTYPE", "TERM",
    "TEMP", "TMP", "TMPDIR", "HOME",
}

# Node/npm/npx -- the most common stdio MCP server launcher in practice --
# and native Windows CLI tools generally do not merely prefer these:
# SYSTEMROOT in particular is required for Windows' own crypto/socket APIs
# to initialize, so a child missing it can fail network calls outright,
# not just behave oddly. APPDATA/LOCALAPPDATA hold npm's own cache/config.
_ENV_ALLOWLIST_WINDOWS = {
    "SYSTEMROOT", "WINDIR", "COMSPEC", "PATHEXT",
    "APPDATA", "LOCALAPPDATA", "PROGRAMDATA",
    "PROGRAMFILES", "PROGRAMFILES(X86)", "PROGRAMW6432",
    "SYSTEMDRIVE", "USERPROFILE", "HOMEDRIVE", "HOMEPATH",
    "NUMBER_OF_PROCESSORS", "PROCESSOR_ARCHITECTURE",
}

_ENV_ALLOWLIST_POSIX = {
    "SHELL", "XDG_RUNTIME_DIR", "XDG_CONFIG_HOME", "XDG_CACHE_HOME",
}

SANDBOXED_ENV_ALLOWLIST = _ENV_ALLOWLIST_COMMON | (
    _ENV_ALLOWLIST_WINDOWS if sys.platform == "win32" else _ENV_ALLOWLIST_POSIX
)

# Trust levels for MCP servers
TRUST_LEVELS = {
    "trusted": {"env_filter": False, "audit": True, "unicode_sanitize": False},
    "sandboxed": {"env_filter": True, "audit": True, "unicode_sanitize": True},
    "untrusted": {"env_filter": True, "audit": True, "unicode_sanitize": True},
}

# Invisible Unicode categories to strip
_INVISIBLE_CATS = {"Cf", "Cc", "Co", "Cs"}  # Format, Control, Private Use, Surrogate
_ALLOWED_CONTROL = {"\n", "\r", "\t"}


def sanitize_env_for_mcp(env: dict = None, trust_level: str = "sandboxed") -> dict:
    """Build the environment a spawned MCP server subprocess actually receives.

    A "trusted" server (an operator's explicit opt-in) gets the full
    inherited environment, unchanged -- that has always been an explicit
    choice, not a leak. Every other trust level gets an environment built
    FROM SANDBOXED_ENV_ALLOWLIST: only the names on it that are present in
    the source are copied over. Never the inverse (inherit everything,
    then strip a list of named secrets) -- see SANDBOXED_ENV_ALLOWLIST's
    own comment for why that shape is retired, not just patched again.

    Matches allowlist names case-insensitively: os.environ.copy() (the
    caller's typical input) degrades to a plain, case-SENSITIVE dict even
    though Windows' own os.environ is case-insensitive, so a variable this
    process sees as "SystemRoot" could just as easily be stored as
    "SYSTEMROOT" by the time it reaches here -- confirmed directly on a
    live Windows environment before relying on it.
    """
    base = env if env is not None else dict(os.environ)
    if not TRUST_LEVELS.get(trust_level, {}).get("env_filter", True):
        return base
    allowed = {name.upper() for name in SANDBOXED_ENV_ALLOWLIST}
    return {k: v for k, v in base.items() if k.upper() in allowed}


def sanitize_unicode(text: str) -> str:
    """Strip invisible Unicode control characters that could be used for injection."""
    if not text:
        return text
    cleaned = []
    for ch in text:
        if ch in _ALLOWED_CONTROL:
            cleaned.append(ch)
        elif unicodedata.category(ch) not in _INVISIBLE_CATS:
            cleaned.append(ch)
    return "".join(cleaned)


def audit_tool_call(server_name: str, tool_name: str, params: dict, result: str = None,
                    trust_level: str = "sandboxed", duration_ms: int = 0):
    """Log an MCP tool invocation for security audit."""
    entry = {
        "ts": datetime.utcnow().isoformat() + "Z",
        "agent_friday.server": server_name,
        "tool": tool_name,
        "trust": trust_level,
        "params_keys": list(params.keys()) if params else [],
        "result_len": len(str(result)) if result else 0,
        "duration_ms": duration_ms,
    }
    try:
        with open(AUDIT_LOG, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry) + "\n")
    except Exception:
        pass


def get_audit_log(limit: int = 100) -> list:
    """Read recent audit log entries."""
    if not AUDIT_LOG.exists():
        return []
    entries = []
    try:
        with open(AUDIT_LOG, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    entries.append(json.loads(line))
    except Exception:
        pass
    return entries[-limit:]


def get_trust_level(server_config: dict) -> str:
    """Get trust level from server config, default sandboxed."""
    return server_config.get("trust_level", "sandboxed")


def validate_tool_input(tool_name: str, params: dict, trust_level: str = "sandboxed") -> dict:
    """Sanitize tool inputs based on trust level."""
    if not TRUST_LEVELS.get(trust_level, {}).get("unicode_sanitize", True):
        return params
    sanitized = {}
    for k, v in (params or {}).items():
        if isinstance(v, str):
            sanitized[k] = sanitize_unicode(v)
        else:
            sanitized[k] = v
    return sanitized


def validate_tool_output(output: str, trust_level: str = "sandboxed") -> str:
    """Sanitize tool outputs based on trust level."""
    if not TRUST_LEVELS.get(trust_level, {}).get("unicode_sanitize", True):
        return output
    if isinstance(output, str):
        return sanitize_unicode(output)
    return output


# ── Pre-launch command scanner ───────────────────────────────────────────────
# Before any MCP server is started we statically inspect its launch command.
# A "block" finding (destructive or download-and-execute command lines) keeps
# the server from booting; a "warn" finding (untrusted launcher, inline secret)
# surfaces in the UI but does not stop the launch. Operators can promote a
# warned server to allow via the allowlist — but the allowlist never overrides
# a block-level finding.

# Runtimes we recognize as legitimate MCP launchers. Anything else "warns".
TRUSTED_LAUNCHERS = {
    "npx", "node", "nodejs", "python", "python3", "py",
    "uv", "uvx", "pipx", "pip", "deno", "bun",
}

# Destructive / download-and-execute command lines → block the launch outright.
_BLOCK_PATTERNS = [
    (re.compile(r"\b(?:curl|wget|iwr|invoke-webrequest|fetch)\b[^|]*\|\s*"
                r"(?:sh|bash|zsh|dash|pwsh|powershell)\b", re.I),
     "download-and-execute pipeline"),
    (re.compile(r"\brm\s+-[a-z]*r[a-z]*f|\brm\s+-[a-z]*f[a-z]*r", re.I),
     "recursive force delete"),
    (re.compile(r"(?:powershell|pwsh)\b.{0,40}?-e(?:nc|ncoded|ncodedcommand)?\b", re.I),
     "encoded powershell command"),
]

# Looks like a real secret rather than a placeholder.
_SECRET_KEY_HINT = re.compile(r"key|token|secret|password|passwd|pwd|credential|auth", re.I)
_SECRET_VALUE_HINT = re.compile(r"\b(?:sk|ghp|gho|ghs|xox[baprs]|pk|AKIA)[-_]?[A-Za-z0-9]{12,}")
_PLACEHOLDER = re.compile(r"\$\{|\$\(|<[^>]+>|your[_-]|changeme|placeholder|example|xxxx", re.I)


def _launcher_name(command: str) -> str:
    """Normalize a launch command to its bare runtime name (basename, no ext)."""
    if not command:
        return ""
    base = re.split(r"[\\/]", str(command).strip())[-1].lower()
    for ext in (".cmd", ".exe", ".bat", ".ps1", ".sh"):
        if base.endswith(ext):
            return base[: -len(ext)]
    return base


def _is_inline_secret(key: str, value) -> bool:
    """A non-placeholder env value that looks like a credential."""
    if not isinstance(value, str):
        return False
    v = value.strip()
    if len(v) < 8 or _PLACEHOLDER.search(v):
        return False
    return bool(_SECRET_KEY_HINT.search(key or "")) or bool(_SECRET_VALUE_HINT.search(v))


def assess_server(name: str, spec: dict) -> dict:
    """Statically assess a single MCP server's launch spec.

    Returns {name, verdict, findings, allowlisted} where verdict is one of
    "allow" | "warn" | "block". An allowlisted server has any "warn" verdict
    promoted to "allow" — but a "block" verdict is never overridden.
    """
    spec = spec or {}
    findings = []

    launcher = _launcher_name(spec.get("command"))
    args = spec.get("args") or []
    cmdline = " ".join([str(spec.get("command") or "")] + [str(a) for a in args])

    for pattern, label in _BLOCK_PATTERNS:
        if pattern.search(cmdline):
            findings.append({"finding": label, "severity": "block"})

    # Remote (Streamable HTTP) servers: bearer tokens must never transit
    # plaintext HTTP. Loopback is exempt (local dev/test servers).
    url = str(spec.get("url") or "")
    if url.lower().startswith("http://"):
        host = re.split(r"[/:]", url[7:], 1)[0].lower()
        if host not in ("127.0.0.1", "localhost", "[::1]"):
            findings.append({"finding": "insecure remote URL (plaintext http)",
                             "severity": "block"})

    if launcher and launcher not in TRUSTED_LAUNCHERS:
        findings.append({"finding": "untrusted launcher", "severity": "warn"})

    for k, v in (spec.get("env") or {}).items():
        if _is_inline_secret(k, v):
            findings.append({"finding": "inline secret in env",
                             "severity": "warn", "key": k})

    if any(f["severity"] == "block" for f in findings):
        raw = "block"
    elif any(f["severity"] == "warn" for f in findings):
        raw = "warn"
    else:
        raw = "allow"

    allowlisted = is_allowlisted(name, spec)
    verdict = "allow" if (allowlisted and raw == "warn") else raw

    return {"name": name, "verdict": verdict,
            "findings": findings, "allowlisted": allowlisted}


def assess_config(cfg: dict) -> dict:
    """Assess every server in an MCP config, with a verdict summary."""
    servers = (cfg or {}).get("servers") or {}
    results = {name: assess_server(name, spec) for name, spec in servers.items()}
    summary = {"allow": 0, "warn": 0, "block": 0}
    for r in results.values():
        summary[r["verdict"]] = summary.get(r["verdict"], 0) + 1
    return {"servers": results, "summary": summary}


# F9: gate_mcp_config's security_note used to be attached only to the
# in-memory deepcopy handed to MCPManager.load_config() -- MCPServerProcess /
# MCPServerHTTP never store it, and the on-disk mcp_servers.json is
# deliberately left untouched (so a later fix to the command doesn't require
# a manual config edit to re-enable). With nowhere durable to read it back,
# both status surfaces (services/connectors.py's _status_for_mcp and
# routes/core_routes.py's /api/mcp/status) saw only the live handshake status
# 'disabled', which matches none of their explicit branches, and fell into a
# generic "failed to start" / bare "disabled" catch-all -- the real reason was
# computed and then thrown away. This name-keyed registry is (re)populated by
# every gate_mcp_config() call (each boot/reload) and is the durable lookup
# both resolvers now consult.
_BLOCKED_REGISTRY: dict[str, str] = {}


def get_blocked_reason(name: str) -> str | None:
    """The security_note gate_mcp_config attached to *name* this run, if any."""
    return _BLOCKED_REGISTRY.get(name) if name else None


def blocked_servers() -> dict:
    """A copy of the full {server name: security_note} registry."""
    return dict(_BLOCKED_REGISTRY)


def gate_mcp_config(cfg: dict) -> dict:
    """Disable any enabled server whose launch command trips a block finding.

    Returns a copy of the config; already-disabled servers pass through
    untouched (no security_note). Scanner errors must never take connectors
    down — callers wrap this in try/except. Also (re)populates
    _BLOCKED_REGISTRY (see get_blocked_reason/blocked_servers) so a security
    block is legible to /api/connectors and /api/mcp/status, not just to the
    in-memory config this function returns (F9).
    """
    import copy
    if not isinstance(cfg, dict):
        return {"servers": {}}
    out = copy.deepcopy(cfg)
    servers = out.get("servers")
    if not isinstance(servers, dict):
        return out
    # Reset the registry for every server named in this config so a
    # previously-blocked server that's since been fixed (or removed) doesn't
    # keep reporting a stale block reason.
    for name in servers:
        _BLOCKED_REGISTRY.pop(name, None)
    for name, spec in servers.items():
        if not isinstance(spec, dict) or not spec.get("enabled", True):
            continue  # already off (or malformed) — leave untouched
        if assess_server(name, spec)["verdict"] == "block":
            spec["enabled"] = False
            note = (
                "Disabled: blocked by extension security "
                "(destructive or download-and-execute launch command)."
            )
            spec["security_note"] = note
            _BLOCKED_REGISTRY[name] = note
    return out


# ── Allowlist ─────────────────────────────────────────────────────────────────
# Q22: is_allowlisted()/add_to_allowlist() used to key approval purely by
# server NAME. assess_server() promotes any future "warn"-level verdict for an
# allowlisted name straight to "allow" -- so editing an already-approved
# server's launch command to something materially different (still
# warn-tier only; a block-tier finding is never bypassed by the allowlist)
# silently inherited the old approval with zero re-review. The allowlist is
# now keyed by a fingerprint of the approved command/args (or url, for a
# remote server) alongside the name: a name is only "still allowlisted" while
# its current launch spec hashes to what was actually approved. Editing the
# command drops it back to normal warn-tier handling until it's re-approved.

def _normalize_command(spec: dict) -> str:
    """A stable string form of a server's launch spec, for fingerprinting."""
    spec = spec or {}
    if spec.get("url"):
        payload = {"url": str(spec.get("url") or "")}
    else:
        payload = {
            "command": str(spec.get("command") or ""),
            "args": [str(a) for a in (spec.get("args") or [])],
        }
    return json.dumps(payload, sort_keys=True)


def _command_fingerprint(spec: dict) -> str:
    """SHA-256 of the normalized launch command -- what add_to_allowlist
    actually approved for a given server name."""
    return hashlib.sha256(_normalize_command(spec).encode("utf-8")).hexdigest()


def _current_server_spec(name: str) -> dict | None:
    """Best-effort lookup of *name*'s currently configured launch spec, used
    when a caller (e.g. the /api/extensions/allowlist route, which only takes
    a name) approves or checks a server without supplying its spec directly.
    None means "no configured entry for this name was found" — distinct from
    an empty-but-present spec ({})."""
    try:
        from agent_friday.services.agent import _load_mcp_servers
        cfg = _load_mcp_servers()
        servers = cfg.get("servers") or {}
        return servers[name] if name in servers else None
    except Exception:
        return None


def get_allowlist() -> dict:
    """Read the persisted map of operator-approved server names to the launch
    command fingerprint they were approved for. A value of None means the
    server was approved without a resolvable spec to fingerprint (only
    reachable via a direct add_to_allowlist(name) call for a name with no
    matching entry in mcp_servers.json — e.g. a caller/test working entirely
    off explicit specs rather than the on-disk config) and is treated as
    still-trusted-by-name, matching the pre-fix behavior for that narrow case
    rather than permanently locking such a name out."""
    try:
        if ALLOWLIST_FILE.exists():
            data = json.loads(ALLOWLIST_FILE.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                return {str(k): (v if v is None else str(v)) for k, v in data.items()}
            if isinstance(data, list):
                # Legacy (pre-Q22-fix) format: names only, no fingerprint was
                # ever recorded. Nothing to match against, so these fall
                # through to normal (unapproved) handling until re-approved --
                # the correct outcome here, not a silent grandfather-in of an
                # unknown historical command.
                return {}
    except Exception:
        pass
    return {}


def is_allowlisted(name: str, spec: dict = None) -> bool:
    """True only if *name* is approved AND its current launch command still
    matches the fingerprint that was approved (Q22). A name-only match with a
    changed command is treated as NOT allowlisted."""
    if not name:
        return False
    allowlist = get_allowlist()
    if name not in allowlist:
        return False
    stored = allowlist[name]
    if stored is None:
        return True
    if spec is None:
        spec = _current_server_spec(name)
        if spec is None:
            return False  # nothing to verify the approval against
    return stored == _command_fingerprint(spec)


def _write_allowlist(data: dict) -> None:
    try:
        ALLOWLIST_FILE.parent.mkdir(parents=True, exist_ok=True)
        ALLOWLIST_FILE.write_text(json.dumps(data, indent=2), encoding="utf-8")
    except Exception:
        pass


def _audit_allowlist(action: str, name: str) -> None:
    try:
        AUDIT_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(AUDIT_FILE, "a", encoding="utf-8") as f:
            f.write(json.dumps({
                "ts": datetime.utcnow().isoformat() + "Z",
                "event": "allowlist",
                "action": action,
                "agent_friday.server": name,
            }) + "\n")
    except Exception:
        pass


def add_to_allowlist(name: str, spec: dict = None) -> dict:
    """Approve a server name for its CURRENT launch command (or *spec*, if
    given explicitly). Returns the updated {name: fingerprint} allowlist.

    If neither *spec* nor a matching mcp_servers.json entry can be found, the
    approval is recorded with no fingerprint (None) — see get_allowlist()'s
    docstring for why that's still treated as approved rather than silently
    rejected.
    """
    name = (name or "").strip()
    if not name:
        return get_allowlist()
    current = get_allowlist()
    resolved = spec if spec is not None else _current_server_spec(name)
    fp = _command_fingerprint(resolved) if resolved is not None else None
    if current.get(name, "__unset__") != fp:
        current[name] = fp
        _write_allowlist(current)
        _audit_allowlist("add", name)
    return current


def remove_from_allowlist(name: str) -> dict:
    """Revoke a server name; returns the updated allowlist."""
    name = (name or "").strip()
    current = get_allowlist()
    if name in current:
        current.pop(name, None)
        _write_allowlist(current)
        _audit_allowlist("remove", name)
    return current

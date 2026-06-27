"""
Agent Friday — Extension Security
Inspired by patterns in Goose (Apache-2.0). All code is original.

Env var blocklists, audit logging, Unicode sanitization, trust levels for MCP.
"""
import os, re, json, time, unicodedata
from pathlib import Path
from datetime import datetime

AUDIT_DIR = Path.home() / ".friday" / "security"
AUDIT_DIR.mkdir(parents=True, exist_ok=True)
AUDIT_LOG = AUDIT_DIR / "mcp_audit.log"

# Persisted operator decisions + a dedicated audit trail for the static
# launch-command scanner (kept separate from the per-call AUDIT_LOG above).
ALLOWLIST_FILE = AUDIT_DIR / "extension_allowlist.json"
AUDIT_FILE = AUDIT_DIR / "extension_audit.jsonl"

# Env vars MCP servers must NEVER see
ENV_BLOCKLIST = {
    "ANTHROPIC_API_KEY", "OPENAI_API_KEY", "GEMINI_API_KEY", "GOOGLE_API_KEY",
    "OPENROUTER_API_KEY", "TOGETHER_API_KEY", "GROQ_API_KEY",
    "FRIDAY_PASSWORD", "FRIDAY_VAULT_KEY", "FRIDAY_HMAC_SECRET",
    "AWS_SECRET_ACCESS_KEY", "AWS_SESSION_TOKEN",
    "GITHUB_TOKEN", "GH_TOKEN", "GITLAB_TOKEN",
    "DATABASE_URL", "DB_PASSWORD", "REDIS_URL",
    "STRIPE_SECRET_KEY", "TWILIO_AUTH_TOKEN",
}

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
    """Filter environment variables based on trust level."""
    if not TRUST_LEVELS.get(trust_level, {}).get("env_filter", True):
        return env or dict(os.environ)
    base = env if env is not None else dict(os.environ)
    return {k: v for k, v in base.items() if k not in ENV_BLOCKLIST}


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

    allowlisted = is_allowlisted(name)
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


def gate_mcp_config(cfg: dict) -> dict:
    """Disable any enabled server whose launch command trips a block finding.

    Returns a copy of the config; already-disabled servers pass through
    untouched (no security_note). Scanner errors must never take connectors
    down — callers wrap this in try/except.
    """
    import copy
    if not isinstance(cfg, dict):
        return {"servers": {}}
    out = copy.deepcopy(cfg)
    servers = out.get("servers")
    if not isinstance(servers, dict):
        return out
    for name, spec in servers.items():
        if not isinstance(spec, dict) or not spec.get("enabled", True):
            continue  # already off (or malformed) — leave untouched
        if assess_server(name, spec)["verdict"] == "block":
            spec["enabled"] = False
            spec["security_note"] = (
                "Disabled: blocked by extension security "
                "(destructive or download-and-execute launch command)."
            )
    return out


# ── Allowlist ─────────────────────────────────────────────────────────────────

def get_allowlist() -> list:
    """Read the persisted set of operator-approved server names."""
    try:
        if ALLOWLIST_FILE.exists():
            data = json.loads(ALLOWLIST_FILE.read_text(encoding="utf-8"))
            if isinstance(data, list):
                return [str(x) for x in data]
    except Exception:
        pass
    return []


def is_allowlisted(name: str) -> bool:
    return bool(name) and name in get_allowlist()


def _write_allowlist(names: list) -> None:
    try:
        ALLOWLIST_FILE.parent.mkdir(parents=True, exist_ok=True)
        ALLOWLIST_FILE.write_text(json.dumps(names, indent=2), encoding="utf-8")
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


def add_to_allowlist(name: str) -> list:
    """Approve a server name; returns the updated allowlist."""
    name = (name or "").strip()
    current = get_allowlist()
    if name and name not in current:
        current.append(name)
        _write_allowlist(current)
        _audit_allowlist("add", name)
    return current


def remove_from_allowlist(name: str) -> list:
    """Revoke a server name; returns the updated allowlist."""
    name = (name or "").strip()
    current = get_allowlist()
    if name in current:
        current = [x for x in current if x != name]
        _write_allowlist(current)
        _audit_allowlist("remove", name)
    return current

"""Doctor: a local diagnostic report (north star §28.1-§28.3, §28.11; A3).

What it holds: versions, checks (each a domain, a state from §28.3 and a fixed
code), error signatures (counts by exception type and logger), drift (which
settings differ from the shipped defaults, by name; whether the versions on
disk agree), and performance numbers (provider latency, the action ledger's
size and check time, disk and memory).

What it never holds: conversation or memory content, file names, paths,
email addresses, account names, server names, setting VALUES, anything from
the vault. It is built only from enumerated fields, and every string in it is
then checked against a strict pattern (letters, digits and a little
punctuation, no "@", none of this machine's home folder, user name or host
name); anything else becomes "<withheld>".

Deterministic: no model is asked anything, nothing is sent anywhere, and
nothing is written except the report itself under <home>/doctor/. Sharing is
by hand only (A3): the owner saves the file and attaches it themselves. Friday
never uploads it.

Read-only on everything else: it never mints a key, pins the rules, migrates a
store or seeds settings while it looks.
"""
from __future__ import annotations

import getpass
import hashlib
import json
import os
import platform
import re
import socket
import sys
import time
from pathlib import Path
from typing import Any, Optional

REPORT_VERSION = 1
KEEP_REPORTS = 50

#: §28.3 health states.
STATES = ("healthy", "degraded", "unavailable_by_configuration", "unavailable_by_hardware",
          "misconfigured", "blocked_by_policy", "broken", "repairing", "unverified")

#: Provider names reported by name; anything else is "other".
KNOWN_PROVIDERS = {"anthropic", "openai", "google", "gemini", "ollama", "llama", "llamacpp",
                   "local", "groq", "mistral", "deepseek", "openrouter", "xai", "together",
                   "elevenlabs", "inworld", "kokoro", "piper", "whisper"}

_SAFE = re.compile(r"^[A-Za-z0-9_.:+\-/ ()%,=#<>\[\]]{0,160}$")
WITHHELD = "<withheld>"


def _home() -> Path:
    from agent_friday.paths import friday_home
    return Path(friday_home())


def reports_dir() -> Path:
    return _home() / "doctor" / "reports"


# ── privacy: every string is checked on the way out ─────────────────────────

def _private_markers() -> list[str]:
    out = set()
    for v in (str(Path.home()), str(_home()), os.environ.get("USERPROFILE", ""),
              os.environ.get("HOME", ""), os.environ.get("USERNAME", ""),
              os.environ.get("USER", "")):
        if v and len(v) >= 3:
            out.add(v.lower())
            out.add(v.replace("\\", "/").lower())
    for fn in (getpass.getuser, socket.gethostname, platform.node):
        try:
            v = fn()
            if v and len(v) >= 3:
                out.add(v.lower())
        except Exception:
            pass
    return sorted(out, key=len, reverse=True)


def _clean(obj: Any, markers: list[str]) -> Any:
    if isinstance(obj, dict):
        out = {}
        for i, (k, v) in enumerate(obj.items()):
            ck = _clean_str(str(k), markers)
            out[ck if ck != WITHHELD else "withheld_%d" % i] = _clean(v, markers)
        return out
    if isinstance(obj, (list, tuple)):
        return [_clean(v, markers) for v in obj]
    if isinstance(obj, str):
        return _clean_str(obj, markers)
    if obj is None or isinstance(obj, (bool, int, float)):
        return obj
    return WITHHELD


def _clean_str(s: str, markers: list[str]) -> str:
    if not _SAFE.match(s) or "@" in s:
        return WITHHELD
    low = s.lower()
    if any(m in low for m in markers):
        return WITHHELD
    return s


# ── checks ──────────────────────────────────────────────────────────────────

def _check(cid: str, domain: str, fn) -> dict:
    t0 = time.perf_counter()
    try:
        state, code = fn()
    except Exception as e:  # noqa: BLE001 - a check that cannot run is unverified
        state, code = "unverified", "check_failed:" + type(e).__name__
    if state not in STATES:
        state, code = "unverified", "bad_state"
    return {"id": cid, "domain": domain, "state": state, "code": code,
            "ms": round((time.perf_counter() - t0) * 1000, 1)}


def _governance_key_present() -> tuple[bool, str]:
    """Whether a governance key exists, WITHOUT creating one."""
    if os.environ.get("FRIDAY_TRIAL") == "1":
        return True, "trial_process"
    try:
        import keyring
        try:
            if keyring.get_password("agent-friday", "governance-key"):
                return True, "keychain"
        except Exception:
            pass
    except ImportError:
        pass
    try:
        from agent_friday.governance import proof_of_integrity as poi
        if Path(poi._GOV_KEY_FILE).exists():
            return True, "file"
    except Exception:
        pass
    return False, "none"


def _c_versions():
    from agent_friday.services import app_version
    vt = app_version.version_truth()
    return ("degraded", "versions_disagree") if vt.get("disagrees") else ("healthy", "ok")


def _settings_file() -> Path:
    return _home() / "settings.json"


def _c_settings():
    p = _settings_file()
    if not p.exists():
        return "unavailable_by_configuration", "no_settings_file"
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return "misconfigured", "settings_unreadable"
    if not isinstance(d, dict):
        return "misconfigured", "settings_not_an_object"
    unknown = _unknown_settings(d)
    return ("degraded", "unknown_keys") if unknown else ("healthy", "ok")


def _defaults() -> dict:
    from agent_friday import core
    return dict(getattr(core, "DEFAULT_SETTINGS", {}) or {})


def _unknown_settings(d: dict) -> list:
    dflt = _defaults()
    return [k for k in d if k not in dflt]


def _c_vault():
    cfg = _home() / "vault" / ".vault_config.json"
    if not cfg.exists():
        return "unavailable_by_configuration", "no_vault"
    try:
        json.loads(cfg.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return "broken", "vault_config_unreadable"
    return "healthy", "ok"


def _c_governance_key():
    ok, where = _governance_key_present()
    return ("healthy", "key_in_" + where) if ok else ("unavailable_by_configuration", "no_key_yet")


def _c_ledger():
    if not _governance_key_present()[0]:
        return "unverified", "no_key_to_check_with"
    from agent_friday.services import action_ledger
    s = action_ledger.summary(migrate=False)
    if s.get("ok") and s.get("intact"):
        return "healthy", "intact"
    if s.get("ok"):
        return "degraded", "breaks_acknowledged"
    return "broken", "breaks_to_review"


def _c_claws():
    if not _governance_key_present()[0]:
        return "unverified", "no_key_to_check_with"
    from agent_friday.routes.owner_security import claws_status
    st = claws_status().get("state")
    return {"intact": ("healthy", "intact"), "not_pinned": ("healthy", "not_pinned_yet"),
            "mismatch": ("blocked_by_policy", "rules_signature_mismatch")}.get(
        st, ("broken", "rules_check_error"))


def _c_egress():
    from agent_friday.services import egress_gate
    res = getattr(egress_gate, "_SELF_TEST_RESULT", None)
    if res is None:
        return "unverified", "self_test_not_run_in_this_process"
    return ("healthy", "self_test_passed") if res.get("ok") else ("broken", "self_test_failed")


def _c_action_gate():
    from agent_friday.governance import action_gate
    klass, _why = action_gate.classify("doctor_unknown_tool_probe", {}, {})
    return ("healthy", "unknown_tools_outward") if klass == action_gate.OUTWARD else \
        ("broken", "unknown_tools_not_outward")


def _c_safe_mode():
    from agent_friday.services import safe_mode
    st = safe_mode.state()
    if st.get("active"):
        return "blocked_by_policy", "safe_mode_" + (st.get("how") or "on")
    return "healthy", "normal_start"


def _c_startup():
    p = _home() / "startup-report.json"
    if not p.exists():
        return "unverified", "no_startup_report"
    d = json.loads(p.read_text(encoding="utf-8"))
    skipped = (d.get("blueprints") or {}).get("skipped") or []
    return ("degraded", "route_modules_skipped:%d" % len(skipped)) if skipped else ("healthy", "ok")


def _c_scheduler():
    from agent_friday.services import safe_mode
    if safe_mode.active():
        return "blocked_by_policy", "paused_in_safe_mode"
    p = _home() / "schedules.json"
    if not p.exists():
        return "unverified", "no_schedules_file"
    json.loads(p.read_text(encoding="utf-8"))
    return "healthy", "ok"


def _c_mcp():
    from agent_friday.services import safe_mode
    p = _home() / "mcp_servers.json"
    if not p.exists():
        return "unavailable_by_configuration", "none_configured"
    d = json.loads(p.read_text(encoding="utf-8"))
    servers = d.get("servers", d) if isinstance(d, dict) else {}
    if safe_mode.active():
        return "blocked_by_policy", "off_in_safe_mode:%d" % len(servers or {})
    return "healthy", "configured:%d" % len(servers or {})


def _c_connectors():
    tok = _home() / "google_accounts" / "tokens"
    n = len(list(tok.glob("*.token.enc"))) if tok.is_dir() else 0
    from agent_friday.services import safe_mode
    if safe_mode.active():
        return "blocked_by_policy", "off_in_safe_mode:%d" % n
    return ("healthy", "google_accounts:%d" % n) if n else ("unavailable_by_configuration", "none")


def _c_backup():
    p = _home() / "backups" / "last_bundle.json"
    if not p.exists():
        return "unavailable_by_configuration", "no_bundle_made"
    d = json.loads(p.read_text(encoding="utf-8"))
    age_days = (time.time() - float(d.get("made_at") or 0)) / 86400
    if not d.get("verified"):
        return "degraded", "last_bundle_not_verified"
    return ("degraded", "last_bundle_older_than_30_days") if age_days > 30 else ("healthy", "verified")


def _c_disk():
    import shutil
    free = shutil.disk_usage(str(_home() if _home().exists() else Path.home())).free
    gb = free / 1e9
    if gb < 2:
        return "degraded", "disk_free_under_2gb"
    return "healthy", "ok"


def _c_models():
    from agent_friday.services import safe_mode
    rt = _home() / "runtime"
    n = len(list(rt.rglob("*.gguf"))) if rt.is_dir() else 0
    if not n:
        return "unavailable_by_configuration", "no_local_model_files"
    if safe_mode.active():
        return "healthy", "local_model_files:%d (not started in safe mode)" % n
    return "healthy", "local_model_files:%d" % n


def _c_crashes():
    from agent_friday.services import crash_forensics
    n = crash_forensics.crash_count()
    return ("degraded", "crashes_recorded:%d" % n) if n else ("healthy", "none_recorded")


def _c_boot():
    from agent_friday.services import boot_guard
    n = int(boot_guard._read_attempt().get("consecutive_failures") or 0)
    return ("degraded", "failed_starts:%d" % n) if n else ("healthy", "ok")


CHECKS = (
    ("app_files", "application files", _c_versions),
    ("settings", "settings schema", _c_settings),
    ("vault", "vault accessibility", _c_vault),
    ("governance_key", "key and credential availability", _c_governance_key),
    ("receipt_chain", "receipt chains", _c_ledger),
    ("rules", "action gate", _c_claws),
    ("egress_gate", "egress gate", _c_egress),
    ("action_gate", "action gate", _c_action_gate),
    ("safe_mode", "startup mode", _c_safe_mode),
    ("startup", "application files", _c_startup),
    ("scheduler", "scheduler and triggers", _c_scheduler),
    ("mcp", "connector authentication", _c_mcp),
    ("connectors", "connector authentication", _c_connectors),
    ("backup", "backup", _c_backup),
    ("disk", "GPU and resource state", _c_disk),
    ("model_files", "model files and checksums", _c_models),
    ("crashes", "application files", _c_crashes),
    ("boot", "update status", _c_boot),
)


# ── errors, drift, performance ──────────────────────────────────────────────

_EXC = re.compile(r"\b([A-Z][A-Za-z0-9_]*(?:Error|Exception))\b")
_LOGGER = re.compile(r"\b(ERROR|CRITICAL)\b[^\w]*([a-z_][a-z0-9_.]{2,80})")


def _tail(p: Path, n: int = 2_000_000) -> str:
    try:
        size = p.stat().st_size
        with open(p, "rb") as f:
            f.seek(max(0, size - n))
            return f.read().decode("utf-8", errors="replace")
    except OSError:
        return ""


def _errors() -> dict:
    """Signatures and counts only: an exception type, a logger (module) name."""
    by_type: dict[str, int] = {}
    by_logger: dict[str, int] = {}
    text = _tail(_home() / "friday.log")
    for line in text.splitlines():
        m = _LOGGER.search(line)
        if m:
            name = m.group(2)
            if name.startswith("agent_friday") or name.count(".") == 0:
                by_logger[name] = by_logger.get(name, 0) + 1
            for t in _EXC.findall(line):
                by_type[t] = by_type.get(t, 0) + 1
    crash = _tail(_home() / "logs" / "crashes.log")
    fatal = sum(crash.count(m) for m in ("Fatal Python error:", "Windows fatal exception:"))
    for t in _EXC.findall(crash):
        by_type[t] = by_type.get(t, 0) + 1
    top = lambda d: [{"signature": k, "count": v}  # noqa: E731
                     for k, v in sorted(d.items(), key=lambda kv: -kv[1])[:20]]
    return {"exception_types": top(by_type), "loggers": top(by_logger), "fatal_crashes": fatal}


def _drift() -> dict:
    out: dict[str, Any] = {}
    try:
        d = json.loads(_settings_file().read_text(encoding="utf-8"))
        dflt = _defaults()
        out["settings_changed_from_default"] = sorted(
            k for k in dflt if k in d and d[k] != dflt[k])
        out["settings_unknown_key_count"] = len([k for k in d if k not in dflt])
        out["settings_missing_key_count"] = len([k for k in dflt if k not in d])
    except Exception as e:  # noqa: BLE001
        out["settings"] = "unreadable:" + type(e).__name__
    try:
        from agent_friday.services import app_version
        vt = app_version.version_truth()
        out["versions"] = {"running": vt.get("running"), "manifest": vt.get("manifest"),
                           "installer": vt.get("installer"), "disagree": bool(vt.get("disagrees"))}
    except Exception as e:  # noqa: BLE001
        out["versions"] = "unreadable:" + type(e).__name__
    return out


def _performance() -> dict:
    out: dict[str, Any] = {}
    try:
        from agent_friday.services import provider_health
        lat = {}
        other = 0
        for name, st in (provider_health.all_stats() or {}).items():
            key = str(name).lower()
            if key not in KNOWN_PROVIDERS:
                other += 1
                key = "other_%d" % other
            lat[key] = {k: st.get(k) for k in ("availability", "requests", "errors",
                                               "error_rate", "p50_ms", "p95_ms")
                        if isinstance(st, dict) and k in st}
        out["providers"] = lat
    except Exception as e:  # noqa: BLE001
        out["providers"] = "unavailable:" + type(e).__name__
    try:
        if _governance_key_present()[0]:
            from agent_friday.services import action_ledger
            s = action_ledger.summary(migrate=False)
            out["action_ledger"] = {k: s.get(k) for k in ("records", "segments", "bytes",
                                                          "verify_ms", "anchor")}
    except Exception as e:  # noqa: BLE001
        out["action_ledger"] = "unavailable:" + type(e).__name__
    try:
        import shutil
        du = shutil.disk_usage(str(_home() if _home().exists() else Path.home()))
        out["disk_free_gb"] = round(du.free / 1e9, 1)
    except Exception:
        pass
    try:
        import psutil
        vm = psutil.virtual_memory()
        out["ram_total_gb"] = round(vm.total / 1e9, 1)
        out["ram_available_gb"] = round(vm.available / 1e9, 1)
    except Exception:
        pass
    return out


def _versions() -> dict:
    out: dict[str, Any] = {"python": platform.python_version(),
                           "os": platform.system(), "os_release": platform.release(),
                           "arch": platform.machine(), "frozen": bool(getattr(sys, "frozen", False))}
    try:
        from agent_friday import release
        out.update(release=release.RELEASE_TAG, release_name=release.RELEASE_NAME,
                   build_sequence=release.BUILD_SEQUENCE)
    except Exception:
        pass
    try:
        from agent_friday.services import app_version
        out["running"] = app_version.version_truth().get("running")
    except Exception:
        pass
    return out


# ── the report ──────────────────────────────────────────────────────────────

def build() -> dict:
    """The report as a dict, cleaned. Deterministic and local."""
    t0 = time.perf_counter()
    stamp = time.strftime("%Y%m%d-%H%M%S", time.gmtime())
    report = {
        "report": "agent-friday-doctor",
        "report_version": REPORT_VERSION,
        "id": "doctor-%s-%s" % (stamp, hashlib.sha256(os.urandom(8)).hexdigest()[:6]),
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "content": "none: versions, checks, error signatures, drift and performance only",
        "sharing": "by hand only; Friday never sends this file anywhere",
        "versions": _versions(),
        "checks": [_check(cid, dom, fn) for cid, dom, fn in CHECKS],
        "errors": _errors(),
        "drift": _drift(),
        "performance": _performance(),
    }
    report["summary"] = {s: sum(1 for c in report["checks"] if c["state"] == s)
                         for s in STATES if any(c["state"] == s for c in report["checks"])}
    report["build_ms"] = round((time.perf_counter() - t0) * 1000, 1)
    return _clean(report, _private_markers())


def render_text(report: dict) -> str:
    """The same report as plain text, for reading and for attaching by hand."""
    lines = ["Agent Friday Doctor report %s" % report.get("id"),
             "Generated %s. %s." % (report.get("generated_at"), report.get("content")),
             "Sharing: %s." % report.get("sharing"), ""]
    v = report.get("versions") or {}
    lines.append("Version: %s (%s), Python %s, %s %s %s" % (
        v.get("release"), v.get("running"), v.get("python"), v.get("os"),
        v.get("os_release"), v.get("arch")))
    lines.append("")
    lines.append("Checks:")
    for c in report.get("checks") or []:
        lines.append("  %-16s %-30s %s" % (c.get("id"), c.get("state"), c.get("code")))
    e = report.get("errors") or {}
    lines += ["", "Errors: %s fatal crash(es) recorded" % e.get("fatal_crashes")]
    for row in e.get("exception_types") or []:
        lines.append("  %s x%s" % (row.get("signature"), row.get("count")))
    d = report.get("drift") or {}
    lines += ["", "Drift: settings changed from default: %s; unknown keys: %s" % (
        ", ".join(d.get("settings_changed_from_default") or []) or "none",
        d.get("settings_unknown_key_count"))]
    p = report.get("performance") or {}
    lines += ["", "Performance: " + json.dumps(p, sort_keys=True)]
    return "\n".join(lines) + "\n"


def save(report: Optional[dict] = None) -> dict:
    """Build (or take) a report and keep it under <home>/doctor/reports/ as
    .json and .txt. Returns the report. Keeps the newest KEEP_REPORTS."""
    report = report or build()
    d = reports_dir()
    d.mkdir(parents=True, exist_ok=True)
    rid = report["id"]
    (d / (rid + ".json")).write_text(json.dumps(report, indent=1, sort_keys=True), encoding="utf-8")
    (d / (rid + ".txt")).write_text(render_text(report), encoding="utf-8")
    olds = sorted(d.glob("doctor-*.json"))
    for old in olds[:-KEEP_REPORTS]:
        for suffix in (".json", ".txt"):
            try:
                old.with_suffix(suffix).unlink()
            except OSError:
                pass
    return report


def saved(report_id: str, kind: str = "json") -> Optional[Path]:
    """The saved file for `report_id`, or None. Names are checked exactly."""
    if not re.fullmatch(r"doctor-\d{8}-\d{6}-[0-9a-f]{6}", report_id or "") or kind not in ("json", "txt"):
        return None
    p = reports_dir() / f"{report_id}.{kind}"
    return p if p.is_file() else None


def history(limit: int = 20) -> list:
    """The newest saved reports: id, time and the state counts."""
    out = []
    for p in sorted(reports_dir().glob("doctor-*.json"), reverse=True)[:limit]:
        try:
            r = json.loads(p.read_text(encoding="utf-8"))
            out.append({"id": r.get("id"), "generated_at": r.get("generated_at"),
                        "summary": r.get("summary")})
        except (OSError, ValueError):
            continue
    return out

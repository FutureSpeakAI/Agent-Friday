"""Doctor: a local diagnostic report (north star §28.1-§28.3, §28.11; A3).

What it holds: versions, checks (each a domain, a state from §28.3 and a fixed
code), error signatures (counts by exception type and logger), drift (which
settings differ from the shipped defaults, by name; whether the versions on
disk agree), and performance numbers (provider latency, the action ledger's
size and check time, disk and memory).

It also lists every store Friday keeps, with its version, size and place in
the data folder (services/store_inventory.py, §37.3-1).

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


def _c_settings_read():
    """Whether this process is serving the last good settings because
    settings.json could not be read (§28.4-2: never a silent reset). The
    file is not touched, and saves are refused until it reads again. It
    reflects the last read: after the file is mended it turns healthy at the
    next read."""
    from agent_friday import core
    if core._settings_read_degraded():
        return "degraded", "settings_unreadable_last_good_served"
    return "healthy", "ok"


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


def _c_authority():
    """Whether the owner revoked all action authority (services/authority.py)."""
    from agent_friday.services import authority
    r = authority.revoked()
    if r and r.get("unreadable"):
        return ("unverified", "authority_unreadable")
    return ("blocked_by_policy", "authority_revoked") if r else ("healthy", "in_force")


def _c_receipt_completeness():
    """Every outward action has its decision and its outcome (services/receipt_audit)."""
    from agent_friday.services import receipt_audit
    return receipt_audit.doctor_check()


def _c_outward_terminal():
    """Every outward action has exactly one terminal receipt, no request ran
    twice, no task said done early (services/outward_audit). Counts only."""
    from agent_friday.services import outward_audit
    return outward_audit.doctor_check()


def _c_backup():
    try:
        from agent_friday.services import backup_schedule
        sched = backup_schedule.doctor_view(_home())
    except Exception:
        sched = {}
    if sched.get("scheduled") and sched.get("failing"):
        return "degraded", "scheduled_bundle_failing"
    if sched.get("scheduled") and sched.get("rehearsal_ok") is False:
        return "degraded", "restore_rehearsal_failed"
    if sched.get("scheduled") and sched.get("overdue"):
        return "degraded", "scheduled_bundle_overdue"
    p = _home() / "backups" / "last_bundle.json"
    if not p.exists():
        return "unavailable_by_configuration", "no_bundle_made"
    d = json.loads(p.read_text(encoding="utf-8"))
    age_days = (time.time() - float(d.get("made_at") or 0)) / 86400
    if not d.get("verified"):
        return "degraded", "last_bundle_not_verified"
    if age_days > 30:
        return "degraded", "last_bundle_older_than_30_days"
    return "healthy", ("verified_scheduled" if sched.get("scheduled") else "verified")


def _c_disk():
    import shutil
    try:
        from agent_friday.services import disk_guard
        if disk_guard.status()["state"] == "low":
            # services/disk_guard: scheduled background work is waiting for space (NS-32.13-1).
            return "degraded", "disk_full_background_paused"
    except Exception:
        pass
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


def _c_local_brain():
    """Whether the local model is serving (§32.2-4). When it is installed and
    not serving, the Doctor page offers its repairs (services/local_repair)."""
    from agent_friday.services import capability_state as cs, local_repair
    s = local_repair.brain()
    if s["state"] == "unknown":
        return "unverified", "local_model_state_unreadable"
    if s["state"] == cs.WORKING:
        return "healthy", "serving"
    if s["down"]:
        from agent_friday.services import safe_mode
        if safe_mode.active():
            return "blocked_by_policy", "not_started_in_safe_mode"
        return "broken", "installed_not_serving_repair_offered"
    return "unavailable_by_configuration", "no_local_model"


def _c_gpu():
    """The graphics card the local models use: read, never changed (§28.2-1).
    CUDA is not initialised here: the count comes from CTranslate2 only when
    the voice has already loaded it, else from one bounded nvidia-smi query;
    safe mode asks nothing. Free memory is not asked (it changes by the
    second)."""
    from agent_friday.services import gpu_probe, local_voice, safe_mode
    if gpu_probe._cuda_hidden():
        return "unavailable_by_configuration", "gpus_hidden_by_setting"
    if safe_mode.active():
        return "unverified", "not_probed_in_safe_mode"
    if "ctranslate2" in sys.modules:
        n = int(gpu_probe.device_count().get("devices") or 0)
    else:
        cards = gpu_probe._run_smi()
        if cards is None:
            return "unverified", "nvidia_smi_did_not_answer"
        n = len(cards)
    if not n:
        return "unavailable_by_hardware", "no_cuda_gpu"
    # cuBLAS matters only to the local transcriber (CTranslate2)
    if local_voice.deps_installed() and not gpu_probe.cublas_status().get("ok"):
        return "misconfigured", "cublas_missing_for_local_voice"
    return "healthy", "cuda_gpus:%d" % n


def _c_local_voice():
    """Speech in and out on this PC: whether its packages are there. The
    voice models themselves are checked by the voice readiness card, not by
    loading them here."""
    from agent_friday.services import local_voice
    if not local_voice.deps_installed():
        return "unavailable_by_configuration", "local_voice_not_installed"
    return "unverified", "packages_installed_models_not_loaded_here"


def _c_document_engine():
    """The document engine (OfficeCLI): present, and matching its pinned
    checksum."""
    from agent_friday.services import office_engine
    st = office_engine.binary_state()
    return {"ok": ("healthy", "checksum_matches"),
            "missing": ("unavailable_by_configuration", "not_installed"),
            "unpinned": ("misconfigured", "no_pinned_checksum"),
            "mismatch": ("broken", "checksum_mismatch")}.get(st, ("unverified", "unreadable"))


def _c_browser_lane():
    """The browser Friday drives for tasks: its automation library is there.
    Nothing is started; a closed browser is normal (it closes when idle)."""
    import importlib.util
    if importlib.util.find_spec("playwright") is None:
        return "unavailable_by_configuration", "playwright_not_installed"
    from agent_friday.services import browser_session
    s = browser_session.current()
    if s is not None and getattr(s, "running", False):
        return "healthy", "running"
    # the browser it downloads is found only when one starts: not here
    return "unverified", "library_installed_browser_not_started_here"


def _c_channels():
    """Remote channels (Telegram, Discord): each switched on has a readable
    token; one that should be listening and is held by a rule says so."""
    from agent_friday.services import safe_mode
    from agent_friday.services.channels import manager
    st = manager.status()
    if not st.get("enabled"):
        return "unavailable_by_configuration", "channels_off"
    on = {n: c for n, c in (st.get("channels") or {}).items() if c.get("enabled")}
    if not on:
        return "unavailable_by_configuration", "no_channel_switched_on"
    # counts, not names: the report should not say which apps the owner uses
    states = [(c.get("health") or {}).get("source_state") for c in on.values()]
    if "present_but_unreadable" in states:
        return "broken", "token_unreadable:%d" % states.count("present_but_unreadable")
    if "missing" in states:
        return "misconfigured", "token_missing:%d" % states.count("missing")
    odd = [s for s in states if s not in ("running", "not-running")]
    if odd:
        return "unverified", "token_not_checked:%d" % len(odd)
    idle = states.count("not-running")
    if idle and not safe_mode.allows("channels"):
        return "blocked_by_policy", "not_started_in_safe_mode:%d" % idle
    if idle:
        return "degraded", "switched_on_not_running:%d" % idle
    return "healthy", "running:%d" % len(on)


def _c_phone():
    """The phone line: switched off, or on with its credentials readable."""
    from agent_friday.phone import config as phone_config
    st = phone_config.status()
    if not st.get("enabled"):
        return "unavailable_by_configuration", "phone_off"
    states = {k: st.get(k) for k in ("auth_token", "api_key_secret")}
    if "unreadable" in states.values():
        return "broken", "credential_unreadable"
    if states.get("auth_token") != "stored":
        return "misconfigured", "auth_token_missing"
    from agent_friday.services import safe_mode
    if not safe_mode.allows("phone"):
        return "blocked_by_policy", "not_started_in_safe_mode"
    if states.get("api_key_secret") != "stored":
        return "degraded", "outbound_key_missing"
    return "healthy", "configured"


def _c_crashes():
    from agent_friday.services import crash_forensics
    n = crash_forensics.crash_count()
    return ("degraded", "crashes_recorded:%d" % n) if n else ("healthy", "none_recorded")


def _c_recall():
    # services/recall_health: a long-term memory store that could not be read in the last ten
    # minutes (NS-32.5-1). Fixed codes only: the source names, never their contents.
    from agent_friday.services import recall_health
    s = recall_health.status()
    if s["state"] == "degraded":
        return "degraded", "recall_degraded:" + ",".join(s["sources"])
    return "healthy", "ok"


def _c_search_index():
    # services/conversation_fts.health (NS-32.6-1): a damaged index is rebuilt from the
    # transcripts, by itself when found and by the owner's press here.
    from agent_friday.services import conversation_fts
    state = conversation_fts.health()["state"]
    if state == "ok":
        return "healthy", "ok"
    if state == "missing":
        return "healthy", "not_built_yet"
    if state == "repairing":
        return "repairing", "conversation_search_index_rebuilding"
    return "degraded", "conversation_search_index_" + state


def _c_update_signature():
    # BD-16's integrity dashboard: whether the last release Friday checked was signed by a key
    # this copy trusts (services/update_check, stored state only: no network). While the copy
    # has no release key, builds ship unsigned with a SHA-256 by the owner's decision: off, not
    # a failure.
    from agent_friday.services import trust_keys, update_check
    if not trust_keys.RELEASE_KEYS:
        return "unavailable_by_configuration", "no_release_key"
    state = update_check.read_state()
    verification = state.get("verification")
    if verification == "verified":
        return "healthy", "last_release_verified"
    if verification in update_check.refused_states():
        return "degraded", "last_release_refused:%s" % verification
    if not state.get("last_success_ts"):
        return "unverified", "not_checked_yet"
    if verification is None and not state.get("update_available"):
        # Checked, and nothing newer was published: there was no release to verify.
        return "healthy", "no_newer_release"
    if verification == "no_trusted_key":
        return "unverified", "last_release_checked_before_the_key"
    # Only fixed codes reach the report (it is shareable): an unknown stored value is not echoed.
    return "unverified", "last_release_unknown"


def _c_boot():
    from agent_friday.services import boot_guard
    n = int(boot_guard._read_attempt().get("consecutive_failures") or 0)
    return ("degraded", "failed_starts:%d" % n) if n else ("healthy", "ok")


def _c_migrations():
    """The data folder's schema (services/migrations): current, from a newer
    Friday, held in safe mode after a failed upgrade, or mid-step."""
    from agent_friday.services import migrations, safe_mode
    home = _home()
    have, want = migrations.current(home), migrations.latest()
    if have > want:
        return "misconfigured", "newer_folder:%d>%d" % (have, want)
    if safe_mode.marker_reason() == safe_mode.MIGRATION_FAILED:
        return "broken", "upgrade_failed_safe_mode_held"
    if migrations._journal_path(home).exists():
        return "repairing", "migration_journal_beside_folder"
    if have < want:                      # runs at the next start (server.py), nothing is running now
        return "unverified", "migration_pending_next_start:%d<%d" % (have, want)
    return "healthy", "schema_%d" % have


def _c_after_upgrade():
    """What an upgrade or restore left open, and rebuilds still waiting
    (services/upgrade_review, services/derived_rebuild). Counts only."""
    from agent_friday.services import derived_rebuild, upgrade_review
    n = upgrade_review.counts(_home()).get("open", 0)
    waiting = derived_rebuild.state(_home()).get("waiting") or []
    if n:
        return "degraded", "open_items:%d" % n
    if waiting:
        return "repairing", "rebuilds_waiting:%d" % len(waiting)
    return "healthy", "nothing_open"


CHECKS = (
    ("app_files", "application files", _c_versions),
    ("settings", "settings schema", _c_settings),
    ("settings_read", "settings schema", _c_settings_read),
    ("vault", "vault accessibility", _c_vault),
    ("governance_key", "key and credential availability", _c_governance_key),
    ("receipt_chain", "receipt chains", _c_ledger),
    ("receipt_completeness", "receipt chains", _c_receipt_completeness),
    ("outward_terminal", "receipt chains", _c_outward_terminal),
    ("authority", "action gate", _c_authority),
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
    ("recall", "memory and search indexes", _c_recall),
    ("search_index", "memory and search indexes", _c_search_index),
    ("model_files", "model files and checksums", _c_models),
    ("local_brain", "GPU and resource state", _c_local_brain),
    ("gpu", "GPU and resource state", _c_gpu),
    ("local_voice", "voice", _c_local_voice),
    ("document_engine", "document engine", _c_document_engine),
    ("browser_lane", "browser lane", _c_browser_lane),
    ("channels", "remote channels", _c_channels),
    ("phone", "remote channels", _c_phone),
    ("crashes", "application files", _c_crashes),
    ("boot", "update status", _c_boot),
    ("update_signature", "update status", _c_update_signature),
    ("migrations", "update status", _c_migrations),
    ("after_upgrade", "update status", _c_after_upgrade),
)


# ── errors, drift, performance ──────────────────────────────────────────────

_EXC = re.compile(r"\b([A-Z][A-Za-z0-9_]*(?:Error|Exception))\b")
#: friday.log's own format (core/__init__.py): "<time> LEVEL    logger.name — message"
_LOGGER = re.compile(r"^\S+ (ERROR|CRITICAL)\s+([A-Za-z_][A-Za-z0-9_.]{0,80}) \u2014 ")
#: Loggers reported by name; any other is counted as "other".
_LOGGER_PREFIXES = ("agent_friday", "friday", "werkzeug", "flask", "urllib3", "root")


def _exception_name(t: str) -> str:
    """A built-in exception's name as it is; anything else (a class a library
    or an extension named) is counted, not named."""
    import builtins
    return t if isinstance(getattr(builtins, t, None), type) else "other_exception"


def _logger_name(n: str) -> str:
    return n if n.split(".")[0] in _LOGGER_PREFIXES else "other_logger"


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
        m = _LOGGER.match(line)
        if m:
            name = _logger_name(m.group(2))
            by_logger[name] = by_logger.get(name, 0) + 1
            for t in _EXC.findall(line):
                t = _exception_name(t)
                by_type[t] = by_type.get(t, 0) + 1
    crash = _tail(_home() / "logs" / "crashes.log")
    fatal = sum(crash.count(m) for m in ("Fatal Python error:", "Windows fatal exception:"))
    for t in _EXC.findall(crash):
        t = _exception_name(t)
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


def _safety_metrics(checks: list) -> dict:
    """The safety and privacy metrics held at zero (§35.7-1): counts only."""
    try:
        from agent_friday.services import safety_metrics
        m = safety_metrics.metrics(checks)
        return {"window_days": m["window_days"],
                "metrics": [{k: x[k] for k in ("id", "value", "target", "unit", "state")} for x in m["metrics"]],
                "attention": m["attention"], "unknown": m["unknown"]}
    except Exception as e:  # noqa: BLE001
        return {"error": "unavailable:" + type(e).__name__}


def _known_issues() -> dict:
    """How many known issues this release lists, by section (§36.0-7)."""
    try:
        from agent_friday.services import known_issues
        return known_issues.summary()
    except Exception as e:  # noqa: BLE001
        return {"available": False, "error": type(e).__name__}


def _after_upgrade() -> dict:
    """Counts only (§37.3-9, §37.3-10): open items the owner should look at,
    by code, and the index rebuilds still waiting. No sentences, no names."""
    out: dict = {}
    try:
        from agent_friday.services import upgrade_review
        out["unresolved"] = upgrade_review.counts()
    except Exception as e:  # noqa: BLE001
        out["unresolved"] = {"error": type(e).__name__}
    try:
        from agent_friday.services import derived_rebuild
        st = derived_rebuild.state()
        out["rebuilds_waiting"] = [w["index"] for w in st["waiting"]]
        out["restore_rebuild_waiting"] = bool(st.get("restore_waiting"))
        out["rebuilds_failed_last"] = sorted({r["index"] for r in st["results"] if not r.get("ok")})
    except Exception as e:  # noqa: BLE001
        out["rebuilds_waiting"] = {"error": type(e).__name__}
    return out


def _stores() -> dict:
    """Every store with its version, size and place in the data folder
    (services/store_inventory.py, §37.3-1). Store names and locations are
    Friday's own; names it does not know are counted, never named."""
    try:
        from agent_friday.services import store_inventory
        return store_inventory.public(store_inventory.cached(_home()))
    except Exception as e:  # noqa: BLE001
        return {"error": "unavailable:" + type(e).__name__}


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
        "content": "none: versions, checks, error signatures, drift, performance and the store inventory only",
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
    report["known_issues"] = _known_issues()
    report["safety_metrics"] = _safety_metrics(report["checks"])
    report = _clean(report, _private_markers())
    # rebuilt from Friday's own constants and numbers (store_inventory.public),
    # so the general filter above, which could blank a store whose name holds
    # a short user name, is not needed for it
    report["stores"] = _stores()
    report["after_upgrade"] = _after_upgrade()
    # Friday's own fixed words, so added after the general filter above
    try:                                 # deterministic repairs only (services/doctor_repairs)
        from agent_friday.services import doctor_repairs
        report["repairs"] = doctor_repairs.offered(report["checks"])
    except Exception as e:  # noqa: BLE001
        report["repairs"] = {"error": type(e).__name__}
    return report


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
    sm = report.get("safety_metrics") or {}
    if sm.get("metrics"):
        lines += ["", "Safety metrics, last %s days (held at zero; self-tests and restores at 100%%): %s" % (
            sm.get("window_days"), "; ".join("%s %s" % (x.get("id"), "unknown" if x.get("value") is None
                                                         else x.get("value")) for x in sm["metrics"]))]
    ki = report.get("known_issues") or {}
    if ki.get("available"):
        lines += ["", "Known issues in this release: %s, listed on the Doctor page (%s)" % (
            ki.get("count"), "; ".join("%s: %s" % (s.get("section"), s.get("count"))
                                       for s in ki.get("by_section") or []))]
    elif "known_issues" in report:
        lines += ["", "Known issues: the list is not in this install."]
    inv = report.get("stores")
    if isinstance(inv, dict) and isinstance(inv.get("stores"), list):
        from agent_friday.services import store_inventory
        lines += [""] + store_inventory.render_text(inv)
    elif isinstance(inv, dict) and inv.get("error"):
        lines += ["", "Stores: %s" % inv["error"]]
    rep = report.get("repairs")
    if isinstance(rep, dict) and rep and "error" not in rep:
        lines += ["", "Repairs offered (none needs a model):"]
        for cid, rs in sorted(rep.items()):
            for r in rs if isinstance(rs, list) else []:
                lines.append("  %-16s %s%s" % (cid, r.get("label"),
                                               (" (this page: %s)" % r["where"]) if r.get("where") else ""))
    au = report.get("after_upgrade")
    if isinstance(au, dict):
        un = au.get("unresolved") if isinstance(au.get("unresolved"), dict) else {}
        wait = au.get("rebuilds_waiting") if isinstance(au.get("rebuilds_waiting"), list) else []
        lines += ["", "After upgrades and restores: %s open item(s)%s; rebuilds waiting: %s" % (
            un.get("open", "unknown"),
            (" (" + ", ".join("%s %s" % (k, v) for k, v in sorted((un.get("by_code") or {}).items())) + ")")
            if un.get("by_code") else "",
            ", ".join(wait) or "none")]
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
    from agent_friday.paths import contained
    try:
        p = contained(reports_dir(), f"{report_id}.{kind}")
    except ValueError:
        return None
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

"""
FRIDAY Desktop v4.4 — Phase B OS Backend (slim entry point).

The 18k-line monolith was decomposed into:
  • core.py            — Flask app, shared state/globals, auth, settings, vault keys
  • services/*.py       — business logic (model_router, agent/tools, news, calendar,
                          wiki, code, creations, voice, notifications, …)
  • routes/*.py         — one Flask Blueprint per domain (chat, voice, news, …)

This file just wires them together: register blueprints, start the background
daemons, and run the dev server. Powered by FutureSpeak.AI
"""
import os
import sys
import threading
import logging

import agent_friday.core as core

_log = logging.getLogger("friday.server")
from agent_friday.core import app, sock, _TESTING

# Explicit imports from the defining modules — the star-import cascade is
# gone. Anything else addressed as `server.<name>` (tests, user scripts) is
# resolved by the module __getattr__ facade at the bottom of this file.
from agent_friday.core import (
    CHAT_HISTORY_FILE,
    CREATIONS_DIR,
    FRIDAY_DIR,
    FRIDAY_PASSWORD,
    WIKI_DIR,
    _HAS_BEHAVIORAL_MONITOR,
    _load_settings,
    get_behavioral_monitor,
)  # noqa: E501
from agent_friday.services.agent import (
    _get_vault_key,
    _mcp_boot,
    _migrate_vault_plaintext,
    _start_kill_hotkey,
)  # noqa: E501
from agent_friday.services.model_router import (
    _generate_wiki_indexes,
    _load_recent_session_summary_on_startup,
)  # noqa: E501
from agent_friday.services.news_engine import (
    _news_archiver_loop,
)  # noqa: E501
from agent_friday.services.notifications import (
    _network_monitor_loop,
    _notification_trigger_loop,
)  # noqa: E501
from agent_friday.services.scheduler import start_scheduler
from agent_friday.services.predictive_workspaces import (
    _predictive_prewarm_loop,
    _prewarm_predicted_boot,
)  # noqa: E501
from agent_friday.services.voice_engine import (
    _notif_engine,
)  # noqa: E501

# ── Blueprints — auto-discovered from routes/*.py ─────────────────────────
# Adding a new route file is now a single-file operation: create
# routes/my_feature.py with a Blueprint named *_bp and it registers itself
# automatically on the next server start.  No manual import needed here.
import importlib as _importlib
from pathlib import Path as _Path
from flask import Blueprint as _Blueprint

# Explicit route-module manifest — the source of truth for the FROZEN (.exe)
# build. In a normal source run, pkgutil auto-discovers the routes/ directory
# (so a new route file is zero-touch), but a PyInstaller onefile has no loose
# .py files to enumerate — the modules live in the packed archive — so filesystem
# and TOC enumeration both come back empty and the entire API 404's. This list
# is the frozen fallback. tests/unit/test_blueprint_discovery.py fails if it
# drifts from the actual routes/ directory, so it can't silently go stale.
ROUTE_MODULES = [
    'activity',
    'ambient', 'budget_policy', 'calendar', 'channels', 'chat', 'code',
    'compute', 'connectors', 'contacts', 'content_pipeline', 'context',
    'control', 'core_routes',
    'costs', 'creations', 'creative_pipeline', 'defederation', 'dreaming', 'edition',
    'ext_security', 'federation', 'finance_health', 'futurespeak', 'goals',
    'google', 'google_accounts', 'hooks', 'insights', 'jobs', 'knowledge_graph',
    'learning', 'messages',
    'news', 'notifications', 'onboarding', 'orchestrator', 'ownership',
    'persona', 'platform', 'projects', 'scheduler', 'seat_gate', 'skills', 'soul', 'tasks', 'todos',
    'user_model', 'voice', 'voice_context', 'wiki', 'work_log', 'workflows',
    'workspace_studio',
]


def _discover_and_register_blueprints(flask_app):
    # Enumerate route modules over the PACKAGE, not a filesystem glob of
    # Path(__file__).parent/"routes": that broke two ways — (1) the repo-root
    # server.py shim exec()s this file so __file__ points at the shim, and (2) a
    # PyInstaller-frozen build has no loose route files on disk. Either way the
    # old glob registered ZERO blueprints and the whole API 404'd. We use
    # pkgutil for source (auto-discovers new files) and fall back to the explicit
    # ROUTE_MODULES manifest when nothing is found (the frozen .exe path).
    import pkgutil
    import agent_friday.routes as _routes_pkg
    _prefix = _routes_pkg.__name__ + "."
    _names: set = set()
    try:
        for _mi in pkgutil.iter_modules(_routes_pkg.__path__, _prefix):
            _leaf = _mi.name.rsplit(".", 1)[-1]
            if not _leaf.startswith("_"):
                _names.add(_mi.name)
    except Exception:
        pass
    if not _names:
        _names = {_prefix + _n for _n in ROUTE_MODULES}
    _registered: list = []
    _failed: list = []
    for _name in sorted(_names):
        try:
            _mod = _importlib.import_module(_name)
        except Exception as _e:
            _failed.append((_name, str(_e)))
            _log.warning("failed to import %s: %s", _name, _e)
            continue
        for _val in vars(_mod).values():
            if isinstance(_val, _Blueprint) and _val not in _registered:
                flask_app.register_blueprint(_val)
                _registered.append(_val)
    if _failed:
        _log.warning("Blueprint auto-discovery: %d registered, %d skipped",
                     len(_registered), len(_failed))
    return _registered

_discover_and_register_blueprints(app)


# ── Back-compat facade (PEP 562) ──────────────────────────────────
# The star-import cascade used to make every app symbol addressable as
# `server.<name>`; the test suite and user scripts rely on that. Resolve
# missing attributes against the layer modules in the cascade's original
# shadowing order (highest layer wins, core last).
_FACADE_MODULES = (
    "agent_friday.services.notifications", "agent_friday.services.ambient_awareness",
    "agent_friday.services.futurespeak", "agent_friday.services.misc_engine",
    "agent_friday.services.predictive_workspaces", "agent_friday.services.voice_engine",
    "agent_friday.services.agent", "agent_friday.services.creations", "agent_friday.services.news_engine",
    "agent_friday.services.code_engine", "agent_friday.services.model_router",
    "agent_friday.services.calendar_engine", "agent_friday.services.wiki_engine", "agent_friday.core",
)


def __getattr__(name):
    import importlib
    for _mod_name in _FACADE_MODULES:
        _m = sys.modules.get(_mod_name)
        if _m is None:
            try:
                _m = importlib.import_module(_mod_name)
            except Exception:
                continue
        if hasattr(_m, name):
            return getattr(_m, name)
    raise AttributeError(f"module 'server' has no attribute {name!r}")


def _residency_boot():
    """Plan the machine, bind the plan to capability_routing, boot the GPU.

    Every step is individually tolerant. The ordering matters:

    1. **Bind before booting.** The binding is what dispatch reads; the boot
       is what makes VRAM match it. Binding first means that even if the GPU
       work fails, the seats still point somewhere real.
    2. **Gate-checked binding.** `seat_binding` refuses to seat a local model
       that is not dual-green, so a plan can never hand dispatch a model it
       will refuse at runtime.
    3. **`embedding` is never bound** — D5 gates that on a re-index path that
       does not exist, and the mismatch fails silently as memory loss.
    """
    try:
        from agent_friday import core as _core
        from agent_friday.services import hardware_profile as _hwp
        from agent_friday.services import residency_catalog as _rc
        from agent_friday.services import residency_arbiter as _ra
        from agent_friday.services import seat_binding as _sb
    except Exception as e:                       # pragma: no cover - import guard
        print(f"  Residency: unavailable ({e})")
        return

    try:
        profile = _hwp.get()
        entries = _rc.installed_entries(profile)
        arb = _ra.Arbiter(profile=profile, entries=entries)
        plan = arb.compute_plan()
        print("  Residency: %s" % _hwp.summary(profile))

        settings = _core._load_settings() or {}
        prop = _sb.apply(plan, settings)
        if prop.get("changes"):
            _core._save_settings(settings)
        print("  Residency seats:\n%s" % _sb.describe(prop))
    except Exception as e:
        print(f"  Residency: planning/binding failed ({e})")
        return

    try:
        _ra.ARBITER = arb                        # process-wide handle
        arb.boot()
        print("  Residency: booted to plan (%s)" % arb.state)
    except Exception as e:
        # A failed boot leaves the seats bound and the machine usable; the
        # arbiter reports DEGRADED rather than the server refusing to start.
        print(f"  Residency: boot failed, seats still bound ({e})")


# ── Background daemons (skipped under FRIDAY_TESTING=1) ───────────
if not _TESTING:
    # Decrypt any onboarding-stored provider API keys into the environment so
    # provider availability + the SDK clients see them (no plaintext in settings).
    try:
        from agent_friday.services.credential_store import bootstrap_provider_env
        _loaded_keys = bootstrap_provider_env()
        if _loaded_keys:
            print(f"  Provider keys: loaded {_loaded_keys} from encrypted store")
    except Exception as _pk_err:
        print(f"  Provider keys: skipped ({_pk_err})")

    threading.Thread(target=_start_kill_hotkey, daemon=True).start()
    # Closed-loop learning: nightly SkillOpt auto-research at 3:30 AM Central is
    # disabled for general release; re-enable once there are 50+ skills.
    # register_daily_job("skillopt-nightly", 3, 30, _skillopt_nightly)

    # Internal scheduler (Part A) — generalizes the old daily-only loop to
    # interval/daily/weekly triggers with a user-editable registry, run history,
    # and retries. Registers built-ins, seeds/reconciles schedules.json, and
    # starts the 60s tick thread (replaces _register_default_daily_jobs +
    # _daily_scheduler_loop).
    #
    # Content pipeline publisher (docs/CONTENT_PIPELINE_SPEC.md §6.2): register
    # the content_publisher/content_analytics builtin tasks BEFORE the scheduler
    # seeds schedules.json so they materialize on first run. Tolerant while the
    # publisher module lands — a missing/broken module never blocks boot.
    try:
        from agent_friday.services import publisher as _content_publisher
        _content_publisher.start()
    except Exception as _cp_err:
        print(f"  Content publisher: skipped ({_cp_err})")
    start_scheduler()

    if _notif_engine:
        threading.Thread(target=_notification_trigger_loop, daemon=True).start()

    # Persistent news archive: grow the per-day article store on the RSS cadence.
    threading.Thread(target=_news_archiver_loop, daemon=True).start()

    # Offline-first resilience: probe connectivity every 30s, auto-switch to
    # local inference when offline, flush the queue + refresh feeds when back.
    threading.Thread(target=_network_monitor_loop, daemon=True).start()

    # MCP connectors: launch configured MCP servers and register their tools.
    _mcp_boot()

    # Predictive workspaces: pre-warm the workspaces this moment usually wants,
    # then keep them warm as the time-of-day shifts.
    threading.Thread(target=_prewarm_predicted_boot, daemon=True).start()
    threading.Thread(target=_predictive_prewarm_loop, daemon=True).start()

    # Ambient connector-health monitoring: watch every connector (Google +
    # MCP) and push a notification on a connected->down edge.
    from agent_friday.services.connectors import connector_health_monitor_loop
    threading.Thread(target=connector_health_monitor_loop, daemon=True).start()

    # Residency: compute the placement plan, bind it into capability_routing,
    # and bring the GPU to that plan (decision Q9).
    #
    # Until this existed the whole residency layer governed nothing: the plan
    # sat in golden files while the live server ran gemma4:12b at a 262144
    # context, 71% of it on the CPU — exactly the placement the layer exists to
    # prevent. Seats were reconciled by hand, which is how `reasoning` came to
    # point at a deleted model.
    #
    # Runs on a daemon thread and is FAIL-SOFT by construction: a residency
    # problem must never stop Friday from starting. Opt out with
    # FRIDAY_NO_ARBITER=1.
    if os.environ.get("FRIDAY_NO_ARBITER") != "1":
        threading.Thread(target=_residency_boot, daemon=True).start()


def _resolve_bind_port():
    """Resolve the port to bind, tolerating an already-in-use port.

    Honours FRIDAY_PORT (default 3000). If that port is busy, scan the next
    few ports for a free one so a second launch (or a leftover process) does
    not crash with a raw traceback. Returns (port, requested, fell_back).
    """
    import socket as _socket
    try:
        requested = int(os.environ.get('FRIDAY_PORT', '3000'))
    except ValueError:
        requested = 3000

    def _free(p):
        # NOTE: do NOT set SO_REUSEADDR here. On Windows SO_REUSEADDR lets a
        # socket bind a port another socket is actively using, which would make
        # this probe report a busy port as free. A plain bind fails cleanly with
        # EADDRINUSE when the port is taken — exactly what we want to detect.
        with _socket.socket(_socket.AF_INET, _socket.SOCK_STREAM) as s:
            try:
                s.bind(('127.0.0.1', p))
                return True
            except OSError:
                return False

    if _free(requested):
        return requested, requested, False
    for candidate in range(requested + 1, requested + 11):
        if _free(candidate):
            return candidate, requested, True
    # Nothing free in the scan window — return the requested port and let the
    # caller surface a clean message after the bind fails.
    return requested, requested, True


def _fail_loud_and_exit(msg):
    """Log to friday.log, print, and (on Windows) raise a visible message
    box, then exit(1). Shared by the single-instance-lock failure and the
    bind-failure paths below — a losing process must never die quietly."""
    print(f"\n  {msg}")
    try:
        import logging as _lg
        _lg.getLogger("friday.server").error(msg)
    except Exception:
        pass
    if sys.platform == "win32":
        try:
            import ctypes
            ctypes.windll.user32.MessageBoxW(None, msg, "Agent Friday failed to start", 0x10)
        except Exception:
            pass
    sys.exit(1)


def _acquire_single_instance_lock():
    """OS-level exclusive lock, held for this process's entire lifetime —
    the authoritative guard against a second production server ever running
    (docs: toolcall-integrity-v5, 2026-08-13 double-launch incident: two
    server processes born the same second; the loser sat alive at ~4MB/0%
    CPU with NOTHING logged, because it never got far enough to log
    anything). Checked here, before any heavy startup work (wiki indexing,
    vault setup, model loading) — so a losing process fails in milliseconds,
    not after minutes of silent, invisible partial init.

    Released automatically by the OS the instant this process exits, for
    ANY reason — clean shutdown, crash, or a hard kill — so there is no
    stale-lock cleanup to get wrong.

    Returns the open file handle (caller MUST keep a reference alive for
    the process's lifetime — closing it releases the lock) or None if
    another live process already holds it.
    """
    lock_path = FRIDAY_DIR / "friday_server.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    # a+b starts at EOF, not byte 0 — an explicit seek(0) before locking is
    # required, or two handles opened against a non-empty file lock
    # DIFFERENT byte positions (both "succeed", no conflict detected at all).
    fh = open(lock_path, "a+b")
    try:
        fh.seek(0)
        if sys.platform == "win32":
            import msvcrt
            msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        fh.close()
        return None
    # Diagnostic metadata (PID, start time) goes in a SEPARATE, unlocked
    # file — Windows file locking is mandatory, not advisory, so writing
    # this into the locked region itself would make it unreadable by
    # anything but the holder, defeating its purpose (an external "who's
    # holding the lock?" check, including this same function's own
    # failure-diagnostic path in __main__ below).
    try:
        from datetime import datetime as _dt
        pid_path = FRIDAY_DIR / "friday_server.pid"
        pid_path.write_text(f"{os.getpid()}\n{_dt.now().isoformat()}\n", encoding="utf-8")
    except Exception:
        pass
    return fh


# ═══════════════════════════════════════════════════════════════
#  MAIN
# ═══════════════════════════════════════════════════════════════
if __name__ == '__main__':
    # Make stdout/stderr encoding-safe for piped/redirected Windows consoles.
    for _stream in (sys.stdout, sys.stderr):
        try:
            _stream.reconfigure(encoding='utf-8', errors='replace')
        except Exception:
            pass

    # Single-instance guard — FIRST, before anything else can hang or race.
    _instance_lock_fh = _acquire_single_instance_lock()
    if _instance_lock_fh is None:
        _lock_path = FRIDAY_DIR / "friday_server.lock"
        _pid_path = FRIDAY_DIR / "friday_server.pid"
        _holder_pid = "unknown"
        try:
            _holder_pid = _pid_path.read_text(encoding="utf-8").splitlines()[0]
        except Exception:
            pass
        _guard_msg = (
            f"Agent Friday could not start: another server process "
            f"(PID {_holder_pid}) already holds the single-instance lock "
            f"at {_lock_path}. Quit it from the tray icon, or end its "
            f"process, then relaunch."
        )
        try:
            import urllib.request as _ur
            _probe_port = os.environ.get('FRIDAY_PORT', '3000')
            with _ur.urlopen(f"http://127.0.0.1:{_probe_port}/api/health", timeout=3):
                _guard_msg += (" That server IS responding to health checks right "
                              "now — it looks healthy, not hung.")
        except Exception:
            _guard_msg += (" That server is NOT responding to health checks right "
                           "now — it may be hung; ending its process is likely safe.")
        _fail_loud_and_exit(_guard_msg)

    _port, _requested, _fell_back = _resolve_bind_port()
    # Publish the port we ACTUALLY bound so loopback URLs built elsewhere —
    # notably the Google OAuth redirect_uri — follow the fallback instead of
    # staying pinned to 3000 and failing with redirect_uri_mismatch.
    core.set_server_port(_port)
    _url = f"http://localhost:{_port}"
    print()
    print("  ╔══════════════════════════════════════════════╗")
    print("  ║   FRIDAY Desktop v5.0 — Super Agent         ║")
    print("  ╠══════════════════════════════════════════════╣")
    print(f"  ║  {_url:<42}║")
    print("  ║  Flask + Gemini API + Three.js Holographic   ║")
    print("  ║  Dock · Floating Windows · Persistent Chat   ║")
    print("  ║  Press Ctrl+C to stop                        ║")
    print("  ╚══════════════════════════════════════════════╝")
    if _fell_back:
        print(f"  Note: port {_requested} was busy — using {_port} instead.")
        print(f"        Set FRIDAY_PORT to pick a specific port.")
    print()
    print(f"  Wiki:      {WIKI_DIR}")
    print(f"  Friday:    {FRIDAY_DIR}")
    print(f"  Creations: {CREATIONS_DIR}")
    print(f"  Chat Log:  {CHAT_HISTORY_FILE}")
    print()

    # Pre-generate wiki directory indexes for index-first navigation.
    try:
        _generate_wiki_indexes()
        print("  Wiki indexes: generated")
    except Exception as _wi_err:
        print(f"  Wiki indexes: skipped ({_wi_err})")

    # Derive the vault key (if FRIDAY_VAULT_PASSPHRASE is set) and encrypt plaintext.
    # Failure here is a security event — log at ERROR and print a prominent banner.
    import logging as _vlog_mod
    _vlog = _vlog_mod.getLogger(__name__)
    try:
        _get_vault_key()
        _migrate_vault_plaintext()
    except Exception as _vk_err:
        _vlog.error(
            "CRITICAL: Vault encryption setup FAILED: %s. "
            "Sensitive data is NOT encrypted at rest.",
            _vk_err,
        )
        print()
        print("  ╔════════════════════════════════════════════════════════════╗")
        print("  ║  SECURITY WARNING: VAULT ENCRYPTION FAILED                ║")
        print(f"  ║  Error: {str(_vk_err)[:52]:<52}║")
        print("  ║  Sensitive vault data is stored as PLAINTEXT at rest.     ║")
        print("  ║  Set FRIDAY_VAULT_PASSPHRASE or run: friday vault-setup   ║")
        print("  ╚════════════════════════════════════════════════════════════╝")
        print()

    # Session memory: surface the most recent end-of-day summary + prime the
    # emotional arc so cross-session continuity and tone adaptation are ready
    # from the first turn.
    try:
        _load_recent_session_summary_on_startup()
    except Exception as _sm_err:
        print(f"  Session memory: skipped ({_sm_err})")

    # Behavioral anomaly monitor — initialise the singleton before first request.
    if _HAS_BEHAVIORAL_MONITOR:
        try:
            _bm = get_behavioral_monitor()
            _bm_hist = _bm.get_history_summary()
            print(f"  Behavioral monitor: ready ({_bm_hist.get('count', 0)} prior traces)")
        except Exception as _bm_boot_err:
            print(f"  Behavioral monitor: skipped ({_bm_boot_err})")

    # Default bind: loopback only (127.0.0.1).  Non-loopback binds expose the
    # server to the network; TLS is required for any such configuration.
    bind_host = os.environ.get("FRIDAY_BIND_HOST", "127.0.0.1")
    _LOOPBACK_HOSTS = {"127.0.0.1", "::1", "localhost"}
    _ssl_context = None
    if bind_host not in _LOOPBACK_HOSTS:
        # R1 (Fable 5 adversarial fixes): refuse to start a network-exposed
        # server with no remote auth key. The request-time guard already denies
        # keyless remote requests (fail-closed), but refusing to boot at all is
        # belt-and-suspenders — the operator finds out NOW, not via 403s later.
        if not (os.environ.get("FRIDAY_REMOTE_KEY", "") or os.environ.get("FRIDAY_PASSWORD", "")):
            if os.environ.get("FRIDAY_ALLOW_KEYLESS_BIND", "").lower() in ("1", "true", "yes"):
                print("  WARNING: FRIDAY_ALLOW_KEYLESS_BIND=1 — network bind with NO auth key.")
                print("           Every remote request will be REJECTED until a key is set.")
            else:
                print()
                print("  ╔══════════════════════════════════════════════════════════════╗")
                print("  ║  REFUSING TO START: network bind without an auth key         ║")
                print("  ║                                                              ║")
                print(f"  ║  FRIDAY_BIND_HOST={bind_host:<43}║")
                print("  ║  exposes the server beyond this machine, but neither         ║")
                print("  ║  FRIDAY_REMOTE_KEY nor FRIDAY_PASSWORD is set.               ║")
                print("  ║                                                              ║")
                print("  ║  Set FRIDAY_REMOTE_KEY to a strong unique value, or bind     ║")
                print("  ║  to 127.0.0.1 (the default). To start anyway (all remote     ║")
                print("  ║  requests still denied): FRIDAY_ALLOW_KEYLESS_BIND=1         ║")
                print("  ╚══════════════════════════════════════════════════════════════╝")
                sys.exit(1)
        _tls_cert = os.environ.get("FRIDAY_TLS_CERT", "")
        _tls_key_path = os.environ.get("FRIDAY_TLS_KEY", "")
        if _tls_cert and _tls_key_path:
            import ssl as _ssl
            _ssl_context = _ssl.SSLContext(_ssl.PROTOCOL_TLS_SERVER)
            _ssl_context.load_cert_chain(_tls_cert, _tls_key_path)
            print(f"  TLS: enabled ({_tls_cert})")
        elif os.environ.get("FRIDAY_SKIP_TLS_WARN", "") not in ("1", "true"):
            print()
            print("  ╔══════════════════════════════════════════════════════════════╗")
            print("  ║  SECURITY WARNING: Network bind without TLS                 ║")
            print("  ║                                                              ║")
            print(f"  ║  FRIDAY_BIND_HOST={bind_host:<43}║")
            print("  ║  No TLS certificate is configured.  All traffic —           ║")
            print("  ║  conversations, vault data, API tokens — travels             ║")
            print("  ║  in PLAINTEXT over the network.                              ║")
            print("  ║                                                              ║")
            print("  ║  To enable TLS:                                              ║")
            print("  ║    FRIDAY_TLS_CERT=/path/to/cert.pem                        ║")
            print("  ║    FRIDAY_TLS_KEY=/path/to/key.pem                          ║")
            print("  ║  To suppress this warning: FRIDAY_SKIP_TLS_WARN=1           ║")
            print("  ╚══════════════════════════════════════════════════════════════╝")
            print()
            if os.environ.get("FRIDAY_REQUIRE_TLS", "").lower() in ("1", "true", "yes"):
                print("  FRIDAY_REQUIRE_TLS=1 — refusing to start without TLS.")
                sys.exit(1)

    # R2 (Fable 5 adversarial fixes): egress-gate startup self-test. Seal a
    # known-SENSITIVE probe once; if the gate cannot withhold it, cloud routing
    # is disabled (model_router._seal_or_block refuses every cloud send) until
    # the gate is fixed — the failure mode is caught at boot, not at first leak.
    try:
        from agent_friday.services.egress_gate import startup_self_test as _gate_self_test
        _gate_res = _gate_self_test()
        if _gate_res.get("ok"):
            print("  Egress gate: self-test passed (sensitive probe withheld)")
        else:
            print()
            print("  ╔══════════════════════════════════════════════════════════════╗")
            print("  ║  SECURITY WARNING: EGRESS GATE SELF-TEST FAILED              ║")
            print(f"  ║  {str(_gate_res.get('error', 'unknown'))[:58]:<60}║")
            print("  ║  Cloud model routing is DISABLED until the gate is fixed.    ║")
            print("  ║  Local models (Ollama) continue to work normally.            ║")
            print("  ╚══════════════════════════════════════════════════════════════╝")
            print()
    except Exception as _gate_err:
        print(f"  Egress gate: self-test errored ({_gate_err}) — cloud sends stay fail-closed per call")

    # Voice model sanity check (H8): a stale/renamed Live model id surfaces as a
    # connect failure that reads like an auth error but isn't. Warn once at boot
    # so it's diagnosable; the same status shows in Settings → Voice.
    try:
        from agent_friday.services.voice_engine import validate_live_model as _vlm
        _mv = _vlm()
        if not _mv.get("ok") and _mv.get("status") in ("unknown", "retired"):
            print(f"  Voice: {_mv.get('detail', 'configured voice model may be invalid')}")
    except Exception:
        pass

    # Hang watchdog (toolcall-integrity-v5, 2026-08-13): arm before app.run()
    # so a hang during request serving — the scenario both silent-hang
    # incidents matched — gets caught. auto_restart_after_dump self-exits
    # after a dump so the tray relaunches; default off, so a dump alone
    # never changes behavior unless the user opts in.
    try:
        _hw_cfg = (_load_settings().get('hang_watchdog') or {})
        if _hw_cfg.get('enabled', True):
            from agent_friday.services import hang_watchdog as _hw

            def _on_hang_dump(_dump_path):
                print(f"\n  HANG WATCHDOG: thread stacks dumped to {_dump_path}")
                if (_load_settings().get('hang_watchdog') or {}).get(
                        'auto_restart_after_dump', False):
                    print("  hang_watchdog.auto_restart_after_dump is on — exiting "
                          "so the tray relaunches.")
                    os._exit(1)

            _hw.start(
                heartbeat_interval_s=_hw_cfg.get('heartbeat_interval_s', 15),
                stall_threshold_s=_hw_cfg.get('stall_threshold_s', 90),
                on_dump=_on_hang_dump,
            )
            print(f"  Hang watchdog: armed (stall threshold "
                  f"{_hw_cfg.get('stall_threshold_s', 90)}s)")
    except Exception as _hw_err:
        print(f"  Hang watchdog: skipped ({_hw_err})")

    try:
        app.run(host=bind_host, port=_port, debug=False, threaded=True,
                ssl_context=_ssl_context)
    except OSError as _bind_err:
        # The single-instance lock above catches almost all of this now, but
        # stays as a backstop for a race the lock can't see — e.g. something
        # else entirely holding the TCP port. Diagnose the occupant, log
        # loudly, and on Windows raise a visible message box; never die quietly.
        _msg = (f"Agent Friday could not start on port {_port}: {_bind_err}.")
        try:
            import urllib.request as _ur, json as _j
            with _ur.urlopen(f"http://127.0.0.1:{_port}/api/health", timeout=3) as _r:
                _h = _j.load(_r)
            _since = _h.get("server_start", "?")
            _msg = (f"Agent Friday could not start: port {_port} is still held by "
                    f"an OLDER Agent Friday server (running since {_since}). "
                    f"Quit it from the tray icon — or kill the python process "
                    f"listening on port {_port} — then relaunch.")
        except Exception:
            _msg += (" Another program may be using the port. Free it or set "
                     "FRIDAY_PORT to a different one.")
        _fail_loud_and_exit(_msg)

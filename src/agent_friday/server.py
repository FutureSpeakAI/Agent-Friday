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

import agent_friday.core as core
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

# ── Blueprints ───────────────────────────────────────────────────
from agent_friday.routes.core_routes import core_bp
from agent_friday.routes.chat import chat_bp
from agent_friday.routes.voice import voice_bp          # also registers the @sock.route('/ws/live') handler
from agent_friday.routes.voice_context import voice_context_bp
from agent_friday.routes.news import news_bp
from agent_friday.routes.tasks import tasks_bp
from agent_friday.routes.calendar import calendar_bp
from agent_friday.routes.messages import messages_bp
from agent_friday.routes.wiki import wiki_bp
from agent_friday.routes.context import context_bp
from agent_friday.routes.creations import creations_bp
from agent_friday.routes.finance_health import fh_bp
from agent_friday.routes.code import code_bp
from agent_friday.routes.futurespeak import fs_bp
from agent_friday.routes.contacts import contacts_bp
from agent_friday.routes.insights import insights_bp
from agent_friday.routes.todos import todos_bp
from agent_friday.routes.workflows import workflows_bp
from agent_friday.routes.google import google_bp
from agent_friday.routes.google_accounts import google_accounts_bp
from agent_friday.routes.skills import skills_bp
from agent_friday.routes.notifications import notif_bp
from agent_friday.routes.control import control_bp
from agent_friday.routes.ambient import ambient_bp
from agent_friday.routes.jobs import jobs_bp
from agent_friday.routes.connectors import connectors_bp
from agent_friday.routes.platform import platform_bp
from agent_friday.routes.workspace_studio import ws_studio_bp
from agent_friday.routes.projects import projects_bp
from agent_friday.routes.creative_pipeline import creative_pipeline_bp
from agent_friday.routes.hooks import hooks_bp
from agent_friday.routes.scheduler import scheduler_bp
from agent_friday.routes.costs import costs_bp
from agent_friday.routes.ownership import ownership_bp
from agent_friday.routes.federation import federation_bp
from agent_friday.routes.defederation import defederation_bp
from agent_friday.routes.ext_security import ext_security_bp

for _bp in (core_bp, chat_bp, voice_bp, voice_context_bp, news_bp, tasks_bp, calendar_bp,
            messages_bp, wiki_bp, context_bp, creations_bp, fh_bp, code_bp, fs_bp, contacts_bp,
            insights_bp, todos_bp, workflows_bp, google_bp, google_accounts_bp, skills_bp, notif_bp,
            control_bp, ambient_bp, jobs_bp, connectors_bp, platform_bp, ws_studio_bp,
            projects_bp, creative_pipeline_bp, hooks_bp, scheduler_bp, costs_bp, ownership_bp,
            federation_bp,
            defederation_bp,
            ext_security_bp):
    app.register_blueprint(_bp)


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
    _port, _requested, _fell_back = _resolve_bind_port()
    _url = f"http://localhost:{_port}"
    print()
    print("  ╔══════════════════════════════════════════════╗")
    print("  ║   FRIDAY Desktop v4.4 — Phase B OS          ║")
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

    try:
        app.run(host=bind_host, port=_port, debug=False, threaded=True,
                ssl_context=_ssl_context)
    except OSError as _bind_err:
        print()
        print(f"  Could not start the server on port {_port}: {_bind_err}")
        print(f"  Another program may be using it. Free the port or set a")
        print(f"  different one, e.g.  FRIDAY_PORT=3010 python server.py")
        sys.exit(1)

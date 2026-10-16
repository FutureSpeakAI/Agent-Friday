"""A fresh install with no keys and nothing connected starts quietly.

The report this guards: first-run setup asked for one key and then "threw a
bunch of errors looking for other connected services". Each test below is one
place that assumed a service existed and complained when it did not. The rule
they share: a service nobody connected is "not connected", silently. No
warning in the console, no failing background probe, no card, no red or amber
light. A service that WAS connected and broke still says so; those paths have
their own tests and are unchanged.

Fixtures are synthetic and nothing here touches the network.
"""
from __future__ import annotations

import logging
import threading
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


class _NoDaemon:
    """An Ollama manager on a computer where Ollama was never installed."""

    def __init__(self, *a, **k):
        pass

    def list_models(self):
        return []

    def is_available(self):
        return False


@pytest.fixture
def no_keys(monkeypatch):
    """Zero keys anywhere: environment, credential store, settings."""
    import os
    for var in list(os.environ):
        if var.endswith(("_API_KEY", "_TOKEN")) and var not in ("FRIDAY_TOKEN",):
            monkeypatch.delenv(var, raising=False)
    from agent_friday.services import credential_store as cs
    monkeypatch.setattr(cs, "provider_key_status", lambda name: "missing")
    monkeypatch.setattr(cs, "get_provider_key", lambda name: None)
    import agent_friday.routing.ollama_manager as om
    monkeypatch.setattr(om, "get_manager", lambda *a, **k: _NoDaemon())
    from agent_friday.services import provider_health as ph
    monkeypatch.setattr(ph, "_ollama_installed", lambda prov: False, raising=False)


# ── 1. The model-list sweep does not chase a connector nobody connected ──────

def test_the_boot_sweep_does_not_warn_about_an_unconnected_higgsfield(no_keys, monkeypatch, caplog):
    import agent_friday.services.model_discovery as md
    import agent_friday.services.higgsfield_catalog as hc
    monkeypatch.setattr(hc, "connector_configured", lambda: False, raising=False)
    import agent_friday.services.agent as agent
    monkeypatch.setattr(agent, "_MCP_MANAGER", None, raising=False)
    # Keyed API providers are not the subject here; keep the sweep offline.
    monkeypatch.setattr(md, "refresh_models", lambda prov: {"status": "skipped"})
    monkeypatch.setattr(md, "read_cache", lambda name: None)
    with caplog.at_level(logging.INFO):
        out = md.refresh_all_stale(force=True)
    hf = [r for r in out if isinstance(r, dict) and r.get("provider") == "higgsfield"]
    assert hf and hf[0]["status"] == "not_connected", out
    noisy = [r for r in caplog.records if r.levelno >= logging.WARNING
             and "higgsfield" in r.getMessage().lower()]
    assert not noisy, [r.getMessage() for r in noisy]
    # "not connected" is not "still starting": no fast boot retries.
    assert md.next_sweep_delay(out, 1) == 3600.0


# ── 2. Health: an Ollama that was never installed is not "down" ──────────────

def test_health_with_zero_keys_is_not_down(no_keys, monkeypatch):
    from agent_friday.services import provider_health as ph
    from agent_friday.services.provider_registry import get_provider_registry
    reg = get_provider_registry()
    keep = [p for p in reg.list_providers()
            if p.get("name") in ("ollama-local", "anthropic", "openai", "google-gemini")]
    monkeypatch.setattr(reg, "list_providers", lambda: keep)
    ph._PROBE_CACHE.clear()
    out = ph.inference_health()
    assert out["status"] != "down", out
    assert out["status"] != "degraded", out
    assert "ollama-local" in out.get("not_connected", []), out
    assert not any(r.get("status") == "down" for r in out.get("providers", [])), out


def test_the_providers_panel_check_says_not_connected_for_a_missing_ollama(no_keys):
    from agent_friday.services import provider_health as ph
    res = ph._check("ollama-local")
    assert res["status"] == "not_connected", res


# ── 3. No run notices before first-run setup has finished ────────────────────

def test_no_routine_cards_before_setup_finishes(monkeypatch):
    import agent_friday.core as core
    from agent_friday.services import scheduler as s
    from agent_friday.services import scheduled_cloud as sc
    from agent_friday.services import voice_engine as ve
    monkeypatch.setattr(core, "_is_existing_install", lambda: False)
    pushed, paused = [], []

    class _Engine:
        def push(self, **kw):
            pushed.append(kw)

        def upsert_status(self, **kw):
            pushed.append(kw)

    monkeypatch.setattr(ve, "_notif_engine", _Engine(), raising=False)
    monkeypatch.setattr(sc, "notify_paused", lambda *a, **k: paused.append(1))
    rec = {"id": "sch_heartbeat", "name": "Heartbeat"}
    for status in ("failed", "missed", "waiting", "complete"):
        s._notify_run(rec, status, "no local model")
    news = {"id": sorted(s.NEWS_ROUTINES)[0], "name": "News"}
    with pytest.raises(Exception):
        s._paused(news, "no local model")
    assert pushed == [], pushed
    assert paused == [], "the paused status entry was raised before setup finished"


def test_after_setup_a_failure_still_reaches_the_owner(monkeypatch):
    """The quiet rule is about a fresh install only."""
    import agent_friday.core as core
    from agent_friday.services import scheduler as s
    from agent_friday.services import voice_engine as ve
    monkeypatch.setattr(core, "_is_existing_install", lambda: True)
    pushed = []

    class _Engine:
        def push(self, **kw):
            pushed.append(kw)

    monkeypatch.setattr(ve, "_notif_engine", _Engine(), raising=False)
    s._notify_run({"id": "sch_heartbeat", "name": "Heartbeat"}, "failed", "it broke")
    assert pushed and pushed[0]["kind"] == "scheduled_failure"


# ── 4. No Gemini key is not a console warning ────────────────────────────────

def test_no_gemini_key_prints_nothing(no_keys, monkeypatch, capsys):
    import agent_friday.core as core
    monkeypatch.setattr(core, "_genai_client", None)
    monkeypatch.setattr(core, "GEMINI_API_KEY", "")
    monkeypatch.setattr(core, "_load_settings", lambda: {})
    for _ in range(3):
        assert core.get_genai_client() is None
    out = capsys.readouterr()
    assert "WARNING" not in out.out + out.err, out


# ── 5. The installer's console is not the server's console ───────────────────

def test_the_wizard_leaves_starting_friday_to_the_installer(monkeypatch):
    from agent_friday import setup_wizard as w
    monkeypatch.setenv("FRIDAY_SETUP_FROM_INSTALLER", "1")
    assert w.launched_by_installer() is True
    monkeypatch.delenv("FRIDAY_SETUP_FROM_INSTALLER")
    assert w.launched_by_installer() is False
    ps1 = (ROOT / "packaging" / "windows" / "install.ps1").read_text(encoding="utf-8-sig")
    wizard = ps1[ps1.index("$wizardExit = 0"):ps1.index("Setup wizard exited")]
    assert "$env:FRIDAY_SETUP_FROM_INSTALLER = '1'" in wizard
    src = (ROOT / "src" / "agent_friday" / "setup_wizard.py").read_text(encoding="utf-8")
    tail = src[src.index("# Launch?"):src.index("def launched_by_installer")]
    assert tail.index("launched_by_installer()") < tail.index("_launch()")


# ── 6. Not connected is grey, never amber ────────────────────────────────────

@pytest.mark.parametrize("name", ["index.html", "ui_parts/app.html"])
def test_a_provider_without_a_key_shows_not_connected(name):
    html = (ROOT / name).read_text(encoding="utf-8")
    dot = html[html.index("function ProviderStatusDot"):]
    dot = dot[:dot.index("function ProviderRow")]
    assert "needs attention (no key)" not in dot
    assert "'not connected'" in dot


# ── 7. Brave: the key is looked for where it is stored ───────────────────────

def test_a_brave_key_in_the_environment_reads_as_connected(no_keys, monkeypatch):
    from agent_friday.services import setup_connections as scx
    monkeypatch.setenv("BRAVE_SEARCH_API_KEY", "example-brave-token-for-tests")  # pragma: allowlist secret
    items = {it["id"]: it for it in scx._search_items()}
    assert items["provider:brave"]["status"] == "connected"


def test_a_brave_key_saved_by_the_old_key_screen_is_used(no_keys, monkeypatch):
    from agent_friday.services import credential_store as cs
    from agent_friday.services import web_search
    monkeypatch.setattr(cs, "get_provider_key",
                        lambda name: "example-brave-token" if name == "brave-search" else None)
    assert web_search.brave_key() == "example-brave-token"
    from agent_friday import setup_brain
    assert {k["provider"] for k in setup_brain.KEYS} >= {"brave"}
    assert "brave-search" not in {k["provider"] for k in setup_brain.KEYS}


# ── 8. A fresh install seeds no MCP servers ──────────────────────────────────

def test_a_fresh_install_lists_no_mcp_servers_it_never_asked_for():
    import agent_friday.services.agent as agent
    assert agent._default_mcp_servers() == {"servers": {}}


# ── 9. A catalogue refresh for an unconnected provider prints nothing ────────

def test_a_catalogue_refresh_without_a_key_prints_nothing(monkeypatch, capsys):
    from agent_friday.routes import core_routes as cr
    from agent_friday.services import hosted_catalog
    monkeypatch.setattr(hosted_catalog, "refresh",
                        lambda name: {"status": "no_key", "provider": name, "count": 0})
    monkeypatch.setattr(cr, "_CATALOG_KICK_AT", {})
    before = set(threading.enumerate())
    cr._kick_stale_catalog_refresh({"anthropic": {"stale": True}})
    for t in set(threading.enumerate()) - before:
        t.join(timeout=5)
    out = capsys.readouterr()
    assert "[CATALOG]" not in out.out, out.out


# ── 10. An image with no vision key gets a plain sentence ────────────────────

def test_an_image_without_a_gemini_key_is_a_plain_sentence(monkeypatch):
    from agent_friday.routes import chat
    import agent_friday.core as core
    monkeypatch.setattr(core, "GEMINI_API_KEY", "")
    from agent_friday.services.provider_registry import get_provider_registry
    monkeypatch.setattr(get_provider_registry(), "is_provider_available",
                        lambda name: False)
    assert chat._gemini_connected() is False
    assert "Settings > Connections" in chat._NO_VISION_KEY
    src = (ROOT / "src" / "agent_friday" / "routes" / "chat.py").read_text(encoding="utf-8")
    assert src.count("_NO_VISION_KEY") >= 3      # the constant and both image paths

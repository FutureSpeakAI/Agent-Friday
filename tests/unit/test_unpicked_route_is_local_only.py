"""North-star A16a: until a route is chosen, Friday is local-only.

A new install whose owner has not picked where Friday thinks makes no cloud
call at all, even with a key entered and even for a one-turn ask the person
confirms, and every place that would have offered the cloud offers "Choose
where Friday thinks" instead. Picking a route (the consent screen, the setup
conversation, the terminal setup's routing question, Settings) ends it. An
install that already existed keeps its stored route and is unaffected.

New behaviour is reached through getattr with a stand-in, so on a tree without
it each test fails on its assertion, not on an AttributeError.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from agent_friday.services import cloud_intent

REPO = Path(__file__).resolve().parents[2]

#: These tests decide "new install" the way a real home does (tests/conftest.py).
pytestmark = pytest.mark.new_install


@pytest.fixture
def real_settings(tmp_path, monkeypatch):
    import agent_friday.core as core
    monkeypatch.setenv("FRIDAY_HOME", str(tmp_path))
    monkeypatch.setattr(core, "FRIDAY_DIR", tmp_path)
    monkeypatch.setattr(core, "SETTINGS_FILE", tmp_path / "settings.json")
    monkeypatch.setitem(core.NETWORK_STATE, "status", "online")
    core._invalidate_settings_cache()
    cloud_intent.clear()
    yield core
    cloud_intent.clear()
    core._invalidate_settings_cache()


def _unpicked() -> bool:
    return bool(getattr(cloud_intent, "route_unpicked", lambda: False)())


def _refused(fn) -> str:
    try:
        fn()
    except cloud_intent.NoCloudIntent as e:
        return e.mode
    return ""


# ── a new install with no pick: local-only, no cloud call at all ─────────────

def test_a_new_install_with_no_pick_is_local_only_for_every_reader(real_settings):
    core = real_settings
    core._load_settings_raw()                                   # seeds settings.json
    assert core._load_settings_raw()["model_routing"]["mode"] == "cloud_only", "nothing stored changes"
    assert core._load_settings()["model_routing"]["mode"] == "local_only"
    assert core._load_settings()["model_routing"]["fallback_to_cloud"] is False
    assert cloud_intent.routing_mode() == "local_only"
    assert _unpicked() is True


def test_no_cloud_call_even_with_a_confirmed_ask_or_a_pinned_job(real_settings, monkeypatch):
    core = real_settings
    core._load_settings_raw()
    tok = cloud_intent.set_intent("user_explicit", source="test", confirmed=True)
    try:
        assert _refused(lambda: cloud_intent.require("anthropic", "chat", "claude-sonnet-5")) \
            == getattr(cloud_intent, "ROUTE_NOT_CHOSEN", "route_not_chosen")
        assert cloud_intent.allows("local_only", cloud_intent.current()) is False
        assert cloud_intent.opens_cloud() is False
    finally:
        cloud_intent.reset_intent(tok)
    monkeypatch.setattr(cloud_intent, "_pin", lambda: "claude-sonnet-5")
    assert _refused(lambda: cloud_intent.require("anthropic", "scheduled job", "claude-sonnet-5")), \
        "a job pinned to a cloud model does not reach the cloud before a pick either"


def test_the_refusal_tells_the_person_to_choose(real_settings):
    real_settings._load_settings_raw()
    try:
        cloud_intent.require("anthropic", "chat", "claude-sonnet-5")
    except cloud_intent.NoCloudIntent as e:
        text = str(e)
    else:
        text = ""
    assert "haven't chosen where I think yet" in text and "Settings > Models" in text


def test_routines_and_pinned_jobs_are_held_not_sent(real_settings, monkeypatch):
    real_settings._load_settings_raw()
    from agent_friday.services import scheduled_cloud
    monkeypatch.setattr(scheduled_cloud, "settings", lambda: {"allow": True})
    monkeypatch.setattr(scheduled_cloud, "model_for", lambda job, cfg=None: "claude-haiku-5")
    assert cloud_intent.background_route("sch_heartbeat", local_serving=False).route != "cloud"
    r = cloud_intent.pinned_job_route("claude-sonnet-5", cloud_ready=lambda m: (True, ""))
    assert r.route == "held", r.reason
    assert "not chosen where Friday thinks" in r.reason


def test_every_offer_is_choose_where_friday_thinks(real_settings):
    real_settings._load_settings_raw()
    from agent_friday.services import local_offer
    for kind in ("local_failed", "no_local_model", "local_only_confirm"):
        offer = local_offer.build(mode="local_only", kind=kind)
        assert offer["kind"] == "route_pick", kind
        assert [c["id"] for c in offer["choices"]] == ["choose_route"]
        assert offer["choices"][0]["tab"] == "intelligence"
        assert offer["confirm_local_only"] is False


def test_a_one_turn_cloud_ask_in_chat_is_refused_even_when_confirmed(real_settings):
    real_settings._load_settings_raw()
    from agent_friday.routes import chat
    assert chat._cloud_turn_decision("cloud", "local_only", True, False) == "confirm"


def test_a_key_check_sends_nothing(real_settings, monkeypatch):
    real_settings._load_settings_raw()
    from agent_friday.services import key_verdict as kv

    def _never(*a, **k):
        raise AssertionError("a key check reached the provider before a route was chosen")
    monkeypatch.setattr(kv, "probe_spec", _never)
    verdict, ping = kv.ping_detail({"type": "anthropic", "name": "anthropic"}, "claude-haiku-5")
    assert verdict == kv.UNKNOWN and "choose where Friday thinks" in ping["error"]


def test_setup_completion_writes_the_stored_route_block_not_the_overlay(real_settings):
    core = real_settings
    core._load_settings_raw()
    from agent_friday.services import setup_complete
    block = setup_complete.routing_delta("cloud_only")["model_routing"]
    assert block["fallback_to_cloud"] is True, "the local-only overlay must never be saved as the owner's"


# ── choosing ends it ─────────────────────────────────────────────────────────

def test_a_route_picked_in_settings_ends_it(real_settings):
    core = real_settings
    core._load_settings_raw()
    core._save_settings({"model_routing": {"mode": "cloud_only"}}, owner_routing_change=True,
                        routing_change_via="settings")
    assert _unpicked() is False
    assert cloud_intent.routing_mode() == "cloud_only"
    assert core._load_settings()["model_routing"]["mode"] == "cloud_only"


def test_the_consent_screens_route_is_a_pick_but_not_cloud_consent(real_settings):
    core = real_settings
    core._load_settings_raw()
    from agent_friday.services import setup_chat
    setup_chat.begin(routing_mode="local_preferred", screen=True)
    mr = core._load_settings_raw()["model_routing"]
    assert mr.get("mode") == "local_preferred" and mr.get("mode_chosen_via") == "consent_screen"
    assert _unpicked() is False
    from agent_friday.privacy import cloud_consent
    assert "consent_screen" not in cloud_consent.ROUTING_CONSENT_VIAS


def test_a_route_the_terminal_setup_kept_without_asking_is_still_local_only(real_settings):
    core = real_settings
    core.SETTINGS_FILE.write_text(json.dumps({"agent_name": "T", "model_routing": {
        "mode": "cloud_only", "mode_chosen": "cloud_only", "mode_chosen_via": "kept"}}), encoding="utf-8")
    # The terminal setup decided "new" before it wrote the file.
    core.update_install_choices_state(install=core.NEW_INSTALL)
    core._invalidate_settings_cache()
    assert cloud_intent.routing_mode() == "local_only"


# ── an existing install is unaffected ────────────────────────────────────────

def test_an_existing_install_keeps_its_stored_route(real_settings):
    core = real_settings
    core.SETTINGS_FILE.write_text(json.dumps({"agent_name": "T", "model_routing": {"mode": "cloud_only"}}),
                                  encoding="utf-8")
    core._invalidate_settings_cache()
    assert cloud_intent.routing_mode() == "cloud_only"
    assert _unpicked() is False
    tok = cloud_intent.require("anthropic", "chat", "claude-sonnet-5")
    assert tok is not None


# ── nothing else reaches the cloud before a pick either ─────────────────────

def _never(*a, **k):
    raise AssertionError("a request reached the cloud before a route was chosen")


def test_the_model_catalog_is_not_fetched_from_the_cloud(real_settings, monkeypatch):
    real_settings._load_settings_raw()
    from agent_friday.services import hosted_catalog as hc
    monkeypatch.setattr(hc, "fetch_anthropic_models", _never)
    monkeypatch.setattr(hc, "fetch_openrouter_models", _never)
    monkeypatch.setattr(hc, "_resolve_api_key", lambda name: "sk-test")  # pragma: allowlist secret
    for name, res in hc.refresh_all().items():
        assert res["status"] != "refreshed", name
        assert "choose where Friday thinks" in res.get("error", ""), name


def test_a_cloud_providers_model_list_is_not_fetched(real_settings, monkeypatch):
    real_settings._load_settings_raw()
    from agent_friday.services import model_discovery as md
    import requests
    monkeypatch.setattr(requests, "get", _never)
    prov = {"name": "openai", "type": "openai-compatible", "base_url": "https://api.openai.com/v1",
            "discovery": {"mode": "api"}, "auth": {"type": "env_var"}}
    res = md.refresh_models(prov)
    assert res["ok"] is False and "choose where Friday thinks" in res.get("error", "")


def test_the_gemini_voice_key_is_not_probed(real_settings):
    real_settings._load_settings_raw()
    from agent_friday.services import voice_engine as ve
    ok, detail = ve.validate_gemini_key("AIza-test", force=True)  # pragma: allowlist secret
    assert "choose where Friday thinks" in detail


def test_voice_decides_local_only_before_probing_the_key():
    src = (REPO / "src/agent_friday/routes/voice.py").read_text(encoding="utf-8")
    i_first = src.find("_local_only_first")
    i_probe = src.find("_ki = resolve_gemini_key()")
    assert 0 < i_first < i_probe


def test_pressing_test_on_a_cloud_key_sends_nothing(real_settings, monkeypatch):
    real_settings._load_settings_raw()
    from agent_friday.services import connections_setup as cs
    from agent_friday import setup_wizard as sw
    from agent_friday import cli
    monkeypatch.setattr(cs, "_test_provider", _never)
    monkeypatch.setattr(cs, "_test_brave", _never)
    monkeypatch.setattr(cs, "_key_present", lambda name: True, raising=False)
    for item in ("provider:anthropic", "provider:brave"):
        out = cs.test(item)
        assert out["state"] == cs.UNKNOWN and "choose where Friday thinks" in out["text"], item
    for fn in (sw._validate_anthropic, sw._validate_gemini,
               cli._validate_anthropic_key, cli._validate_gemini_key):
        ok, msg = fn("sk-test")  # pragma: allowlist secret
        assert ok is None and "choose where Friday thinks" in msg, fn.__name__


def test_a_missing_key_still_says_no_key_is_saved(real_settings, monkeypatch):
    real_settings._load_settings_raw()
    from agent_friday.services import connections_setup as cs
    monkeypatch.setattr(cs, "_key_present", lambda name: False, raising=False)
    monkeypatch.setattr(cs, "_test_provider",
                        lambda name: cs._out(cs.NOT_CONNECTED, "No key is saved yet."))
    assert cs.test("provider:openrouter")["state"] == cs.NOT_CONNECTED


def test_a_deep_provider_check_sends_nothing(real_settings, monkeypatch):
    real_settings._load_settings_raw()
    from agent_friday.services import provider_health as ph
    monkeypatch.setattr(ph, "inference_probe", _never)
    import requests
    monkeypatch.setattr(requests, "get", _never)
    monkeypatch.setattr(requests, "post", _never)
    provs = {
        "anthropic": {"name": "anthropic", "type": "anthropic",
                      "base_url": "https://api.anthropic.com", "auth": {"type": "env_var"}},
        "kie": {"name": "kie", "type": "kie", "base_url": "https://api.kie.ai",
                "auth": {"type": "env_var"}},
        "openai": {"name": "openai", "type": "openai-compatible",
                   "base_url": "https://api.openai.com/v1", "auth": {"type": "env_var"}},
    }
    monkeypatch.setattr(ph, "_CACHE", {})
    monkeypatch.setattr(ph, "_provider", lambda name: provs.get(name))
    monkeypatch.setattr(ph, "_key_state", lambda prov: "ok")
    for name in provs:
        res = ph.check_provider(name, deep=True, use_cache=False)
        assert res.get("status") == "not_checked", (name, res)


def test_saving_the_ollama_address_does_not_save_the_overlay(real_settings, monkeypatch):
    core = real_settings
    core._load_settings_raw()
    from agent_friday.services import connections_setup as cs
    from agent_friday.services import provider_registry as pr
    monkeypatch.setattr(pr.get_provider_registry(), "update_provider", lambda *a, **k: None)
    cs.save_ollama("http://localhost:11434")
    mr = json.loads(core.SETTINGS_FILE.read_text(encoding="utf-8"))["model_routing"]
    assert mr.get("ollama_url") == "http://localhost:11434"
    assert mr.get("mode") == "cloud_only" and mr.get("fallback_to_cloud") is True, \
        "the never-persist local-only overlay was written to disk"


def test_a_damaged_install_record_is_kept_and_stays_local_only(real_settings):
    core = real_settings
    core._load_settings_raw()                                   # a new install, unpicked
    core._INSTALL_ORIGIN_CACHE.clear()
    core.install_origin_file().write_text("{not json", encoding="utf-8")
    assert core.install_origin() in (core.NEW_INSTALL, core.EXISTING_INSTALL)
    assert core.install_origin_file().read_text(encoding="utf-8") == "{not json", \
        "an unreadable install record must not be overwritten"
    core.update_install_choices_state(daily_cap_card="shown")
    assert core.install_origin_file().read_text(encoding="utf-8") == "{not json"
    core._invalidate_settings_cache()
    assert cloud_intent.routing_mode() == "local_only"
    assert _unpicked() is True


def test_an_unverified_routing_change_is_not_a_pick(real_settings):
    core = real_settings
    core._load_settings_raw()
    core._save_settings({"model_routing": {"mode": "cloud_only"}}, owner_routing_change=True,
                        routing_change_via="unverified")
    assert _unpicked() is True
    assert cloud_intent.routing_mode() == "local_only"


def test_only_the_consent_screens_first_finish_is_a_pick(real_settings):
    core = real_settings
    core._load_settings_raw()
    from agent_friday.services import setup_chat
    setup_chat.begin(screen=True)                       # finished with no route given
    setup_chat.begin(routing_mode="cloud_only", screen=True)   # called again: not a pick
    assert _unpicked() is True


def test_the_consent_screens_route_needs_the_pages_own_click(real_settings):
    core = real_settings
    core._load_settings_raw()
    from agent_friday.services import setup_chat
    import inspect
    if "screen" not in inspect.signature(setup_chat.begin).parameters:
        pytest.fail("begin() cannot tell the consent page's own click from any other call")
    setup_chat.begin(routing_mode="cloud_only")        # not the page's verified click
    assert _unpicked() is True
    src = (REPO / "src/agent_friday/routes/setup_chat.py").read_text(encoding="utf-8")
    assert "screen_session(request)" in src


def test_a_verified_pick_stays_made_after_an_unverified_change(real_settings):
    core = real_settings
    core._load_settings_raw()
    core._save_settings({"model_routing": {"mode": "local_only"}}, owner_routing_change=True,
                        routing_change_via="settings")
    assert _unpicked() is False
    # The chat's "switch to cloud" button is an owner change that is not tied to a screen.
    core._save_settings({"model_routing": {"mode": "cloud_only"}}, owner_routing_change=True,
                        routing_change_via="unverified")
    assert _unpicked() is False
    assert cloud_intent.routing_mode() == "cloud_only"


def test_a_local_provider_can_still_be_tested(real_settings, monkeypatch):
    real_settings._load_settings_raw()
    from agent_friday.services import key_verdict as kv
    calls = []
    monkeypatch.setattr(kv, "probe_spec", lambda prov, model: calls.append(1) or None)
    kv.ping_detail({"type": "ollama", "name": "ollama-local",
                    "base_url": "http://127.0.0.1:11434"}, "qwen")
    assert calls, "a provider on this computer is not the cloud; its check may run"


def test_the_voice_setup_does_not_call_an_unchecked_key_ok():
    src = (REPO / "src/agent_friday/routes/voice.py").read_text(encoding="utf-8")
    assert "if _ki.get(\"key\") and _unpicked:" in src


def test_a_spoken_yes_cannot_answer_the_choose_prompt(real_settings):
    real_settings._load_settings_raw()
    from agent_friday.services import local_offer
    offer = local_offer.build(mode="local_only", kind="local_failed")
    local_offer.remember("conv-a16a", offer, "hello")
    assert local_offer.pending("conv-a16a", [offer["id"]]) is None


def test_work_plans_do_not_offer_the_cloud(real_settings, monkeypatch):
    core = real_settings
    core._load_settings_raw()
    monkeypatch.setattr(core, "ANTHROPIC_API_KEY", "sk-test", raising=False)  # pragma: allowlist secret
    from agent_friday.routes import work_plan
    assert work_plan._cloud_available() is False


def test_the_router_refusal_asks_to_choose_not_to_send_to_claude(real_settings):
    real_settings._load_settings_raw()
    from agent_friday.routing.model_router import ModelRouter
    r = ModelRouter.__new__(ModelRouter)
    r.config = {"mode": "local_only", "fallback_to_cloud": True}
    out = r._local_unavailable("chat", "no model loaded")
    assert out["offer_cloud_switch"] is False
    assert "haven't chosen where I think" in out["warning"]
    assert "send this one turn to Claude" not in out["warning"]


# ── web search, web pages and add-on connectors (owner ruling 2026-10-21) ───

def _web_text() -> str:
    return getattr(cloud_intent, "ROUTE_NOT_CHOSEN_WEB_TEXT", "no web hold yet")


def _no_network(monkeypatch):
    import requests
    import urllib.request
    monkeypatch.setattr(requests, "get", _never)
    monkeypatch.setattr(requests, "post", _never)
    monkeypatch.setattr(requests, "head", _never)
    monkeypatch.setattr(urllib.request, "urlopen", _never)


def test_web_search_is_held_with_the_choose_prompt(real_settings, monkeypatch):
    real_settings._load_settings_raw()
    _no_network(monkeypatch)
    from agent_friday.services import web_search as ws
    for fn in ("_wigolo_search", "_firecrawl_search", "_brave", "_duckduckgo"):
        if hasattr(ws, fn):
            monkeypatch.setattr(ws, fn, _never)
    out = ws.search("weather in Lisbon")
    assert out["results"] == [] and out["detail"] == _web_text()
    monkeypatch.setattr(ws, "brave_key", lambda: "BSA-test")  # pragma: allowlist secret
    assert ws.verify_key()["summary"] == _web_text()


def test_firecrawl_is_held(real_settings, monkeypatch):
    real_settings._load_settings_raw()
    _no_network(monkeypatch)
    from agent_friday.services import firecrawl as fc
    monkeypatch.setattr(fc, "configured", lambda: True)
    monkeypatch.setattr(fc, "api_key", lambda: "fc-test")  # pragma: allowlist secret
    assert fc.search("x")["error"] == _web_text()
    assert fc.scrape("https://example.com")["error"] == _web_text()
    assert fc.credits()["error"] == _web_text()


def test_a_held_search_is_not_cached_as_broken(real_settings, monkeypatch):
    real_settings._load_settings_raw()
    _no_network(monkeypatch)
    from agent_friday.services import web_search as ws
    monkeypatch.setitem(ws._canary_cache, "ok", None)
    out = ws.canary(force=True)
    assert out.get("route_not_chosen") is True and out["detail"] == _web_text()
    assert ws._canary_cache["ok"] is None


def test_job_board_scans_and_google_say_held_not_broken(real_settings, monkeypatch):
    real_settings._load_settings_raw()
    _no_network(monkeypatch)
    from agent_friday.services import career_ops, google_accounts, gmail_read
    with pytest.raises(career_ops.CareerError) as e:
        career_ops.fetch_json("https://boards-api.greenhouse.io/v1/boards/x/jobs")
    assert str(e.value) == _web_text()
    monkeypatch.setattr(google_accounts, "_raw_credentials", _never)
    monkeypatch.setattr(google_accounts, "_mark_status", _never)
    assert google_accounts.credentials_for("acct1") is None
    assert gmail_read._no_creds("acct1")["error"] == _web_text()


def test_reading_a_web_page_is_held(real_settings, monkeypatch):
    real_settings._load_settings_raw()
    _no_network(monkeypatch)
    from agent_friday.services import web_fetch, page_reader
    res = web_fetch.fetch("https://example.com/a", use_cache=False)
    assert res["ok"] is False and res["error"] == _web_text()
    with pytest.raises(Exception) as e:
        page_reader.fetch("https://example.com/b")
    assert str(e.value) == _web_text()


class _FakeServer:
    def __init__(self):
        self.status = ""
        self.tools = []
        self.started = 0

    def start(self):
        self.started += 1
        self.status = "ready"
        return True

    def stop(self):
        pass

    def call_tool(self, *a, **k):
        raise AssertionError("an add-on connector was called before a route was chosen")


def test_add_on_connectors_wait_and_are_not_called(real_settings):
    real_settings._load_settings_raw()
    from agent_friday import mcp_client
    mgr = mcp_client.MCPManager()
    sp = _FakeServer()
    mgr.servers = {"github": sp}
    mgr.start_all()
    mgr._start_one("github", None)
    assert sp.started == 0
    assert sp.status == getattr(mcp_client, "WAITING_FOR_ROUTE", "waiting_for_route")
    assert mgr.restart("github") is False and sp.started == 0
    out = mgr.call("github", "search_issues", {})
    assert out.startswith("[NOT RUN]") and _web_text() in out


def test_choosing_a_route_starts_the_waiting_connectors(real_settings, monkeypatch):
    core = real_settings
    core._load_settings_raw()
    from agent_friday import mcp_client
    from agent_friday.services import agent
    mgr = mcp_client.MCPManager()
    sp = _FakeServer()
    mgr.servers = {"github": sp}
    mgr.start_all()
    assert sp.started == 0 and sp.status == "waiting_for_route", "held before the pick"
    monkeypatch.setattr(agent, "_MCP_MANAGER", mgr)
    monkeypatch.setattr(agent, "_mcp_register_server_tools", lambda *a, **k: None)
    core._save_settings({"model_routing": {"mode": "local_preferred"}}, owner_routing_change=True,
                        routing_change_via="settings")
    import time
    for _ in range(50):
        if sp.started:
            break
        time.sleep(0.05)
    assert sp.started == 1 and sp.status == "ready"


def test_connecting_a_connector_or_signing_in_to_google_is_held(real_settings, monkeypatch):
    real_settings._load_settings_raw()
    _no_network(monkeypatch)
    from agent_friday.services import connectors, google_accounts
    monkeypatch.setattr(connectors, "_launch_google_connect", _never)
    monkeypatch.setattr(connectors, "_connect_mcp", _never)
    for key in [k for k, d in connectors.CONNECTOR_DEFS.items() if d.get("kind") in ("oauth", "mcp")][:4]:
        out = connectors.connect_connector(key, {})
        assert out["ok"] is False and out["message"] == _web_text(), key
    monkeypatch.setattr(google_accounts, "_mark_status", _never)
    monkeypatch.setattr(google_accounts, "_raw_credentials", _never)
    assert google_accounts.credentials_for("acct1") is None


def test_the_agent_refuses_web_and_connector_tools_plainly(real_settings):
    real_settings._load_settings_raw()
    from agent_friday.services import agent
    hook = getattr(agent, "_hook_route_not_chosen", None)
    assert hook is not None, "no tool hook holds web and connector tools"

    class _Ctx:
        def __init__(self, name):
            self.tool_name = name
            self.input = {}
    for name in ("search_web", "browse_web", "deep_research", "browser_open", "mcp_github_search",
                 "search_news", "career_scan", "query_calendar", "create_calendar_event",
                 "draft_email", "read_doc", "site_action", "domain_action"):
        verdict = hook(_Ctx(name))
        assert verdict != agent._hooks.ALLOW and _web_text() in str(verdict), name
    assert hook(_Ctx("read_file")) == agent._hooks.ALLOW
    pre = [h for h in agent._hooks.list_hooks() if h["phase"] == "pre"]
    mine = next(h for h in pre if h["name"] == "route_not_chosen")
    assert mine["critical"] is True


def test_channels_and_social_posting_are_held(real_settings, monkeypatch):
    real_settings._load_settings_raw()
    from agent_friday.services.channels import base
    from agent_friday.services import platforms

    class _Chan(base.ChannelAdapter):
        name = "telegram"

        def token(self):
            return "t"

        def _send_raw(self, chat_id, text):
            raise AssertionError("a message went out before a route was chosen")
    ch = _Chan()
    assert ch.start()["error"] == "route_not_chosen" and not ch._running
    assert ch.send("1", "hi")["error"] == "route_not_chosen"
    class _Adapter:
        def configure(self, cfg):
            pass
    monkeypatch.setattr(platforms, "_held", lambda mod: False)
    monkeypatch.setattr(platforms, "_make_adapter", lambda mod: _Adapter())
    monkeypatch.setattr(platforms, "load_config", lambda: {})
    monkeypatch.setattr(platforms, "_ADAPTERS", {})
    for name in list(platforms.ADAPTER_MODULES)[:3]:
        assert platforms.get_adapter(name) is None, name


def test_existing_installs_keep_web_and_connectors(real_settings, monkeypatch):
    core = real_settings
    core.SETTINGS_FILE.write_text(json.dumps({"agent_name": "T", "model_routing": {"mode": "cloud_only"}}),
                                  encoding="utf-8")
    core._invalidate_settings_cache()
    assert cloud_intent.outbound_held("web_search") == ""
    from agent_friday import mcp_client
    mgr = mcp_client.MCPManager()
    sp = _FakeServer()
    mgr.servers = {"github": sp}
    mgr._start_one("github", None)
    assert sp.started == 1


# ── the page ─────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("page", ["index.html", "ui_parts/app.html"])
def test_the_chat_card_opens_the_choice(page):
    src = (REPO / page).read_text(encoding="utf-8")
    assert "id === 'choose_route'" in src or "id==='choose_route'" in src
    assert "tab: 'intelligence'" in src or "tab:'intelligence'" in src

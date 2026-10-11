"""Local modes reach the cloud only when the owner asked (RM-1b, F1 and F7-F19).

Each test names the audit finding it closes. Fixtures are synthetic: fake
clients, fake registries, a temporary settings file. Nothing touches a model or
the network.
"""
from __future__ import annotations

import ast
import json
import types
from pathlib import Path

import pytest

from agent_friday import core
from agent_friday.services import cloud_mode_policy as cmp

LOCAL_MODES = ("local_preferred", "local_only")
SRC = Path(__file__).resolve().parents[2] / "src" / "agent_friday"


def _mode(monkeypatch, mode):
    monkeypatch.setattr(core, "_load_settings",
                        lambda: {"model_routing": {"mode": mode}})


# ── the shared answer ────────────────────────────────────────────────────────

class TestPolicy:
    def test_cloud_only_allows_the_cloud(self, monkeypatch):
        _mode(monkeypatch, "cloud_only")
        assert cmp.cloud_allowed("x") is True

    @pytest.mark.parametrize("mode", LOCAL_MODES)
    def test_local_modes_need_a_recorded_intent(self, monkeypatch, mode):
        _mode(monkeypatch, mode)
        assert cmp.cloud_allowed("x") is False

    def test_an_explicit_pick_counts_under_local_preferred_only(self, monkeypatch):
        _mode(monkeypatch, "local_preferred")
        assert cmp.cloud_allowed("x", explicit=True) is True
        _mode(monkeypatch, "local_only")
        assert cmp.cloud_allowed("x", explicit=True) is False

    @pytest.mark.parametrize("mode", LOCAL_MODES)
    def test_a_scheduled_cloud_pin_is_an_intent(self, monkeypatch, mode):
        from agent_friday.services.local_only_guard import cloud_pinned
        _mode(monkeypatch, mode)
        with cloud_pinned("claude-haiku-4-5", "heartbeat"):
            assert cmp.cloud_allowed("x") is True
        assert cmp.cloud_allowed("x") is False

    def test_an_unreadable_setting_fails_closed(self, monkeypatch):
        def boom():
            raise RuntimeError("unreadable")
        monkeypatch.setattr(core, "_load_settings", boom)
        assert cmp.current_mode() == "local_only"
        assert cmp.cloud_allowed("x") is False


# ── F1: background tasks do not bind the subagent seat ───────────────────────

class TestF1BackgroundBinding:
    def _bind(self, explicit=None, default="claude-sonnet-5"):
        from agent_friday.services import agent
        return agent._background_model_binding(explicit, default)

    def test_cloud_only_keeps_the_subagent_seat(self, monkeypatch):
        _mode(monkeypatch, "cloud_only")
        assert self._bind() == "claude-sonnet-5"

    @pytest.mark.parametrize("mode", LOCAL_MODES)
    def test_local_modes_bind_nothing_for_an_unpinned_task(self, monkeypatch, mode):
        _mode(monkeypatch, mode)
        assert self._bind() is None

    def test_a_per_task_pick_counts_under_local_preferred(self, monkeypatch):
        _mode(monkeypatch, "local_preferred")
        assert self._bind(explicit="claude-sonnet-5") == "claude-sonnet-5"
        _mode(monkeypatch, "local_only")
        assert self._bind(explicit="claude-sonnet-5") is None

    def test_a_scheduled_cloud_pin_keeps_the_binding(self, monkeypatch):
        from agent_friday.services.local_only_guard import cloud_pinned
        _mode(monkeypatch, "local_preferred")
        with cloud_pinned("claude-haiku-4-5", "daily creation"):
            assert self._bind() == "claude-sonnet-5"

    @pytest.mark.parametrize("mode", LOCAL_MODES)
    def test_a_local_seat_id_is_always_safe_to_bind(self, monkeypatch, mode):
        _mode(monkeypatch, mode)
        assert self._bind(default="bonsai2:27b") == "bonsai2:27b"

    @pytest.mark.parametrize("mode", LOCAL_MODES)
    def test_a_cloud_relayed_ollama_tag_is_not_local(self, monkeypatch, mode):
        _mode(monkeypatch, mode)
        assert self._bind(default="glm-4.6:cloud") is None

    def test_the_worker_hands_both_legs_the_filtered_binding(self):
        tree = ast.parse((SRC / "services" / "agent.py").read_text(encoding="utf-8-sig"))
        worker = next(n for n in ast.walk(tree)
                      if isinstance(n, ast.FunctionDef) and n.name == "_task_worker_untraced")
        models = []
        for call in ast.walk(worker):
            if (isinstance(call, ast.Call) and isinstance(call.func, ast.Name)
                    and call.func.id == "_generate_agent"):
                kw = {k.arg: k.value for k in call.keywords}
                models.append(kw.get("model"))
        assert len(models) == 2
        assert all(isinstance(m, ast.Name) and m.id == "_bind_model" for m in models), (
            "a background task leg bound the raw subagent seat")


# ── F7: provider health spends nothing unless the owner asks ─────────────────

@pytest.fixture
def paid_provider(monkeypatch):
    from agent_friday.services import provider_health as ph
    import agent_friday.services.provider_registry as pr
    prov = {"name": "anthropic", "type": "anthropic", "enabled": True}

    class _Reg:
        def list_providers(self):
            return [prov]

        def get_provider(self, name):
            return prov if name == "anthropic" else None

    calls = {"list": 0, "create": 0}

    class _Models:
        def list(self, **kw):
            calls["list"] += 1
            return []

    class _Msgs:
        def create(self, **kw):
            calls["create"] += 1
            return types.SimpleNamespace(content=[types.SimpleNamespace(type="text")])

    client = types.SimpleNamespace(models=_Models(), messages=_Msgs())
    monkeypatch.setattr(pr, "get_provider_registry", lambda: _Reg())
    monkeypatch.setattr(core, "get_anthropic_client", lambda: client)
    monkeypatch.setattr(ph, "_has_key", lambda p: True)
    monkeypatch.setattr(ph, "resident_model_for", lambda p: "claude-sonnet-5")
    ph.reset_probe_cache()
    ph._CACHE.clear()
    yield ph, calls
    ph.reset_probe_cache()
    ph._CACHE.clear()


class TestF7ProviderHealth:
    def test_cloud_mode_polling_checks_the_key_and_generates_nothing(
            self, monkeypatch, paid_provider):
        ph, calls = paid_provider
        _mode(monkeypatch, "cloud_only")
        out = ph.inference_health()
        assert calls["create"] == 0, "a status poll spent tokens"
        assert calls["list"] == 1
        row = out["providers"][0]
        assert row["status"] == "ok" and row["proved_inference"] is False

    @pytest.mark.parametrize("mode", LOCAL_MODES)
    def test_local_modes_do_not_contact_the_provider_at_all(
            self, monkeypatch, paid_provider, mode):
        ph, calls = paid_provider
        _mode(monkeypatch, mode)
        out = ph.inference_health()
        assert calls == {"list": 0, "create": 0}
        assert out["providers"][0]["proved_inference"] is False

    def test_the_owner_asking_runs_one_probe_kept_for_fifteen_minutes(
            self, monkeypatch, paid_provider):
        ph, calls = paid_provider
        _mode(monkeypatch, "cloud_only")
        ph.inference_health(owner_requested=True)
        ph.inference_health(owner_requested=True)
        assert calls["create"] == 1
        ts, res = ph._PROBE_CACHE["anthropic"]
        ph._PROBE_CACHE["anthropic"] = (ts - 14 * 60, res)
        ph.inference_health(owner_requested=True)
        assert calls["create"] == 1, "a 14 minute old answer was probed again"
        ph._PROBE_CACHE["anthropic"] = (ts - 16 * 60, res)
        ph.inference_health(owner_requested=True)
        assert calls["create"] == 2

    @pytest.mark.parametrize("mode", LOCAL_MODES + ("cloud_only",))
    def test_the_providers_panel_deep_check_is_the_owner_asking(
            self, monkeypatch, paid_provider, mode):
        ph, calls = paid_provider
        _mode(monkeypatch, mode)
        res = ph.check_provider("anthropic", deep=True, use_cache=False)
        assert calls["create"] == 1
        assert res["proved_inference"] is True


# ── F8: video and music generation honour Local only ─────────────────────────

class TestF8MediaGeneration:
    @pytest.fixture
    def sent(self, monkeypatch):
        from agent_friday.services import creative_engine as ce
        from agent_friday.services import higgsfield_generate as hf
        out = []
        monkeypatch.setattr(ce, "_spend_cap_halt", lambda *a, **k: None)
        monkeypatch.setattr(hf, "is_higgsfield_model", lambda m: True)
        monkeypatch.setattr(hf, "generate",
                            lambda *a, **k: out.append(a) or {"status": "ok"})
        return out

    def test_video_is_refused_under_local_only(self, monkeypatch, sent):
        from agent_friday.services import creative_engine as ce
        _mode(monkeypatch, "local_only")
        res = ce.generate_video("a quiet harbour at dawn", model="seedance-x")
        assert res["status"] == "unavailable"
        assert sent == [], "the video prompt reached a cloud provider"

    def test_video_still_runs_where_the_cloud_is_allowed(self, monkeypatch, sent):
        from agent_friday.services import creative_engine as ce
        _mode(monkeypatch, "cloud_only")
        res = ce.generate_video("a quiet harbour at dawn", model="seedance-x")
        assert res == {"status": "ok"} and len(sent) == 1

    def test_music_is_refused_under_local_only(self, monkeypatch, sent):
        from agent_friday.services import music_engine as me
        _mode(monkeypatch, "local_only")
        res = me.generate_music("calm piano for studying")
        assert res["status"] == "unavailable"
        assert sent == [], "the music prompt reached a cloud provider"

    def test_music_still_runs_where_the_cloud_is_allowed(self, monkeypatch, sent):
        from agent_friday.services import music_engine as me
        _mode(monkeypatch, "cloud_only")
        res = me.generate_music("calm piano for studying")
        assert res == {"status": "ok"} and len(sent) == 1

    def test_a_local_only_run_guard_also_refuses(self, monkeypatch, sent):
        from agent_friday.services import creative_engine as ce
        from agent_friday.services.local_only_guard import local_only
        _mode(monkeypatch, "cloud_only")
        with local_only("daily creation"):
            res = ce.generate_video("a quiet harbour at dawn", model="seedance-x")
        assert res["status"] == "unavailable" and sent == []


# ── F9: avatar growth needs the mode as well as consent ──────────────────────

class TestF9AvatarGrowth:
    @pytest.fixture
    def growth(self, monkeypatch):
        from agent_friday.services import avatar_growth as ag
        from agent_friday.privacy import cloud_consent as cc
        from agent_friday.services import one_key
        monkeypatch.setattr(cc, "resolve", lambda *a, **k: types.SimpleNamespace(
            answered=True, choice="cloud_unrestricted"))
        monkeypatch.setattr(one_key, "anthropic_ready", lambda: True)
        monkeypatch.setattr(one_key, "openrouter_ready", lambda: False)
        return ag

    def test_the_frontier_author_runs_in_cloud_only(self, monkeypatch, growth):
        _mode(monkeypatch, "cloud_only")
        ok, _why, _fixes = growth.availability("frontier")
        assert ok is True

    @pytest.mark.parametrize("mode", LOCAL_MODES)
    def test_the_frontier_author_waits_in_local_modes(self, monkeypatch, growth, mode):
        _mode(monkeypatch, mode)
        ok, why, fixes = growth.availability("frontier")
        assert ok is False
        assert "routing mode" in why
        assert "connect_cloud" not in fixes

    def test_seeded_needs_no_cloud_in_any_mode(self, monkeypatch, growth):
        _mode(monkeypatch, "local_only")
        assert growth.availability("seeded")[0] is True

    def test_a_cloud_author_the_owner_chose_counts_under_local_preferred(
            self, monkeypatch, growth):
        _mode(monkeypatch, "local_preferred")
        assert growth.availability("cloud:anthropic:claude-x")[0] is True
        _mode(monkeypatch, "local_only")
        assert growth.availability("cloud:anthropic:claude-x")[0] is False


# ── F10: research scoping ────────────────────────────────────────────────────

class TestF10ResearchScoping:
    def _c(self, disposition, cloud_allowed=True):
        return types.SimpleNamespace(
            disposition=disposition,
            protection=types.SimpleNamespace(cloud_allowed=cloud_allowed))

    def test_cloud_only_scopes_in_the_cloud(self, monkeypatch):
        from agent_friday.services.research import harness
        _mode(monkeypatch, "cloud_only")
        assert harness._scoping_in_cloud(self._c("when_away")) is True

    @pytest.mark.parametrize("mode", LOCAL_MODES)
    def test_local_modes_scope_on_the_brain_unless_the_owner_chose_the_cloud(
            self, monkeypatch, mode):
        from agent_friday.services.research import harness
        _mode(monkeypatch, mode)
        assert harness._scoping_in_cloud(self._c("when_away")) is False
        assert harness._scoping_in_cloud(self._c("now_local")) is False
        assert harness._scoping_in_cloud(self._c("now_cloud")) is (mode == "local_preferred")

    def test_the_protection_plan_still_vetoes(self, monkeypatch):
        from agent_friday.services.research import harness
        _mode(monkeypatch, "cloud_only")
        assert harness._scoping_in_cloud(self._c("now_cloud", cloud_allowed=False)) is False


# ── F11: task resume ─────────────────────────────────────────────────────────

class TestF11TaskResume:
    @pytest.fixture
    def resume_env(self, monkeypatch):
        from agent_friday.services import agent as ag
        from agent_friday.services import task_resume as tr
        monkeypatch.setattr(ag, "_prepare_task_start", lambda tid: True)
        monkeypatch.setattr(tr, "resumability", lambda tid: {
            "resumable": True, "needs_confirmation": False, "iteration": 3,
            "attempts": 0, "reason": ""})
        monkeypatch.setattr(tr, "read", lambda tid: {
            "convo": [{"role": "user", "content": "go"}], "model": "claude-sonnet-5",
            "loop": "anthropic"})
        monkeypatch.setattr(tr, "_ledger_resumability",
                            lambda tid, attempts=0: {"resumable": False})
        return tr

    @pytest.mark.parametrize("mode", LOCAL_MODES)
    def test_a_local_mode_refuses_with_an_offer_and_sends_nothing(
            self, monkeypatch, resume_env, mode):
        from agent_friday.services import agent as ag
        _mode(monkeypatch, mode)
        sent = []
        monkeypatch.setattr(ag, "_call_claude_agent", lambda *a, **k: sent.append(1))
        with pytest.raises(resume_env.ResumeRefused) as err:
            resume_env.resume("t1")
        assert sent == []
        assert "cloud" in str(err.value) and mode.replace("_", " ") in str(err.value)

    def test_a_task_with_a_ledger_continues_locally_instead(
            self, monkeypatch, resume_env):
        _mode(monkeypatch, "local_preferred")
        monkeypatch.setattr(resume_env, "_ledger_resumability",
                            lambda tid, attempts=0: {"resumable": True, "iteration": 2})
        monkeypatch.setattr(resume_env, "_resume_from_ledger",
                            lambda tid, verdict: ("from the ledger", []))
        assert resume_env.resume("t1") == ("from the ledger", [])

    def test_the_owner_saying_so_resumes_on_the_cloud_loop(self, monkeypatch, resume_env):
        from agent_friday.services import agent as ag

        class _Reached(Exception):
            pass

        def reached(*a, **k):
            raise _Reached()
        _mode(monkeypatch, "local_preferred")
        monkeypatch.setattr(resume_env, "_bump_attempts", lambda tid: None)
        monkeypatch.setattr(ag, "_carry_workflow_baseline", lambda tid: None)
        monkeypatch.setattr(ag, "_call_claude_agent", reached)
        with pytest.raises(_Reached):
            resume_env.resume("t1", allow_cloud=True)

    def test_cloud_only_resumes_as_before(self, monkeypatch, resume_env):
        from agent_friday.services import agent as ag

        class _Reached(Exception):
            pass

        def reached(*a, **k):
            raise _Reached()
        _mode(monkeypatch, "cloud_only")
        monkeypatch.setattr(resume_env, "_bump_attempts", lambda tid: None)
        monkeypatch.setattr(ag, "_carry_workflow_baseline", lambda tid: None)
        monkeypatch.setattr(ag, "_call_claude_agent", reached)
        with pytest.raises(_Reached):
            resume_env.resume("t1")


# ── F12, F13, F17: the chat route's decisions ────────────────────────────────

class TestChatRouteDecisions:
    def test_computer_control_override_respects_the_mode(self, monkeypatch):
        from agent_friday.routes import chat
        _mode(monkeypatch, "cloud_only")
        assert chat._computer_control_cloud_override(True, True, False) == "override"
        # local_preferred: the owner's Computer Control switch is a user_explicit
        # intent. local_only: held, for the escalation card.
        _mode(monkeypatch, "local_preferred")
        assert chat._computer_control_cloud_override(True, True, False) == "override"
        _mode(monkeypatch, "local_only")
        assert chat._computer_control_cloud_override(True, True, False) == "held"
        assert chat._computer_control_cloud_override(False, True, False) == ""
        assert chat._computer_control_cloud_override(True, False, False) == ""
        assert chat._computer_control_cloud_override(True, True, True) == ""

    def test_a_cloud_turn_under_local_only_asks_first(self):
        from agent_friday.routes import chat
        d = chat._cloud_turn_decision
        assert d("cloud", "local_only", False, False) == "confirm"
        assert d("cloud", "local_only", True, False) == "allow"
        assert d("cloud", "local_preferred", False, False) == "allow"
        assert d("cloud", "cloud_only", False, False) == "allow"
        assert d("cloud", "local_only", True, True) == "vault"
        assert d("", "local_only", False, False) == ""
        assert d("local", "local_only", False, False) == ""

    def test_the_per_turn_confirm_is_the_local_offer_with_one_flag(self):
        from agent_friday.services import local_offer
        card = local_offer.build(mode="local_only", kind="local_only_confirm")
        assert "local-only mode" in card["text"] and "Nothing has been sent" in card["text"]
        assert [c["id"] for c in card["choices"]] == ["send_claude", "retry_local"]
        assert not any(k in c for c in card["choices"] for k in ("selected", "default"))

    def test_a_redacted_vault_send_needs_a_say_so_in_local_modes(self):
        from agent_friday.routes import chat
        need = chat._vault_redact_needs_confirm
        route = {"vault_access": True, "provider": "cloud", "refuse": False}
        assert need(route, "local_preferred", False) is True
        assert need(route, "local_only", False) is True
        assert need(route, "local_preferred", True) is False
        assert need(route, "cloud_only", False) is False
        assert need(dict(route, provider="local"), "local_only", False) is False
        assert need(dict(route, refuse=True), "local_only", False) is False
        assert need(dict(route, vault_access=False), "local_only", False) is False

    def test_there_is_one_confirm_mechanism(self):
        """The old hold card (`cloud_confirm` / `confirm_cloud_once`) is gone from
        the route and both UI files; the offer's `confirm_local_only` replaces it."""
        root = SRC.parents[1]
        chat_src = (SRC / "routes" / "chat.py").read_text(encoding="utf-8-sig")
        assert "confirm_cloud_once" not in chat_src and "cloud_confirm" not in chat_src
        for rel in ("index.html", "ui_parts/app.html"):
            text = (root / rel).read_text(encoding="utf-8")
            assert "confirm_cloud_once" not in text and "cloud_confirm" not in text, rel
            assert "confirm_local_only" in text, rel


# ── F14: relayed Ollama tags are not local candidates ────────────────────────

class TestF14CloudTags:
    def test_cloud_tagged_models_are_filtered_out(self, monkeypatch):
        import agent_friday.routing.ollama_manager as om
        from agent_friday.routing.model_router import ModelRouter
        from agent_friday.services import model_store, residency_catalog

        class _Daemon:
            def is_available(self):
                return True

            def list_models(self):
                return [{"name": "gemma4:e2b", "size_gb": 3.0},
                        {"name": "glm-4.6:cloud", "size_gb": 0.0},
                        {"name": "gpt-oss:120b-cloud", "size_gb": 0.0}]

        monkeypatch.setattr(om, "get_manager", lambda *a, **k: _Daemon())
        monkeypatch.setattr(model_store, "available", lambda: {})
        monkeypatch.setattr(residency_catalog, "can_generate", lambda m: True)
        names = [m["name"] for m in ModelRouter(config={})._local_candidates()]
        assert names == ["gemma4:e2b"]


# ── F15: the seat warm never leaves the machine ──────────────────────────────

class TestF15SeatWarm:
    @pytest.fixture
    def warm_env(self, monkeypatch):
        from agent_friday.services import seat_warm
        monkeypatch.setattr(core, "_load_settings", lambda: {})
        monkeypatch.setattr(seat_warm, "_is_brain", lambda m: True)
        monkeypatch.setattr(seat_warm, "canonical_head", lambda ws="": "HEAD")
        return seat_warm

    def test_the_warm_runs_inside_a_local_only_run(self, monkeypatch, warm_env):
        from agent_friday.services import agent as ag
        from agent_friday.services import local_only_guard
        seen = []
        monkeypatch.setattr(ag, "_generate_agent",
                            lambda *a, **k: seen.append(local_only_guard.is_active())
                            or ("", []))
        assert warm_env.warm("brain-model")["warmed"] is True
        assert seen == [True], "the warm ran outside the local-only guard"

    def test_a_failed_warm_logs_and_stops(self, monkeypatch, warm_env):
        from agent_friday.services import agent as ag
        calls = []

        def boom(*a, **k):
            calls.append(1)
            raise RuntimeError("seat went away")
        monkeypatch.setattr(ag, "_generate_agent", boom)
        out = warm_env.warm("brain-model")
        assert out["warmed"] is False and calls == [1]

    def test_a_cloud_transport_inside_the_warm_is_refused(self, monkeypatch, warm_env):
        from agent_friday.services import agent as ag
        from agent_friday.services import local_only_guard as g
        monkeypatch.setattr(ag, "_generate_agent",
                            lambda *a, **k: g.refuse_if_active("anthropic", "claude-x"))
        out = warm_env.warm("brain-model")
        assert out["warmed"] is False and "local-only" in out["reason"]


# ── F18: the two no-op settings ──────────────────────────────────────────────

class TestF18Settings:
    def test_news_local_only_is_gone(self):
        assert "news_local_only" not in core.DEFAULT_SETTINGS

    def test_fallback_to_cloud_means_offer(self, monkeypatch):
        from agent_friday.routing.model_router import ModelRouter
        r = ModelRouter(config={"mode": "local_preferred", "fallback_to_cloud": True})
        res = r._local_unavailable("simple", "no local model")
        assert res["provider"] != "cloud" and res["refuse"] is True
        assert res["offer_cloud_switch"] is True
        r = ModelRouter(config={"mode": "local_preferred", "fallback_to_cloud": False})
        res = r._local_unavailable("simple", "no local model")
        assert res["refuse"] is True and res["offer_cloud_switch"] is False

    def test_the_default_still_offers(self):
        assert core.DEFAULT_SETTINGS["model_routing"]["fallback_to_cloud"] is True

    def test_the_settings_guide_documents_the_new_meaning(self):
        guide = (SRC.parents[1] / "docs" / "user-guide" / "configuration.md").read_text(
            encoding="utf-8")
        assert "news_local_only" not in guide
        assert "never sends anything by itself" in guide


# ── F19: smart is local_preferred ────────────────────────────────────────────

class TestF19Smart:
    def test_the_router_reads_smart_as_local_preferred(self):
        from agent_friday.routing.model_router import ModelRouter
        assert ModelRouter(config={"mode": "smart"}).mode == "local_preferred"

    def test_the_direct_caller_mode_reads_smart_as_local_preferred(self, monkeypatch):
        from agent_friday.services.local_only_guard import routing_mode
        _mode(monkeypatch, "smart")
        assert routing_mode() == "local_preferred"

    def test_media_tools_treat_smart_as_local_first(self, monkeypatch):
        from agent_friday.services import media_tools
        _mode(monkeypatch, "smart")
        assert media_tools._routing_mode() in media_tools._LOCAL_FIRST_MODES

    def test_the_setup_reader_offers_the_cloud_for_smart_as_for_local_preferred(
            self, monkeypatch):
        from agent_friday.services import setup_reader
        monkeypatch.setattr(setup_reader, "local_model", lambda: None)
        monkeypatch.setattr(setup_reader, "cloud_model",
                            lambda: {"model": "m", "provider": "p"})
        assert setup_reader.options("smart") == setup_reader.options("local_preferred")
        assert setup_reader.options("smart")["cloud"] == {"model": "m", "provider": "p"}

    def test_analyze_mode_reads_smart_as_local_preferred(self, monkeypatch):
        from agent_friday.routes import core_routes
        _mode(monkeypatch, "smart")
        monkeypatch.setattr(core_routes, "_load_settings",
                            lambda: {"model_routing": {"mode": "smart"}})
        assert core_routes._analyze_mode() == "local_preferred"

    def test_the_picker_no_longer_offers_smart(self):
        text = (SRC.parents[1] / "ui_parts" / "app.html").read_text(encoding="utf-8")
        assert "{id:'smart',label:'Smart'}" not in text


class TestF19Migration:
    @pytest.fixture
    def home(self, tmp_path, monkeypatch):
        monkeypatch.setattr(core, "FRIDAY_DIR", tmp_path)
        monkeypatch.setattr(core, "SETTINGS_FILE", tmp_path / "settings.json")
        monkeypatch.setitem(core._SMART_MIGRATION_TRIED, 0, False)
        core._invalidate_settings_cache()
        yield tmp_path
        core._invalidate_settings_cache()

    def test_a_stored_smart_is_renamed_once_and_nothing_else_changes(self, home):
        path = home / "settings.json"
        path.write_text(json.dumps({"model_routing": {
            "mode": "smart", "ollama_url": "http://127.0.0.1:11434"},
            "agent_name": "Test"}), encoding="utf-8")
        loaded = core._load_settings_raw()
        assert loaded["model_routing"]["mode"] == "local_preferred"
        saved = json.loads(path.read_text(encoding="utf-8"))
        assert saved["model_routing"]["mode"] == "local_preferred"
        assert saved["model_routing"]["ollama_url"] == "http://127.0.0.1:11434"
        assert saved["agent_name"] == "Test"

    def test_a_current_mode_is_left_alone_on_disk(self, home):
        path = home / "settings.json"
        body = {"model_routing": {"mode": "local_only"}}
        path.write_text(json.dumps(body), encoding="utf-8")
        before = path.read_bytes()
        assert core._load_settings_raw()["model_routing"]["mode"] == "local_only"
        assert path.read_bytes() == before

    def test_the_read_normalises_even_when_the_write_cannot_happen(self, home, monkeypatch):
        path = home / "settings.json"
        path.write_text(json.dumps({"model_routing": {"mode": "smart"}}), encoding="utf-8")

        def refuse(*a, **k):
            raise OSError("read-only")
        monkeypatch.setattr(core, "_save_settings", refuse)
        assert core._load_settings_raw()["model_routing"]["mode"] == "local_preferred"

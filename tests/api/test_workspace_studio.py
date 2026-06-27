"""Tests for Workspace Studio — Friday as a per-workspace customization agent.

Covers the safety-critical and behavioural contract: patch sanitization (no CSS
injection / script breakout), the apply→snapshot→merge flow, undoable revert and
reset, the agentic chat turn (with an injected generator so no model is hit), and
the HTTP routes (which run offline because conftest auto-stubs the model).
"""

import json
import shutil

import pytest

from agent_friday.services import workspace_studio as ws


@pytest.fixture(autouse=True)
def clean_studio():
    """Isolate each test: wipe the on-disk studio docs before and after."""
    if ws.WS_STUDIO_DIR.exists():
        shutil.rmtree(ws.WS_STUDIO_DIR, ignore_errors=True)
    ws.WS_STUDIO_DIR.mkdir(parents=True, exist_ok=True)
    yield
    if ws.WS_STUDIO_DIR.exists():
        shutil.rmtree(ws.WS_STUDIO_DIR, ignore_errors=True)


def _gen(reply):
    """An injected generate() that returns a fixed reply, ignoring inputs."""
    return lambda messages, system, orb_label: reply


# ── sanitization (security) ──────────────────────────────────────────────────
class TestSanitization:
    def test_css_strips_style_and_script_breakout(self):
        dirty = ".ws-custom-root .card{color:red} </style><script>alert(1)</script>"
        clean = ws._sanitize_css(dirty)
        assert "<script" not in clean
        assert "</style" not in clean
        assert "color:red" in clean

    def test_css_strips_js_url_import_and_expression(self):
        clean = ws._sanitize_css(
            ".ws-custom-root{background:url(javascript:alert(1))}"
            "@import 'evil.css'; .x{width:expression(alert(1))}"
        )
        assert "javascript:" not in clean
        assert "@import" not in clean
        assert "expression(" not in clean

    def test_css_length_capped(self):
        assert len(ws._sanitize_css("a" * 99999)) <= 8000

    def test_patch_drops_unknown_keys(self):
        out = ws._sanitize_patch({"css": ".ws-custom-root{}", "evil": "rm -rf",
                                   "onclick": "x", "summary": "ok"})
        assert set(out.keys()) <= ws._ALLOWED_KEYS
        assert "evil" not in out and "onclick" not in out

    def test_patch_accent_validation(self):
        assert ws._sanitize_patch({"accent": "#00d4ff"})["accent"] == "#00d4ff"
        assert ws._sanitize_patch({"accent": "00d4ff"})["accent"] == "#00d4ff"
        # invalid hex is rejected (key absent)
        assert "accent" not in ws._sanitize_patch({"accent": "redish"})

    def test_patch_density_whitelist(self):
        assert ws._sanitize_patch({"density": "compact"})["density"] == "compact"
        assert ws._sanitize_patch({"density": "ultra"})["density"] is None

    def test_patch_hidden_and_actions_capped(self):
        out = ws._sanitize_patch({
            "hidden": ["x"] * 100,
            "actions": [{"label": "L", "prompt": "P"}] * 50 + [{"bad": 1}],
        })
        assert len(out["hidden"]) <= 40
        assert len(out["actions"]) <= 8
        assert all("label" in a and "prompt" in a for a in out["actions"])


# ── merge semantics ──────────────────────────────────────────────────────────
class TestMerge:
    def test_present_overrides_none_clears(self):
        cur = {"accent": "#fff", "density": "compact"}
        merged = ws._merge_customization(cur, {"accent": "#000", "density": None})
        assert merged["accent"] == "#000"
        assert "density" not in merged

    def test_summary_never_persisted(self):
        merged = ws._merge_customization({}, {"summary": "x", "accent": "#000"})
        assert "summary" not in merged


# ── apply / revert / reset ───────────────────────────────────────────────────
class TestApplyRevertReset:
    def test_apply_snapshots_then_merges(self):
        doc, ver = ws.apply_customization("home", {"accent": "#00d4ff", "summary": "tint"})
        assert ver is not None
        assert doc["customization"]["accent"] == "#00d4ff"
        # The snapshot captured the PRE-change (empty) state.
        assert ver["customization"] == {}

    def test_empty_patch_no_version(self):
        doc, ver = ws.apply_customization("home", {"bogus": 1})
        assert ver is None
        assert doc["customization"] == {}

    def test_revert_restores_and_is_undoable(self):
        ws.apply_customization("home", {"accent": "#111", "summary": "a"})
        _, ver2 = ws.apply_customization("home", {"accent": "#222", "summary": "b"})
        # ver2 snapshotted the "#111" state — revert to it.
        doc = ws.revert_customization("home", ver2["id"])
        assert doc["customization"]["accent"] == "#111"
        # Revert itself pushed a "before revert" snapshot (undoable).
        assert any(v["label"] == "before revert" for v in doc["versions"])

    def test_revert_unknown_version_returns_none(self):
        ws.apply_customization("home", {"accent": "#111"})
        assert ws.revert_customization("home", "vDEADBEEF") is None

    def test_reset_clears_but_snapshots(self):
        ws.apply_customization("home", {"accent": "#111", "summary": "a"})
        doc = ws.reset_customization("home")
        assert doc["customization"] == {}
        assert any(v["label"] == "before reset" for v in doc["versions"])

    def test_all_customizations_skips_empty(self):
        ws.apply_customization("home", {"accent": "#111"})
        ws.reset_customization("calendar")  # empty -> not surfaced
        out = ws.all_customizations()
        assert "home" in out
        assert "calendar" not in out


# ── patch extraction ─────────────────────────────────────────────────────────
class TestPatchExtraction:
    def test_extract_and_strip(self):
        text = ('Done — rounding the cards.\n'
                '```friday-customize\n{"summary":"round","accent":"#0f0"}\n```')
        assert ws._extract_patch(text) == {"summary": "round", "accent": "#0f0"}
        assert "friday-customize" not in ws._strip_patch_block(text)
        assert ws._strip_patch_block(text) == "Done — rounding the cards."

    def test_no_block_returns_none(self):
        assert ws._extract_patch("just a chat reply") is None


# ── agentic chat turn (injected generator, no model) ─────────────────────────
class TestChatTurn:
    def test_plain_reply_applies_nothing(self):
        res = ws.workspace_chat_turn("home", "Home", "how does this work?",
                                     generate=_gen("It shows your day."))
        assert res["applied"] is False
        assert res["response"] == "It shows your day."
        doc = ws.load_ws_doc("home")
        assert len(doc["chat"]) == 2  # user + friday

    def test_patch_reply_applies_and_is_revertible(self):
        reply = ('On it.\n```friday-customize\n'
                 '{"summary":"compact home","density":"compact"}\n```')
        res = ws.workspace_chat_turn("home", "Home", "make it denser",
                                     generate=_gen(reply))
        assert res["applied"] is True
        assert res["response"] == "On it."           # patch block stripped
        assert res["customization"]["density"] == "compact"
        assert res["change"] == "compact home"
        # The returned revert_to undoes the change (back to empty).
        doc = ws.revert_customization("home", res["revert_to"])
        assert doc["customization"] == {}

    def test_malicious_css_from_model_is_sanitized(self):
        reply = ('done\n```friday-customize\n'
                 '{"summary":"x","css":".ws-custom-root .card{color:red} '
                 '</style><script>steal()</script>"}\n```')
        res = ws.workspace_chat_turn("home", "Home", "round the cards",
                                     generate=_gen(reply))
        css = res["customization"].get("css", "")
        assert "<script" not in css and "</style" not in css
        assert "color:red" in css


# ── HTTP routes (offline: conftest auto-stubs the model) ─────────────────────
class TestRoutes:
    def test_customizations_endpoint(self, client):
        ws.apply_customization("home", {"accent": "#111"})
        resp = client.get("/api/workspace/customizations")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["status"] == "ok"
        assert "home" in data["customizations"]

    def test_chat_get_shape(self, client):
        resp = client.get("/api/workspace/calendar/chat")
        assert resp.status_code == 200
        data = resp.get_json()
        assert set(data.keys()) >= {"status", "workspace", "chat", "customization", "versions"}

    def test_chat_post_requires_message(self, client):
        resp = client.post("/api/workspace/home/chat", json={})
        assert resp.status_code == 400

    def test_chat_post_runs_offline(self, client):
        """The stubbed model returns canned text with no patch block."""
        resp = client.post("/api/workspace/home/chat", json={"message": "hi"})
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["status"] == "ok"
        assert data["applied"] is False
        assert data["response"]

    def test_revert_route(self, client):
        _, ver = ws.apply_customization("home", {"accent": "#abc", "summary": "tint"})
        resp = client.post("/api/workspace/home/revert", json={"version_id": ver["id"]})
        assert resp.status_code == 200
        assert resp.get_json()["customization"] == {}

    def test_revert_requires_version(self, client):
        assert client.post("/api/workspace/home/revert", json={}).status_code == 400

    def test_revert_unknown_version_404(self, client):
        resp = client.post("/api/workspace/home/revert", json={"version_id": "vNOPE"})
        assert resp.status_code == 404

    def test_reset_route(self, client):
        ws.apply_customization("home", {"accent": "#abc"})
        resp = client.post("/api/workspace/home/reset")
        assert resp.status_code == 200
        assert resp.get_json()["customization"] == {}

    def test_chat_clear_route(self, client):
        ws.workspace_chat_turn("home", "Home", "hi", generate=_gen("hello"))
        resp = client.post("/api/workspace/home/chat/clear")
        assert resp.status_code == 200
        assert resp.get_json()["chat"] == []

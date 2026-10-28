"""§34.6 release-gate egress tests, end to end, in CI.

  NS-34.6-4   vault content on every path a prompt takes to a cloud model,
              including the vault gate itself failing
  NS-34.6-5   file grants: a granted file's text passes, and stops passing the
              moment the grant is revoked, overruled by a deny or expires
  NS-34.6-11  voice transcripts: the session review that carries a voice
              transcript to a task model
  NS-34.6-12  images: metadata (EXIF, GPS) never leaves; image blocks in a
              cloud payload get their own ledger row
  NS-34.6-13  remote model substitution: a cloud-tagged model behind the local
              daemon is gated as cloud (the predicted-provider re-gate tests
              in tests/gauntlet now run in CI too)

The wire is the suite's offline transport (tests/api/conftest.py); nothing
reaches a model or Google.
"""
from __future__ import annotations

import io
import json
import time

import pytest

import agent_friday.core as core
import agent_friday.services.model_router as mr
from agent_friday.services import egress_gate as eg
from agent_friday.services import file_grants as fg

# Hard-tagged TIER_2 (the vault's active tasks) and plain enough that no
# keyword or pattern in the egress gate would catch it: only the vault's
# tiering keeps it off a cloud model.
TODO_SECRET = "pick up the blue bicycle from Marta on Thursday"
TASK_PROMPT = "give me a status update on my pending tasks"

# Withheld by the egress gate when nothing registered it (see
# tests/unit/test_egress_grant_origin.py).
PRIVATE_PARA = "todo: call the accountant about the account balance"

SSN = "123-45-6789"  # pragma: allowlist secret


def _log_rows(path):
    if not path.exists():
        return []
    return [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]


def _clear_registries():
    with eg._TRUSTED_LOCK:
        eg._PUBLIC_PARAS.clear()
        eg._PUBLIC_ORIGINS.clear()
        eg._OVERRIDE_PARAS.clear()
        eg._PROVIDER_ECHO.clear()
        for name in ("_PARA_ORIGINS", "_OVERRIDE_ORIGINS"):
            reg = getattr(eg, name, None)
            if reg is not None:
                reg.clear()


# ── NS-34.6-4 vault content ──────────────────────────────────────────────────

@pytest.fixture
def vault_todo(monkeypatch):
    monkeypatch.setattr(mr, "_load_vault_summary",
                        lambda: {"active_todos": [{"status": "open", "task": TODO_SECRET}]})
    monkeypatch.setattr(mr, "_vault_local_only", lambda: True)


def _context_prompt(provider, tmp_path):
    from agent_friday.privacy.vault_access import VaultAccessControl
    prompt, _sources = mr._build_context_prompt(
        TASK_PROMPT, workspace="task", provider=provider,
        vault_control=VaultAccessControl(log_path=tmp_path / "vault.jsonl"))
    return prompt


class TestVaultContent:
    def test_cloud_prompt_withholds_the_tier2_section(self, vault_todo, tmp_path):
        assert TODO_SECRET not in _context_prompt("cloud", tmp_path)

    def test_local_prompt_keeps_it(self, vault_todo, tmp_path):
        assert TODO_SECRET in _context_prompt("local", tmp_path)

    def test_a_failing_vault_gate_fails_closed_for_cloud(self, vault_todo, tmp_path, monkeypatch):
        from agent_friday.privacy import vault_access as va
        monkeypatch.setattr(va.VaultAccessControl, "assemble_prompt",
                            lambda *a, **k: (_ for _ in ()).throw(RuntimeError("gate broke")))
        assert TODO_SECRET not in _context_prompt("cloud", tmp_path)

    def test_a_failing_vault_gate_still_serves_a_local_seat(self, vault_todo, tmp_path, monkeypatch):
        from agent_friday.privacy import vault_access as va
        monkeypatch.setattr(va.VaultAccessControl, "assemble_prompt",
                            lambda *a, **k: (_ for _ in ()).throw(RuntimeError("gate broke")))
        assert TODO_SECRET in _context_prompt("local", tmp_path)

    def test_a_cloud_tagged_model_is_cloud_when_the_gate_fails(self, vault_todo, tmp_path, monkeypatch):
        from agent_friday.privacy import vault_access as va
        monkeypatch.setattr(va.VaultAccessControl, "assemble_prompt",
                            lambda *a, **k: (_ for _ in ()).throw(RuntimeError("gate broke")))
        prompt, _ = mr._build_context_prompt(
            TASK_PROMPT, workspace="task", provider="ollama", model="glm-4.6:cloud",
            vault_control=va.VaultAccessControl(log_path=tmp_path / "vault.jsonl"))
        assert TODO_SECRET not in prompt


@pytest.fixture
def anthropic_cloud(monkeypatch, patch_app):
    """Cloud only, the suite's fake Anthropic client, no other key."""
    s = dict(core.DEFAULT_SETTINGS)
    s["model_routing"] = dict(s.get("model_routing") or {})
    s["model_routing"]["mode"] = "cloud_only"
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.delenv("OR_API_KEY", raising=False)
    patch_app("_load_settings", lambda *a, **k: s)
    from agent_friday.services import credential_store as cs
    monkeypatch.setattr(cs, "get_provider_key", lambda name: None)
    import agent_friday.services.demo_mode as dm
    monkeypatch.setattr(dm, "is_demo", lambda *a, **k: False)


def test_chat_to_a_cloud_model_carries_no_vault_task(client, anthropic_cloud, vault_todo, offline_calls):
    client.post("/api/chat", json={"message": TASK_PROMPT})
    wire = json.dumps(offline_calls["anthropic"]) + json.dumps(offline_calls["requests"])
    assert offline_calls["anthropic"] or offline_calls["requests"], "no cloud call was made"
    assert TODO_SECRET not in wire


# ── NS-34.6-5 file grants ────────────────────────────────────────────────────

@pytest.fixture
def grants(tmp_path, monkeypatch):
    ledger = tmp_path / "file_grants.jsonl"
    monkeypatch.setattr(fg, "_ledger_path", lambda: ledger)
    fg._invalidate_cache()
    _clear_registries()
    yield
    fg._invalidate_cache()
    _clear_registries()


def _granted_file(tmp_path):
    p = tmp_path / "notes.txt"
    p.write_text(PRIVATE_PARA, encoding="utf-8")
    g = fg.create_file_grant(str(p))
    fg.on_file_read(p, PRIVATE_PARA)
    return p, g


class TestFileGrants:
    def test_a_granted_paragraph_passes_with_its_grant_named(self, grants, tmp_path):
        log = tmp_path / "egress.jsonl"
        _p, g = _granted_file(tmp_path)
        assert eg._gate_text(PRIVATE_PARA, "anthropic", "tool_result", log_path=log) == PRIVATE_PARA
        assert any(r.get("action") == "allow" and f"user-grant:{g['id']}" in r.get("reason", "")
                   for r in _log_rows(log))

    def test_without_a_grant_it_is_withheld(self, grants, tmp_path):
        assert PRIVATE_PARA not in eg._gate_text(PRIVATE_PARA, "anthropic", "tool_result")

    def test_revoking_the_grant_stops_it_at_once(self, grants, tmp_path):
        _p, g = _granted_file(tmp_path)
        fg.revoke(g["id"])
        fg._invalidate_cache()
        assert PRIVATE_PARA not in eg._gate_text(PRIVATE_PARA, "anthropic", "tool_result")

    def test_a_later_deny_mark_stops_it_at_once(self, grants, tmp_path):
        p, _g = _granted_file(tmp_path)
        fg.create_deny_mark(str(p), "file")
        fg._invalidate_cache()
        assert PRIVATE_PARA not in eg._gate_text(PRIVATE_PARA, "anthropic", "tool_result")

    def test_a_revoked_grant_stops_it_inside_a_longer_field(self, grants, tmp_path):
        _p, g = _granted_file(tmp_path)
        fg.revoke(g["id"])
        fg._invalidate_cache()
        payload = "The weather is mild today.\n\n" + PRIVATE_PARA
        assert PRIVATE_PARA not in eg._gate_text(payload, "anthropic", "tool_result")

    def test_an_expired_folder_grant_stops_it(self, grants, tmp_path):
        folder = tmp_path / "project"
        folder.mkdir()
        p = folder / "notes.txt"
        p.write_text(PRIVATE_PARA, encoding="utf-8")
        try:
            fg.create_scope_grant(str(folder), "folder", 3.0 / 86400.0)
        except Exception as e:  # a temporary folder may count as too broad here
            pytest.skip("folder grant refused for the test folder: %s" % e)
        fg._invalidate_cache()
        fg.on_file_read(p, PRIVATE_PARA)
        assert eg._gate_text(PRIVATE_PARA, "anthropic", "tool_result") == PRIVATE_PARA
        time.sleep(3.2)
        fg._invalidate_cache()
        assert PRIVATE_PARA not in eg._gate_text(PRIVATE_PARA, "anthropic", "tool_result")


# ── NS-34.6-11 voice transcripts ─────────────────────────────────────────────

class TestVoiceTranscript:
    TURNS = [("my social security number is " + SSN + ", remember it", "I won't store that."),
             ("also " + TODO_SECRET, "Noted.")]

    def _spawned(self, monkeypatch, settings):
        from agent_friday.services import voice_engine as ve
        got = {}
        monkeypatch.setattr(ve, "_spawn_task", lambda **kw: got.update(kw) or "task-1")
        monkeypatch.setattr(ve, "_load_settings", lambda *a, **k: settings)
        ve._spawn_voice_distill(self.TURNS, session_key="s1")
        return got

    def test_the_transcript_review_is_sealed_before_a_cloud_model(self, monkeypatch, tmp_path):
        got = self._spawned(monkeypatch, {})
        assert SSN in got.get("prompt", ""), "the review prompt should carry the transcript"
        sealed = eg.seal_outbound({"model": "claude-sonnet-5", "max_tokens": 100,
                                   "messages": [{"role": "user", "content": got["prompt"]}]},
                                  "anthropic", log_path=tmp_path / "egress.jsonl")
        assert SSN not in json.dumps(sealed)

    def test_an_off_record_call_is_never_reviewed(self, monkeypatch):
        assert self._spawned(monkeypatch, {"off_record": True}) == {}


# ── NS-34.6-12 images and image metadata ─────────────────────────────────────

def _photo_with_metadata(size=(8, 4)):
    from PIL import Image
    im = Image.new("RGB", size, (200, 10, 10))
    ex = Image.Exif()
    ex[0x010F] = "TestCamMake"      # Make
    ex[0x0131] = "OwnerSoftware"    # Software
    ex[0x0112] = 6                  # Orientation: rotate 90 to view
    try:
        ex[0x8825] = {1: "N", 2: (51.0, 30.0, 0.0), 3: "W", 4: (0.0, 7.0, 0.0)}
    except Exception:
        pass
    buf = io.BytesIO()
    im.save(buf, "JPEG", exif=ex)
    return buf.getvalue()


class TestImageMetadata:
    def test_strip_removes_exif_and_keeps_the_picture_upright(self):
        from PIL import Image
        from agent_friday.services.image_egress import strip_metadata
        raw = _photo_with_metadata()
        assert b"TestCamMake" in raw
        out, what = strip_metadata(raw)
        assert what == "stripped"
        assert b"TestCamMake" not in out and b"OwnerSoftware" not in out
        with Image.open(io.BytesIO(out)) as im:
            assert im.format == "JPEG"
            assert len(im.getexif()) == 0
            assert im.getexif().get_ifd(0x8825) == {}
            assert im.size == (4, 8)    # the orientation was applied

    def test_png_text_and_exif_chunks_go(self):
        from PIL import Image, PngImagePlugin
        from agent_friday.services.image_egress import strip_metadata
        info = PngImagePlugin.PngInfo()
        info.add_text("Author", "Owner Name Here")
        buf = io.BytesIO()
        Image.new("RGB", (4, 4), (0, 0, 255)).save(buf, "PNG", pnginfo=info)
        out, _what = strip_metadata(buf.getvalue())
        assert b"Owner Name Here" not in out
        with Image.open(io.BytesIO(out)) as im:
            assert im.format == "PNG" and im.size == (4, 4)

    def test_unreadable_bytes_go_unchanged_and_say_so(self):
        from agent_friday.services.image_egress import strip_metadata
        out, what = strip_metadata(b"\x89PNG\r\n\x1a\nnot really")
        assert out == b"\x89PNG\r\n\x1a\nnot really"
        assert what.startswith("not_stripped:")

    def test_inspect_image_sends_no_metadata(self, tmp_path, monkeypatch):
        from agent_friday.services import media_tools as mt
        img = tmp_path / "photo.jpg"
        img.write_bytes(_photo_with_metadata())
        sent = []

        class _Models:
            def generate_content(self, model, contents):
                sent.append(contents)
                return type("R", (), {"text": "a red square"})()

        monkeypatch.setattr(mt, "_gemini_client", lambda: type("C", (), {"models": _Models()})())
        monkeypatch.setattr(mt, "_resolve_media_path", lambda raw: (img, None))
        monkeypatch.setattr(mt, "_routing_mode", lambda: "smart")
        monkeypatch.setattr(eg, "_gate_text", lambda text, provider, field, log_path=None: text)
        rows = []
        monkeypatch.setattr(eg, "record_binary_egress",
                            lambda provider, field, **kw: rows.append((field, kw)) or {})
        out = mt._tool_inspect_image({"path": str(img), "question": "what is it?"})
        assert "a red square" in out
        part = sent[0][1]
        data = part.inline_data.data
        assert b"TestCamMake" not in data
        assert rows and "image metadata: stripped" in rows[0][1]["reason"]

    def test_an_image_block_in_a_cloud_payload_gets_a_ledger_row(self, tmp_path):
        log = tmp_path / "egress.jsonl"
        payload = {"model": "claude-sonnet-5", "max_tokens": 100, "messages": [
            {"role": "user", "content": [{"type": "text", "text": "what is on my screen?"}]},
            {"role": "assistant", "content": [{"type": "tool_use", "id": "t1",
                                               "name": "screenshot", "input": {}}]},
            {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "t1", "content": [
                {"type": "image", "source": {"type": "base64", "media_type": "image/png",
                                             "data": "aGVsbG8gd29ybGQ="}}]}]}]}
        eg.seal_outbound(payload, "anthropic", log_path=log)
        rows = [r for r in _log_rows(log) if r.get("tier") == "UNCLASSIFIABLE"]
        assert rows, _log_rows(log)
        assert rows[0]["bytes"] > 0


# ── NS-34.6-13 remote model substitution ─────────────────────────────────────

def test_a_cloud_tagged_model_behind_the_local_daemon_is_gated(tmp_path):
    _clear_registries()
    payload = {"model": "glm-4.6:cloud", "messages": [
        {"role": "user", "content": "My SSN is " + SSN + ", file it."}]}
    sealed = eg.seal_outbound(payload, "ollama", log_path=tmp_path / "egress.jsonl",
                              model="glm-4.6:cloud")
    assert SSN not in json.dumps(sealed)

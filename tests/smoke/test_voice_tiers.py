"""Per-tier end-to-end voice smoke tests — the verification gate.

Contract (docs/VOICE_SYSTEM_SPEC.md §10): every tier either passes a real
audio round-trip, or SKIPS with an actionable message that tells a human
exactly what to install. A silent pass-by-omission is a spec violation.

  Tier 1 (local CPU): real Piper TTS → WAV → real faster-whisper STT loopback.
  Tier 2 (local GPU): runs only when torch-CUDA + NeMo are actually usable;
                      otherwise skips with the exact remediation. The GPU
                      *detection* contract is asserted regardless.
  Tier 3 (Gemini Live): configuration sanity always; a REAL connect probe only
                      when FRIDAY_SMOKE_CLOUD=1 (network + key + ~cents).

Run: pytest tests/smoke -q          (part of the default offline suite)
     FRIDAY_SMOKE_CLOUD=1 pytest tests/smoke -q   (adds the live connect)
"""
import io
import os
import wave

import pytest


def _tier1_ready():
    from agent_friday.services.local_voice import get_local_voice_engine
    eng = get_local_voice_engine()
    return eng.available() and eng.models_ready()


# ── Tier 1: synth speech → STT → canned response → TTS → byte assertions ─────

@pytest.mark.skipif(
    not _tier1_ready(),
    reason="Tier-1 voice not installed — run: pip install -e .[voice-local-lite] "
           "then let the models download (Settings → Voice → Setup Wizard → "
           "Download), and re-run this smoke test.")
class TestTier1LocalCpu:
    def test_tts_stt_loopback(self):
        from agent_friday.services.local_voice import get_local_voice_engine
        eng = get_local_voice_engine()
        assert eng.ensure_ready(), (
            f"Tier-1 engine failed to load: {getattr(eng, 'last_error', '')}")

        # TTS: canned response → 24 kHz PCM16 bytes.
        pcm24 = eng.synthesize("Hello boss, the voice system is working.")
        assert pcm24 and len(pcm24) > 24000, "TTS produced <0.5 s of audio"

        # Downsample 24 kHz → 16 kHz (whisper input rate) by simple decimation
        # through the same float path the client uses.
        import numpy as np
        arr = np.frombuffer(pcm24, dtype=np.int16).astype(np.float32)
        idx = (np.arange(0, len(arr), 1.5)).astype(np.int64)
        idx = idx[idx < len(arr)]
        pcm16k = arr[idx].astype(np.int16).tobytes()

        # STT: the spoken words must come back out.
        text = eng.transcribe(pcm16k).lower()
        assert "voice" in text or "working" in text or "hello" in text, (
            f"STT loopback failed — transcript: {text!r}")

    def test_tts_wav_container(self):
        # The /api/voice/setup/test contract: BytesIO WAV, parseable, non-empty.
        from agent_friday.services.voice_engine import _synthesize_tts_wav_local
        buf = _synthesize_tts_wav_local("Smoke test.")
        assert buf is not None, (
            "No local TTS engine available — install pyttsx3 or Tier-1 voice")
        assert isinstance(buf, io.BytesIO)
        with wave.open(buf, "rb") as wf:
            assert wf.getnframes() > 0


# ── Tier 2: GPU tier — run when possible, actionable skip otherwise ──────────

class TestTier2LocalGpu:
    def test_gpu_detection_contract(self):
        """gpu_status() must report a physical NVIDIA GPU even when torch is
        CPU-only (the nvidia-smi fall-through), so the UI can offer the
        upgrade. Machines with no NVIDIA GPU are exempt."""
        from agent_friday.services.nemo_voice import gpu_status
        import shutil as _sh
        g = gpu_status()
        if _sh.which("nvidia-smi") and not g.get("cuda"):
            assert g.get("device"), (
                "nvidia-smi is present but gpu_status() reports no device — "
                "the CPU-only-torch fall-through regressed")
            assert "torch" in (g.get("detail") or "").lower(), (
                f"detail lacks the torch-CUDA remediation: {g.get('detail')!r}")

    def test_gpu_roundtrip_or_actionable_skip(self):
        from agent_friday.services.nemo_voice import gpu_tier_ready, gpu_status
        if not gpu_tier_ready():
            g = gpu_status()
            pytest.skip(
                f"Tier-2 GPU voice not runnable: {g.get('detail') or 'no CUDA'}. "
                f"Install from Settings → Voice → Setup Wizard → 'Install GPU "
                f"tier', or: pip install torch --index-url "
                f"https://download.pytorch.org/whl/cu126 && pip install -e "
                f".[voice-local-gpu]")
        from agent_friday.services.local_voice import get_local_voice_engine
        eng = get_local_voice_engine()
        eng.select_tier("gpu")
        try:
            assert eng.ensure_ready(), (
                f"GPU tier reported ready but failed to load: "
                f"{getattr(eng, 'last_error', '')}")
            pcm = eng.synthesize("GPU voice tier is working.")
            assert pcm and len(pcm) > 24000
        finally:
            eng.select_tier("cpu")


# ── Tier 3: Gemini Live — config sanity always, real connect when opted in ───

class TestTier3GeminiLive:
    def test_model_chain_sanity(self):
        from agent_friday.services import voice_engine as ve
        chain = [ve.LIVE_MODEL, ve.LIVE_MODEL_FALLBACK, ve.LIVE_MODEL_FALLBACK2]
        assert len(set(chain)) == 3
        for mid in chain:
            res = ve.validate_live_model(mid)
            assert res["ok"], f"{mid}: {res['detail']}"

    @pytest.mark.skipif(
        os.environ.get("FRIDAY_SMOKE_CLOUD") != "1",
        reason="Live Gemini connect probe — set FRIDAY_SMOKE_CLOUD=1 (needs a "
               "valid GEMINI_API_KEY and network; costs fractions of a cent).")
    @pytest.mark.real_provider_paths
    def test_live_connect_probe(self):
        import asyncio
        from agent_friday.services import voice_engine as ve
        key = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
        assert key, "GEMINI_API_KEY not set — configure it in Settings → Providers"
        from google import genai
        from google.genai import types

        async def probe(model):
            client = genai.Client(api_key=key)
            cfg = types.LiveConnectConfig(response_modalities=["AUDIO"])
            async with client.aio.live.connect(model=model, config=cfg):
                return True

        for mid in (ve.LIVE_MODEL, ve.LIVE_MODEL_FALLBACK, ve.LIVE_MODEL_FALLBACK2):
            assert asyncio.run(asyncio.wait_for(probe(mid), timeout=30)), (
                f"{mid} failed a live connect — it may have been retired; move "
                f"it to _RETIRED_LIVE_MODELS and pick a replacement from "
                f"models.list (bidiGenerateContent)")

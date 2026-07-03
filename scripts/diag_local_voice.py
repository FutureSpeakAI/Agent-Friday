"""Diagnostic: verify the Tier-1 local voice engine (faster-whisper + Piper).

Round-trips text -> Piper TTS -> PCM -> faster-whisper ASR -> text so we know
the whole on-device pipeline works, not just that imports succeed.

Usage: venv\Scripts\python scripts\diag_local_voice.py
"""
import sys
import time
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from agent_friday.services.local_voice import (
    ASR_RATE,
    PLAYBACK_RATE,
    _resample_pcm16,
    deps_status,
    get_local_voice_engine,
)

print("deps:", deps_status())
eng = get_local_voice_engine()
print("available:", eng.available(), "| models_ready:", eng.models_ready(),
      "| resolved tier:", eng.resolve_tier({}))

t0 = time.time()
ok = eng.ensure_ready(progress=lambda m: print("  progress:", m))
print(f"ensure_ready: {ok} ({time.time()-t0:.1f}s)")
if not ok:
    sys.exit(1)

text_in = "Hello, the local voice pipeline is working correctly."
t0 = time.time()
pcm24 = eng.synthesize(text_in)
print(f"TTS: {len(pcm24)} bytes @24k ({time.time()-t0:.1f}s)")

pcm16 = _resample_pcm16(pcm24, PLAYBACK_RATE, ASR_RATE)
t0 = time.time()
text_out = eng.transcribe(pcm16)
print(f"ASR ({time.time()-t0:.1f}s): {text_out!r}")
print("perf:", eng.perf_stats())
ok_round = "local voice" in text_out.lower() or "working" in text_out.lower()
print("ROUND-TRIP:", "PASS" if ok_round else "CHECK OUTPUT ABOVE")

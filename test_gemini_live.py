"""
Smoke test for Gemini Live API connectivity.
Tests: SDK, API key, audio extraction path, multi-turn conversation.

Usage:
  venv\Scripts\python test_gemini_live.py
  (GEMINI_API_KEY must be in the environment — set by start.bat)
"""
import asyncio
import os
import sys

try:
    from google import genai
    from google.genai import types
    print(f"[test] google-genai imported OK (version: {genai.__version__})")
except ImportError as e:
    print(f"ERROR: google-genai not installed: {e}")
    sys.exit(1)

GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")
if not GEMINI_API_KEY:
    print("ERROR: GEMINI_API_KEY not set. Run via start.bat or set the env var manually.")
    sys.exit(1)

print(f"[test] API key: {GEMINI_API_KEY[:8]}... (present)")
MODEL = "gemini-3.1-flash-live-preview"
print(f"[test] Model: {MODEL}")


async def test_audio_multiturn():
    """
    Test: AUDIO mode, two turns in one session.
    Verifies: audio extraction, output_transcription, multi-turn via while True loop.
    """
    print("\n--- TEST: AUDIO multi-turn ---")
    client = genai.Client(api_key=GEMINI_API_KEY)
    config = types.LiveConnectConfig(
        response_modalities=[types.Modality.AUDIO],
        speech_config=types.SpeechConfig(
            voice_config=types.VoiceConfig(
                prebuilt_voice_config=types.PrebuiltVoiceConfig(voice_name="Aoede")
            )
        ),
        output_audio_transcription=types.AudioTranscriptionConfig(),
        realtime_input_config=types.RealtimeInputConfig(
            turn_coverage="TURN_INCLUDES_ONLY_ACTIVITY",
        ),
    )

    questions = [
        "Say hello in exactly one sentence.",
        "What is two plus two? Answer in one word.",
    ]
    q_idx = 0
    turns_done = 0
    total_audio_bytes = 0

    try:
        async with client.aio.live.connect(model=MODEL, config=config) as session:
            print(f"[test] Connected! Sending Q1: {questions[0]!r}")
            await session.send_realtime_input(text=questions[0])

            while turns_done < len(questions):
                async for response in session.receive():
                    sc = response.server_content
                    if sc:
                        if sc.model_turn:
                            for part in sc.model_turn.parts:
                                if part.inline_data:
                                    total_audio_bytes += len(part.inline_data.data)
                                    print(f"[test] AUDIO: {len(part.inline_data.data)} bytes (total {total_audio_bytes})")
                                if part.text:
                                    print(f"[test] TEXT: {part.text!r}")
                        if sc.output_transcription:
                            print(f"[test] TRANSCRIPT: {sc.output_transcription.text!r}")
                        if sc.turn_complete:
                            turns_done += 1
                            print(f"[test] Turn {turns_done} complete. Audio so far: {total_audio_bytes} bytes")
                            if turns_done < len(questions):
                                q_idx += 1
                                print(f"[test] Sending Q{q_idx+1}: {questions[q_idx]!r}")
                                await session.send_realtime_input(text=questions[q_idx])
                            break  # re-enter the outer while to get next turn's receive iterator
                # session.receive() iterator ended — outer while re-enters it for the next turn

        if turns_done == len(questions) and total_audio_bytes > 0:
            print(f"\n[test] ALL TESTS PASSED — {turns_done} turns, {total_audio_bytes} total audio bytes")
        else:
            print(f"\n[test] PARTIAL: turns_done={turns_done}, total_audio={total_audio_bytes}")

    except Exception as e:
        print(f"[test] ERROR: {type(e).__name__}: {e}")
        import traceback
        traceback.print_exc()


async def main():
    await test_audio_multiturn()
    print("\n[test] Done.")


if __name__ == "__main__":
    asyncio.run(main())

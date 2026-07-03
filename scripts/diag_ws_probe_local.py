"""Probe /ws/voice-local (Tier-1 on-device voice) on the running server."""
import json
import sys
import time

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
import websocket

URL = "ws://127.0.0.1:3000/ws/voice-local"
print("connecting to", URL)
ws = websocket.create_connection(URL, timeout=120)
ws.settimeout(120)

start = time.time()
audio_frames = 0
sent = False
try:
    while time.time() - start < 120:
        try:
            raw = ws.recv()
        except websocket.WebSocketTimeoutException:
            print("TIMEOUT")
            break
        except Exception as e:
            print("RECV END:", type(e).__name__, str(e)[:200])
            break
        if not raw:
            continue
        msg = json.loads(raw)
        t = msg.get("type")
        if t == "audio":
            audio_frames += 1
            if audio_frames in (1, 5) or audio_frames % 20 == 0:
                print(f"FRAME audio #{audio_frames}")
            continue
        print("FRAME", json.dumps(msg, ensure_ascii=False)[:300])
        if t == "status" and msg.get("text", "").startswith("live") and not sent:
            sent = True
            ws.send(json.dumps({"type": "text",
                                "text": "Reply with only the three words: local voice okay"}))
            print(">> sent text turn")
        if t == "turn_end" and sent:
            print(">> turn_end after text — local path verified")
            ws.send(json.dumps({"type": "end"}))
            break
        if t == "error":
            print(">> ERROR — closing")
            break
finally:
    try:
        ws.close()
    except Exception:
        pass
print(f"SUMMARY: audio_frames={audio_frames} elapsed={time.time()-start:.1f}s")

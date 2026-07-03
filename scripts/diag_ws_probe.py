"""Probe the running Friday server's /ws/live exactly like the browser does.

Captures every frame (status / error / text / audio counts) so we can see
what the production handler actually reports when a voice session starts.
"""
import json
import sys
import time

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
import websocket

URL = "ws://127.0.0.1:3000/ws/live"
print("connecting to", URL)
ws = websocket.create_connection(URL, timeout=75)
ws.settimeout(75)

start = time.time()
audio_frames = 0
audio_b64_chars = 0
sent_text = False
got_live = False

try:
    while time.time() - start < 75:
        try:
            raw = ws.recv()
        except websocket.WebSocketTimeoutException:
            print("TIMEOUT waiting for next frame")
            break
        except Exception as e:
            print("RECV END:", type(e).__name__, str(e)[:200])
            break
        if not raw:
            continue
        try:
            msg = json.loads(raw)
        except Exception:
            print("RAW FRAME:", str(raw)[:120])
            continue
        t = msg.get("type")
        if t == "audio":
            audio_frames += 1
            audio_b64_chars += len(msg.get("data", ""))
            if audio_frames in (1, 10) or audio_frames % 100 == 0:
                print(f"FRAME audio #{audio_frames} (cum {audio_b64_chars} b64 chars)")
            continue
        print("FRAME", json.dumps(msg, ensure_ascii=False)[:500])
        if t == "status" and msg.get("text") == "live":
            got_live = True
        if t == "turn_end":
            if not sent_text:
                sent_text = True
                ws.send(json.dumps({"type": "text",
                                    "text": "Reply with only the words: voice test okay"}))
                print(">> sent text turn after greeting")
            else:
                print(">> SECOND turn_end — full round trip verified")
                ws.send(json.dumps({"type": "end"}))
                break
        if t == "error":
            print(">> ERROR FRAME received — closing")
            break
finally:
    try:
        ws.close()
    except Exception:
        pass

print(f"SUMMARY: got_live={got_live} audio_frames={audio_frames} "
      f"audio_b64_chars={audio_b64_chars} elapsed={time.time()-start:.1f}s")

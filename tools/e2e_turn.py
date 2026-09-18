"""Drive one real /api/chat turn on conv-main and record everything.

Detached so a shell timeout cannot abort it mid-generation. Writes:
  ~/.friday/runtime/e2e_result.json   the full reply payload (or the error)
"""
import json
import os
import time
import urllib.error
import urllib.request

OUT = os.path.expanduser("~/.friday/runtime/e2e_result.json")
MSG = os.environ.get("E2E_MSG", "What's on my calendar tomorrow?")


def main():
    body = json.dumps({"message": MSG, "conversation_id": "conv-main"}).encode()
    req = urllib.request.Request(
        "http://localhost:3000/api/chat", data=body,
        headers={"Content-Type": "application/json"}, method="POST")
    rec = {"sent": MSG, "t0": time.time()}
    try:
        with urllib.request.urlopen(req, timeout=900) as r:
            rec["status"] = r.status
            rec["reply"] = json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        rec["status"] = e.code
        rec["error_body"] = e.read().decode()[:4000]
    except Exception as e:
        rec["status"] = "exception"
        rec["error_body"] = repr(e)
    rec["elapsed_s"] = round(time.time() - rec["t0"], 1)
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(rec, f, indent=2, ensure_ascii=False)
    print("done", rec.get("status"), rec["elapsed_s"])


if __name__ == "__main__":
    main()

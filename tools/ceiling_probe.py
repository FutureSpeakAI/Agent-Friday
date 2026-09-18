"""Find the seat's ACTUAL accepted request size by observation.

Sends progressively larger requests with max_tokens=1 (so nothing is really
generated) and reports the first size the seat refuses. Read-mostly: a refused
request costs nothing, an accepted one costs a single token.
"""
import json
import os
import sys
import urllib.error
import urllib.request

SEAT = os.environ.get("FRIDAY_SEAT", "http://127.0.0.1:8095")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))


def _post(path, body, timeout=120):
    req = urllib.request.Request(
        SEAT + path, data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()[:300]


def ntok(text):
    return len(_post("/tokenize", {"content": text})[1].get("tokens") or [])


def main():
    from agent_friday.services.agent import CLAUDE_TOOLS
    from agent_friday.routing.model_router import anthropic_to_openai_tools
    tools = anthropic_to_openai_tools(CLAUDE_TOOLS)

    # A filler word that is reliably ~1 token, so target sizes are honest.
    unit = " tokenising"
    per = ntok(unit * 100) / 100.0

    print(f"{'target':>8} {'true_prompt':>12} {'tools':>6} {'status':>7}  detail")
    for target in (4000, 8000, 12000, 16000, 20000, 24000, 28000, 31000):
        for with_tools in (True, False):
            filler = unit * int(target / per)
            msgs = [{"role": "user", "content": filler}]
            body = {"messages": msgs, "max_tokens": 1, "temperature": 0}
            if with_tools:
                body["tools"] = tools
                body["tool_choice"] = "auto"
            # measure what the template really renders
            tbody = {"messages": msgs}
            if with_tools:
                tbody["tools"] = tools
            rendered = _post("/apply-template", tbody)[1]["prompt"]
            true_n = ntok(rendered)
            code, detail = _post("/v1/chat/completions", body)
            ok = "OK" if code == 200 else str(code)
            note = "" if code == 200 else str(detail)[:120].replace("\n", " ")
            print(f"{target:>8} {true_n:>12} {str(with_tools):>6} {ok:>7}  {note}")


if __name__ == "__main__":
    main()

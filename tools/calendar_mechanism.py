"""Why did the model not call query_calendar when it HAD query_calendar?

Sends the same question to the same seat with the same fitted tool set, twice:
short context vs. Stephen's real conv-main history. If the short one emits a
tool call and the long one does not, the transcript is the cause, not the
budget and not the tool.

Diagnostic only. Nothing is fixed here.
"""
import json
import os
import sys
import urllib.request

SEAT = "http://127.0.0.1:8095"
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
Q = "What's on my calendar tomorrow?"


def post(path, body, timeout=300):
    req = urllib.request.Request(
        SEAT + path, data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode())


def main():
    from agent_friday.services.agent import CLAUDE_TOOLS
    from agent_friday.routing.model_router import anthropic_to_openai_tools
    from agent_friday.services import tool_budget as tb
    M = "gemma4:e2b-fridayweaver-1.0"

    hist = []
    path = os.path.expanduser("~/.friday/conversations/conv-main/messages.jsonl")
    for line in open(path, encoding="utf-8"):
        try:
            d = json.loads(line)
        except Exception:
            continue
        t, r = d.get("text"), d.get("role")
        if isinstance(t, str) and t and r in ("user", "friday"):
            hist.append({"role": "assistant" if r == "friday" else r,
                         "content": t})
    hist = hist[-100:]

    fitted, _ = tb.fit_tools_to_seat(M, CLAUDE_TOOLS, prompt_cost=10116,
                                     intent=Q)
    tools = anthropic_to_openai_tools(list(fitted))
    names = [t["function"]["name"] for t in tools]
    print(f"tools sent: {len(tools)}   query_calendar present: "
          f"{'query_calendar' in names}")

    for label, msgs in (("SHORT (question only)", [{"role": "user", "content": Q}]),
                        ("LONG (conv-main tail)", hist + [{"role": "user", "content": Q}])):
        body = {"model": M, "messages": msgs, "tools": tools,
                "tool_choice": "auto", "temperature": 0.7, "max_tokens": 400,
                "chat_template_kwargs": {"enable_thinking": False}}
        try:
            r = post("/v1/chat/completions", body)
        except Exception as e:
            print(f"\n--- {label} ---\n  ERROR {e}")
            continue
        m = r["choices"][0]["message"]
        tc = m.get("tool_calls")
        print(f"\n--- {label} ---")
        print(f"  finish_reason : {r['choices'][0].get('finish_reason')}")
        print(f"  tool_calls    : "
              f"{[c['function']['name'] for c in tc] if tc else 'NONE'}")
        print(f"  content       : {repr((m.get('content') or '')[:320])}")


if __name__ == "__main__":
    main()

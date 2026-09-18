"""Length, or contamination? Same history, calendar narrations removed.

If stripping the prior calendar answers restores the tool call at the SAME
context length, the cause is what the history says, not how long it is.
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


def ntok(t):
    return len(post("/tokenize", {"content": t})["tokens"])


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

    # Same messages, same count; calendar-answer text replaced with filler of
    # comparable length so the CONTEXT LENGTH is held roughly constant.
    scrubbed, n_scrub = [], 0
    for m in hist:
        c = m["content"]
        if m["role"] == "assistant" and "calendar" in c.lower():
            c = ("Noted, boss. I've logged that against the project file and "
                 "will keep it in view. " * max(1, len(c) // 80))[:len(c)]
            n_scrub += 1
        scrubbed.append({"role": m["role"], "content": c})

    fitted, _ = tb.fit_tools_to_seat(M, CLAUDE_TOOLS, prompt_cost=10116,
                                     intent=Q)
    tools = anthropic_to_openai_tools(list(fitted))

    print(f"assistant messages scrubbed: {n_scrub}")
    for label, msgs in (("ORIGINAL history", hist),
                        ("SCRUBBED history", scrubbed)):
        full = msgs + [{"role": "user", "content": Q}]
        size = ntok("\n".join(m["content"] for m in full))
        body = {"model": M, "messages": full, "tools": tools,
                "tool_choice": "auto", "temperature": 0.7, "max_tokens": 400,
                "chat_template_kwargs": {"enable_thinking": False}}
        r = post("/v1/chat/completions", body)
        m0 = r["choices"][0]["message"]
        tc = m0.get("tool_calls")
        print(f"\n--- {label}  (~{size:,} tokens of transcript) ---")
        print(f"  tool_calls : "
              f"{[c['function']['name'] for c in tc] if tc else 'NONE'}")
        print(f"  content    : {repr((m0.get('content') or '')[:220])}")


if __name__ == "__main__":
    main()

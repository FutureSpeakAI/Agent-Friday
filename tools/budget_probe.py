"""Measure the tool budget's estimate against the seat's own tokenizer.

Run:  venv\Scripts\python.exe tools\budget_probe.py

Answers one question by observation: how far does chars/4 fall short of what
the chat template actually renders for N tool declarations? Everything here is
read-only -- it asks the running seat to tokenize, it does not generate.
"""
import json
import os
import sys
import urllib.request

SEAT = os.environ.get("FRIDAY_SEAT", "http://127.0.0.1:8095")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))


def _post(path, body, timeout=60):
    req = urllib.request.Request(
        SEAT + path, data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode())


def ntok(text):
    return len(_post("/tokenize", {"content": text}).get("tokens") or [])


def main():
    props = json.loads(urllib.request.urlopen(SEAT + "/props", timeout=5)
                       .read().decode())
    n_ctx = int(props["default_generation_settings"]["n_ctx"])
    print(f"seat n_ctx (served)            : {n_ctx:,}")

    from agent_friday.services.agent import CLAUDE_TOOLS
    from agent_friday.routing.model_router import anthropic_to_openai_tools
    from agent_friday.services.model_router import FRIDAY_SYSTEM_PROMPT

    tools = anthropic_to_openai_tools(CLAUDE_TOOLS)
    print(f"registry tools                 : {len(CLAUDE_TOOLS)}")

    # Stephen's real history. Stored schema is {role: user|friday|system,
    # text: ...}; the wire shape is {role: user|assistant, content: ...}.
    path = os.path.expanduser("~/.friday/conversations/conv-main/messages.jsonl")
    tail = int(os.environ.get("FRIDAY_TAIL", "102"))
    msgs = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                d = json.loads(line)
            except Exception:
                continue
            role = d.get("role")
            text = d.get("text")
            if not isinstance(text, str) or not text:
                continue
            if role == "friday":
                role = "assistant"
            if role not in ("user", "assistant"):
                continue
            msgs.append({"role": role, "content": text})
    msgs = msgs[-tail:]
    print(f"conv-main turns (tail {tail})     : {len(msgs)}")

    # THE MEASUREMENT THAT MATTERS: transcript chars/4 vs. real tokens.
    tchars = sum(len(m["content"]) for m in msgs)
    t_est = tchars // 4
    t_true = ntok("\n".join(m["content"] for m in msgs))
    print(f"  transcript chars             : {tchars:,}")
    print(f"  transcript chars/4 estimate  : {t_est:,}")
    print(f"  transcript TRUE tokens       : {t_true:,}"
          f"   ({t_true / max(1, t_est):.2f}x estimate)")

    convo = [{"role": "system", "content": FRIDAY_SYSTEM_PROMPT}] + msgs

    # --- what the budget currently believes -------------------------------
    est_prompt = sum(len(m["content"]) for m in convo) // 4
    est_tools = len(json.dumps(CLAUDE_TOOLS)) // 4
    print(f"\nESTIMATE (chars/4)")
    print(f"  prompt                       : {est_prompt:,}")
    print(f"  {len(CLAUDE_TOOLS)} tool declarations       : {est_tools:,}")
    print(f"  total                        : {est_prompt + est_tools:,}")

    # --- what the seat actually renders -----------------------------------
    def rendered(messages, tool_list):
        body = {"messages": messages}
        if tool_list:
            body["tools"] = tool_list
        return _post("/apply-template", body)["prompt"]

    true_notools = ntok(rendered(convo, None))
    true_tools = ntok(rendered(convo, tools))
    print(f"\nTRUE (seat /apply-template + /tokenize)")
    print(f"  prompt, no tools             : {true_notools:,}")
    print(f"  prompt + {len(tools)} tools          : {true_tools:,}")
    print(f"  tool declarations cost       : {true_tools - true_notools:,}")
    print(f"  vs estimate                  : "
          f"{(true_tools - true_notools) / max(1, est_tools):.2f}x")
    print(f"\n  headroom against {n_ctx:,}      : {n_ctx - true_tools:,}"
          f"  {'OVERFLOW' if true_tools > n_ctx else 'ok'}")


if __name__ == "__main__":
    main()

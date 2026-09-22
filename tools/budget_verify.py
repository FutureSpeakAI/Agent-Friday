"""Verify the three repairs against the LIVE seat and the real registry."""
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
MODEL = os.environ.get("FRIDAY_MODEL", "gemma4:e2b-fridayweaver-1.0")


def main():
    from agent_friday.services.agent import CLAUDE_TOOLS
    from agent_friday.services import tool_budget as tb

    print(f"window seen by budget : {tb._window(MODEL):,}")

    path = os.path.expanduser("~/.friday/conversations/conv-main/messages.jsonl")
    msgs = []
    for line in open(path, encoding="utf-8"):
        try:
            d = json.loads(line)
        except Exception:
            continue
        t, r = d.get("text"), d.get("role")
        if isinstance(t, str) and t and r in ("user", "friday"):
            msgs.append({"role": "assistant" if r == "friday" else r,
                         "content": t})
    msgs = msgs[-102:]
    system = "You are Friday." * 900          # stand-in bulky system prompt
    intent = "what is on my calendar tomorrow?"

    # 1. TRUE MEASUREMENT ---------------------------------------------------
    true_n = tb.measure_request(MODEL, system, msgs, None)
    est_n = (len(system) + sum(len(m["content"]) for m in msgs)) // 4
    print(f"\n[1] measured prompt   : {true_n}")
    print(f"    chars/4 estimate  : {est_n:,}")
    assert true_n, "measure_request returned nothing — seat unreachable?"

    # 2. INTENT RANKING -----------------------------------------------------
    kept, note = tb.fit_tools_to_seat(
        MODEL, CLAUDE_TOOLS, intent=intent, system=system, messages=msgs)
    names = [t.get("name") for t in kept]
    print(f"\n[2] kept {len(names)}/{len(CLAUDE_TOOLS)} tools")
    print(f"    query_calendar kept: {'query_calendar' in names}")
    assert "query_calendar" in names, "THE regression: calendar tool trimmed"

    # 3. IDEMPOTENCE (no second trim) ---------------------------------------
    grown = (system or "") + "\n\n[SEAT] " + (note or "")
    again, note2 = tb.fit_tools_to_seat(
        MODEL, kept, intent=intent, system=grown, messages=msgs)
    print(f"\n[3] re-fit: {len(kept)} -> {len(again)}  (note again: "
          f"{note2 is not None})")
    assert len(again) == len(kept), "trimmed twice"
    assert note2 is None

    # 4. THE WHOLE REQUEST ACTUALLY FITS ------------------------------------
    from agent_friday.routing.model_router import anthropic_to_openai_tools
    final = tb.measure_request(MODEL, grown, msgs,
                               anthropic_to_openai_tools(list(again)))
    win = tb._window(MODEL)
    print(f"\n[4] final rendered    : {final:,} of {win:,}"
          f"   headroom {win - final:,}")
    assert final < win, "final request still overflows"
    print("\nALL CHECKS PASSED")


if __name__ == "__main__":
    main()

"""Tool-calling eval for the voice front: the judge of decision D3 (local
voice spec §9). Rig-only: it needs a serving front seat and is not collected
by pytest (no ``test_`` prefix).

Each case is one utterance and what a correct front does with it: call one
named tool with its required arguments, call NOTHING (a distractor), or hand
the work to ``delegate_to_friday``. The score is exact: the first tool call's
name, and every required argument present. Pass bar: >= 90 % for the default
front; Qwen3-1.7B is adopted beside the brain only at >= 85 %.

Usage (with the front serving, e.g. after arming a session):
    FRIDAY_HOME=<scratch> python tests/rig/voice_tool_eval.py \
        --base http://127.0.0.1:8125 --model qwen3-4b-instruct-2507 \
        --out runtime/voice/bench/tool_eval_<model>.json
"""
from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.request

NOTHING = None
DELEGATE = "delegate_to_friday"

#: (utterance, expected tool | None | DELEGATE, required args that must appear)
CASES = [
    ("Who is invited to this Crew conversation?", "list_crew", ()),
    ("Ask Mira to review the evidence.", "ask_crew", ("agent", "request")),
    ("Guide running Crew task task-review-01: focus on missing source citations.",
     "steer_crew", ("task_id", "message")),
    ("Ask the agent on Crew task task-review-01 what evidence it has found so far; don't change its instructions.",
     "talk_crew", ("task_id", "message")),
    ("Draft a new Crew agent named Mira to review evidence.", "propose_crew_agent", ("name", "role")),
    ("What's on my calendar this afternoon?", "query_calendar", ()),
    ("Am I free at three tomorrow?", "query_calendar", ()),
    ("Any urgent emails?", "check_email", ()),
    ("Did I hear back from the landlord?", "check_email", ()),
    ("Any other stories on the rail strike?", "search_news", ()),
    ("What's in the news?", "get_briefing", ()),
    ("Read me today's briefing.", "get_briefing", ()),
    ("Search the web for the opening hours of the city library.", "search_web", ("query",)),
    ("Look up how tall the Eiffel Tower is online.", "search_web", ("query",)),
    ("Open example.org for me.", "open_url", ("url",)),
    ("Can I trust apnews.com?", "get_source_trust", ("domain",)),
    ("Give me the deep dive on https://example.org/story", "get_article_deep_dive", ("url",)),
    ("What do my notes say about the garden project?", "search_wiki", ("query",)),
    ("Find my wiki page on tax deadlines.", "search_wiki", ("query",)),
    ("Take me to the news workspace.", "navigate_workspace", ("workspace",)),
    ("Switch to settings.", "navigate_workspace", ("workspace",)),
    ("Ask my local model what my sister's birthday is.", "ask_local_for_context", ("question",)),
    ("Show me the email from the plumber.", "navigate_to", ("kind",)),
    ("Open the conversation about the move.", "navigate_to", ("kind",)),
    ("How's the system doing right now?", "check_situation", ()),
    ("Hide the chat tray.", "set_chat_tray", ()),
    ("Put the chat on the left side.", "set_chat_tray", ()),
    ("Show my day.", "show_my_day", ()),
    ("Make the chat full screen in this workspace.", "set_workspace_layout", ("fullscreen_chat",)),
    ("Archive all the newsletters in my inbox.", "organize_email", ("action",)),
    ("Label the invoices from this week as finance.", "organize_email", ("action",)),
    ("Move the scanned receipts into the taxes folder.", "organize_files", ("action",)),
    ("Rename the file draft one to final.", "organize_files", ("action",)),
    ("Tag my recipe pages with cooking.", "organize_wiki", ("action",)),
    ("Undo that last change.", "undo_action", ()),
    ("Yes, go ahead with card c-42.", "answer_card", ("card_id", "decision")),
    ("No, cancel card c-17.", "answer_card", ("card_id", "decision")),
    ("Run the morning workflow.", "run_workflow", ("name",)),
    ("Is the weekly cleanup workflow still running?", "workflow_status", ()),
    ("What did we say last week about the dentist?", "search_past_conversations", ("query",)),
    ("Find our conversation about the car insurance.", "search_past_conversations", ("query",)),
    ("Yes, share request r-9 is fine.", "answer_share_request", ("request_id", "decision")),
    ("Change request r-9 so it leaves out my address.", "revise_share_request",
     ("request_id", "instruction")),
    ("Start a background task called price-watch that checks flight prices daily.",
     "spawn_task", ("name", "prompt")),
    ("Ask Friday's deeper mind what I decided about the kitchen quote.", "ask_friday", ("question",)),
    ("Remember that I care most about the budget right now and want short answers.",
     "note_conversation_state", ()),
    ("Search the news for interest rates.", "search_news", ()),
    ("Any unread mail?", "check_email", ()),
    ("What's next on my schedule?", "query_calendar", ()),
    ("Pull up today's top stories.", "search_news", ()),
    # Local models, workspaces and codebase chats.
    ("What's the biggest model I could run on this computer?", "local_models_advise",
     ("question",)),
    ("Improve the chore wheel workspace.", "improve_workspace", ("workspace",)),
    ("I'm happy with the new layout, swap it in.", "workspace_swap", ()),
    ("Use Opus for this codebase chat.", "codebase_seat", ("which", "model")),
    ("Use my key for this codebase.", "codebase_key", ("profile",)),
    ("How much has this codebase chat cost so far?", "codebase_costs", ()),
    ("Let Claude's agent edit this codebase.", "codebase_engine", ("engine",)),
    ("Have the agent add a search box to the page.", "codebase_agent", ("task",)),
    ("Run the tests in this codebase.", "codebase_run", ("command",)),
    ("Open my garden planner project.", "open_project", ("project",)),
    ("Show me the preview.", "show_preview", ()),
    ("Go into build mode.", "build_mode", ()),
    # Distractors: nothing to call.
    ("Thanks, that's all.", NOTHING, ()),
    ("Ha, that's funny.", NOTHING, ()),
    ("Good morning!", NOTHING, ()),
    ("Can you say that again more slowly?", NOTHING, ()),
    ("What's two plus two?", NOTHING, ()),
    ("I'm just thinking out loud here.", NOTHING, ()),
    ("Okay.", NOTHING, ()),
    ("Never mind.", NOTHING, ()),
    ("You sound great today.", NOTHING, ()),
    ("Hold on a second.", NOTHING, ()),
    # Heavy work: hand it to the background agent.
    ("Draft a two-page report comparing three home insurers and save it to my documents.",
     DELEGATE, ("request",)),
    ("Go through my last month of receipts and build a spreadsheet of spending by category.",
     DELEGATE, ("request",)),
    ("Research the best routes for a week-long cycling trip and write up a plan.",
     DELEGATE, ("request",)),
    ("Clean up my downloads folder, sort everything into sensible folders and report back.",
     DELEGATE, ("request",)),
    ("Read all my notes on the house project and write a summary page in the wiki.",
     DELEGATE, ("request",)),
]

SYSTEM = ("You are Agent Friday in a live voice conversation. Use a tool when the "
          "user asks for something a tool does; for long multi-step work call "
          "delegate_to_friday; for chit-chat call nothing and just reply.")


def score_case(case, response) -> dict:
    utt, want, required = case
    msg = ((response.get("choices") or [{}])[0].get("message") or {})
    calls = msg.get("tool_calls") or []
    got = calls[0]["function"]["name"] if calls else None
    args = {}
    if calls:
        try:
            args = json.loads(calls[0]["function"].get("arguments") or "{}")
        except ValueError:
            args = None
    ok = got == want and (want is None or (isinstance(args, dict)
                                           and all(r in args for r in required)))
    return {"utterance": utt, "want": want, "got": got, "ok": bool(ok)}


def run(base, model, contract, cases=CASES) -> dict:
    results = []
    t0 = time.time()
    for case in cases:
        body = {"model": model, "temperature": 0, "max_tokens": 160,
                "messages": [{"role": "system", "content": SYSTEM},
                             {"role": "user", "content": case[0]}],
                "tools": contract["tools"],
                "chat_template_kwargs": {"enable_thinking": False}}
        req = urllib.request.Request(base.rstrip("/") + "/v1/chat/completions",
                                     data=json.dumps(body).encode(),
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=120) as r:
            results.append(score_case(case, json.loads(r.read().decode())))
    n_ok = sum(r["ok"] for r in results)
    return {"model": model, "cases": len(results), "passed": n_ok,
            "score": round(n_ok / max(1, len(results)), 4),
            "seconds": round(time.time() - t0, 1), "results": results}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", required=True)
    ap.add_argument("--model", required=True)
    ap.add_argument("--out", required=True)
    a = ap.parse_args(argv)
    from agent_friday.services.voice_engine import build_voice_tool_contract
    out = run(a.base, a.model, build_voice_tool_contract())
    with open(a.out, "w", encoding="utf-8") as f:
        json.dump(out, f, indent=2)
    print(f"{a.model}: {out['passed']}/{out['cases']} = {out['score']:.1%}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

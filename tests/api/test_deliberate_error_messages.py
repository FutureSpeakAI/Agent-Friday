"""The two places that pass an error's words on on purpose pass only the words.

The Grants screen shows the governance gate's own explanation of why a tool is
outward, and the Start-My-Day prompt tells the model which live source failed,
so it can say so. Both go through user_errors.message_only: one line, no
traceback, no file paths, bounded length. docs/security/codeql-dismissals.md
records why these two are shown rather than replaced by an error id.
"""
from __future__ import annotations

from agent_friday.user_errors import message_only

LEAKY = ('boom at C:\\Users\\someone\\AppData\\Local\\x.py line 3\n'
         'Traceback (most recent call last):\n'
         '  File "/home/someone/app/y.py", line 9, in f')


def test_message_only_keeps_the_words_and_drops_the_rest():
    out = message_only(RuntimeError(LEAKY))
    assert out.startswith("boom at <path>")
    assert "Traceback" not in out and "someone" not in out and "\n" not in out
    assert message_only("x" * 500).endswith("…") and len(message_only("x" * 500)) == 200
    assert message_only("") == "unknown error"


def test_grants_screen_shows_the_gates_reason_without_paths(client, monkeypatch):
    from agent_friday.governance import action_gate

    def fake_classify(name, args):
        return action_gate.OUTWARD, "the office command could not be classified (" + LEAKY + ")"
    monkeypatch.setattr(action_gate, "classify", fake_classify)
    r = client.get("/api/governance/outward-tools")
    assert r.status_code == 200
    whys = [t["why"] for t in r.get_json()["tools"]]
    assert whys and all(w.startswith("the office command could not be classified") for w in whys)
    body = r.get_data(as_text=True)
    assert "Traceback" not in body and "someone" not in body


def test_start_my_day_names_the_failure_without_paths(client, monkeypatch):
    from agent_friday.routes import voice_context

    def boom():
        raise RuntimeError(LEAKY)
    monkeypatch.setattr(voice_context, "_gather_live_briefing_context", boom)
    r = client.get("/api/voice/start-my-day")
    assert r.status_code == 200
    prompt = r.get_json()["prompt"]
    assert "(could not load all live data: boom at <path>" in prompt
    assert "Traceback" not in prompt and "someone" not in prompt

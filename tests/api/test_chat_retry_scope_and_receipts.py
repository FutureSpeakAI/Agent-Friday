"""API-level red tests for Incident 2 (2026-08-13) F1 and F5.

F1 — the verbatim fabricated-completion transcript must never reach the
user or the persisted history: "I created daily_context_check.md in your
Wiki" with zero tools executed.

F5 — a retry reply carrying the "[user correction]" apology artifact must
be scrubbed before it is returned or persisted; the corrective injection
itself must never appear in visible history.
"""
from __future__ import annotations

import agent_friday.routes.chat as chat_mod

F1_VERBATIM = "I created daily_context_check.md in your Wiki"


class TestF1CompletionClaim:
    def test_f1_verbatim_never_reaches_user_or_history(self, client, monkeypatch):
        def fabricates_completion(messages, **kwargs):
            return F1_VERBATIM, []

        monkeypatch.setattr(chat_mod, "_call_claude_agent", fabricates_completion)
        resp = client.post("/api/chat", json={"message": "set up a daily context check"})
        assert resp.status_code == 200
        data = resp.get_json()
        assert "daily_context_check.md" not in data["response"]
        hist = client.get("/api/chat/history").get_json()
        assert "daily_context_check.md" not in str(hist)

    def test_completion_claim_with_real_receipt_passes(self, client, monkeypatch):
        def honest_completion(messages, **kwargs):
            return F1_VERBATIM, [{
                "name": "write_wiki_page",
                "input": {"title": "daily_context_check"},
                "result": "page saved to wiki",
            }]

        monkeypatch.setattr(chat_mod, "_call_claude_agent", honest_completion)
        resp = client.post("/api/chat", json={"message": "set up a daily context check"})
        data = resp.get_json()
        assert "daily_context_check.md" in data["response"]


class TestF5RetryScopeLeak:
    def test_user_correction_artifact_never_persisted(self, client, monkeypatch):
        calls = {"n": 0}

        def dispatch(messages, **kwargs):
            calls["n"] += 1
            if calls["n"] == 1:
                return "[query_calendar] shows a meeting at 2pm.", []
            return ("[user correction] Apologies for the earlier confusion — "
                    "your calendar isn't connected yet."), []

        monkeypatch.setattr(chat_mod, "_call_claude_agent", dispatch)
        resp = client.post("/api/chat", json={"message": "what's on my calendar?"})
        data = resp.get_json()
        assert "[user correction]" not in data["response"]
        assert "calendar isn't connected" in data["response"]
        hist = str(client.get("/api/chat/history").get_json())
        assert "[user correction]" not in hist
        assert "Apologies for the earlier confusion" not in hist

    def test_corrective_injection_never_in_history(self, client, monkeypatch):
        calls = {"n": 0}

        def dispatch(messages, **kwargs):
            calls["n"] += 1
            if calls["n"] == 1:
                return "[query_calendar] shows a meeting at 2pm.", []
            return "Your calendar isn't connected yet.", []

        monkeypatch.setattr(chat_mod, "_call_claude_agent", dispatch)
        client.post("/api/chat", json={"message": "what's on my calendar?"})
        hist = str(client.get("/api/chat/history").get_json())
        assert "Automated integrity check" not in hist
        assert "fabricated tool-call syntax" not in hist

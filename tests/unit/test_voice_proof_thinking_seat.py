"""A seat that spends the proof's budget thinking is alive, not absent.

The mind proof asks for eight tokens. A thinking-first seat spends them
inside its own reasoning and returns `content: ''` with
`finish_reason: 'length'` — so the proof refused it, the manifest's mind stage
stayed unproven, and /ws/live told the owner "Friday's local model is not
running, so memory and the knowledge graph are out of reach" about a seat that
answered 17x23 in two seconds.

Measured on the live seat, 2026-09-30: bonsai2:27b at max_tokens 8 with
thinking on returns content '' / finish 'length'; the same call with
enable_thinking False returns 'OK' / finish 'stop'.
`needs_thinking_disabled("bonsai2:27b")` is False, so the existing guard —
which lists the families that must never think on a tool turn — does not
cover it.

The proof now retries once with thinking off and room to finish. Real turns
are untouched: this is the proof adapting to the seat, not a change to how
the seat is used.
"""
import json
import pytest

vm = pytest.importorskip("agent_friday.services.voice_manifest")


def _resp(content="", finish="stop", tool_calls=None):
    msg = {"content": content}
    if tool_calls:
        msg["tool_calls"] = tool_calls
    return {"choices": [{"message": msg, "finish_reason": finish}],
            "timings": {"prompt_n": 11}, "usage": {}}


@pytest.fixture
def seat(monkeypatch):
    """Capture every request body the proof sends, and script the replies."""
    sent = []
    plan = {"replies": []}

    class _R:
        def __init__(self, payload):
            self._p = json.dumps(payload).encode()

        def read(self):
            return self._p

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def fake_urlopen(req, timeout=None):
        sent.append(json.loads(req.data.decode()))
        return _R(plan["replies"].pop(0) if plan["replies"] else _resp("OK"))

    import urllib.request
    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setattr(vm, "compute_contract",
                        lambda s: {"_system": "sys", "fits": True, "tools": [],
                                   "window": 131072})
    # The seat and its base come from the services, not from the selection arg.
    from agent_friday.services import local_seats, tool_budget
    plan["seat"] = "bonsai2:27b"
    monkeypatch.setattr(local_seats, "resolve", lambda which: plan["seat"])
    monkeypatch.setattr(tool_budget, "_seat_base",
                        lambda s: "http://127.0.0.1:8090")
    return sent, plan


def _run(plan=None, seat_name="bonsai2:27b"):
    if plan is not None:
        plan["seat"] = seat_name
    return vm.ENGINE_RUNNERS["mind"]({}, lambda *a, **k: None)


# ── The bug ────────────────────────────────────────────────────────────────

def test_a_thinking_seat_is_proven_after_one_retry(seat):
    sent, plan = seat
    plan["replies"] = [_resp("", "length"), _resp("OK", "stop")]
    out = _run()
    assert out["content"] == "OK", "the seat answered; the proof must accept it"
    assert len(sent) == 2, "exactly one retry"
    assert sent[1]["chat_template_kwargs"] == {"enable_thinking": False}
    assert sent[1]["max_tokens"] > sent[0]["max_tokens"], (
        "the retry needs room to finish, or it fails the same way")


def test_the_first_ask_is_unchanged(seat):
    """The retry is a fallback, not a new default: a seat that answers plainly
    is still asked exactly as before."""
    sent, plan = seat
    plan["replies"] = [_resp("OK", "stop")]
    _run()
    assert len(sent) == 1, "no retry when the first ask answered"
    assert "chat_template_kwargs" not in sent[0]
    assert sent[0]["max_tokens"] == 8


# ── What must not change ───────────────────────────────────────────────────

def test_a_seat_that_answers_nothing_twice_is_still_refused(seat):
    """A genuinely dead seat must still refuse, or the proof proves nothing."""
    sent, plan = seat
    plan["replies"] = [_resp("", "length"), _resp("", "length")]
    with pytest.raises(vm.ProofRefused) as e:
        _run()
    assert "no completion" in str(e.value.message if hasattr(e.value, "message")
                                  else e.value)
    assert len(sent) == 2, "it tried the retry before refusing"


def test_a_stop_with_empty_content_is_not_retried(seat):
    """finish_reason 'stop' with nothing in it is not a budget problem; it is
    a seat with nothing to say, and retrying would only hide that."""
    sent, plan = seat
    plan["replies"] = [_resp("", "stop")]
    with pytest.raises(vm.ProofRefused):
        _run()
    assert len(sent) == 1, "no retry for an empty stop"


def test_a_tool_call_still_counts_as_an_answer(seat):
    sent, plan = seat
    plan["replies"] = [_resp("", "tool_calls",
                             tool_calls=[{"id": "1", "function": {"name": "x"}}])]
    out = _run()
    assert out["seat"] == "bonsai2:27b"
    assert len(sent) == 1


def test_a_family_the_guard_covers_still_asks_with_thinking_off_first(seat,
                                                                     monkeypatch):
    """gemma4 must not need the retry at all: the guard puts thinking off on
    the FIRST ask, which is what keeps its real turns working too."""
    sent, plan = seat
    from agent_friday.services import channel_toolcalls as ct
    monkeypatch.setattr(ct, "needs_thinking_disabled", lambda m: "gemma4" in m)
    plan["replies"] = [_resp("OK", "stop")]
    _run(plan, "gemma4:26b")
    assert sent[0]["chat_template_kwargs"] == {"enable_thinking": False}
    assert len(sent) == 1


def test_the_retry_keeps_the_contract_and_tools(seat):
    """The retry must prove the same thing the first ask would have."""
    sent, plan = seat
    plan["replies"] = [_resp("", "length"), _resp("OK", "stop")]
    _run()
    assert sent[1]["model"] == sent[0]["model"]
    assert sent[1]["messages"] == sent[0]["messages"]

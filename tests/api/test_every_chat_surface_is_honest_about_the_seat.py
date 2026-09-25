"""Whatever answers, the user is told -- on every chat surface.

A user who picks a cloud model (e.g. Opus 5.5) and gets a reply from a local
one (e.g. Bonsai2) must be told, on every chat surface, and the reply must
render as markdown rather than literal asterisks.

`index.html` has two chat surfaces:

* the main chat posts to `/api/chat/stream`, which runs `chat()`;
* `ConversationWindow` -- the "new window" -- posts to `/api/chat/send`.

`chat()` carries a seat-divergence notice. `chat_send()` is a second, complete
dispatch path and must carry the same notice, or a substitution there is
silent. `ConversationWindow` must render replies through the shared markdown
renderer, or every `**bold**` arrives as literal asterisks.

Why a local model can answer a cloud pick:

    route(msgs, task_context={'has_tools': True})
      -> local/bonsai2:27b, "local_preferred/smart - kept on a local seat"

With `model_routing.mode` = `local_preferred`, that branch returns a local seat
whenever one is up, consulting `capability_routing.reasoning` only through
`if want in names` -- a set of LOCAL model names, which a cloud pick can never
join.

So the notice must not say the chosen model "is not installed on this machine",
which of a cloud model is false and points the user at installing it. The
reason is the routing mode, and that is what a user can act on.
"""

import json
import pathlib

import pytest

import agent_friday.routes.chat as chat_mod


REPO = pathlib.Path(__file__).resolve().parents[2]


# ─────────────────────────────────────────────────────────────────────────────
# The wording. One helper builds every seat notice, so there is one place for
# "say the true reason" to be right or wrong.
# ─────────────────────────────────────────────────────────────────────────────

def test_a_cloud_pick_kept_local_by_the_mode_blames_the_mode():
    text = chat_mod._seat_divergence_text(
        "claude-opus-5-5", "bonsai2:27b", routing_mode="local_preferred")

    assert text, "a cloud pick answered by a local model is a divergence"
    assert "claude-opus-5-5" in text and "bonsai2:27b" in text
    assert "not installed" not in text, (
        "Opus 5.5 is a cloud model; telling the user to install it is false "
        "and unactionable")
    low = text.lower()
    assert "local preferred" in low or "routing mode" in low, (
        "the notice must name the setting that caused this: %r" % text)


def test_a_local_pick_that_is_absent_still_says_so():
    """The original case this notice was written for, kept working."""
    text = chat_mod._seat_divergence_text(
        "gemma4:12b", "bonsai2:27b", routing_mode="local_preferred")

    assert text and "not installed" in text
    assert "gemma4:12b" in text and "bonsai2:27b" in text


def test_the_computer_control_override_keeps_its_own_words():
    text = chat_mod._seat_divergence_text(
        "bonsai2:27b", "claude-sonnet-5", routing_mode="local_preferred",
        cc_override=True, overridden_local_model="bonsai2:27b")

    assert text and "Computer Control" in text


def test_no_notice_when_the_chosen_model_is_the_one_that_answered():
    assert chat_mod._seat_divergence_text(
        "claude-opus-5-5", "claude-opus-5-5",
        routing_mode="cloud_only") is None


def test_a_cloud_pick_answered_locally_under_a_cloud_mode_does_not_blame_the_mode():
    """With mode `cloud_only`, saying "routing mode is 'Cloud only', so this
    turn stayed on this machine" is self-contradictory. Under a mode that does not prefer
    local, the cause is unknown from here -- say what happened, not why."""
    text = chat_mod._seat_divergence_text(
        "claude-sonnet-5", "bonsai2:27b", routing_mode="cloud_only")

    assert text
    assert "not installed" not in text
    low = text.lower()
    assert "so this turn stayed on this machine" not in low, (
        "blamed a mode that does not prefer local: %r" % text)
    assert "bonsai2:27b" in text and "claude-sonnet-5" in text


def test_no_notice_when_nothing_was_chosen():
    """An unset seat is the absence of a choice, not a choice that was ignored."""
    assert chat_mod._seat_divergence_text(
        None, "bonsai2:27b", routing_mode="local_preferred") is None


# ─────────────────────────────────────────────────────────────────────────────
# The surface that was silent.
# ─────────────────────────────────────────────────────────────────────────────

@pytest.fixture
def seated_opus(monkeypatch):
    """Opus 5.5 in the reasoning seat, local_preferred routing.

    Patched on `chat` itself, not on `core`: chat.py does
    `from agent_friday.core import _load_settings` at import time, so it holds
    its own reference and patching the core module leaves the route reading the
    real settings file.
    """
    real = chat_mod._load_settings

    def _patched(*a, **k):
        s = dict(real(*a, **k) or {})
        s["capability_routing"] = dict(s.get("capability_routing") or {})
        s["capability_routing"]["reasoning"] = {
            "model": "claude-opus-5-5", "provider": "anthropic"}
        s["model_routing"] = dict(s.get("model_routing") or {})
        s["model_routing"]["mode"] = "local_preferred"
        return s

    monkeypatch.setattr(chat_mod, "_load_settings", _patched)
    return _patched


@pytest.fixture
def answered_by_bonsai(monkeypatch):
    """Make the ACTUAL generator local."""
    from agent_friday.services import attribution
    monkeypatch.setattr(
        attribution, "last_generation",
        lambda *a, **k: {"model": "bonsai2:27b", "seat": "local"},
        raising=False)
    monkeypatch.setattr(attribution, "fallback_chain", lambda *a, **k: [],
                        raising=False)


def test_chat_send_announces_the_substitution(client, seated_opus,
                                              answered_by_bonsai, monkeypatch):
    """`/api/chat/send` -- the new-window surface -- must say what answered.

    Without a divergence check this turn comes back with a bonsai2 reply, a
    truthful `model` field, and no word to the user about the seat they chose.
    """
    monkeypatch.setattr(chat_mod, "_SEAT_NOTICES_SENT", set(), raising=False)

    resp = client.post("/api/chat/send",
                       json={"message": "What do you think of the harness?"})
    assert resp.status_code == 200
    body = resp.get_json()

    notice = body.get("seat_notice")
    assert notice, (
        "the endpoint answered with a different model than the chosen seat and "
        "said nothing; response keys were %s" % sorted(body))
    assert "claude-opus-5-5" in notice and "bonsai2:27b" in notice
    assert "not installed" not in notice


def test_the_reply_still_reports_the_model_that_actually_answered(
        client, seated_opus, answered_by_bonsai, monkeypatch):
    """The label was already honest and must stay honest: the fix adds an
    explanation, it does not relabel the reply as Opus 5.5."""
    monkeypatch.setattr(chat_mod, "_SEAT_NOTICES_SENT", set(), raising=False)

    body = client.post("/api/chat/send", json={"message": "hi"}).get_json()
    assert body["friday_msg"].get("model") == "bonsai2:27b"
    assert body["friday_msg"].get("seat") == "local"


# ─────────────────────────────────────────────────────────────────────────────
# The asterisks.
# ─────────────────────────────────────────────────────────────────────────────

def _conversation_window_source():
    """The body of `ConversationWindow` from the served UI.

    `index.html` is the authoritative UI (`ui_parts/app.html` is a mirror and
    does not contain this component at all), so it is what gets asserted.
    """
    src = (REPO / "index.html").read_text(encoding="utf-8", errors="replace")
    def body(name):
        start = src.index("function " + name + "(")
        # The next top-level declaration ends the component.
        end = src.index("\nfunction ", start + 10)
        return src[start:end]

    win = body("ConversationWindow")
    # The window draws its transcript through ChatSurface, the component the
    # docked chat uses, so what it renders is ChatSurface's code.
    if "React.createElement(ChatSurface," in win:
        win += body("ChatSurface")
    return win


def test_the_new_window_renders_markdown_like_every_other_surface():
    """Friday replies are markdown. A surface that prints them raw shows the
    user `**like this**`."""
    body = _conversation_window_source()
    assert "FridayDoc" in body or "renderFridayMarkdown" in body, (
        "ConversationWindow renders reply text without the shared markdown "
        "renderer, so bold and lists arrive as punctuation")


def test_the_new_window_no_longer_prints_reply_text_raw():
    """Pin the construct that prints markdown raw: the bubble's only child
    being the unrendered string."""
    body = _conversation_window_source()
    assert "}, m.text)))" not in body, (
        "the message bubble still renders m.text raw")


def test_user_messages_are_still_shown_verbatim():
    """A user's own text is not markdown and must not be reinterpreted --
    asterisks they typed stay asterisks."""
    body = _conversation_window_source()
    assert "m.role === 'user'" in body, (
        "the renderer no longer distinguishes the user's own text")

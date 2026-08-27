"""Layer 4 of the sensitivity classifier: the seat it adjudicates with.

Layer 4 spent its entire life returning "no opinion" because it POSTed a
hardcoded `gemma4:latest` to a daemon that has never had that tag. The output
of a broken layer and the output of an unopinionated one were the same value,
so nothing ever surfaced it.

These tests pin the parts of the fix that would fail silently again.
"""
import logging

import pytest

from agent_friday.services import sensitivity_classifier as sc


class _Mgr:
    def __init__(self, names):
        self._names = names

    def list_models(self):
        return [{"name": n} for n in self._names]


@pytest.fixture
def daemon(monkeypatch):
    """Control what the OLLAMA daemon reports, independent of local_seats."""
    def _set(ollama_names, merged_rows=None):
        from agent_friday.routing import ollama_manager
        from agent_friday.services import local_seats
        monkeypatch.setattr(ollama_manager, "get_manager",
                            lambda: _Mgr(ollama_names))
        if merged_rows is None:
            merged_rows = [(n, 6.0) for n in ollama_names]
        monkeypatch.setattr(local_seats, "installed", lambda *a, **k: merged_rows)
        monkeypatch.setattr(local_seats, "resolve", lambda role, cfg=None: None)
    return _set


def test_the_seat_is_never_one_the_daemon_cannot_serve(daemon, monkeypatch):
    """THE REGRESSION THIS FILE EXISTS FOR.

    `local_seats.installed()` deliberately MERGES two registries: Ollama's tags
    and Friday's own llama-server runtime store. Layer 4 talks only to Ollama.
    The first version of this fix resolved against the merged view, got
    `gemma4:12b` — a real model, served by llama-server, absent from Ollama —
    and the daemon answered 404 in 0.0s, which the layer reported as "no
    opinion". Swapping a hardcoded name for a resolved one fixed nothing,
    because the registry consulted was not the registry that serves the call.
    """
    from agent_friday.services import local_seats
    # Ollama has one model; the merged view also contains a llama-server seat.
    daemon(["qwen3:8b"],
           merged_rows=[("qwen3:8b", 5.2), ("gemma4:12b", 7.4)])
    # ...and the configured preference names the llama-server one.
    monkeypatch.setattr(local_seats, "resolve", lambda role, cfg=None: "gemma4:12b")

    seat = sc._llm_seat()
    assert seat == "qwen3:8b", f"picked {seat!r}, which Ollama cannot serve"


def test_a_cloud_seat_is_never_handed_to_the_local_layer(daemon, monkeypatch):
    """The `judge` role resolves via `reasoning`, which is often a CLOUD model.

    On the reference machine it resolved to 'claude-sonnet-5'. This layer's
    contract is that content never leaves the machine; POSTing that name to
    localhost merely 404s, so the guarantee held BY ACCIDENT. Hold it on
    purpose.
    """
    from agent_friday.services import local_seats
    daemon(["qwen3:8b"])
    monkeypatch.setattr(local_seats, "resolve",
                        lambda role, cfg=None: "claude-sonnet-5")
    assert sc._llm_seat() == "qwen3:8b"


def test_no_seat_when_the_daemon_lists_nothing(daemon):
    daemon([])
    assert sc._llm_seat() is None


def test_a_toy_model_is_not_an_adjudicator(daemon):
    """functiongemma:270m is a 270M function-caller, not a judge."""
    daemon(["functiongemma:270m"],
           merged_rows=[("functiongemma:270m", 0.3)])
    assert sc._llm_seat() is None


def test_missing_seat_is_logged_rather_than_silently_returning_no_opinion(
        daemon, caplog):
    """0 means "no opinion" AND "could not ask". Those must not look identical."""
    daemon([])
    with caplog.at_level(logging.INFO, logger="friday.privacy.classifier"):
        assert sc._local_llm_tier("anything at all") == 0
    assert any("Layer 4" in r.message or "Layer 4" in r.getMessage()
               for r in caplog.records), "a skipped layer must say so"


# ── the request the daemon actually receives ────────────────────────────────

class _Resp:
    def __init__(self, payload, ok=True, status=200):
        self._p, self.ok, self.status_code = payload, ok, status

    def json(self):
        return {"response": self._p}

    @property
    def text(self):
        return str(self._p)


@pytest.fixture
def capture_post(monkeypatch, daemon):
    daemon(["qwen3:8b"])
    sent = {}

    def fake_post(url, json=None, timeout=None, **kw):
        sent.update({"url": url, "body": json, "timeout": timeout})
        return _Resp(sent.get("_reply", "PRIVATE"))

    import requests
    monkeypatch.setattr(requests, "post", fake_post)
    return sent


def test_thinking_is_disabled_or_the_verdict_never_arrives(capture_post):
    """MEASURED 2026-08-26: with `think` unset, Gemma4-12B-QAT spent the whole
    num_predict budget emitting `<|channel>thought ...` and never reached a
    verdict — at num_predict=64 it was still reasoning. Every current local
    seat is a thinking model, so this flag is required, not an optimisation."""
    sc._local_llm_tier("something ambiguous")
    assert capture_post["body"]["think"] is False


def test_the_call_survives_a_cold_load(capture_post):
    """A cold load measured 25.9s against ~2.7s warm. A 20s ceiling timed out
    on every first call and reported it as "no opinion"."""
    sc._local_llm_tier("something ambiguous")
    assert capture_post["timeout"] >= 30


def test_it_never_leaves_the_machine(capture_post):
    sc._local_llm_tier("something ambiguous")
    assert capture_post["url"].startswith("http://localhost:11434")


@pytest.mark.parametrize("reply,expect", [
    ("PRIVATE", sc.Tier.PRIVATE),
    ("SENSITIVE", sc.Tier.SENSITIVE),
    ("PUBLIC", sc.Tier.PUBLIC),
    # The original parser read `.split()[0]`, so a prefixed verdict was read as
    # the wrong word and an EMPTY reply raised IndexError into a blanket
    # `except` — a parse bug wearing the costume of a network failure.
    ("Classification: PRIVATE", sc.Tier.PRIVATE),
    ("  sensitive  ", sc.Tier.SENSITIVE),
    ("", 0),
    ("I am not sure", 0),
])
def test_the_verdict_is_read_from_anywhere_in_the_reply(
        monkeypatch, daemon, reply, expect):
    daemon(["qwen3:8b"])
    import requests
    monkeypatch.setattr(requests, "post",
                        lambda *a, **k: _Resp(reply))
    assert sc._local_llm_tier("x") == expect


def test_an_http_error_is_no_opinion_not_a_crash(monkeypatch, daemon):
    daemon(["qwen3:8b"])
    import requests
    monkeypatch.setattr(requests, "post",
                        lambda *a, **k: _Resp("", ok=False, status=404))
    assert sc._local_llm_tier("x") == 0


# ── what the layer can do to a decision ─────────────────────────────────────

def test_layer_four_can_only_escalate(monkeypatch):
    """It folds in through max(candidates), so it can withhold more, never less.

    That is why over-redaction is the only risk worth measuring here, and why
    the committed over-redaction guards are the ones that matter.
    """
    monkeypatch.setattr(sc, "_local_llm_tier", lambda t: sc.Tier.PUBLIC)
    text = "my kid's IEP meeting is Thursday at the school"
    without = sc.classify(text, use_llm=False)
    with_llm = sc.classify(text, use_llm=True)
    assert with_llm >= without, "Layer 4 must never lower a tier"


def test_the_known_good_guards_are_unreachable_by_layer_four(monkeypatch):
    """The committed guards pass use_embeddings=False, and Layer 4 is gated on
    emb_tier > 0 — so they cannot exercise it either way. Recorded so nobody
    reads those passing tests as evidence ABOUT Layer 4."""
    called = []
    monkeypatch.setattr(sc, "_local_llm_tier",
                        lambda t: called.append(t) or sc.Tier.SENSITIVE)
    for benign in ("CDC seasonal flu vaccination guidance for adults",
                   "Thank you for the courtesy of a quick reply"):
        sc.classify(benign, egress=True, use_presidio=False,
                    use_embeddings=False, use_llm=True)
    assert not called, "Layer 4 fired in a configuration that gates it out"

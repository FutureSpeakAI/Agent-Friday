"""A successful chat turn must say which model served it.

2026-09-22: Stephen asked Bonsai2 a question twice and Sonnet 5 answered,
insisting it was Bonsai2. Nothing lied at the routing layer - routing already
refuses rather than substitutes (chat.py's seat_missing path, "I have not
answered from a different model, because you asked for that one"). Two real
bugs stopped him selecting bonsai2 at all: the catalogue took 18.9 s so the
picker timed out, and the catalogue could not see the running seat because the
endpoints file was read with the wrong key. The turn then ran on the configured
default, correctly.

What made it UNFALSIFIABLE from his chair is that the success reply never said
who answered, so the only way to ask was to ask the model - and a model answers
that from its system prompt, which tells it that it is Agent Friday.

Every refusal path already reported the model. This pins the success path.
"""
from __future__ import annotations

import pytest


def _post(client, msg="hello"):
    return client.post("/api/chat", json={"message": msg}).get_json()


def test_a_successful_turn_reports_who_served_it(client):
    d = _post(client)
    assert d.get("response"), "no reply at all: %s" % str(d)[:200]
    assert "served_by" in d, (
        "the reply does not say which model answered; keys: %s"
        % sorted(d)[:20])
    s = d["served_by"]
    assert "model" in s and "provider" in s and "local" in s


def test_the_reported_model_is_not_empty(client):
    s = _post(client).get("served_by") or {}
    assert s.get("model"), (
        "served_by.model is empty - reporting nothing is the same failure as "
        "reporting nothing, dressed as a field")


def test_local_flag_is_a_bool_not_a_guess(client):
    s = _post(client).get("served_by") or {}
    assert isinstance(s.get("local"), bool)


def test_it_matches_what_the_router_resolved(client, monkeypatch):
    """Pin that the field follows the ROUTER, not a hardcoded string.

    Otherwise this could be satisfied by echoing the configured default back,
    which is exactly the illusion being removed.
    """
    import agent_friday.routing.model_router as rmr

    real_get = rmr.get_router

    class _Stub:
        def __init__(self, inner):
            self._inner = inner

        def route(self, *a, **k):
            info = self._inner.route(*a, **k)
            if isinstance(info, dict) and not info.get("refuse"):
                info = dict(info, model="probe-model-xyz", provider="probe")
            return info

        def __getattr__(self, n):
            return getattr(self._inner, n)

    monkeypatch.setattr(rmr, "get_router",
                        lambda *a, **k: _Stub(real_get(*a, **k)))
    s = _post(client).get("served_by") or {}
    assert s.get("model") == "probe-model-xyz", (
        "served_by did not follow the router's resolution; got %r" % s)

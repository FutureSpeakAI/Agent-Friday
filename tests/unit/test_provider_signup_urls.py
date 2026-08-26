"""Every provider that asks for a key must say where to get one.

Stephen, after setting Friday up on Janet's laptop 2026-08-26:

    "The UI must also include clickable buttons that open the sign up pages
     for various API/MCP services Friday can use."

The Providers panel asked for a key and named the environment variable it
would be stored under — `needs ANTHROPIC_API_KEY`, `Paste GEMINI_API_KEY`.
That is the author's vocabulary. It tells someone who already has a key
where to put it, and tells someone who does not have one nothing at all.
The gap between "I need a key" and "I have a key" was never part of the
product; it was assumed to have been crossed already, off-screen, because
for the author it always had been.

So a descriptor that demands an api key carries the page that issues one,
and it is applied in `normalize_descriptor` — the one gate every descriptor
passes, whether it ships with Friday, arrives as a JSON in
~/.friday/providers/, or is typed into the Add Provider form. `/api/providers`
spreads the descriptor wholesale, so the button follows the data.

These assertions run against the REGISTRY, not the source literals: what the
panel renders is what matters, and the registry is what it reads.
"""
from __future__ import annotations

import pytest

from agent_friday.routing.provider_descriptors import normalize_descriptor
from agent_friday.services.provider_registry import get_provider_registry

ROWS = get_provider_registry().list_providers()
KEYED = [p for p in ROWS if (p.get("auth") or {}).get("type") == "env_var"]
KEYLESS = [p for p in ROWS if (p.get("auth") or {}).get("type") == "none"]


def test_the_fixtures_actually_found_providers():
    """Guard the guard — an empty list would make every test below vacuous."""
    assert len(KEYED) >= 10, "expected the built-in keyed providers, got %d" % len(KEYED)
    assert KEYLESS, "expected at least one keyless (local) provider"


@pytest.mark.parametrize("prov", KEYED, ids=[p["name"] for p in KEYED])
def test_a_provider_that_wants_a_key_says_where_to_get_one(prov):
    url = prov.get("signup_url")
    assert url, (
        "%s asks for %s and offers no way to obtain it — the exact wall Janet "
        "hit" % (prov["name"], (prov.get("auth") or {}).get("key"))
    )
    assert url.startswith("https://"), "%s: signup_url must be https" % prov["name"]


@pytest.mark.parametrize("prov", KEYED, ids=[p["name"] for p in KEYED])
def test_the_signup_url_points_at_the_provider_not_a_search(prov):
    """A link is only useful if it lands on the issuer's own console."""
    url = prov["signup_url"]
    for bad in ("google.com/search", "bing.com", "duckduckgo"):
        assert bad not in url, "%s: signup_url is a search, not a console" % prov["name"]


def test_local_providers_are_not_given_a_signup_link():
    """Ollama and the in-process engines need no account. Offering one would
    be the mirror error: a button that teaches the wrong thing."""
    for p in KEYLESS:
        assert not p.get("signup_url"), (
            "%s needs no key; a signup link there is noise" % p["name"])


def test_a_hand_written_descriptor_may_supply_its_own_link():
    """A provider Friday has never heard of still gets the button, so long as
    its author fills the field in. The table is a convenience, not a gate."""
    norm = normalize_descriptor({
        "name": "some-lan-box", "label": "LAN box",
        "adapter": "openai-compatible",
        "base_url": "https://llm.example.internal/v1",
        "auth": {"type": "env_var", "key": "LAN_BOX_KEY"},
        "signup_url": "https://llm.example.internal/keys",
    })
    assert norm["signup_url"] == "https://llm.example.internal/keys"


def test_an_unknown_keyed_descriptor_without_a_link_is_left_alone():
    """No invention. An empty field is honest; a guessed URL is not."""
    norm = normalize_descriptor({
        "name": "mystery-co", "label": "Mystery",
        "adapter": "openai-compatible",
        "base_url": "https://api.mystery.example/v1",
        "auth": {"type": "env_var", "key": "MYSTERY_KEY"},
    })
    assert norm.get("signup_url", "") == ""

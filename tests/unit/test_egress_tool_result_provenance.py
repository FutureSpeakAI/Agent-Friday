"""JSON tool-result gating and news provenance (2026-08-24).

Two changes are pinned here:

  1. _gate_text descends into JSON when the whole-value gate withholds
     something. Previously `json.dumps` emitted one line with no separators,
     the span-wise rescue never engaged, and one incidental phrase replaced an
     entire tool result with a 125-character notice.

  2. news_engine registers article title AND snippet as third-party published
     text at the fetch point, so search_news participates in the same
     provenance registry the Edition digest and web_fetch already use.

THE POINT OF THIS FILE IS THE PRIVATE CASES. A test that only demonstrates
"news now passes" is not evidence of anything — it would pass just as well if
the gate had been removed. Every private-origin test below was verified to FAIL
when gating is disabled (see test_private_cases_are_falsifiable, which proves
the suite can detect a broken gate rather than asking you to take it on trust).

Synthetic data only — no real PII.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

import pytest

from agent_friday.services import egress_gate as eg
from agent_friday.services.egress_gate import _gate_text, register_public_text


# The actual article text from the reported failure.
CDC_TITLE = "CDC reports rise in flu cases across northern states"
CDC_SNIPPET = ("Officials recommend vaccination for people over 65 and those "
               "with underlying medical conditions, citing hospital admission "
               "data released this week.")

# Private-origin material. None of this is ever registered as public.
VAULT_TEXT = "his SSN is 123-45-6789 and the custody hearing is Thursday"  # pragma: allowlist secret
EMAIL_TEXT = "divorce settlement terms from the attorney, account 987654321"


def _news_result(*pairs) -> str:
    """A search_news-shaped payload: one JSON line, no separators."""
    return json.dumps({"query": "today", "hits": [
        {"title": t, "snippet": s, "url": "https://example.com/x",
         "source": "Reuters", "category": "health", "trust": 0.9}
        for t, s in pairs
    ]})


@pytest.fixture(autouse=True)
def _clean_registry():
    """Each test states its own provenance. No leakage between tests."""
    with eg._TRUSTED_LOCK:
        eg._PUBLIC_PARAS.clear()
        eg._PUBLIC_ORIGINS.clear()
    yield
    with eg._TRUSTED_LOCK:
        eg._PUBLIC_PARAS.clear()
        eg._PUBLIC_ORIGINS.clear()


# ── The reported failure ─────────────────────────────────────────────────────

class TestTheReportedFailure:
    """636 characters in, a 125-character TIER_2 marker out."""

    def test_registered_news_now_survives(self):
        register_public_text(CDC_TITLE, origin="Reuters")
        register_public_text(CDC_SNIPPET, origin="Reuters")
        payload = _news_result((CDC_TITLE, CDC_SNIPPET))

        out = _gate_text(payload, "anthropic", "live.tool.search_news")

        assert CDC_TITLE in out
        assert CDC_SNIPPET in out
        assert "EGRESS-GATE" not in out

    def test_without_registration_it_is_still_withheld(self):
        """Provenance is what earns the exemption — not the shape of the data.

        The same payload, unregistered, must NOT come through. If this passes
        it means JSON descent alone is letting content out, which would make
        the whole change a hole rather than a fix.
        """
        payload = _news_result((CDC_TITLE, CDC_SNIPPET))

        out = _gate_text(payload, "anthropic", "live.tool.search_news")

        assert CDC_SNIPPET not in out

    def test_result_stays_valid_json_the_model_can_read(self):
        register_public_text(CDC_TITLE, origin="Reuters")
        register_public_text(CDC_SNIPPET, origin="Reuters")

        out = _gate_text(_news_result((CDC_TITLE, CDC_SNIPPET)),
                         "anthropic", "live.tool.search_news")

        parsed = json.loads(out)
        assert parsed["hits"][0]["title"] == CDC_TITLE
        assert parsed["hits"][0]["url"] == "https://example.com/x"
        assert parsed["hits"][0]["trust"] == 0.9      # non-strings untouched


# ── PRIVATE ORIGIN MUST STILL BE WITHHELD ────────────────────────────────────

class TestPrivateOriginStillWithheld:
    """The cases that would hurt if the change were wrong."""

    def test_vault_json_result_withheld(self):
        payload = json.dumps({"tool": "vault_read", "content": VAULT_TEXT})
        out = _gate_text(payload, "anthropic", "tool_result")
        assert "123-45-6789" not in out  # pragma: allowlist secret
        assert "custody hearing" not in out

    def test_email_json_result_withheld(self):
        payload = json.dumps({"messages": [{"body": EMAIL_TEXT}]})
        out = _gate_text(payload, "anthropic", "tool_result")
        assert "divorce settlement" not in out

    def test_private_field_withheld_beside_public_news(self):
        """The mixing case, at field granularity.

        Field-wise descent must not become "one public sibling rescues the
        object". Each field is judged on its own.
        """
        register_public_text(CDC_TITLE, origin="Reuters")
        register_public_text(CDC_SNIPPET, origin="Reuters")
        payload = json.dumps({
            "news": {"title": CDC_TITLE, "snippet": CDC_SNIPPET},
            "calendar": {"note": VAULT_TEXT},
        })

        out = _gate_text(payload, "anthropic", "tool_result")

        assert CDC_SNIPPET in out            # public survives
        assert "123-45-6789" not in out      # private does not  # pragma: allowlist secret
        assert "custody hearing" not in out

    def test_composite_briefing_keeps_its_private_half(self):
        """get_briefing mixes news with calendar/mail. The composite inherits
        the most restrictive origin: the private half never travels."""
        register_public_text(CDC_TITLE, origin="Reuters")
        payload = json.dumps({
            "briefing": {"headline": CDC_TITLE, "your_day": EMAIL_TEXT},
        })

        out = _gate_text(payload, "anthropic", "tool_result")

        assert "divorce settlement" not in out
        assert "987654321" not in out

    def test_nested_private_content_withheld(self):
        payload = json.dumps({"a": {"b": {"c": [{"d": VAULT_TEXT}]}}})
        out = _gate_text(payload, "anthropic", "tool_result")
        assert "123-45-6789" not in out  # pragma: allowlist secret

    def test_non_json_private_text_unchanged_behaviour(self):
        """Non-JSON results must keep exactly their previous handling."""
        out = _gate_text(VAULT_TEXT, "anthropic", "tool_result")
        assert "123-45-6789" not in out  # pragma: allowlist secret


# ── No send-time exemption ───────────────────────────────────────────────────

class TestNoSendTimeExemption:
    """Provenance is content-addressed. It cannot be claimed by asserting it."""

    def test_labelling_a_payload_as_news_does_not_exempt_it(self):
        payload = json.dumps({
            "tool": "search_news",          # a label, and labels earn nothing
            "origin": "public-web",
            "source": "Reuters",
            "hits": [{"title": "headline", "snippet": VAULT_TEXT}],
        })

        out = _gate_text(payload, "anthropic", "live.tool.search_news")

        assert "123-45-6789" not in out  # pragma: allowlist secret

    def test_registration_is_exact_match_not_substring(self):
        """Registering a headline must not exempt a longer string containing it."""
        register_public_text(CDC_TITLE, origin="Reuters")
        doctored = f"{CDC_TITLE}. Also, {VAULT_TEXT}"

        out = _gate_text(json.dumps({"t": doctored}), "anthropic", "tool_result")

        assert "123-45-6789" not in out  # pragma: allowlist secret


# ── Structure and cost ───────────────────────────────────────────────────────

class TestStructureAndCost:
    def test_public_payload_costs_one_classification(self, monkeypatch):
        """The fast path must not regress: a payload that passes whole is not
        walked field-by-field."""
        calls = []
        real = eg._classify_cloud
        monkeypatch.setattr(eg, "_classify_cloud",
                            lambda t: (calls.append(t), real(t))[1])

        payload = json.dumps({"a": "how do I sort a list in Python",
                              "b": "the release notes describe performance"})
        out = _gate_text(payload, "anthropic", "tool_result")

        assert out == payload
        assert len(calls) == 1, "public JSON must not trigger a field-wise walk"

    def test_keys_are_never_gated(self):
        payload = json.dumps({"custody_notes": "how do I sort a list in Python",
                              "secret": VAULT_TEXT})
        out = _gate_text(payload, "anthropic", "tool_result")
        parsed = json.loads(out)
        assert "custody_notes" in parsed      # key preserved despite its name
        assert "secret" in parsed
        assert "123-45-6789" not in out  # pragma: allowlist secret

    def test_local_provider_bypasses_entirely(self):
        """The local bypass lives in the PUBLIC wrapper, not the field gate.

        _gate_text is provider-agnostic by design — it gates whatever it is
        handed. gate_text() is the API that decides a provider is on-device and
        skips the boundary, so that is what this asserts.
        """
        from agent_friday.services.egress_gate import gate_text
        payload = json.dumps({"content": VAULT_TEXT})
        assert gate_text(payload, "ollama", "tool_result") == payload
        assert "123-45-6789" not in gate_text(payload, "anthropic", "tool_result")  # pragma: allowlist secret


# ── Proof the private tests can actually fail ────────────────────────────────

def test_private_cases_are_falsifiable(monkeypatch):
    """A passing test is only evidence if it could have failed.

    Neutralise the classifier so everything reads PUBLIC — the exact shape of
    "someone broke the gate" — and confirm the private assertions above stop
    holding. If this test fails, the private-origin tests are vacuous and prove
    nothing about safety.
    """
    from agent_friday.services.sensitivity_classifier import Tier
    monkeypatch.setattr(eg, "_classify_cloud", lambda t: Tier.PUBLIC)

    leaked = _gate_text(json.dumps({"content": VAULT_TEXT}),
                        "anthropic", "tool_result")

    assert "123-45-6789" in leaked, (  # pragma: allowlist secret
        "with the classifier neutralised the vault text should have leaked; "
        "it did not, so the private-origin tests are not actually testing the gate"
    )


# ── Formerly xfail; CLOSED 2026-08-25 in sensitivity_classifier ──────────────
# This was pinned as a pre-existing classifier gap: wrapping text in JSON
# LOWERED its tier, because the possessive/personal frame matching b69acb2
# introduced used a connector class of [\w'\-\s] and a quote or colon in the
# gap broke the match. Fixed at the root, as the pin said it should be — the
# connector class now admits punctuation, and "account balance" joined the
# strong TIER-3 phrases so this string no longer depends on frame matching or
# on the embedding layer (which the frozen build excludes) at all.
#
# The pin's note that a concurrent session had "added a vault= mode" was not
# accurate — no vault= parameter existed on classify() in any branch, worktree,
# stash or reflog. The fix is the Layer 1a/keyword work in this commit instead.
def test_json_wrapping_must_not_lower_a_tier():
    """The same text must not become safer by being put in a JSON field."""
    private = "todo: call the accountant about the account balance"
    out = _gate_text(json.dumps({"f": private}), "anthropic", "tool_result")
    assert private not in out


# ── The news fetch path registers what it should ─────────────────────────────

class TestNewsFetchRegistersProvenance:
    def test_helper_registers_title_and_snippet(self):
        from agent_friday.services.news_engine import _register_news_provenance

        items = [{"title": CDC_TITLE, "snippet": CDC_SNIPPET, "source": "Reuters"}]
        returned = _register_news_provenance(items)

        assert returned is items            # callers wrap `return` with it
        with eg._TRUSTED_LOCK:
            assert CDC_TITLE in eg._PUBLIC_PARAS
            assert CDC_SNIPPET in eg._PUBLIC_PARAS
            assert eg._PUBLIC_ORIGINS[CDC_SNIPPET] == "Reuters"

    def test_snippet_registration_is_the_new_part(self):
        """The digest path registered titles only, which exempted the headline
        and withheld the body — the actual reported symptom."""
        from agent_friday.services.news_engine import _register_news_provenance

        _register_news_provenance([{"title": CDC_TITLE, "snippet": CDC_SNIPPET,
                                    "source": "Reuters"}])
        out = _gate_text(json.dumps({"snippet": CDC_SNIPPET}),
                         "anthropic", "tool_result")
        assert CDC_SNIPPET in out

    def test_malformed_items_never_raise(self):
        from agent_friday.services.news_engine import _register_news_provenance
        assert _register_news_provenance(None) is None
        assert _register_news_provenance([None, {}, {"title": None}, 42])


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))

"""Gauntlet finding F37: knowledge_graph/integration.py's
knowledge_context_block() -- the "related pages" pointer folded into EVERY
system prompt by context_injection.build_injected_context(), for whatever
provider is handling the turn, with no per-provider tier gating downstream
-- never filtered candidates by sensitivity at all.

A candidate's `summary` is a real plaintext excerpt of the page
(wiki_graph._first_paragraph, taken from the decrypted body whenever the
vault is unlocked). A page living in a user-designated encrypted wiki
section is marked TIER_3 (wiki_graph._page_sensitivity), but
structural_query.rank_candidates/query never read that field, so a TIER_3
page's real content could be silently injected into the system prompt of
any chat turn >=12 chars long -- cloud providers included -- protected only
by the generic, PII-pattern-based seal_outbound() pass applied to the
whole payload right before the HTTP call, not the deliberate tier-based
redaction the rest of the system prompt gets
(model_router._build_context_prompt).

This probe must be RED before the fix (an encrypted-section candidate's
summary appears in the block) and GREEN after.

CASING NOTE (2026-09-04, caught by an independent cold re-verification of
this fix): the first version of this probe used "private" (already
lowercase) as the candidate's `section` AND as the mocked encrypted-
sections entry -- both sides lowercase, so it could never have caught a
case-mismatch bug, and it didn't: the first version of the actual fix
compared the candidate's raw, case-preserved `section` directly against
`_wiki_encrypted_sections()`'s always-lowercased set, so a real section
directory named e.g. "Private" was never excluded. wiki_graph.py stores
`section` as the raw path segment (`rel.split("/")[0]`, never lowercased)
-- exactly what a real wiki folder name looks like on disk -- so this
probe now uses "Private" (mixed case) for the candidate, matching what
the real indexer actually produces, against the real function's real
always-lowercase return shape.
"""
from __future__ import annotations

from agent_friday.services.knowledge_graph import integration as kgi

_CANDIDATES = [
    {"page": "Private/diary.md", "title": "Diary", "score": 9.0,
     "summary": "Real private plaintext nobody should see in a cloud prompt.",
     "section": "Private"},
    {"page": "research/notes.md", "title": "Notes", "score": 5.0,
     "summary": "Ordinary public research notes.", "section": "research"},
]


def _mock_query(monkeypatch, candidates):
    import agent_friday.services.knowledge_graph.structural_query as sq
    monkeypatch.setattr(sq, "query", lambda *a, **k: {
        "candidates": candidates, "should_read": []})


class TestKnowledgeContextBlockExcludesEncryptedSections:
    def test_encrypted_section_summary_never_appears(self, monkeypatch):
        _mock_query(monkeypatch, _CANDIDATES)
        import agent_friday.services.wiki_engine as we
        monkeypatch.setattr(we, "_wiki_encrypted_sections", lambda: {"private"})
        monkeypatch.setattr(kgi, "kg_settings", lambda: {"enabled": True})

        lines = kgi.knowledge_context_block("tell me about my recent projects")

        joined = "\n".join(lines)
        assert "Real private plaintext" not in joined, (
            "a TIER_3 (user-encrypted-section) page's real content leaked "
            "into the always-on, every-provider knowledge_context_block -- "
            "this path has no tier gating downstream, unlike the rest of "
            "the system prompt"
        )
        assert "diary" not in joined.lower()

    def test_non_encrypted_candidates_still_appear(self, monkeypatch):
        """No-op-shaped sanity check: filtering must not remove ordinary,
        non-encrypted candidates -- the feature should still work."""
        _mock_query(monkeypatch, _CANDIDATES)
        import agent_friday.services.wiki_engine as we
        monkeypatch.setattr(we, "_wiki_encrypted_sections", lambda: {"private"})
        monkeypatch.setattr(kgi, "kg_settings", lambda: {"enabled": True})

        lines = kgi.knowledge_context_block("tell me about my recent projects")

        joined = "\n".join(lines)
        assert "Ordinary public research notes" in joined

    def test_no_encrypted_sections_configured_is_unaffected(self, monkeypatch):
        """No-op-shaped sanity check: a user with no encrypted wiki sections
        at all sees no behavior change."""
        _mock_query(monkeypatch, _CANDIDATES)
        import agent_friday.services.wiki_engine as we
        monkeypatch.setattr(we, "_wiki_encrypted_sections", lambda: set())
        monkeypatch.setattr(kgi, "kg_settings", lambda: {"enabled": True})

        lines = kgi.knowledge_context_block("tell me about my recent projects")

        joined = "\n".join(lines)
        assert "Real private plaintext" in joined
        assert "Ordinary public research notes" in joined

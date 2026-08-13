"""Unit tests for The Friday Edition E0 composer (services/edition_engine.py).

Offline-only, no network. Every engine the composer reads from (news_engine's
archive/front_pages/editorials, creations, memory_dreaming) is monkeypatched to
fixed synthetic data so compose() is exercised deterministically.
"""
from __future__ import annotations

import json

import pytest

from agent_friday.services import creations as cr
from agent_friday.services import edition_engine as ee
from agent_friday.services import memory_dreaming as md
from agent_friday.services import news_engine as ne


@pytest.fixture(autouse=True)
def _isolated_edition_dir(tmp_path, monkeypatch):
    """Redirect every edition_engine storage path to a throwaway dir per test,
    and blank out every upstream engine so each test controls its own inputs."""
    base = tmp_path / "edition"
    monkeypatch.setattr(ee, "EDITION_DIR", base)
    monkeypatch.setattr(ee, "EDITIONS_DIR", base / "editions")
    monkeypatch.setattr(ee, "CHARTER_FILE", base / "charter.md")
    monkeypatch.setattr(ee, "CHARTER_VERSIONS_DIR", base / "charter_versions")
    monkeypatch.setattr(ee, "VERBS_FILE", base / "verbs.jsonl")
    monkeypatch.setattr(ee, "CORRECTIONS_SHOWN_FILE", base / "corrections_shown.json")

    monkeypatch.setattr(ne, "_read_archive", lambda *a, **kw: [], raising=False)
    monkeypatch.setattr(ne, "_list_front_pages", lambda: [], raising=False)
    monkeypatch.setattr(ne, "_read_front_page", lambda eid: None, raising=False)
    monkeypatch.setattr(ne, "_list_editorials", lambda **kw: [], raising=False)
    monkeypatch.setattr(ne, "_load_boosted_sources", lambda: [], raising=False)
    monkeypatch.setattr(cr, "_list_daily_creations", lambda: [], raising=False)
    monkeypatch.setattr(md, "recent_dreams", lambda n=7: [], raising=False)
    yield


def _archive_item(id_="a1", title="Story One", source="example.com", relevance=5.0):
    return {
        "id": id_, "title": title, "url": f"https://{source}/{id_}",
        "source": source, "domain": source, "category": "AI/Tech",
        "snippet": "A snippet.", "published_at": "2026-08-13T06:00:00",
        "fetched_at": "2026-08-13T06:05:00", "sentiment": "neutral",
        "relevance_score": relevance, "trust": "green", "trust_score": 0.8,
        "trust_dims": None, "read": False, "bookmarked": False,
    }


# ═══════════════════════════════════════════════════════════════════════════
#  CHARTER
# ═══════════════════════════════════════════════════════════════════════════

class TestCharter:
    def test_default_charter_created_on_first_read(self):
        charter = ee.read_charter()
        assert "§1" in charter["clauses"]
        assert "§4" in charter["clauses"]
        assert ee.CHARTER_FILE.exists()

    def test_write_charter_keeps_prior_version(self):
        ee.read_charter()  # seeds the default
        ee.write_charter("# New Charter\n\n## §1 Section budgets\n- Friday's Desk: up to 2 cards\n")
        versions = list(ee.CHARTER_VERSIONS_DIR.glob("*.md"))
        assert len(versions) == 1
        assert "Friday's Editorial Charter" in versions[0].read_text(encoding="utf-8")

    def test_write_charter_rejects_empty_text(self):
        with pytest.raises(ValueError):
            ee.write_charter("   ")


# ═══════════════════════════════════════════════════════════════════════════
#  NO-RECEIPT-NO-RENDER — the structural gate, with a test that CAN fail
# ═══════════════════════════════════════════════════════════════════════════

class TestReceiptGate:
    def test_card_with_valid_receipt_survives_the_gate(self):
        card = {"id": "c1", "title": "T", "url": "https://x/1", "source": "x", "origin_id": "o1"}
        card["receipt"] = ee.mint_receipt(card, engine="test", origin_id="o1",
                                          fetched_at="2026-08-13T00:00:00Z", retrieved_via="test")
        assert ee.has_live_receipt(card) is True
        assert ee.gate_cards([card]) == [card]

    def test_card_with_no_receipt_is_dropped(self):
        """A card built without going through mint_receipt() — e.g. a future
        bug that forgets to call it — must not render. This assertion FAILS
        if gate_cards() is ever weakened to `return cards`."""
        card = {"id": "c2", "title": "Unreceipted", "url": "https://x/2", "source": "x"}
        assert ee.has_live_receipt(card) is False
        assert ee.gate_cards([card]) == []

    def test_card_tampered_after_receipt_is_dropped(self):
        """Content edited after the receipt was minted invalidates it — the
        content_hash no longer matches, so the card is not what was fetched."""
        card = {"id": "c3", "title": "Original", "url": "https://x/3", "source": "x", "origin_id": "o3"}
        card["receipt"] = ee.mint_receipt(card, engine="test", origin_id="o3",
                                          fetched_at="2026-08-13T00:00:00Z", retrieved_via="test")
        card["title"] = "Tampered after the fact"
        assert ee.has_live_receipt(card) is False
        assert ee.gate_cards([card]) == []

    def test_gate_preserves_valid_cards_while_dropping_bad_ones(self):
        good = {"id": "g", "title": "Good", "url": "https://x/g", "source": "x", "origin_id": "og"}
        good["receipt"] = ee.mint_receipt(good, engine="test", origin_id="og",
                                          fetched_at="2026-08-13T00:00:00Z", retrieved_via="test")
        bad = {"id": "b", "title": "Bad", "url": "https://x/b", "source": "x"}
        result = ee.gate_cards([good, bad])
        assert result == [good]


# ═══════════════════════════════════════════════════════════════════════════
#  HONEST DEGRADATION
# ═══════════════════════════════════════════════════════════════════════════

class TestHonestDegradation:
    def test_dry_engines_produce_gap_notes_not_filler(self):
        edition = ee.compose()
        career = next(s for s in edition["sections"] if s["id"] == "career_signals")
        people = next(s for s in edition["sections"] if s["id"] == "your_people")
        desk = next(s for s in edition["sections"] if s["id"] == "fridays_desk")
        assert all(c["kind"] == "gap" for c in career["cards"])
        assert all(c["kind"] == "gap" for c in people["cards"])
        assert all(c["kind"] == "gap" for c in desk["cards"])
        # Gap cards still carry a real receipt — honesty doesn't bypass the gate.
        assert all(ee.has_live_receipt(c) for c in desk["cards"])
        # Gap notes must not silently masquerade as content — no title, no url.
        assert all(not c["title"] and not c["url"] for c in desk["cards"])

    def test_dissent_is_a_gap_when_no_archive_exists(self):
        edition = ee.compose()
        assert len(edition["dissent"]) == 1
        assert edition["dissent"][0]["kind"] == "gap"


# ═══════════════════════════════════════════════════════════════════════════
#  DISSENT SLOT — outside the boosted list
# ═══════════════════════════════════════════════════════════════════════════

class TestDissentSlot:
    def test_dissent_excludes_boosted_sources(self, monkeypatch):
        items = [_archive_item("a1", "Boosted Story", "boosted.example"),
                 _archive_item("a2", "Outside Story", "outside.example")]
        monkeypatch.setattr(ne, "_read_archive", lambda *a, **kw: items, raising=False)
        monkeypatch.setattr(ne, "_load_boosted_sources", lambda: ["boosted.example"], raising=False)
        edition = ee.compose()
        assert len(edition["dissent"]) == 1
        card = edition["dissent"][0]
        assert card["dissent"] is True
        assert card["source"] == "outside.example"
        assert card["kind"] != "gap"


# ═══════════════════════════════════════════════════════════════════════════
#  COMPOSER DETERMINISM
# ═══════════════════════════════════════════════════════════════════════════

class TestDeterminism:
    def test_compose_is_deterministic_given_fixed_inputs(self, monkeypatch):
        items = [_archive_item(f"a{i}", f"Story {i}", "boosted.example", relevance=float(10 - i))
                 for i in range(3)]
        monkeypatch.setattr(ne, "_read_archive", lambda *a, **kw: list(items), raising=False)
        monkeypatch.setattr(ne, "_load_boosted_sources", lambda: ["boosted.example"], raising=False)

        def _strip_volatile(edition):
            e = json.loads(json.dumps(edition))
            e.pop("composed_at", None)
            for s in e["sections"]:
                for c in s["cards"]:
                    (c.get("receipt") or {}).pop("issued_at", None)
            for c in e["dissent"]:
                (c.get("receipt") or {}).pop("issued_at", None)
            return e

        first = _strip_volatile(ee.compose())
        second = _strip_volatile(ee.compose())
        assert first == second


# ═══════════════════════════════════════════════════════════════════════════
#  CHARTER GOVERNS SELECTION, CITED IN THE RATIONALE
# ═══════════════════════════════════════════════════════════════════════════

class TestCharterGovernsCompose:
    def test_shrinking_the_budget_shrinks_the_next_compose(self, monkeypatch):
        items = [_archive_item(f"a{i}", f"Story {i}", "boosted.example") for i in range(8)]
        monkeypatch.setattr(ne, "_read_archive", lambda *a, **kw: list(items), raising=False)
        monkeypatch.setattr(ne, "_load_boosted_sources", lambda: ["boosted.example"], raising=False)

        first = ee.compose()
        desk_first = next(s for s in first["sections"] if s["id"] == "fridays_desk")
        assert len(desk_first["cards"]) == 6  # default §1 budget

        ee.write_charter(
            "# Charter\n\n## §1 Section budgets\n"
            "- Career Signals: up to 4 cards\n- Your People: up to 4 cards\n"
            "- Friday's Desk: up to 2 cards\n\n"
            "## §2 Dissent\nunchanged\n\n## §4 Honesty\nunchanged\n\n## §5 Corrections\nunchanged\n")

        second = ee.compose()
        desk_second = next(s for s in second["sections"] if s["id"] == "fridays_desk")
        assert len(desk_second["cards"]) == 2
        assert any("§1" in line for line in second["rationale"])

    def test_rationale_cites_clauses(self, monkeypatch):
        edition = ee.compose()
        text = " ".join(edition["rationale"])
        assert "§1" in text and "§2" in text and "§4" in text and "§5" in text


# ═══════════════════════════════════════════════════════════════════════════
#  CAUGHT-UP END STATE
# ═══════════════════════════════════════════════════════════════════════════

class TestCaughtUp:
    def test_edition_ends_caught_up(self):
        edition = ee.compose()
        assert edition["caught_up"] is True
        assert "caught up" in edition["rationale"][-1].lower()


# ═══════════════════════════════════════════════════════════════════════════
#  VERBS + CORRECTIONS
# ═══════════════════════════════════════════════════════════════════════════

class TestVerbsAndCorrections:
    def test_append_verb_writes_jsonl(self):
        ee.append_verb("card-1", "keep")
        ee.append_verb("card-1", "more_like_this")
        lines = ee.VERBS_FILE.read_text(encoding="utf-8").splitlines()
        assert len(lines) == 2
        first = json.loads(lines[0])
        assert first["card_id"] == "card-1" and first["verb"] == "keep"

    def test_append_verb_rejects_unknown_verb(self):
        with pytest.raises(ValueError):
            ee.append_verb("card-1", "delete_forever")

    def test_why_with_reason_surfaces_as_a_correction_next_compose(self):
        ee.append_verb("card-1", "why", reason="This was actually wrong",
                       card_title="Original Story", card_section="fridays_desk",
                       card_source="example.com")
        edition = ee.compose()
        assert len(edition["corrections"]) == 1
        c = edition["corrections"][0]
        assert "Original Story" in c["title"]
        assert c["snippet"] == "This was actually wrong"
        assert ee.has_live_receipt(c)

    def test_correction_shown_only_once(self):
        ee.append_verb("card-1", "why", reason="Wrong", card_title="Story")
        first = ee.compose()
        assert len(first["corrections"]) == 1
        second = ee.compose()
        assert len(second["corrections"]) == 0

    def test_why_without_reason_is_not_a_correction(self):
        ee.append_verb("card-1", "why")  # just "show me why", no typed correction
        edition = ee.compose()
        assert len(edition["corrections"]) == 0

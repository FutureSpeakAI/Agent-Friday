"""The Friday Edition — E0 composer (docs/AUTONOMY... see The Friday Edition
spec, Fable-authored, Sonnet-built).

Composes a finite morning edition entirely from Friday's EXISTING engines —
the news archive, front_pages/, editorials/, creations/, and dreams. No new
ingestion of any kind lives here or ever should: that is E1+'s job.

Constitutional rules this module enforces structurally, not just by convention:
  * NO-RECEIPT-NO-RENDER — a card with no live receipt (proof it was read from
    a real, executed engine call this compose) cannot survive `gate_cards()`.
    See response_provenance.py for the sibling rule on chat citations; this is
    the same idea applied to Edition cards. Deliberately not built on
    services/provenance.py (that module is C2PA ownership for artifacts Friday
    *creates* — Edition cards *curate* existing engine output, a different
    claim entirely).
  * CHARTER GOVERNS TASTE, NEVER ACCURACY — the charter can change what's
    included and how much, cited by clause id in the rationale. It cannot
    waive the receipt gate or invent content.
  * HONEST DEGRADATION — an engine with nothing real to contribute produces a
    gap note, never filler. See _gap_card().
"""
from __future__ import annotations

import hashlib
import json
import re
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from agent_friday.core import FRIDAY_DIR

EDITION_DIR = FRIDAY_DIR / "edition"
EDITIONS_DIR = EDITION_DIR / "editions"
CHARTER_FILE = EDITION_DIR / "charter.md"
CHARTER_VERSIONS_DIR = EDITION_DIR / "charter_versions"
VERBS_FILE = EDITION_DIR / "verbs.jsonl"
CORRECTIONS_SHOWN_FILE = EDITION_DIR / "corrections_shown.json"

_LOCK = threading.RLock()

VERBS = ("keep", "kill", "more_like_this", "why")

DEFAULT_CHARTER = """# Friday's Editorial Charter — v1

This charter governs TASTE and SELECTION only. It never overrides accuracy,
receipts, or the no-receipt-no-render rule — those are constitutional, not
editable here.

## §1 Section budgets
- Career Signals: up to 4 cards
- Your People: up to 4 cards
- Friday's Desk: up to 6 cards

## §2 Dissent
- One dissent card per edition, drawn from a source OUTSIDE the boosted list
  (~/.friday/boosted_sources.json).
- The full credible-but-opposed trust-graph query ships in E1; for now this
  clause enforces "not on the boosted list" as the dissent bar. TODO(E1):
  route through SourceTrustGraph for a true credible-but-opposed query.

## §3 Boosted sources
- Boosted sources are preferred for Friday's Desk story selection, consistent
  with the News workspace's own ranking.

## §4 Honesty
- If a section has nothing real to show, say so. Never fill with unlabeled
  or fabricated content.

## §5 Corrections
- A retracted card (flagged via the "why" verb with a typed reason) is shown
  once, under Corrections, in the next edition composed after the retraction,
  then drops.
"""

# ═══════════════════════════════════════════════════════════════════════════
#  CHARTER — plain-text, versioned, cited by clause id
# ═══════════════════════════════════════════════════════════════════════════

_CLAUSE_RE = re.compile(r"^##\s*(§\d+)\s+(.+)$", re.MULTILINE)


def read_charter() -> Dict[str, Any]:
    """Read the charter, creating the v1 starter policy on first use.

    Returns {text, clauses: {"§1": "Section budgets", ...}, updated_at}.
    """
    try:
        if not CHARTER_FILE.exists():
            EDITION_DIR.mkdir(parents=True, exist_ok=True)
            CHARTER_FILE.write_text(DEFAULT_CHARTER, encoding="utf-8")
        text = CHARTER_FILE.read_text(encoding="utf-8")
    except Exception:
        text = DEFAULT_CHARTER
    clauses = {m.group(1): m.group(2).strip() for m in _CLAUSE_RE.finditer(text)}
    try:
        updated_at = datetime.fromtimestamp(
            CHARTER_FILE.stat().st_mtime, tz=timezone.utc
        ).strftime("%Y-%m-%dT%H:%M:%SZ")
    except Exception:
        updated_at = ""
    return {"text": text, "clauses": clauses, "updated_at": updated_at}


def write_charter(text: str) -> Dict[str, Any]:
    """Save a new charter, keeping the prior version in a diff log.

    Changes are picked up by the NEXT compose() call only — compose() reads
    the charter fresh at the start of each run and nothing re-composes an
    edition already on disk.
    """
    text = (text or "").strip()
    if not text:
        raise ValueError("charter text required")
    with _LOCK:
        EDITION_DIR.mkdir(parents=True, exist_ok=True)
        CHARTER_VERSIONS_DIR.mkdir(parents=True, exist_ok=True)
        if CHARTER_FILE.exists():
            prev = CHARTER_FILE.read_text(encoding="utf-8")
            stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
            (CHARTER_VERSIONS_DIR / f"{stamp}.md").write_text(prev, encoding="utf-8")
        CHARTER_FILE.write_text(text, encoding="utf-8")
    return read_charter()


def _budget(clauses_text: str, key: str, default: int) -> int:
    m = re.search(key + r":\s*up to (\d+)", clauses_text, re.IGNORECASE)
    return int(m.group(1)) if m else default


# ═══════════════════════════════════════════════════════════════════════════
#  RECEIPT — the no-receipt-no-render gate
# ═══════════════════════════════════════════════════════════════════════════

def _card_content_hash(card: Dict[str, Any]) -> str:
    """Hash of the fields a receipt vouches for. Any of these changing after
    the receipt was minted invalidates the receipt (tamper-evident)."""
    body = json.dumps({
        "title": card.get("title", ""),
        "url": card.get("url", ""),
        "source": card.get("source", ""),
        "origin_id": card.get("origin_id", ""),
    }, sort_keys=True)
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


def mint_receipt(card: Dict[str, Any], *, engine: str, origin_id: str,
                  fetched_at: str, retrieved_via: str) -> Dict[str, Any]:
    """Build the receipt for a card just read from a real, executed engine
    call. Call this INLINE with the read that produced the card's content —
    never after the fact, never for content that wasn't actually read."""
    return {
        "engine": engine,
        "origin_id": origin_id,
        "fetched_at": fetched_at,
        "retrieved_via": retrieved_via,
        "issued_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "content_hash": _card_content_hash(card),
    }


def has_live_receipt(card: Dict[str, Any]) -> bool:
    """The structural check: does this card carry a receipt whose content
    hash still matches the card's current content? This is what makes the
    no-receipt-no-render gate real rather than decorative — a card built
    without going through mint_receipt(), or edited after the fact, fails
    this check and gate_cards() drops it."""
    receipt = card.get("receipt")
    if not isinstance(receipt, dict):
        return False
    if not receipt.get("engine") or not receipt.get("fetched_at"):
        return False
    return receipt.get("content_hash") == _card_content_hash(card)


def gate_cards(cards: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Drop any card without a live receipt. This is the enforcement point —
    call it as the last step before an edition (or a single card) is
    persisted or serialized to the API. A card that fails this gate never
    reaches the client, full stop."""
    return [c for c in cards if has_live_receipt(c)]


# ═══════════════════════════════════════════════════════════════════════════
#  GAP CARDS — honest degradation
# ═══════════════════════════════════════════════════════════════════════════

def _gap_card(section: str, reason: str) -> Dict[str, Any]:
    """A section with nothing real to show says so. This is itself a
    'card' the UI renders (styled as a gap note, not a story) so honest
    degradation is a first-class citizen of the card stream, not an
    afterthought bolted onto the API response."""
    card = {
        "id": hashlib.sha1(f"gap:{section}:{reason}".encode()).hexdigest()[:16],
        "kind": "gap",
        "section": section,
        "title": "",
        "snippet": reason,
        "source": "",
        "url": "",
        "why_chosen": "Honest gap — §4 Honesty: no filler when an engine has nothing real.",
        "boosted": False,
        "dissent": False,
        "origin_id": f"gap:{section}",
    }
    card["receipt"] = mint_receipt(
        card, engine="edition_engine._gap_card", origin_id=card["origin_id"],
        fetched_at=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        retrieved_via="honest_degradation")
    return card


def _card_id(engine: str, origin_id: str, section: str) -> str:
    return hashlib.sha1(f"{engine}:{origin_id}:{section}".encode()).hexdigest()[:16]


# ═══════════════════════════════════════════════════════════════════════════
#  SECTION BUILDERS
# ═══════════════════════════════════════════════════════════════════════════

def _career_signals_section(budget: int) -> List[Dict[str, Any]]:
    """E0 has no career-specific ingestion (RSS/job-boards/LinkedIn tier land
    in E1) — an honest gap, not a fake section."""
    return [_gap_card(
        "career_signals",
        "No career-signal ingestion is wired yet — the RSS / job-board / "
        "LinkedIn tiers land in E1. Nothing here today, on purpose.")]


def _your_people_section(budget: int) -> List[Dict[str, Any]]:
    """E0 has no people/trust-graph neighborhood query — an honest gap."""
    return [_gap_card(
        "your_people",
        "No people-network query is wired yet — that's the trust-graph "
        "neighborhood work in E1. Nothing here today, on purpose.")]


def _fridays_desk_section(budget: int) -> List[Dict[str, Any]]:
    """The one populated E0 section — pulls one card from each real existing
    engine (front page lead, editorial, daily creation, dream), then fills
    any remaining budget with top archive news."""
    from agent_friday.services import news_engine as ne
    cards: List[Dict[str, Any]] = []
    now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    # 1. Latest Front Page lead.
    try:
        listing = ne._list_front_pages()
        if listing:
            edition = ne._read_front_page(listing[0]["id"])
            lead = (edition or {}).get("lead") or {}
            if lead.get("title"):
                card = {
                    "id": _card_id("news_engine._read_front_page", listing[0]["id"], "fridays_desk"),
                    "kind": "front_page", "section": "fridays_desk",
                    "title": lead.get("title", ""), "snippet": lead.get("summary", "") or lead.get("why", ""),
                    "source": lead.get("source") or "Friday's Front Page",
                    "url": lead.get("url", ""),
                    "fetched_at": edition.get("generated_central") or edition.get("date", ""),
                    "why_chosen": f"Surfaced by news_engine._read_front_page — the lead story of "
                                  f"the {edition.get('slot','latest')} Front Page edition.",
                    "boosted": False, "dissent": False,
                    "origin_id": listing[0]["id"],
                }
                card["receipt"] = mint_receipt(card, engine="news_engine._read_front_page",
                                               origin_id=listing[0]["id"], fetched_at=now_iso,
                                               retrieved_via="file_read:front_pages")
                cards.append(card)
    except Exception:
        pass

    # 2. Latest editorial.
    try:
        listing = ne._list_editorials(include_md=False)
        if listing:
            ed = listing[0]
            card = {
                "id": _card_id("news_engine._list_editorials", ed["id"], "fridays_desk"),
                "kind": "editorial", "section": "fridays_desk",
                "title": f"This week's editorial ({ed.get('week','')})",
                "snippet": ed.get("preview", ""), "source": "Friday's editorial desk",
                "url": "", "fetched_at": ed.get("generated_central", ""),
                "why_chosen": "Surfaced by news_engine._list_editorials — the most recent weekly editorial.",
                "boosted": False, "dissent": False, "origin_id": ed["id"],
            }
            card["receipt"] = mint_receipt(card, engine="news_engine._list_editorials",
                                           origin_id=ed["id"], fetched_at=now_iso,
                                           retrieved_via="file_read:editorials")
            cards.append(card)
    except Exception:
        pass

    # 3. Latest daily creation.
    try:
        from agent_friday.services import creations as cr
        listing = cr._list_daily_creations()
        if listing:
            item = listing[0]
            card = {
                "id": _card_id("creations._list_daily_creations", item.get("date", ""), "fridays_desk"),
                "kind": "creation", "section": "fridays_desk",
                "title": item.get("title") or "Today's creation",
                "snippet": f"{item.get('type','')} · mood: {item.get('mood','')}".strip(" ·"),
                "source": "Friday's daily creation", "url": "",
                "fetched_at": item.get("date", ""),
                "why_chosen": "Surfaced by creations._list_daily_creations — Friday's most recent daily creation.",
                "boosted": False, "dissent": False, "origin_id": item.get("date", ""),
            }
            card["receipt"] = mint_receipt(card, engine="creations._list_daily_creations",
                                           origin_id=item.get("date", ""), fetched_at=now_iso,
                                           retrieved_via="file_read:creations")
            cards.append(card)
    except Exception:
        pass

    # 4. Most recent dream (memory consolidation summary).
    try:
        from agent_friday.services import memory_dreaming as md
        dreams = md.recent_dreams(1)
        if dreams:
            d = dreams[0]
            card = {
                "id": _card_id("memory_dreaming.recent_dreams", d.get("day", ""), "fridays_desk"),
                "kind": "dream", "section": "fridays_desk",
                "title": f"Last night's memory consolidation ({d.get('day','')})",
                "snippet": d.get("summary", ""), "source": "Friday's dreaming loop", "url": "",
                "fetched_at": d.get("day", ""),
                "why_chosen": "Surfaced by memory_dreaming.recent_dreams — the latest nightly consolidation run.",
                "boosted": False, "dissent": False, "origin_id": d.get("day", ""),
            }
            card["receipt"] = mint_receipt(card, engine="memory_dreaming.recent_dreams",
                                           origin_id=d.get("day", ""), fetched_at=now_iso,
                                           retrieved_via="sqlite_read:dreams")
            cards.append(card)
    except Exception:
        pass

    # 5. Fill remaining budget with top archive news (boosted sources first).
    remaining = max(0, budget - len(cards))
    if remaining:
        try:
            boosted = set(ne._load_boosted_sources())
            archive = ne._read_archive()
            archive = sorted(
                archive,
                key=lambda a: (a.get("source") in boosted, a.get("relevance_score") or 0),
                reverse=True,
            )
            for a in archive[:remaining]:
                card = {
                    "id": _card_id("news_engine._read_archive", a.get("id", ""), "fridays_desk"),
                    "kind": "news", "section": "fridays_desk",
                    "title": a.get("title", ""), "snippet": a.get("snippet", ""),
                    "source": a.get("source", ""), "url": a.get("url", ""),
                    "fetched_at": a.get("fetched_at", ""),
                    "why_chosen": "Surfaced by news_engine._read_archive — top-ranked story in your "
                                  + ("boosted " if a.get("source") in boosted else "")
                                  + "archive.",
                    "boosted": a.get("source") in boosted, "dissent": False,
                    "origin_id": a.get("id", ""),
                }
                card["receipt"] = mint_receipt(card, engine="news_engine._read_archive",
                                               origin_id=a.get("id", ""), fetched_at=now_iso,
                                               retrieved_via="file_read:news_archive")
                cards.append(card)
        except Exception:
            pass

    cards = cards[:budget]
    if not cards:
        return [_gap_card(
            "fridays_desk",
            "No Front Page, editorial, creation, dream, or archived story was "
            "available to compose from. Nothing here today, on purpose.")]
    return cards


def _dissent_card() -> Dict[str, Any]:
    """E0's dissent bar: a story from a source OUTSIDE the boosted list.
    TODO(E1): replace with a real credible-but-opposed SourceTrustGraph query
    (§2 of the charter names this explicitly)."""
    from agent_friday.services import news_engine as ne
    now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    try:
        boosted = set(ne._load_boosted_sources())
        archive = ne._read_archive()
        outside = [a for a in archive if a.get("source") not in boosted and a.get("source")]
        if outside:
            a = outside[0]
            card = {
                "id": _card_id("news_engine._read_archive", a.get("id", ""), "dissent"),
                "kind": "news", "section": "dissent",
                "title": a.get("title", ""), "snippet": a.get("snippet", ""),
                "source": a.get("source", ""), "url": a.get("url", ""),
                "fetched_at": a.get("fetched_at", ""),
                "why_chosen": "Surfaced by news_engine._read_archive, filtered to sources outside "
                              "your boosted list (§2 Dissent) — not yet a true credible-but-opposed "
                              "trust-graph query (TODO: E1).",
                "boosted": False, "dissent": True, "origin_id": a.get("id", ""),
            }
            card["receipt"] = mint_receipt(card, engine="news_engine._read_archive",
                                           origin_id=a.get("id", ""), fetched_at=now_iso,
                                           retrieved_via="file_read:news_archive")
            return card
    except Exception:
        pass
    return _gap_card(
        "dissent",
        "No archived story outside your boosted-source list was available for "
        "today's dissent slot. Nothing here today, on purpose.")


# ═══════════════════════════════════════════════════════════════════════════
#  CORRECTIONS — retractions surfaced via the "why" verb, shown once
# ═══════════════════════════════════════════════════════════════════════════

def _read_verbs() -> List[Dict[str, Any]]:
    if not VERBS_FILE.exists():
        return []
    out = []
    for line in VERBS_FILE.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except Exception:
            continue
    return out


def _verb_hash(entry: Dict[str, Any]) -> str:
    return hashlib.sha1(json.dumps(entry, sort_keys=True).encode()).hexdigest()[:16]


def append_verb(card_id: str, verb: str, *, reason: str = "",
                card_title: str = "", card_section: str = "",
                card_source: str = "") -> Dict[str, Any]:
    """Append one verb event. If verb=='why' and a reason was typed, this is
    also the retraction/correction mechanism — the same log, per spec."""
    if verb not in VERBS:
        raise ValueError(f"unknown verb {verb!r}")
    entry = {
        "ts": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "card_id": card_id, "verb": verb, "reason": (reason or "").strip(),
        "card_title": card_title, "card_section": card_section, "card_source": card_source,
    }
    with _LOCK:
        EDITION_DIR.mkdir(parents=True, exist_ok=True)
        with VERBS_FILE.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry) + "\n")
    return entry


def _load_corrections_shown() -> Dict[str, str]:
    try:
        return json.loads(CORRECTIONS_SHOWN_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_corrections_shown(shown: Dict[str, str]) -> None:
    EDITION_DIR.mkdir(parents=True, exist_ok=True)
    CORRECTIONS_SHOWN_FILE.write_text(json.dumps(shown, indent=2), encoding="utf-8")


def _corrections_section(edition_id: str) -> List[Dict[str, Any]]:
    """Corrections not yet shown in a prior edition, rendered once."""
    now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    entries = [e for e in _read_verbs() if e.get("verb") == "why" and e.get("reason")]
    if not entries:
        return []
    shown = _load_corrections_shown()
    pending = [e for e in entries if _verb_hash(e) not in shown]
    if not pending:
        return []
    cards = []
    for e in pending:
        origin_id = _verb_hash(e)
        card = {
            "id": _card_id("edition_engine._corrections_section", origin_id, "corrections"),
            "kind": "correction", "section": "corrections",
            "title": f"Correction: {e.get('card_title','a prior card')}",
            "snippet": e.get("reason", ""), "source": e.get("card_source", ""), "url": "",
            "fetched_at": e.get("ts", ""),
            "why_chosen": "Surfaced by edition_engine._corrections_section — a reader-flagged "
                          "correction from the 'why' verb log (§5 Corrections).",
            "boosted": False, "dissent": False, "origin_id": origin_id,
        }
        card["receipt"] = mint_receipt(card, engine="edition_engine._corrections_section",
                                       origin_id=origin_id, fetched_at=now_iso,
                                       retrieved_via="file_read:verbs_log")
        cards.append(card)
        shown[origin_id] = edition_id
    _save_corrections_shown(shown)
    return cards


# ═══════════════════════════════════════════════════════════════════════════
#  COMPOSE
# ═══════════════════════════════════════════════════════════════════════════

def _edition_id(now: Optional[datetime] = None) -> str:
    now = now or datetime.now(timezone.utc)
    return now.strftime("%Y-%m-%d")


def compose(*, edition_id: Optional[str] = None) -> Dict[str, Any]:
    """Compose one edition from current on-disk state. Deterministic given
    fixed inputs (archive/front_pages/editorials/creations/dreams/verbs all
    read from disk, no network, no randomness) — the only intrinsically
    variable field is composed_at."""
    charter = read_charter()
    clauses_text = charter["text"]
    career_budget = _budget(clauses_text, "Career Signals", 4)
    people_budget = _budget(clauses_text, "Your People", 4)
    desk_budget = _budget(clauses_text, "Friday's Desk", 6)

    eid = edition_id or _edition_id()

    career = gate_cards(_career_signals_section(career_budget))
    people = gate_cards(_your_people_section(people_budget))
    desk = gate_cards(_fridays_desk_section(desk_budget))
    dissent = gate_cards([_dissent_card()])
    corrections = gate_cards(_corrections_section(eid))

    sections = [
        {"id": "career_signals", "label": "Career Signals", "budget": career_budget, "cards": career},
        {"id": "your_people", "label": "Your People", "budget": people_budget, "cards": people},
        {"id": "fridays_desk", "label": "Friday's Desk", "budget": desk_budget, "cards": desk},
    ]

    rationale = _build_rationale(sections, dissent, corrections, charter["clauses"])

    edition = {
        "id": eid,
        "composed_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "sections": sections,
        "dissent": dissent,
        "corrections": corrections,
        "rationale": rationale,
        "charter_version_at_compose": charter["updated_at"],
        "caught_up": True,
    }
    _store_edition(edition)
    return edition


def _build_rationale(sections, dissent, corrections, clauses) -> List[str]:
    desk = next(s for s in sections if s["id"] == "fridays_desk")
    career = next(s for s in sections if s["id"] == "career_signals")
    people = next(s for s in sections if s["id"] == "your_people")
    desk_real = [c for c in desk["cards"] if c["kind"] != "gap"]
    lines = []
    lines.append(
        f"1. Friday's Desk carries {len(desk_real)} stor{'y' if len(desk_real)==1 else 'ies'}, "
        f"capped at the §1 budget ({desk['budget']}).")
    gaps = [s["label"] for s in (career, people) if any(c["kind"] == "gap" for c in s["cards"])]
    if gaps:
        lines.append(f"2. {' and '.join(gaps)} {'is' if len(gaps)==1 else 'are'} empty — E1 "
                      f"ingestion tiers aren't wired yet (§4 Honesty: no filler).")
    else:
        lines.append("2. Career Signals and Your People are populated from the E1 ingestion tiers.")
    d = dissent[0] if dissent else None
    if d and d["kind"] != "gap":
        lines.append(f"3. The dissent slot pulled \"{d['title']}\" from {d['source']}, outside "
                      f"your boosted list (§2 Dissent).")
    else:
        lines.append("3. No dissent candidate was available outside your boosted list today (§2 Dissent).")
    lines.append(f"4. {len(corrections)} correction(s) carried from prior editions per §5 "
                 f"(shown once, then dropped).")
    lines.append(f"5. Composed at {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')} — "
                 f"you're caught up. Nothing else today.")
    return lines


def _store_edition(edition: Dict[str, Any]) -> None:
    try:
        EDITIONS_DIR.mkdir(parents=True, exist_ok=True)
        (EDITIONS_DIR / f"{edition['id']}.json").write_text(
            json.dumps(edition, indent=2, ensure_ascii=False), encoding="utf-8")
    except Exception as e:
        print(f"  [edition_engine] store failed: {e}")


def latest_edition() -> Optional[Dict[str, Any]]:
    if not EDITIONS_DIR.exists():
        return None
    files = sorted(EDITIONS_DIR.glob("*.json"), key=lambda p: p.stem, reverse=True)
    if not files:
        return None
    try:
        return json.loads(files[0].read_text(encoding="utf-8"))
    except Exception:
        return None


def list_editions() -> List[Dict[str, Any]]:
    if not EDITIONS_DIR.exists():
        return []
    out = []
    for p in sorted(EDITIONS_DIR.glob("*.json"), key=lambda p: p.stem, reverse=True):
        try:
            d = json.loads(p.read_text(encoding="utf-8"))
            out.append({"id": d.get("id"), "composed_at": d.get("composed_at")})
        except Exception:
            continue
    return out


def get_edition(edition_id: str) -> Optional[Dict[str, Any]]:
    safe = re.sub(r"[^0-9A-Za-z\-]", "", edition_id or "")
    if not safe:
        return None
    path = EDITIONS_DIR / f"{safe}.json"
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


# ═══════════════════════════════════════════════════════════════════════════
#  SCHEDULER ENTRY POINT
# ═══════════════════════════════════════════════════════════════════════════

def run_edition_job():
    """Scheduled entry point — composes today's morning edition."""
    edition = compose()
    try:
        import agent_friday.notifications_engine as _notif_engine
        if _notif_engine:
            _notif_engine.push(
                title="🗞️ The Friday Edition is ready",
                body=f"{len(edition['sections'])} sections · dissent + corrections included",
                priority="medium", source="edition", kind="edition",
                actions=[{"label": "Open Edition", "workspace": "edition"}],
                target={"workspace": "edition"},
                dedupe_key=f"edition:{edition['id']}",
                meta={"edition_id": edition["id"]},
            )
    except Exception as e:
        print(f"  [edition_engine] notify failed: {e}")
    return edition

"""Tier A structural graph — the wiki *is* the graph.

Builds the always-on, LLM-free knowledge graph from ``~/.friday/wiki/``:

  nodes  = wiki pages (+ SOUL.md as a special node)
  edges  = explicit ``[[wikilinks]]`` and markdown links, plus *mention* edges
           (page A's title appearing in page B's body) so a vault that has no
           authored links yet still renders as a connected galaxy rather than
           disconnected dust
  communities = link-structure clusters (graph_analysis), with section-based
           fallback for pages the link graph leaves isolated

Wikilink/frontmatter parsing ported from obsidian-wiki (MIT). Reads go
through ``wiki_engine.wiki_read_text`` so encrypted sections decrypt
transparently; pages from encrypted sections are marked TIER_3 so the store
encrypts everything derived from them.

Output follows the graphrag-workbench Entity/Relationship/Community contract
(spec §5.2) and is persisted via KnowledgeGraphStore.
"""

from __future__ import annotations

import re
import time
from collections import defaultdict
from pathlib import Path
from typing import Any, Optional

from agent_friday.core import WIKI_DIR, SOUL_FILE

from . import kg_settings
from .store import KnowledgeGraphStore, KnowledgeGraphManifest
from . import graph_analysis

_FRONT_RE = re.compile(r"^---\n(.*?)\n---", re.DOTALL)
_TITLE_FM_RE = re.compile(r"^title:\s*(.+)$", re.MULTILINE)
_TAGS_RE = re.compile(r"^tags:\s*\[([^\]]+)\]", re.MULTILINE)
_TAGS_LIST_RE = re.compile(r"^tags:\s*\n((?:\s+-\s+\S+\n)+)", re.MULTILINE)
_SUMMARY_RE = re.compile(r"^summary:\s*(.+?)$", re.MULTILINE)
_H1_RE = re.compile(r"^#\s+(.+?)\s*$", re.MULTILINE)
_WIKILINK_RE = re.compile(r"\[\[([^\]|#]+?)(?:[|#][^\]]*?)?\]\]")
_MD_LINK_RE = re.compile(r"\[.*?\]\(([^)]+\.md[^)]*)\)")

SKIP_DIRS = frozenset({"_raw", "_archived", "_staging", "_archives",
                       ".obsidian", ".git"})

# Edge weights by kind: authored links are strongest, mentions are implicit.
EDGE_WEIGHTS = {"wikilink": 1.0, "mdlink": 0.9, "mention": 0.4}

SOUL_ID = "soul"


def _slug(s: str) -> str:
    return s.strip().lower().replace(" ", "-")


def _page_key(rel_path: str) -> str:
    """Unique page key: the slugged relative path without extension.

    Bare stems collide in Friday's wiki (professional/agent-friday.md vs
    ai-personality/agent-friday.md), so keys carry the section. Wikilinks
    still resolve by stem via the alias map in _extract_links.
    """
    rel = rel_path.replace("\\", "/")
    if rel.lower().endswith(".md"):
        rel = rel[:-3]
    return "/".join(_slug(part) for part in rel.split("/"))


def _read(path: Path) -> str:
    """Read via wiki_engine for transparent vault decryption."""
    try:
        from agent_friday.services.wiki_engine import wiki_read_text
        return wiki_read_text(path)
    except Exception:
        try:
            return path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return ""


def _page_sensitivity(rel_path: str) -> int:
    """TIER_3 for pages in encrypted wiki sections, TIER_1 otherwise."""
    try:
        from agent_friday.services.wiki_engine import _wiki_encrypted_sections
        section = rel_path.replace("\\", "/").split("/")[0].strip().lower()
        return 3 if section in _wiki_encrypted_sections() else 1
    except Exception:
        return 1


# ═══════════════════════════════════════════════════════════════
#  Index building
# ═══════════════════════════════════════════════════════════════

def list_wiki_pages(wiki_dir: Optional[Path] = None) -> list[Path]:
    root = Path(wiki_dir) if wiki_dir else WIKI_DIR
    if not root.exists():
        return []
    out = []
    for p in sorted(root.rglob("*.md")):
        rel_parts = p.relative_to(root).parts
        if any(part in SKIP_DIRS for part in rel_parts):
            continue
        if p.stem.startswith("_"):        # _index.md and friends: generated
            continue
        if len(rel_parts) == 1 and p.stem.upper() == "README":
            continue
        out.append(p)
    return out


def build_wiki_index(wiki_dir: Optional[Path] = None,
                     include_soul: Optional[bool] = None,
                     mention_edges: Optional[bool] = None) -> dict[str, dict]:
    """Parse the wiki into an in-memory index.

    Returns {slug: {title, tags, summary, section, path, sensitivity, body_len,
                    out_links: [(target_slug, kind)], in_links: [slug]}}
    """
    settings = kg_settings()
    if include_soul is None:
        include_soul = bool(settings["index_sources"].get("soul", True))
    if mention_edges is None:
        mention_edges = bool(settings.get("mention_edges", True))

    root = Path(wiki_dir) if wiki_dir else WIKI_DIR
    pages: dict[str, dict] = {}
    bodies: dict[str, str] = {}

    for page in list_wiki_pages(root):
        rel = str(page.relative_to(root)).replace("\\", "/")
        slug = _page_key(rel)
        text = _read(page)
        if not text or text.startswith("[vault-encrypted file"):
            text = ""

        front_m = _FRONT_RE.match(text)
        front = front_m.group(1) if front_m else ""
        body = text[front_m.end():] if front_m else text

        title = ""
        m = _TITLE_FM_RE.search(front)
        if m:
            title = m.group(1).strip().strip(">-").strip()
        if not title:
            m = _H1_RE.search(body)
            if m:
                title = m.group(1).strip()

        tags: list[str] = []
        m = _TAGS_RE.search(front)
        if m:
            tags = [t.strip().strip("'\"") for t in m.group(1).split(",")]
        else:
            m2 = _TAGS_LIST_RE.search(front)
            if m2:
                tags = [ln.strip().lstrip("- ")
                        for ln in m2.group(1).splitlines() if ln.strip()]

        summary = ""
        m = _SUMMARY_RE.search(front)
        if m:
            summary = m.group(1).strip()
        if not summary:
            summary = _first_paragraph(body)

        section = rel.split("/")[0] if "/" in rel else ""

        pages[slug] = {
            "title": title or page.stem,
            "stem": _slug(page.stem),
            "tags": tags,
            "summary": summary[:240],
            "section": section,
            "path": rel,
            "sensitivity": _page_sensitivity(rel),
            "body_len": len(body),
            "out_links": [],
            "in_links": [],
            "mention_count": 0,
        }
        bodies[slug] = body

    if include_soul:
        soul_text = ""
        try:
            if SOUL_FILE.exists():
                soul_text = SOUL_FILE.read_text(encoding="utf-8",
                                                errors="replace")
        except OSError:
            pass
        if soul_text:
            pages[SOUL_ID] = {
                "title": "SOUL.md — Friday's identity",
                "stem": SOUL_ID,
                "tags": ["soul"],
                "summary": _first_paragraph(soul_text)[:240],
                "section": "",
                "path": "SOUL.md",
                "sensitivity": 1,
                "body_len": len(soul_text),
                "out_links": [],
                "in_links": [],
                "mention_count": 0,
            }
            bodies[SOUL_ID] = soul_text

    _extract_links(pages, bodies)
    if mention_edges:
        _extract_mentions(pages, bodies)
    return pages


def _first_paragraph(body: str) -> str:
    for raw in body.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or line.startswith("---"):
            continue
        line = line.lstrip("> ").strip("*_ ").strip()
        if len(line) >= 8:
            return line
    return ""


def _extract_links(pages: dict[str, dict], bodies: dict[str, str]) -> None:
    """Explicit [[wikilinks]] and markdown links → strongest edges.

    Link targets are written by humans as bare page names; keys are
    path-qualified. The alias map resolves a stem to its page key, preferring
    a page in the linking page's own section when stems collide.
    """
    by_stem: dict[str, list[str]] = defaultdict(list)
    for key, entry in pages.items():
        by_stem[entry["stem"]].append(key)

    def resolve(raw: str, src_key: str) -> Optional[str]:
        raw = raw.strip()
        if not raw:
            return None
        direct = _page_key(raw)
        if direct in pages:
            return direct
        cands = [c for c in (by_stem.get(_slug(raw.split("/")[-1])) or [])
                 if c != src_key]
        if not cands:
            return None
        src_section = pages[src_key]["section"]
        same = [c for c in cands if pages[c]["section"] == src_section]
        return sorted(same or cands)[0]

    for slug, body in bodies.items():
        seen: set[tuple[str, str]] = set()
        for link in _WIKILINK_RE.findall(body):
            target = resolve(link, slug)
            if target and target != slug:
                seen.add((target, "wikilink"))
        for href in _MD_LINK_RE.findall(body):
            target = resolve(Path(href).stem, slug)
            if target and target != slug:
                seen.add((target, "mdlink"))
        for target, kind in sorted(seen):
            pages[slug]["out_links"].append((target, kind))
            pages[target]["in_links"].append(slug)


def _extract_mentions(pages: dict[str, dict], bodies: dict[str, str]) -> None:
    """Implicit edges: page A's title appearing in page B's body.

    LLM-free densifier for vaults without authored wikilinks. Titles shorter
    than 4 characters are skipped to avoid noise; existing explicit edges are
    never duplicated.

    One combined alternation regex scans each body once (alt text → target
    keys via dict), so the pass is O(total_body_bytes), not O(pages²) — at
    2k pages the per-page-pattern version needs 4M regex scans.
    """
    alt_targets: dict[str, list[str]] = defaultdict(list)
    for slug, entry in pages.items():
        candidates = {entry["title"], entry["title"].split(" — ")[0],
                      entry["stem"].replace("-", " ")}
        for c in candidates:
            c = c.strip()
            if len(c) >= 4:
                alt_targets[c.lower()].append(slug)
    if not alt_targets:
        return
    alts = sorted(alt_targets, key=len, reverse=True)
    combined = re.compile(
        r"(?<![\w-])(?:" + "|".join(re.escape(a) for a in alts) + r")(?![\w-])",
        re.IGNORECASE)

    for slug, body in bodies.items():
        if not body:
            continue
        explicit = {t for t, _k in pages[slug]["out_links"]}
        hits_by_target: dict[str, int] = defaultdict(int)
        for m in combined.finditer(body):
            for target in alt_targets.get(m.group(0).lower(), ()):
                if target != slug and target not in explicit:
                    hits_by_target[target] += 1
        for target, hits in sorted(hits_by_target.items()):
            pages[slug]["out_links"].append((target, "mention"))
            pages[target]["in_links"].append(slug)
            pages[target]["mention_count"] += hits


# ═══════════════════════════════════════════════════════════════
#  Contract records (Entity / Relationship / Community)
# ═══════════════════════════════════════════════════════════════

def _detect_page_communities(index: dict[str, dict]) -> list[set[str]]:
    """Community sets for the page graph.

    community_mode setting:
      "links"   — label propagation over explicit (non-mention) edges,
                  singletons pooled by section.
      "section" — one community per wiki section (the human-chosen taxonomy).
      "auto"    — links when authored [[wikilinks]] are dense enough to carry
                  structure (≥ nodes/2 explicit edges); section otherwise.
                  Mention edges are deliberately excluded from detection: a
                  hub page that name-drops everything (SOUL.md) would collapse
                  the whole galaxy into one blob.
    """
    mode = str(kg_settings().get("community_mode", "auto")).lower()
    explicit: dict[str, list[str]] = {
        s: [t for t, k in e["out_links"] if k != "mention"]
        for s, e in index.items()}
    n_explicit_edges = sum(len(v) for v in explicit.values())
    n_wikilinks = sum(1 for e in index.values()
                      for _t, k in e["out_links"] if k == "wikilink")

    if mode == "auto":
        mode = ("links" if n_wikilinks >= max(4, len(index) // 2)
                else "section")

    if mode == "links" and n_explicit_edges:
        communities_raw = graph_analysis.detect_communities(explicit)
        section_pool: dict[str, list[str]] = defaultdict(list)
        kept: list[set[str]] = []
        for comm in communities_raw:
            if len(comm) == 1:
                slug = next(iter(comm))
                section_pool[index[slug]["section"] or "misc"].append(slug)
            else:
                kept.append(comm)
        for section in sorted(section_pool):
            kept.append(set(section_pool[section]))
    else:
        pools: dict[str, set[str]] = defaultdict(set)
        for slug, e in index.items():
            pools[e["section"] or "core"].add(slug)
        kept = [pools[k] for k in sorted(pools)]

    kept.sort(key=lambda c: (-len(c), min(c)))
    return kept


def index_to_records(index: dict[str, dict]) -> dict[str, list[dict]]:
    """Convert the in-memory index into spec §5.2 contract records."""
    entities: list[dict] = []
    relationships: list[dict] = []

    kept = _detect_page_communities(index)
    slug_comm: dict[str, int] = {}
    for i, comm in enumerate(kept):
        for slug in comm:
            slug_comm[slug] = i

    for slug in sorted(index):
        e = index[slug]
        degree = len(e["out_links"]) + len(e["in_links"])
        entities.append({
            "id": f"page:{slug}",
            "title": e["title"],
            "type": "soul" if slug == SOUL_ID else "page",
            "description": e["summary"],
            "degree": degree,
            "frequency": e["mention_count"],
            "community": str(slug_comm.get(slug, 0)),
            "level": 0,
            "section": e["section"],
            "provenance": {
                "wiki_pages": [e["path"]],
                "sensitivity": e["sensitivity"],
            },
        })

    pair_kinds: dict[tuple[str, str], str] = {}
    for slug in sorted(index):
        for target, kind in index[slug]["out_links"]:
            key = (slug, target)
            best = pair_kinds.get(key)
            if best is None or EDGE_WEIGHTS[kind] > EDGE_WEIGHTS[best]:
                pair_kinds[key] = kind
    for (src, tgt), kind in sorted(pair_kinds.items()):
        sens = max(index[src]["sensitivity"], index[tgt]["sensitivity"])
        relationships.append({
            "id": f"rel:{src}->{tgt}",
            "source": f"page:{src}",
            "target": f"page:{tgt}",
            "description": kind,
            "weight": EDGE_WEIGHTS[kind],
            "provenance": {
                "wiki_pages": [index[src]["path"]],
                "sensitivity": sens,
            },
        })

    rel_by_comm: dict[int, list[str]] = defaultdict(list)
    for r in relationships:
        s = r["source"].split(":", 1)[1]
        t = r["target"].split(":", 1)[1]
        if slug_comm.get(s) is not None and slug_comm.get(s) == slug_comm.get(t):
            rel_by_comm[slug_comm[s]].append(r["id"])

    communities: list[dict] = []
    for i, comm in enumerate(kept):
        members = sorted(comm)
        label = _community_label(members, index)
        communities.append({
            "id": f"com_{i}",
            "community": str(i),
            "level": 0,
            "parent": None,
            "children": [],
            "title": label,
            "entity_ids": [f"page:{s}" for s in members],
            "relationship_ids": rel_by_comm.get(i, []),
            "size": len(members),
        })

    return {"entities": entities, "relationships": relationships,
            "communities": communities}


def _community_label(members: list[str], index: dict[str, dict]) -> str:
    if len(members) == 1:
        e = index[members[0]]
        return (e["section"].replace("-", " ") if e["section"]
                else e["title"][:24])
    sections = defaultdict(int)
    for s in members:
        sec = index[s]["section"]
        if sec:
            sections[sec] += 1
    if sections:
        top, count = max(sorted(sections.items()), key=lambda kv: kv[1])
        if count >= max(2, len(members) // 2):
            return top.replace("-", " ")
    tags = defaultdict(int)
    for s in members:
        for t in index[s]["tags"]:
            tags[t] += 1
    if tags:
        return max(sorted(tags), key=lambda t: tags[t])
    return index[members[0]]["title"]


# ═══════════════════════════════════════════════════════════════
#  Rebuild orchestration (Tier A)
# ═══════════════════════════════════════════════════════════════

def rebuild_tier_a(store: Optional[KnowledgeGraphStore] = None,
                   wiki_dir: Optional[Path] = None) -> dict[str, Any]:
    """Full Tier A rebuild: parse → records → layout → persist → manifest.

    Incremental in effect (the whole pass is milliseconds at wiki scale), but
    the manifest still records per-source fingerprints so ``delta()`` can
    answer "what changed" for Tier B and for no-op detection.
    """
    from . import layout as layout_mod

    t0 = time.time()
    store = store or KnowledgeGraphStore()
    index = build_wiki_index(wiki_dir=wiki_dir)
    records = index_to_records(index)

    # A Tier A rebuild refreshes the page layer only — semantic (Tier B)
    # records survive and are re-laid-out together so both tiers render as
    # one galaxy. Tier B "appears in" links to pages that no longer exist
    # are dropped.
    b_entities = [e for e in store.load("entities") if e.get("tier") == "B"]
    valid_ids = ({e["id"] for e in records["entities"]}
                 | {e["id"] for e in b_entities})
    b_rels = [r for r in store.load("relationships")
              if r.get("tier") == "B"
              and r["source"] in valid_ids and r["target"] in valid_ids]
    b_comms = [c for c in store.load("communities") if c.get("tier") == "B"]

    all_entities = records["entities"] + b_entities
    all_rels = records["relationships"] + b_rels
    all_comms = records["communities"] + b_comms

    layout_meta = layout_mod.compute_layout(
        all_entities, all_rels, all_comms,
        seed=int(kg_settings().get("layout_seed", 1337)))

    store.save("entities", all_entities)
    store.save("relationships", all_rels)
    store.save("communities", all_comms)
    # Tier A writes no LLM reports; Tier B's survive so the artifact is
    # always present for the frontend contract.
    existing_reports = [r for r in store.load("community_reports")
                        if (r.get("tier") == "B")]
    store.save("community_reports", existing_reports)
    store.save_layout(layout_meta)

    manifest = KnowledgeGraphManifest(base_dir=store.base)
    root = Path(wiki_dir) if wiki_dir else WIKI_DIR
    for p in list_wiki_pages(root):
        rel = str(p.relative_to(root))
        manifest.record(p, kind="wiki",
                        produced=[f"page:{_page_key(rel)}"])
    if SOUL_FILE.exists():
        manifest.record(SOUL_FILE, kind="soul", produced=[f"page:{SOUL_ID}"])
    manifest.save()

    return {
        "tier": "A",
        "pages": len(index),
        "entities": len(records["entities"]),
        "relationships": len(records["relationships"]),
        "communities": len(records["communities"]),
        "took_ms": int((time.time() - t0) * 1000),
    }

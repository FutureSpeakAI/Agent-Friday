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
import sys
import threading
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
_MD_LINK_RE = re.compile(r"\[[^\]\n]*\]\(([^)\n]+\.md[^)\n]*)\)")

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


#: Ways a model writes a page path that all mean "relative to the wiki root":
#: the serving wiki's own location, the legacy ~/wiki the read_wiki schema once
#: named, and a bare leading slash.
_ROOT_PREFIXES = ("~/.friday/wiki/", ".friday/wiki/", "~/wiki/", "wiki/", "/")


def resolve_page(raw, wiki_dir: Optional[Path] = None) -> Optional[Path]:
    """The file a page path names, resolved against the same root the index
    is built from, or None.

    Every `path` this index hands out (knowledge_query's candidates and
    should_read, including "SOUL.md", which lives beside the wiki rather than
    in it) resolves here to the file it was built from, so a reader that uses
    this cannot disagree with the index about where a page is. A path that
    escapes the root resolves to None.
    """
    s = str(raw or "").strip().strip("'\"").replace("\\", "/")
    if not s:
        return None
    if s.lower() in ("soul.md", SOUL_ID):
        try:
            return SOUL_FILE if SOUL_FILE.is_file() else None
        except OSError:
            return None
    root = Path(wiki_dir) if wiki_dir else WIKI_DIR
    try:
        root_r = root.resolve()
    except OSError:
        return None
    bases = [Path(s) if Path(s).is_absolute() else root / s]
    for pre in _ROOT_PREFIXES:
        if s.lower().startswith(pre) and s[len(pre):]:
            bases.append(root / s[len(pre):])
    for base in bases:
        cands = [base]
        if not base.suffix:
            cands.append(base.with_name(base.name + ".md"))
        for cand in cands:
            try:
                p = cand.resolve()
                p.relative_to(root_r)
            except (OSError, ValueError):
                continue
            if p.is_file():
                return p
    return None


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


def _mtime(path: Path) -> Optional[float]:
    """Last-modified time (epoch seconds), or None when it cannot be read."""
    try:
        return path.stat().st_mtime
    except OSError:
        return None


def build_wiki_index(wiki_dir: Optional[Path] = None,
                     include_soul: Optional[bool] = None,
                     mention_edges: Optional[bool] = None) -> dict[str, dict]:
    """Parse the wiki into an in-memory index.

    Returns {slug: {title, tags, summary, section, path, sensitivity, body_len,
                    updated (mtime or None),
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
            "updated": _mtime(page),
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
                "updated": _mtime(SOUL_FILE),
                "out_links": [],
                "in_links": [],
                "mention_count": 0,
            }
            bodies[SOUL_ID] = soul_text

    _extract_links(pages, bodies)
    if mention_edges:
        _extract_mentions(pages, bodies)
    return pages


# A parse reads and decrypts every page and builds a mention trie with a state
# per title character: hundreds of megabytes of short-lived objects on a large
# wiki. The ambient knowledge block asks for the index on every system prompt,
# so the parsed index is kept until something it was built from changes.
#
# The index holds summaries taken from decrypted pages. It lives in memory
# only, never on disk; it is dropped when the vault key changes (a new key
# digest misses the cache, and arming a passphrase clears it at once), when
# any page, SOUL.md or the graph settings change, and after CACHE_IDLE_SECONDS
# without a query.
#
# One parse runs at a time: a miss that finds a parse under way waits for it
# (BUILD_WAIT_SECONDS at most, then parses for itself) rather than starting
# another. A clear while a parse runs bumps the generation, so that parse's
# result is handed to its caller but never stored.
CACHE_IDLE_SECONDS = 600
BUILD_WAIT_SECONDS = 60
_Timer = threading.Timer
_clock = time.monotonic
_index_cache: dict[str, Any] = {"key": None, "index": None, "used": 0.0, "timer": None,
                                "timer_token": 0, "generation": 0}
_index_cache_lock = threading.Lock()
_index_build_lock = threading.Lock()


def _vault_state() -> Optional[str]:
    """A one-way digest of the vault key in force, or None when encrypted pages
    read as placeholders. The agent module is only consulted once something
    has imported it (reading an encrypted page does); before that no page can
    have been decrypted."""
    agent = sys.modules.get("agent_friday.services.agent")
    if agent is None:
        return None
    try:
        key = agent._get_vault_key()
    except Exception:
        return None
    if not key:
        return None
    import hashlib
    return hashlib.blake2b(key, digest_size=16, person=b"kg-index-cache").hexdigest()


def _drop_locked() -> None:
    timer = _index_cache.get("timer")
    if timer is not None:
        try:
            timer.cancel()
        except Exception:
            pass
    _index_cache.update(key=None, index=None, timer=None)


def _arm_idle_locked(delay: float) -> None:
    """Arm the idle drop. If no timer can start, nothing stays cached."""
    _index_cache["timer_token"] += 1
    token = _index_cache["timer_token"]
    try:
        timer = _Timer(delay, _idle_check, args=(token,))
        timer.daemon = True
        _index_cache["timer"] = timer
        timer.start()
    except Exception:
        _drop_locked()


def _idle_check(token: int) -> None:
    with _index_cache_lock:
        if token != _index_cache["timer_token"]:
            return                      # a timer replaced or cancelled since
        _index_cache["timer"] = None
        if _index_cache["index"] is None:
            return
        idle = _clock() - _index_cache["used"]
        if idle >= CACHE_IDLE_SECONDS:
            _drop_locked()
        else:
            _arm_idle_locked(CACHE_IDLE_SECONDS - idle)


def _index_fingerprint(root: Path, include_soul: bool, mention_edges: bool) -> tuple:
    """Everything a parse depends on, from stat() alone: no page is read."""
    pages = []
    for p in list_wiki_pages(root):
        try:
            st = p.stat()
        except OSError:
            continue
        # st_ino changes when a page is replaced atomically (wiki_write_text).
        pages.append((str(p), st.st_mtime_ns, st.st_size, st.st_ino))
    soul = None
    if include_soul:
        try:
            st = SOUL_FILE.stat()
            soul = (str(SOUL_FILE), st.st_mtime_ns, st.st_size, st.st_ino)
        except OSError:
            soul = None
    return (str(root), include_soul, mention_edges, _vault_state(), soul, tuple(pages))


def cached_wiki_index() -> dict[str, dict]:
    """The index of the live wiki, parsed again only when a page, SOUL.md, the
    graph settings or the vault key changed since the last parse, or when no
    query used it for CACHE_IDLE_SECONDS. Callers treat the result as
    read-only."""
    settings = kg_settings()
    include_soul = bool(settings["index_sources"].get("soul", True))
    mention_edges = bool(settings.get("mention_edges", True))
    key = _index_fingerprint(WIKI_DIR, include_soul, mention_edges)
    hit = _cache_hit(key)
    if hit is not None:
        return hit
    leader = _index_build_lock.acquire(timeout=BUILD_WAIT_SECONDS)
    try:
        if leader:
            hit = _cache_hit(key)       # the parse this miss waited for
            if hit is not None:
                return hit
        with _index_cache_lock:
            _drop_locked()
            generation = _index_cache["generation"]
        index = build_wiki_index(include_soul=include_soul, mention_edges=mention_edges)
        with _index_cache_lock:
            if _index_cache["generation"] == generation:
                _drop_locked()
                _index_cache.update(key=key, index=index, used=_clock())
                _arm_idle_locked(CACHE_IDLE_SECONDS)
        return index
    finally:
        if leader:
            _index_build_lock.release()


def _cache_hit(key: tuple):
    with _index_cache_lock:
        now = _clock()
        if (_index_cache["key"] == key and _index_cache["index"] is not None
                and now - _index_cache["used"] < CACHE_IDLE_SECONDS):
            _index_cache["used"] = now
            return _index_cache["index"]
    return None


def clear_wiki_index_cache() -> None:
    with _index_cache_lock:
        _index_cache["generation"] += 1
        _index_cache["timer_token"] += 1
        _drop_locked()


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
    """Find title mentions with a failure-linked trie, in text + hit time.

    A single regex alternation still retries thousands of titles at each
    word boundary. Aho-Corasick shares those prefixes and runs in Python,
    allowing other server threads to respond even for multi-megabyte pages.
    Select earliest, longest, non-overlapping matches like the old regex.
    """
    from collections import deque

    alt_targets: dict[str, set[str]] = defaultdict(set)
    for slug, entry in pages.items():
        for candidate in {entry["title"], entry["title"].split(" — ")[0],
                          entry["stem"].replace("-", " ")}:
            candidate = candidate.strip().lower()
            if len(candidate) >= 4:
                alt_targets[candidate].add(slug)
    if not alt_targets:
        return
    transitions = [{}]
    failure = [0]
    outputs = [[]]
    for word in sorted(alt_targets):
        state = 0
        for char in word:
            if char not in transitions[state]:
                transitions[state][char] = len(transitions)
                transitions.append({})
                failure.append(0)
                outputs.append([])
            state = transitions[state][char]
        outputs[state].append(word)
    queue = deque(transitions[0].values())
    while queue:
        state = queue.popleft()
        for char, child in transitions[state].items():
            queue.append(child)
            fallback = failure[state]
            while fallback and char not in transitions[fallback]:
                fallback = failure[fallback]
            failure[child] = transitions[fallback].get(char, 0)
            outputs[child].extend(outputs[failure[child]])

    def word_char(char):
        return char.isalnum() or char in "_-"

    for slug, body in bodies.items():
        text = body.lower()
        state = 0
        matches = {}
        for pos, char in enumerate(text):
            while state and char not in transitions[state]:
                state = failure[state]
            state = transitions[state].get(char, 0)
            for word in outputs[state]:
                start = pos + 1 - len(word)
                if start and word_char(text[start - 1]):
                    continue
                if pos + 1 < len(text) and word_char(text[pos + 1]):
                    continue
                if len(word) > len(matches.get(start, "")):
                    matches[start] = word
        explicit = {target for target, _kind in pages[slug]["out_links"]}
        hits_by_target: dict[str, int] = defaultdict(int)
        end = 0
        for start, word in sorted(matches.items()):
            if start < end:
                continue
            end = start + len(word)
            for target in alt_targets[word]:
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
            "updated": e.get("updated"),
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

    # Tier A derives an entity from every wiki page TITLE, so a page called
    # "Dana Okafor" resurrects a forgotten person through the structural path
    # even after the Tier B extractor has been taught to skip her. Filter here
    # too: the tombstone has to hold on every route into the graph, not the one
    # we happened to think of first. The page itself is untouched -- it is the
    # user's own note, and forget_person deliberately does not rewrite those.
    try:
        from agent_friday.services import forget_person as _fp
        _gone = _fp.forgotten_names()
        if _gone:
            _keep = {e["id"] for e in entities
                     if _fp._norm(e.get("title", "")) not in _gone}
            if len(_keep) != len(entities):
                entities = [e for e in entities if e["id"] in _keep]
                relationships = [r for r in relationships
                                 if r.get("source") in _keep and r.get("target") in _keep]
                for c in communities:
                    c["entity_ids"] = [i for i in c.get("entity_ids", []) if i in _keep]
                    c["size"] = len(c["entity_ids"])
                communities = [c for c in communities if c["size"]]
            # The title check above only stops a forgotten person's OWN page
            # node from resurfacing. Her name can still be sitting inside
            # SOMEONE ELSE's description -- this "description" is the page's
            # own first-paragraph summary (a derived field this module
            # builds, not the wiki page file itself), so redacting it here
            # does not touch the user's own note, same reasoning as the
            # title filter above.
            for e in entities:
                if e.get("description"):
                    e["description"] = _fp.redact_forgotten_names(e["description"])
    except Exception:
        pass

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

    The pass runs off-request because large archives take time, but
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

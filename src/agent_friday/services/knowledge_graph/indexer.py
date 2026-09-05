"""Tier B — GraphRAG semantic indexing (spec §6.2, §5.4).

Chunks the corpus (wiki bodies, conversation turns, cognitive-memory facts,
SOUL.md), extracts entities + relationships with the Microsoft-GraphRAG
prompt pipeline (vendored verbatim from graphrag-workbench under prompts/),
merges duplicate entities, detects communities (reusing Tier A's detector),
writes LLM community reports, and embeds entity text locally.

Routing rules, revised 2026-09-03 (superseding the original spec §5.4
tier-based sovereignty rule, which pinned TIER_2/3 chunks local no matter
what the user chose for this — that was a per-tier override of a per-user
choice, and it is gone: "he is not asking for a system that decides for
people, he's asking for one that does what the person picked"):
  * The indexer NEVER opens a socket. Every LLM call goes through
    model_router._generate_text, which seals cloud payloads via
    egress_gate.seal_outbound (or, when the user has separately chosen
    model_routing.unrestricted_cloud, passes them through with a ledger
    row and no other change — the same rule as everywhere else in the app).
  * indexing_mode is a strict PER-USER CHOICE, not a per-chunk decision:
    "local" (the default) always uses an installed local model — nothing
    for this pass ever reaches the network, at any sensitivity. "cloud"
    always routes through the egress-gated cloud default — what content
    actually reaches the wire is the gate's decision, uniformly, the same
    way it decides for every other cloud call in the app.
  * classify-before-extract still holds: each chunk's sensitivity is
    resolved before any LLM call, and travels with it as an INPUT to the
    egress gate's own decision — it no longer overrides the user's mode
    choice here.
  * egress_gate.gate_operational() == False disables cloud indexing
    outright; a missing/uninstalled local model disables local indexing
    outright — both checked ONCE at the top of reindex_tier_b, not
    discovered one failed chunk at a time.
  * Derived records inherit their source's sensitivity, so the store
    encrypts anything derived from TIER_2/3 sources at rest, regardless of
    which mode produced them.
"""

from __future__ import annotations

import hashlib
import json
import re
import time
from pathlib import Path
from typing import Any, Callable, Iterable, Optional

from agent_friday.core import WIKI_DIR, SOUL_FILE, _load_settings

from . import kg_settings
from . import graph_analysis
from .store import (KnowledgeGraphStore, KnowledgeGraphManifest, canonical)
from .wiki_graph import list_wiki_pages, _read, _page_sensitivity, _page_key

PROMPT_DIR = Path(__file__).parent / "prompts"

TUPLE_DELIM = "<|>"
RECORD_DELIM = "##"
COMPLETION_DELIM = "<|COMPLETE|>"
ENTITY_TYPES = "person,organization,project,tool,concept,event,place"

CHUNK_SIZE = 1200
CHUNK_OVERLAP = 100
MAX_REPORT_COMMUNITIES = 24        # cap LLM cost per index pass


def _prompt(name: str) -> str:
    return (PROMPT_DIR / f"{name}.txt").read_text(encoding="utf-8")


def _register_trusted_prompts() -> None:
    """Register the prompt templates (Friday-authored constants) with the
    egress gate so scaffolding paragraphs don't trip the classifier. User
    data is interpolated at call time and is still gated span-wise."""
    try:
        from agent_friday.services.egress_gate import register_trusted_text
        for f in PROMPT_DIR.glob("*.txt"):
            register_trusted_text(f.read_text(encoding="utf-8"))
    except Exception:
        pass


_register_trusted_prompts()


# ═══════════════════════════════════════════════════════════════
#  Corpus assembly
# ═══════════════════════════════════════════════════════════════

def _chunk_text(text: str, size: int = CHUNK_SIZE,
                overlap: int = CHUNK_OVERLAP) -> list[str]:
    if len(text) <= size:
        return [text] if text.strip() else []
    out, start = [], 0
    while start < len(text):
        out.append(text[start:start + size])
        start += size - overlap
    return out


def gather_chunks(sources: Optional[dict] = None) -> list[dict]:
    """Corpus → chunk dicts: {id, text, sensitivity, provenance, source_path}.

    Sensitivity resolution happens HERE — before any LLM ever sees the text.
    """
    settings = kg_settings()
    src_cfg = sources or settings["index_sources"]
    chunks: list[dict] = []

    if src_cfg.get("wiki", True):
        for page in list_wiki_pages():
            rel = str(page.relative_to(WIKI_DIR)).replace("\\", "/")
            text = _read(page)
            if not text or text.startswith("[vault-encrypted file"):
                continue
            sens = _page_sensitivity(rel)
            for i, part in enumerate(_chunk_text(text)):
                chunks.append({
                    "id": f"wiki:{_page_key(rel)}#{i}",
                    "text": part,
                    "sensitivity": sens,
                    "source_path": str(page),
                    "provenance": {"wiki_pages": [rel], "sensitivity": sens},
                })

    if src_cfg.get("soul", True) and SOUL_FILE.exists():
        try:
            soul = SOUL_FILE.read_text(encoding="utf-8", errors="replace")
        except OSError:
            soul = ""
        for i, part in enumerate(_chunk_text(soul)):
            chunks.append({
                "id": f"soul:#{i}", "text": part, "sensitivity": 1,
                "source_path": str(SOUL_FILE),
                "provenance": {"wiki_pages": ["SOUL.md"], "sensitivity": 1},
            })

    if src_cfg.get("cognitive", True):
        for c in _cognitive_chunks():
            chunks.append(c)

    if src_cfg.get("conversations", True):
        for c in _conversation_chunks():
            chunks.append(c)

    return chunks


def _classify_free_text(text: str) -> int:
    """Sensitivity for non-wiki text. Falls back to TIER_2 (private) when the
    classifier is unavailable — memories default to not-for-cloud."""
    try:
        from agent_friday.services.sensitivity_classifier import classify, Tier
        return int(classify(text, default=Tier.PRIVATE))
    except Exception:
        return 2


def _cognitive_chunks() -> Iterable[dict]:
    try:
        from agent_friday.cognitive_memory import CognitiveMemory
        mem = CognitiveMemory()
        mem_dir = Path(getattr(mem, "memory_dir"))
    except Exception as e:
        # Total failure here is indistinguishable from "no memories yet"
        # unless it is said out loud — see _conversation_chunks below, where
        # the same shape of swallow hid a broken source for good.
        print(f"  [KG] cognitive memory source failed, no cognitive facts "
              f"indexed: {e}")
        return []
    out = []
    if not mem_dir.exists():
        return []
    for f in sorted(mem_dir.glob("*.json")):
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if data.get("quarantined"):
            continue
        content = str(data.get("content") or "")
        if not content.strip():
            continue
        sens = _classify_free_text(content)
        out.append({
            "id": f"cog:{f.stem}", "text": content, "sensitivity": sens,
            "source_path": str(f),
            "provenance": {"cognitive_keys": [f.stem], "sensitivity": sens},
        })
    return out


def _conversation_chunks(limit: int = 400) -> Iterable[dict]:
    """Chat turns from `ConversationMemory.recent()`.

    `recent()` returns dicts shaped {text, role, timestamp, date, session_id,
    topic_keywords} — no `turn_id` (it never surfaces Chroma's internal doc
    id). That is fine: the id below falls back to a hash of the content,
    which is stable across re-indexes and gives the same chunk id for the
    same turn every time, keeping "delta" mode's dedup working.

    Bug history: this used to call a `recent_turns(limit=...)` method that
    `ConversationMemory` has never had, and read a `content` field that
    `recent()` has never returned either. The bare `except Exception: return
    []` below turned that AttributeError into an empty list indistinguishable
    from "no conversations yet" — so the conversation source has never
    actually indexed a single turn. Fixed by calling the real method with its
    real field name, and by no longer swallowing the failure silently.
    """
    try:
        from agent_friday.conversation_memory import ConversationMemory
        cm = ConversationMemory()
        if not cm.available():
            return []
        turns = cm.recent(n=limit)
    except Exception as e:
        print(f"  [KG] conversation source failed, no conversation turns "
              f"indexed: {e}")
        return []
    out = []
    for t in turns or []:
        content = str(t.get("text") or "")
        if len(content.strip()) < 40:      # skip trivia
            continue
        sens = _classify_free_text(content)
        tid = t.get("turn_id") or hashlib.sha1(content.encode()).hexdigest()[:10]
        out.append({
            "id": f"conv:{tid}", "text": content[:CHUNK_SIZE],
            "sensitivity": sens, "source_path": f"conversation:{tid}",
            "provenance": {"conversations": [str(tid)], "sensitivity": sens},
        })
    return out


# ═══════════════════════════════════════════════════════════════
#  LLM routing (mode + sensitivity → model choice)
# ═══════════════════════════════════════════════════════════════

class CloudIndexingDisabled(RuntimeError):
    pass


class LocalIndexingUnavailable(RuntimeError):
    """Raised when mode="local" is chosen but nothing installed can run
    extraction. Distinct from CloudIndexingDisabled so a caller (or a log)
    can tell which side of the choice failed."""
    pass


def _local_model() -> str:
    """The user's PREFERRED local model name — a name, not a promise it is
    installed. `_available_local_model()` below is what actually checks.

    The fallback was `gemma3:4b` in both branches — defect H3 again. Indexing
    does not itself call tools, so this one was harmless in practice, which is
    exactly why it survived: a wrong default that never visibly breaks is the
    kind that is still there years later, waiting to be copied somewhere it
    matters. Derived from the ladder now, like every other default.
    """
    from agent_friday.services.model_plan import FLOOR_MODEL
    try:
        mr = (_load_settings() or {}).get("model_routing") or {}
        return mr.get("local_model") or FLOOR_MODEL
    except Exception:
        return FLOOR_MODEL


def _available_local_model() -> Optional[str]:
    """The local model Tier B will actually use, or None if nothing
    installed can do the job.

    2026-09-03, item #1 of the "default is broken for everyone" instruction:
    `_local_model()` names a model with no check that it is INSTALLED. On a
    fresh install where the user picked Claude-only (no local model ever
    downloaded) or a different rung of the ladder, every extraction call
    failed against a model that was never on the machine — one call at a
    time, 348 times on the reference machine, before anyone found out. This
    asks Ollama what is actually there.

    Preference order: the configured/floor preference (`_local_model()`), if
    it is installed and tool-capable; otherwise the smallest installed
    tool-capable model, by the same ladder `model_plan.BRAIN_MODELS` already
    orders by footprint; otherwise None — meaningfully different from a
    fallback string, because "no model" must be a distinguishable state a
    caller can act on, not silently become gemma3:4b (which cannot call
    tools and was never a real answer either).
    """
    try:
        from agent_friday.services.model_plan import TOOL_CAPABLE_IDS, BRAIN_MODELS
        from agent_friday.routing.ollama_manager import get_manager
        installed = {m.get("name") or m.get("model")
                    for m in (get_manager().list_models() or [])}
    except Exception:
        return None
    capable_installed = installed & TOOL_CAPABLE_IDS
    if not capable_installed:
        return None
    preferred = _local_model()
    if preferred in capable_installed:
        return preferred
    for m in BRAIN_MODELS:
        if m["id"] in capable_installed:
            return m["id"]
    return None


def _resolve_model(sensitivity: int, mode: str) -> tuple[Optional[str], bool]:
    """Return (model, is_local_pin) for a chunk.

    A strict PER-USER CHOICE (2026-09-03), not a per-tier override: earlier
    versions of this function pinned "sensitive" chunks local even when the
    user had chosen "cloud" for this. "He is not asking for a system that
    decides for people, he's asking for one that does what the person
    picked" — so that second-guessing is gone. `sensitivity` stays a
    parameter for call-site compatibility; it no longer changes the
    decision here. What content is actually safe to send is the egress
    gate's job, the same as every other cloud call in the app: tier-based
    redaction normally, or a full pass-through under the separate,
    explicitly-chosen `model_routing.unrestricted_cloud` — this function
    only decides ROUTING (local vs. cloud), never content.

    mode "local" → the model resolved by `_available_local_model()`
                    (raises LocalIndexingUnavailable if nothing qualifies —
                    see the fail-fast check at the top of reindex_tier_b,
                    which is where this is actually meant to be caught;
                    this per-chunk path is the safety net, not the primary
                    check, so a model that disappears mid-run cannot fall
                    through to cloud silently).
    mode "cloud" → the routed default; requires a healthy egress gate.
    """
    if mode == "cloud":
        try:
            from agent_friday.services.egress_gate import gate_operational
            if not gate_operational():
                raise CloudIndexingDisabled(
                    "egress gate self-test failed — cloud indexing refused")
        except CloudIndexingDisabled:
            raise
        except Exception:
            raise CloudIndexingDisabled("egress gate unavailable")
        return None, False                 # routed default (egress-gated)
    model = _available_local_model()
    if model is None:
        raise LocalIndexingUnavailable(
            "local indexing selected, but no installed model can run "
            "extraction (checked against the tool-capable ladder)")
    return model, True


def _llm(messages, system: Optional[str], sensitivity: int, mode: str,
         orb_label: Optional[str] = None) -> str:
    """Single LLM entry point for the whole indexer (spec §5.4)."""
    model, _pinned = _resolve_model(sensitivity, mode)
    from agent_friday.services.model_router import _generate_text
    return _generate_text(messages, system=system, model=model,
                          max_tokens=4096, workspace="research",
                          orb_label=orb_label)


# ═══════════════════════════════════════════════════════════════
#  Extraction parsing
# ═══════════════════════════════════════════════════════════════

_ENT_RE = re.compile(r'\("entity"\s*' + re.escape(TUPLE_DELIM)
                     + r'(.*?)\)', re.DOTALL)
_REL_RE = re.compile(r'\("relationship"\s*' + re.escape(TUPLE_DELIM)
                     + r'(.*?)\)', re.DOTALL)


def parse_extraction(raw: str) -> tuple[list[dict], list[dict]]:
    """Parse the tuple-format output of the extract_graph prompt."""
    ents, rels = [], []
    for m in _ENT_RE.finditer(raw or ""):
        parts = [p.strip() for p in m.group(1).split(TUPLE_DELIM)]
        if len(parts) >= 3 and parts[0]:
            # Local models decorate types ("<tool>", "TYPE: person") — keep
            # letters only so downstream color/filter logic stays clean.
            etype = re.sub(r"[^a-z]", "", parts[1].strip().lower())
            ents.append({"title": parts[0].strip().upper(),
                         "type": etype or "concept",
                         "description": parts[2].strip()})
    for m in _REL_RE.finditer(raw or ""):
        parts = [p.strip() for p in m.group(1).split(TUPLE_DELIM)]
        if len(parts) >= 3 and parts[0] and parts[1]:
            try:
                weight = float(parts[3]) / 10.0 if len(parts) > 3 else 0.5
            except ValueError:
                weight = 0.5
            rels.append({"source": parts[0].strip().upper(),
                         "target": parts[1].strip().upper(),
                         "description": parts[2].strip(),
                         "weight": max(0.1, min(weight, 1.0))})
    return ents, rels


def _ent_id(title: str) -> str:
    return "ent_" + hashlib.sha1(title.strip().upper().encode()).hexdigest()[:12]


# ═══════════════════════════════════════════════════════════════
#  Index pass
# ═══════════════════════════════════════════════════════════════

def reindex_tier_b(store: Optional[KnowledgeGraphStore] = None,
                   mode: str = "delta",
                   progress: Optional[Callable[[str], None]] = None,
                   llm=None) -> dict[str, Any]:
    """Run the Tier B semantic index pass.

    mode "delta" re-extracts only chunks whose source changed since the
    manifest entry; "full" re-extracts everything. ``llm`` is injectable for
    tests; defaults to the gated ``_llm``.
    """
    t0 = time.time()
    store = store or KnowledgeGraphStore()
    settings = kg_settings()
    indexing_mode = str(settings.get("indexing_mode", "local"))
    call = llm or _llm
    say = progress or (lambda msg: None)

    chunks = gather_chunks()
    say(f"corpus: {len(chunks)} chunks")

    # Fail FAST, not 348 times. Item #1 of the 2026-09-03 instruction: on a
    # machine with no usable local model, the old code discovered that one
    # doomed extraction call at a time, every single chunk, before ever
    # saying so — the same shape of silent failure as the conversation-
    # source bug fixed the same day, one layer up. Checked once, here,
    # before any chunk is attempted.
    #
    # Gated on `call is _llm`: an injected `llm` (tests, or a caller
    # bringing its own extraction function) owns its own backend and does
    # not need a real Ollama or a real egress gate to exist -- that's the
    # whole point of the injection point. Without this gate, every "local"
    # unit test silently depends on whatever happens to be installed on the
    # machine running pytest, which is exactly the kind of environment-
    # dependent pass this file's own docstring ("nothing leaves the
    # process") promises isn't happening.
    if call is _llm and indexing_mode == "local" and _available_local_model() is None:
        from agent_friday.services.model_plan import FLOOR_MODEL
        say(f"TIER B CANNOT RUN: indexing_mode is 'local' but no installed "
            f"model can run extraction. Pull one (e.g. 'ollama pull "
            f"{FLOOR_MODEL}') or switch Settings -> Knowledge Graph to Cloud.")
        return {
            "tier": "B", "mode": indexing_mode, "chunks": len(chunks),
            "extracted": 0, "extract_failures": 0, "first_failure": None,
            "degraded": True, "skipped_tier3": 0, "entities": 0,
            "relationships": 0, "communities": 0, "reports": 0,
            "embedded": 0, "took_ms": int((time.time() - t0) * 1000),
            "error": "no_local_model",
            "message": f"Local indexing is selected, but no locally-installed "
                       f"model can run extraction. Run 'ollama pull "
                       f"{FLOOR_MODEL}' (or switch Settings -> Knowledge "
                       f"Graph to Cloud) and try again.",
        }
    if call is _llm and indexing_mode == "cloud":
        try:
            from agent_friday.services.egress_gate import gate_operational
            _cloud_ok = gate_operational()
        except Exception:
            _cloud_ok = False
        if not _cloud_ok:
            say("TIER B CANNOT RUN: indexing_mode is 'cloud' but the egress "
                "gate self-test failed — cloud indexing refused rather than "
                "risk an unsealed send.")
            return {
                "tier": "B", "mode": indexing_mode, "chunks": len(chunks),
                "extracted": 0, "extract_failures": 0, "first_failure": None,
                "degraded": True, "skipped_tier3": 0, "entities": 0,
                "relationships": 0, "communities": 0, "reports": 0,
                "embedded": 0, "took_ms": int((time.time() - t0) * 1000),
                "error": "egress_gate_unavailable",
                "message": "Cloud indexing is selected, but the egress "
                           "gate's own self-test failed, so no chunk was "
                           "sent. Fix the gate or switch to Local.",
            }

    manifest = KnowledgeGraphManifest(base_dir=store.base)
    if mode == "delta":
        file_paths = sorted({c["source_path"] for c in chunks
                             if not c["source_path"].startswith("conversation:")})
        d = manifest.delta(file_paths)
        stale = set(d["new"]) | set(d["changed"])
        todo = [c for c in chunks
                if (c["source_path"].startswith("conversation:")
                    and c["source_path"] not in manifest.sources)  # turns are immutable
                or (not c["source_path"].startswith("conversation:")
                    and canonical(c["source_path"]) in stale)]
    else:
        todo = list(chunks)
    say(f"to extract: {len(todo)} chunks (mode={mode})")

    # ── extract ──────────────────────────────────────────────
    extract_tpl = _prompt("extract_graph")
    entities: dict[str, dict] = {e["id"]: e for e in store.load("entities")
                                 if e.get("tier") == "B"} if mode == "delta" else {}
    relationships: dict[str, dict] = {r["id"]: r for r in
                                      store.load("relationships")
                                      if r.get("tier") == "B"} if mode == "delta" else {}
    skipped_tier3 = 0
    extracted = 0
    extract_failures = 0
    first_failure = None

    for chunk in todo:
        sens = chunk["sensitivity"]
        # `model`/`pinned` themselves are unused here -- this call exists
        # purely for its exception, as a mid-run safety net behind the
        # top-level fail-fast (a model disappearing or the gate going down
        # partway through a run must not fall through to cloud silently).
        # `_llm` re-resolves for real inside itself; an injected `llm`
        # (tests, or a caller bringing its own extraction function) owns
        # its own backend and doesn't need this machine's Ollama or gate
        # to exist, so the safety net only runs for the real default path
        # -- same reasoning as the top-level checks above.
        if call is _llm:
            try:
                _resolve_model(sens, indexing_mode)
            except (CloudIndexingDisabled, LocalIndexingUnavailable) as e:
                # No per-tier degrade-to-local here anymore: the mode is the
                # user's choice, not something this loop overrides chunk by
                # chunk. The top-level check in reindex_tier_b already
                # verified the chosen path was viable before this loop
                # started; a mid-run failure here is the rare case (gate
                # went down, model got uninstalled) and is counted like any
                # other failure, not silently routed around.
                say(f"resolve failed for {chunk['id']}: {e}")
                extract_failures += 1
                if first_failure is None:
                    first_failure = f"{type(e).__name__}: {e}"
                continue
        prompt = (extract_tpl
                  .replace("{entity_types}", ENTITY_TYPES)
                  .replace("{tuple_delimiter}", TUPLE_DELIM)
                  .replace("{record_delimiter}", RECORD_DELIM)
                  .replace("{completion_delimiter}", COMPLETION_DELIM)
                  .replace("{input_text}", chunk["text"]))
        try:
            raw = call([{"role": "user", "content": prompt}], None, sens,
                       indexing_mode, orb_label="🧠 indexing knowledge")
        except Exception as e:
            say(f"extract failed for {chunk['id']}: {e}")
            extract_failures += 1
            if first_failure is None:
                first_failure = f"{type(e).__name__}: {e}"
            continue
        ents, rels = parse_extraction(raw)
        extracted += 1
        for e in ents:
            eid = _ent_id(e["title"])
            cur = entities.get(eid)
            if cur is None:
                entities[eid] = {
                    "id": eid, "title": e["title"].title(),
                    "type": e["type"], "description": e["description"],
                    "descriptions": [e["description"]],
                    "degree": 0, "frequency": 1, "level": 0, "tier": "B",
                    "provenance": dict(chunk["provenance"]),
                }
            else:
                cur["frequency"] += 1
                cur.setdefault("descriptions", [cur.get("description", "")])
                if e["description"] not in cur["descriptions"]:
                    cur["descriptions"].append(e["description"])
                prov = cur["provenance"]
                for k, v in chunk["provenance"].items():
                    if k == "sensitivity":
                        prov["sensitivity"] = max(prov.get("sensitivity", 1), v)
                    else:
                        prov[k] = sorted(set(prov.get(k, [])) | set(v))
        for r in rels:
            sid, tid = _ent_id(r["source"]), _ent_id(r["target"])
            if sid == tid:
                continue
            rid = f"rel_{sid[4:]}_{tid[4:]}"
            if rid not in relationships:
                relationships[rid] = {
                    "id": rid, "source": sid, "target": tid,
                    "description": r["description"], "weight": r["weight"],
                    "tier": "B", "provenance": dict(chunk["provenance"]),
                }
        manifest.record(chunk["source_path"], kind="tierb",
                        produced=[chunk["id"]])

    # ASCII only: progress strings reach cp1252 Windows consoles via callbacks.
    say(f"extracted {extracted} chunks -> {len(entities)} entities, "
        f"{len(relationships)} relationships ({skipped_tier3} TIER_3 skipped)")

    # ── people the user has asked Friday to forget ───────────
    # BEFORE the dangling-relationship sweep below, so removing them takes
    # their edges with it for free. Without this the nightly reindex reads the
    # same wiki pages and conversation turns and rebuilds every person a user
    # deleted -- a delete button that works until the user goes to bed.
    # services/forget_person.py owns the list.
    try:
        from agent_friday.services import forget_person as _fp
        _gone = _fp.forgotten_names()
        if _gone:
            _before = len(entities)
            entities = {eid: e for eid, e in entities.items()
                        if _fp._norm(e.get("title", "")) not in _gone}
            if len(entities) != _before:
                say(f"excluded {_before - len(entities)} forgotten "
                    f"{'person' if _before - len(entities) == 1 else 'people'}")
    except Exception as _fe:
        # Never let this fail an index run -- but say so, because silently
        # re-deriving a deleted person is the failure that matters.
        say(f"forget-list check failed, people may be re-derived: {_fe}")

    # drop dangling relationships
    relationships = {rid: r for rid, r in relationships.items()
                     if r["source"] in entities and r["target"] in entities}

    # ── summarize merged descriptions ────────────────────────
    sum_tpl = _prompt("summarize_descriptions")
    for e in entities.values():
        descs = e.get("descriptions") or []
        if len(descs) > 1 and len(" ".join(descs)) > 300:
            prompt = (sum_tpl.replace("{entity_name}", e["title"])
                      .replace("{description_list}", json.dumps(descs))
                      .replace("{max_length}", "120"))
            try:
                e["description"] = call(
                    [{"role": "user", "content": prompt}], None,
                    e["provenance"].get("sensitivity", 1), indexing_mode,
                    orb_label="🧠 merging knowledge").strip()
            except Exception:
                e["description"] = " ".join(descs)[:400]
        elif descs:
            e["description"] = descs[0]
        e.pop("descriptions", None)

    # degree
    for e in entities.values():
        e["degree"] = 0
    for r in relationships.values():
        entities[r["source"]]["degree"] += 1
        entities[r["target"]]["degree"] += 1

    # ── communities (reuse Tier A detector) ──────────────────
    outgoing = {eid: [] for eid in entities}
    for r in relationships.values():
        outgoing[r["source"]].append(r["target"])
    comms = [c for c in graph_analysis.detect_communities(outgoing) if len(c) > 1]
    communities, reports = [], []
    report_tpl = _prompt("community_report_graph")
    for i, comm in enumerate(comms):
        members = sorted(comm)
        cid = f"bcom_{i}"
        comm_sens = max(entities[m]["provenance"].get("sensitivity", 1)
                        for m in members)
        crels = [r for r in relationships.values()
                 if r["source"] in comm and r["target"] in comm]
        for m in members:
            entities[m]["community"] = f"B{i}"
        communities.append({
            "id": cid, "community": f"B{i}", "level": 0, "parent": None,
            "children": [], "tier": "B",
            "title": f"cluster B{i}",
            "entity_ids": members,
            "relationship_ids": [r["id"] for r in crels],
            "size": len(members),
            "provenance": {"sensitivity": comm_sens},
        })
        if i < MAX_REPORT_COMMUNITIES:
            ctx = "Entities:\n" + "\n".join(
                f"- {entities[m]['title']} ({entities[m]['type']}): "
                f"{entities[m]['description'][:200]}" for m in members[:40])
            ctx += "\nRelationships:\n" + "\n".join(
                f"- {entities[r['source']]['title']} -> "
                f"{entities[r['target']]['title']}: {r['description'][:150]}"
                for r in crels[:60])
            prompt = (report_tpl.replace("{input_text}", ctx)
                      .replace("{max_report_length}", "600"))
            try:
                raw = call([{"role": "user", "content": prompt}], None,
                           comm_sens, indexing_mode,
                           orb_label="🧠 writing community report")
                rep = _parse_report(raw)
            except Exception:
                rep = None
            if rep:
                reports.append({
                    "id": f"brep_{i}", "community": f"B{i}", "level": 0,
                    "tier": "B",
                    "title": rep.get("title", f"cluster B{i}"),
                    "summary": rep.get("summary", ""),
                    "full_content": rep.get("full_content", ""),
                    "rank": float(rep.get("rating", 5.0) or 5.0),
                    "provenance": {"sensitivity": comm_sens},
                })
                communities[-1]["title"] = rep.get("title", communities[-1]["title"])

    # entities not in any multi-member community
    for e in entities.values():
        e.setdefault("community", "B_misc")

    # ── embeddings (local, optional) ─────────────────────────
    embedded = _embed_entities(list(entities.values()), say)

    # ── persist: merge with Tier A records ───────────────────
    tier_a_entities = [e for e in store.load("entities")
                       if e.get("tier") != "B"]
    tier_a_rels = [r for r in store.load("relationships")
                   if r.get("tier") != "B"]
    tier_a_comms = [c for c in store.load("communities")
                    if c.get("tier") != "B"]

    linked = _link_to_pages(entities, tier_a_entities)

    all_entities = tier_a_entities + list(entities.values())
    all_rels = tier_a_rels + list(relationships.values()) + linked
    all_comms = tier_a_comms + communities

    from . import layout as layout_mod
    layout_meta = layout_mod.compute_layout(
        all_entities, all_rels, all_comms,
        seed=int(settings.get("layout_seed", 1337)))

    store.save("entities", all_entities)
    store.save("relationships", all_rels)
    store.save("communities", all_comms)
    store.save("community_reports", reports)
    store.save_layout(layout_meta)
    manifest.save()

    # Tier B used to fail SILENTLY and completely: `indexing_mode` defaulted
    # to a local model no install actually delivered, every call raised, was
    # caught above, and the chunk was skipped -- Tier A still worked, so the
    # run reported success and produced a structural-only graph. The
    # no-viable-model case is now caught once, up front (see the fail-fast
    # check near the top of this function); this remaining branch is for
    # the rarer case where the chosen path WAS viable but every single call
    # still failed for some other reason (a transient outage, a bad prompt
    # template edit, etc.) -- still worth saying out loud, not the same
    # failure as the one that motivated this whole rewrite.
    degraded = bool(chunks) and extracted == 0 and extract_failures > 0
    if degraded:
        say("TIER B PRODUCED NOTHING: all %d extraction calls failed (%s). "
            "The map has structural links only, not the semantic layer. "
            "indexing_mode=%s." % (extract_failures, first_failure, indexing_mode))
    elif extract_failures:
        say("tier B: %d of %d chunks failed extraction"
            % (extract_failures, extract_failures + extracted))

    info = {
        "tier": "B", "mode": indexing_mode,
        "chunks": len(chunks), "extracted": extracted,
        "extract_failures": extract_failures,
        "first_failure": first_failure,
        "degraded": degraded,
        "skipped_tier3": skipped_tier3,
        "entities": len(entities), "relationships": len(relationships),
        "communities": len(communities), "reports": len(reports),
        "embedded": embedded,
        "took_ms": int((time.time() - t0) * 1000),
    }
    say(f"tier B done in {info['took_ms']}ms")
    try:
        from agent_friday.routes.knowledge_graph import emit_kg_event
        emit_kg_event("reindexed", info)
    except Exception:
        pass
    return info


def _parse_report(raw: str) -> Optional[dict]:
    """Community-report prompt returns JSON (sometimes fenced)."""
    if not raw:
        return None
    m = re.search(r"\{.*\}", raw, re.DOTALL)
    if not m:
        return None
    try:
        data = json.loads(m.group(0))
    except ValueError:
        return None
    findings = data.get("findings") or []
    full = data.get("summary", "") + "\n\n" + "\n".join(
        f"- {f.get('summary', '')}: {f.get('explanation', '')}"
        if isinstance(f, dict) else f"- {f}" for f in findings)
    return {"title": data.get("title", ""), "summary": data.get("summary", ""),
            "full_content": full.strip(), "rating": data.get("rating", 5.0)}


def _link_to_pages(entities: dict[str, dict],
                   tier_a_entities: list[dict]) -> list[dict]:
    """Weave extracted entities into the page galaxy: if an entity's
    provenance names a wiki page that exists as a Tier A node, add a
    lightweight 'appears in' edge so the two tiers render as one graph."""
    page_by_path = {}
    for e in tier_a_entities:
        for p in (e.get("provenance") or {}).get("wiki_pages", []):
            page_by_path[p] = e["id"]
    out = []
    for e in entities.values():
        for p in (e.get("provenance") or {}).get("wiki_pages", []):
            pid = page_by_path.get(p)
            if pid:
                out.append({
                    "id": f"rel_{e['id'][4:]}_{hashlib.sha1(pid.encode()).hexdigest()[:8]}",
                    "source": e["id"], "target": pid,
                    "description": "appears in", "weight": 0.3, "tier": "B",
                    "provenance": dict(e.get("provenance") or {}),
                })
    return out


def _embed_entities(entities: list[dict], say) -> int:
    """Embed entity title+description into a dedicated local ChromaDB
    collection (all-MiniLM-L6-v2 — on-device, never leaves the machine)."""
    try:
        from agent_friday.conversation_memory import ConversationMemory
        cm = ConversationMemory()
        if not cm._ensure():
            return 0
        client = cm._client
        coll = client.get_or_create_collection("knowledge-graph")
        ids, docs, metas = [], [], []
        for e in entities:
            ids.append(e["id"])
            docs.append(f"{e['title']}: {e.get('description', '')[:500]}")
            metas.append({"type": e.get("type", ""),
                          "community": str(e.get("community", ""))})
        if ids:
            coll.upsert(ids=ids, documents=docs, metadatas=metas)
        return len(ids)
    except Exception as e:
        say(f"embeddings unavailable: {e}")
        return 0

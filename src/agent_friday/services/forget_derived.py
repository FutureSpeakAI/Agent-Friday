"""Forget reaches what Friday derived from the owner's conversations.

North star NS-15.7-2 ("forget traverses derived indexes, graph entities,
summaries, caches and proposals"), NS-15.7-3 (a deletion receipt listing what
was removed, what could not be removed, and what is retained for integrity)
and NS-36.2-9 (deletion traversal).

Forget-person (services/forget_person) already removed the person's records
and every store registered with services/forget_registry. It did not reach
what was made from the conversations the owner had about them, so after a
forget Friday still recalled "Dana said the contract is late", the day's
summary still opened the next conversation with it, and the memory card
still carried a line about Dana. This module registers those stores:

``conversation_recall``  the recall indexes: Friday's vector memory
                         (ConversationMemory, ChromaDB) and conversation search
                         (services/conversation_fts). The turns that name the
                         person are DELETED from both, so they are never
                         recalled or searched again; conversation search keeps
                         their ids so a rebuild from the transcripts does not
                         bring them back. The in-memory review buffer
                         (memory_review) drops them too.
``conversation_graph``   the knowledge graph's nodes extracted from those turns:
                         retraction is requested through memory_derived's queue
                         (indexer.purge_conversation_turns, the rules a
                         correction uses). The graph reads its conversation
                         source from the vector memory just cleaned, so its
                         next pass does not re-derive them; nodes titled with
                         the person's name went in forget_person's step 3.
``day_summaries``        a day summary that names the person, or was made from
                         a removed turn, is removed and queued to be written
                         again (memory_derived.mark_summaries). The summary job
                         reads the day's turns from the vector memory (model_
                         router._generate_session_summary), not from the
                         transcript, so the summary written again cannot bring
                         the person back.
``memory_card``          lines on the memory card (core_memory ME.md / FRIDAY.md)
                         that name the person; the owner's forget is the owner's
                         action, so they go at once, with every journal copy.
``memory_card_pending``  suggestions in the card's review queue that name them.
``action_receipts``      nothing is removed: listed as retained.

WHAT IS KEPT, AND WHY (the receipt says so, per store)
------------------------------------------------------
* The conversation transcript (and the chat mirror built from it) is the
  owner's own record of what they said; forget-person's standing policy is
  that Friday does not rewrite it. The receipt counts the messages that still
  name the person, as "retained". The owner can delete any one message from
  the history (conversation_fts.delete_message).
* Action receipts (actions/receipts.jsonl) are the append-only record of what
  Friday did on the owner's behalf; they are counted, not changed.

Matching in free text is forget_registry.subject_matcher: case-sensitive,
whole-word (forgetting "Will" never removes "I will"). Names shorter than three
characters are not matched and the receipt lists them as not removable.

Turns said AFTER the forget are the owner's new words and are indexed as
usual. Lifting the tombstone (unforget) does not restore anything removed here.
"""
from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

_log = logging.getLogger(__name__)

_TRANSCRIPT_REASON = ("your own conversation history, in your words: Friday does not rewrite it (you can "
                      "delete any message from History). It is no longer recalled, searched or summarised")
_RECEIPTS_REASON = ("the append-only record of actions Friday took for you; it stays as the evidence of "
                    "what was done")


def _matcher(names, emails):
    from agent_friday.services import forget_registry as fr
    return fr.subject_matcher(names, emails)


def _short(m) -> List[Dict[str, str]]:
    return [{"what": "the name %r in free text" % n,
             "reason": "shorter than %d characters: too likely to match ordinary words" % _min()}
            for n in getattr(m, "skipped", [])]


def _min() -> int:
    from agent_friday.services import forget_registry as fr
    return fr.MIN_NAME


# ── reading the stores ──────────────────────────────────────────────────────

def _memory() -> Tuple[Optional[object], str]:
    """(the conversation memory, "") or (None, why it was not checked). A store
    that was never created holds nothing: (None, "")."""
    try:
        from agent_friday.services.model_router import _get_conversation_memory
        mem = _get_conversation_memory()
    except Exception as e:
        return None, "the conversation memory could not be loaded (%s)" % type(e).__name__
    if getattr(mem, "_collection", None) is None:
        d = Path(getattr(mem, "persist_dir", "") or "")
        if not str(d) or not d.exists() or not any(d.iterdir()):
            return None, ""
    if not mem.available():
        return None, "the conversation memory could not be opened, so it was not checked"
    return mem, ""


def _transcript_hits(m) -> List[Tuple[str, dict]]:
    """(conversation id, message) for every stored transcript message naming them."""
    out = []
    try:
        from agent_friday.services import conversations as cv
        if not cv._root().exists():
            return out
        for conv in cv.list_all(include_archived=True):
            cid = conv.get("id")
            if not cid:
                continue
            for msg in cv._stored_messages(cid):
                if isinstance(msg, dict) and m(msg.get("text")):
                    out.append((cid, msg))
    except Exception as e:
        _log.warning("forget: transcripts not read: %s", e)
    return out


def _day_of(ts) -> str:
    try:
        return datetime.fromtimestamp(float(ts)).strftime("%Y-%m-%d")
    except (TypeError, ValueError, OSError):
        return ""


def _summary_days_naming(m) -> List[str]:
    from agent_friday.services import memory_derived as md
    from agent_friday.services import model_router as mr
    d = Path(mr.SESSION_SUMMARY_DIR)
    if not d.exists():
        return []
    out = []
    for p in sorted(d.glob("*.md")):
        if not md._valid_day(p.stem):
            continue
        try:
            if m(p.read_text(encoding="utf-8")):
                out.append(p.stem)
        except OSError:
            continue
    return out


def _summary_exists(day: str) -> bool:
    from agent_friday.services import model_router as mr
    try:
        return mr._session_summary_path(day, "md").exists()
    except Exception:
        return False


def _receipts_naming(m) -> int:
    try:
        from agent_friday.services import action_journal as aj
        p = aj._path()
        if not p.exists():
            return 0
        n = 0
        with open(p, "r", encoding="utf-8", errors="ignore") as fh:
            for line in fh:
                if m(line):
                    n += 1
        return n
    except Exception:
        return 0


# ── the stores ──────────────────────────────────────────────────────────────

def recall_find(names, emails) -> int:
    m = _matcher(names, emails)
    if not m:
        return 0
    ids = set()
    mem, _why = _memory()
    if mem is not None:
        ids |= {str(r["id"]) for r in (mem.matching(m) or [])}
    try:
        from agent_friday.services import conversation_fts as fts
        ids |= set(fts.forget_turns(m, apply=False)["mids"])
    except Exception:
        pass
    return len(ids)


def recall_purge(names, emails) -> Dict[str, Any]:
    """The recall indexes, then the graph and the day summaries made from them
    (in that order: the summaries are written again from the cleaned index)."""
    m = _matcher(names, emails)
    not_removable = _short(m)
    removed_ids, texts, days = set(), [], set()
    transcript = _transcript_hits(m) if m else []

    # 1. Friday's vector memory.
    if m:
        mem, why = _memory()
        if why:
            not_removable.append({"what": "conversation memory (vector recall)", "reason": why})
        if mem is not None:
            hits = mem.matching(m)
            if hits is None:
                not_removable.append({"what": "conversation memory (vector recall)",
                                      "reason": "it could not be read, so it was not checked"})
            elif hits:
                mem.delete(ids=[h["id"] for h in hits])
                removed_ids |= {str(h["id"]) for h in hits}
                texts += [h["text"] for h in hits]
                days |= {str(h.get("date") or "")[:10] for h in hits}

    # 2. Conversation search; the transcript ids are kept out of any rebuild.
    if m:
        try:
            from agent_friday.services import conversation_fts as fts
            got = fts.forget_turns(m, mids=[str(msg.get("id")) for _c, msg in transcript if msg.get("id")])
            removed_ids |= set(got["mids"])
            days |= set(got["dates"])
        except Exception as e:
            not_removable.append({"what": "conversation search", "reason": "it could not be updated (%s)"
                                  % type(e).__name__})

    # The in-memory review buffer (what the next periodic review would read).
    try:
        from agent_friday.services import memory_review
        for t in texts + [str(msg.get("text") or "") for _c, msg in transcript]:
            memory_review.forget_text(t)
    except Exception:
        pass

    # 3. The knowledge graph's nodes made from those turns.
    graph = {"store": "conversation_graph", "removed": 0, "kind": "conversation turns retracted from the "
             "knowledge graph", "not_removable": [], "retained": []}
    turn_texts = list(dict.fromkeys(
        t for t in texts + [str(msg.get("text") or "") for _c, msg in transcript] if t.strip()))
    if turn_texts:
        try:
            from agent_friday.services import memory_derived as md
            out = md.queue_graph_retraction(turn_texts) or {}
            graph["removed"] = len(md._turn_ids(turn_texts))
            if out.get("deferred"):
                graph["kind"] += " (queued: applied when the running graph pass saves)"
        except Exception as e:
            graph["not_removable"].append({"what": "knowledge graph", "reason": "retraction could not be "
                                           "queued (%s)" % type(e).__name__})

    # 4. Day summaries that name them, or were made from a removed turn.
    summaries = {"store": "day_summaries", "removed": 0, "kind": "day summaries removed and queued to be "
                 "written again", "not_removable": [], "retained": []}
    if m:
        try:
            from agent_friday.services import memory_derived as md
            days |= {_day_of(msg.get("ts")) for _c, msg in transcript}
            want = set(_summary_days_naming(m)) | {d for d in days if md._valid_day(d) and _summary_exists(d)}
            summaries["removed"] = len(md.mark_summaries(want))
        except Exception as e:
            summaries["not_removable"].append({"what": "day summaries", "reason": "they could not be removed "
                                               "(%s)" % type(e).__name__})

    retained = []
    if transcript:
        retained.append({"what": "messages in your conversation history", "count": len(transcript),
                         "reason": _TRANSCRIPT_REASON})
    return {"removed": len(removed_ids), "kind": "conversation turns removed from recall and search",
            "not_removable": not_removable, "retained": retained, "also": [graph, summaries]}


def card_find(names, emails) -> int:
    m = _matcher(names, emails)
    if not m:
        return 0
    from agent_friday.services import core_memory
    c = core_memory.forget_matching(m, apply=False)
    return c["lines"] + c["pending"]


def card_purge(names, emails) -> Dict[str, Any]:
    m = _matcher(names, emails)
    from agent_friday.services import core_memory
    c = core_memory.forget_matching(m) if m else {"lines": 0, "pending": 0, "journal": 0}
    return {"removed": c["lines"], "kind": "memory card lines", "not_removable": _short(m),
            "also": [{"store": "memory_card_pending", "removed": c["pending"],
                      "kind": "memory card suggestions awaiting review"},
                     {"store": "memory_card_history", "removed": c["journal"],
                      "kind": "copies in the memory card's change history"}]}


def receipts_find(names, emails) -> int:
    return 0                                     # nothing here is removed


def receipts_purge(names, emails) -> Dict[str, Any]:
    m = _matcher(names, emails)
    n = _receipts_naming(m) if m else 0
    return {"removed": 0, "kind": "action receipts",
            "retained": ([{"what": "action receipts that name them", "count": n, "reason": _RECEIPTS_REASON}]
                         if n else [])}


def register() -> None:
    from agent_friday.services import forget_registry as fr
    fr.register_store("conversation_recall", recall_find, recall_purge)
    fr.register_store("memory_card", card_find, card_purge)
    fr.register_store("action_receipts", receipts_find, receipts_purge)

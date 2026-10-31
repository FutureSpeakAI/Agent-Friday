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
                         recalled or searched again; their ids are kept
                         durably (forgotten-turns.json beside the tombstones,
                         mirrored in conversation search's ``forgotten``) so a
                         rebuild from the transcripts does not bring them
                         back, and
                         ConversationMemory.index refuses those ids too (a turn
                         being indexed while the forget ran, or a rebuild of the
                         vector memory). The in-memory review buffer
                         (memory_review) drops every exchange naming them, and
                         the chat, the local voice model's history and Crew
                         replay those turns to a model only as a "removed"
                         placeholder (routes/chat, voice_delivery.local_history,
                         crew_runtime._history); a new voice call's pin of
                         recent calls leaves them out
                         (conversation_recall.recent_voice_pin).
``conversation_graph``   the knowledge graph's nodes extracted from those turns:
                         retraction is requested through memory_derived's queue
                         (indexer.purge_conversation_turns, the rules a
                         correction uses). The count is of turns queued; when
                         no graph pass is running it is applied at once and the
                         receipt gives what went. The graph reads its
                         conversation source from the vector memory just
                         cleaned, so its next pass does not re-derive them;
                         nodes titled with the person's name went in
                         forget_person's step 3.
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
``user_model``           facts in the older user model (user_model.db) that name
                         them; the one-time move of those facts onto the card
                         runs before the card is purged, so it cannot bring a
                         line back.
``dreams``               the nightly dreaming log (dreams.db, dreams/<day>.md and
                         the <day>.json sidecar with its source message ids):
                         consolidated facts and topics naming them leave each
                         day; the day's summary line is written again.
``action_receipts``      nothing is removed: listed as retained.
``context_log``          nothing is removed: listed as retained, with its
                         retention period.
``egress_log``           nothing is removed: listed as retained.

WHAT IS KEPT, AND WHY (the receipt says so, per store)
------------------------------------------------------
* The conversation transcript (and the chat window shown from it) is the
  owner's own record of what they said; forget-person's standing policy is
  that Friday does not rewrite it. The receipt counts the messages that still
  name the person, as "retained". The owner can delete any one message from
  the history (conversation_fts.delete_message).
* Action receipts (actions/receipts.jsonl) are the append-only record of what
  Friday did on the owner's behalf; they are counted, not changed.
* The context log (vault/context-log/) records what was put in front of a
  model, for the owner's own audit; it is pruned by its retention setting
  (Settings > Privacy > Context Logging), not by a forget.
* The egress log (vault/egress-log.jsonl) is the audit of what left this
  computer; its reason fields can quote the text that was gated. Kept.

Matching in free text is forget_registry.subject_matcher (whole words; a
multi-word name in any case, a one-word name case-sensitively). Names that
would take far more than the person (the owner's own, Friday's, an ordinary
word on its own) and names shorter than three characters are not matched, and
the receipt lists them as not removable.

Running forget again is safe: what is already gone counts 0, and days and
graph turns already handled are not queued again.

Turns said AFTER the forget are the owner's new words and are indexed as
usual. Lifting the tombstone (unforget) does not restore anything removed here.
"""
from __future__ import annotations

import json
import logging
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

_log = logging.getLogger(__name__)

#: The id ConversationMemory.index gives a turn stored without a message id
#: (uuid4().hex). Transcript message ids are shorter (secrets.token_hex(8)).
_LEGACY_ID = re.compile(r"^[0-9a-f]{32}$")

_TRANSCRIPT_REASON = ("your own conversation history, in your words: Friday does not rewrite it (you can "
                      "delete any message from History). Those messages are no longer recalled, searched, "
                      "summarised or replayed to the model as conversation context")
_RECEIPTS_REASON = ("the append-only record of actions Friday took for you; it stays as the evidence of "
                    "what was done")
_EGRESS_REASON = ("the audit record of what left this computer and why; a reason can quote the text that "
                  "was checked. Kept with no expiry, as the evidence of every send")


def _matcher(names, emails):
    from agent_friday.services import forget_registry as fr
    return fr.subject_matcher(names, emails)


def _keyword_matcher(m):
    """For stores that keep lowercase keywords (dream topics): a keyword equal
    to a forgotten name, or to a word of a multi-word one that is not refused
    (an ordinary word, the owner's or Friday's name)."""
    from agent_friday.services import forget_registry as fr
    names = set(getattr(m, "names", ()) or ())
    words = {w for n in names if " " in n for w in n.split(" ") if len(w) >= fr.MIN_NAME}
    words -= set(fr.name_guard(words))
    words = {w for w in words if w not in fr.COMMON_WORDS}
    keys = names | words
    return lambda k: str(k or "").strip().lower() in keys


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
        _log.warning("forget: transcripts not read: %s", type(e).__name__)
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


def _lines_naming(path: Path, m, field: Optional[str] = None) -> int:
    """Lines of a JSONL file that name them (in `field` only, when given)."""
    if not path.exists():
        return 0
    n = 0
    with open(path, "r", encoding="utf-8", errors="ignore") as fh:
        for line in fh:
            if field is None:
                n += bool(m(line))
                continue
            try:
                n += bool(m(str((json.loads(line) or {}).get(field) or "")))
            except (ValueError, AttributeError):
                continue
    return n


def _receipts_naming(m) -> int:
    try:
        from agent_friday.services import action_journal as aj
        return _lines_naming(aj._path(), m)
    except Exception:
        return 0


def _scrubbed(e: BaseException, what: str) -> str:
    """A reason carrying an exception, marked so the HTTP boundary swaps it
    (routes._errors.public_result) while the model keeps the real text."""
    from agent_friday.user_errors import clip, exception_text
    return clip(exception_text(e, what + " (%s)"), 200)


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
    return purge_turns(m, _transcript_hits(m) if m else [])


def purge_turns(m, transcript, index_match=None, retained_reason: str = "",
                retained_what: str = "", by_id: bool = False) -> Dict[str, Any]:
    """The recall-index purge forget-person runs, for any set of turns
    (services/forget_kinds reuses it for a fact and a conversation): `m` is a
    predicate on a turn's text (with ``notes()`` for what it cannot match and
    a truth value for whether it can match at all), `transcript` the
    (conversation id, message) pairs it covers. `index_match` is the predicate
    run over conversation search's own rows (default `m`): a conversation's
    forget passes one that matches nothing, so only its own message ids go.
    `by_id` is a conversation's forget: each transcript message id is deleted
    from the vector memory whatever its text, and a vector row `m` accepts by
    its text is taken only when it is one of those ids, or a legacy row (stored
    under a random id before turns carried their message id) dated on a day of
    this conversation's own turns. A row under any other message id belongs to
    another conversation and is never taken, nor added to the forgotten list;
    the count is of this conversation's turns only. The receipt row has the
    same shape as forget-person's."""
    not_removable = list(m.notes()) if hasattr(m, "notes") else []
    removed_ids, texts, days = set(), [], set()
    t_ids = [str(msg.get("id")) for _c, msg in transcript if msg.get("id")]
    own_ids = set(t_ids)
    own_days = {_day_of(msg.get("ts")) for _c, msg in transcript} - {""} if by_id else set()

    def _ours(h) -> bool:
        """With `by_id`: is this vector row one of this conversation's turns?"""
        if not by_id:
            return True
        hid = str(h.get("id") or "")
        if hid in own_ids:
            return True
        return bool(_LEGACY_ID.match(hid)) and str(h.get("date") or "")[:10] in own_days
    # Turns an earlier forget already took out: their days and graph turns
    # were handled then, so running forget again queues nothing twice.
    try:
        from agent_friday.services import conversation_fts as fts
        already = fts.forgotten_ids(t_ids)
    except Exception:
        already = set()
    fresh = [(c, msg) for c, msg in transcript if str(msg.get("id") or "") not in already]

    # 1. Friday's vector memory.
    vector_ids: List[str] = []
    left: set = set()
    if m:
        mem, why = _memory()
        if why:
            not_removable.append({"what": "conversation memory (vector recall)", "reason": why})
        if mem is not None:
            hits = mem.matching(m)
            if hits is not None:
                hits = [h for h in hits if _ours(h)]
            if hits is None:
                not_removable.append({"what": "conversation memory (vector recall)",
                                      "reason": "it could not be read, so it was not checked"})
            elif hits:
                want = [str(h["id"]) for h in hits]
                vector_ids = want
                n = mem.delete(ids=want)
                gone = set(want)
                if int(n or 0) < len(want):
                    after = mem.matching(m)
                    left = ({str(r["id"]) for r in after if _ours(r)} & set(want)) if after is not None else set(want)
                    gone -= left
                    if left:
                        not_removable.append({
                            "what": "turns in conversation memory (vector recall)", "count": len(left),
                            "reason": "the memory did not delete them, so they may still be recalled; "
                                      "running forget again retries"})
                removed_ids |= gone
                texts += [h["text"] for h in hits if str(h["id"]) in gone]
                days |= {str(h.get("date") or "")[:10] for h in hits if str(h["id"]) in gone}
            if by_id:
                # The transcript's own ids (the vector memory stores a turn
                # under its message id): each one, whatever its text.
                for mid in t_ids:
                    if mid in removed_ids or mid in left:
                        continue
                    try:
                        if int(mem.delete(ids=[mid]) or 0):
                            removed_ids.add(mid)
                    except Exception as e:
                        not_removable.append({"what": "conversation memory (vector recall)",
                                              "reason": _scrubbed(e, "a turn could not be deleted")})
                        break

    # 2. Conversation search; the transcript ids (and the vector memory's,
    #    which are the same message ids) are kept out of any rebuild.
    if m:
        try:
            from agent_friday.services import conversation_fts as fts
            got = fts.forget_turns(m if index_match is None else index_match, mids=t_ids + vector_ids)
            # Counted once per turn across both indexes, and not at all while
            # the vector memory still holds it (listed as not removable above).
            removed_ids |= set(got["mids"]) - left
            days |= set(got["dates"])
            if got.get("durable") is False:
                not_removable.append({
                    "what": "the lasting record of the forgotten turns",
                    "reason": "it could not be saved, so if the search index were rebuilt from scratch these "
                              "turns could come back; running forget again retries"})
        except Exception as e:
            not_removable.append({"what": "conversation search",
                                  "reason": _scrubbed(e, "it could not be updated")})

    # The record of which replies recalled which turns (services/memory_influence,
    # ids only) keeps no id of a forgotten turn, as after any other delete.
    if m and removed_ids:
        try:
            from agent_friday.services import memory_influence
            memory_influence.forget_refs(sorted(removed_ids))
        except Exception:
            pass

    # The in-memory review buffer (what the next periodic review would read):
    # every buffered exchange naming them, including a turn still in flight.
    if m:
        try:
            from agent_friday.services import memory_review
            memory_review.forget_matching(m)
        except Exception as e:
            not_removable.append({"what": "conversation turns waiting for the next memory review",
                                  "reason": _scrubbed(e, "they could not be dropped")})

    # 3. The knowledge graph's nodes made from those turns (only turns this run
    #    took out: a second forget queues nothing again).
    graph = {"store": "conversation_graph", "removed": 0,
             "kind": "conversation turns queued for retraction from the knowledge graph",
             "not_removable": [], "retained": []}
    turn_texts = list(dict.fromkeys(
        t for t in texts + [str(msg.get("text") or "") for _c, msg in fresh] if t.strip()))
    if turn_texts:
        try:
            from agent_friday.services import memory_derived as md
            out = md.queue_graph_retraction(turn_texts) or {}
            n = len(md._turn_ids(turn_texts))
            graph["removed"] = n
            if out.get("refused"):
                graph["not_removable"].append({
                    "what": "knowledge graph", "count": n,
                    "reason": "the graph refused the save; the turns stay queued and go on its next pass"})
            elif not out.get("deferred") and "entities" in out:
                graph["kind"] = ("conversation turns retracted from the knowledge graph now (%d entities and "
                                 "%d links removed, %d described again, %d community summaries removed)"
                                 % (int(out.get("entities") or 0), int(out.get("relationships") or 0),
                                    int(out.get("described_again") or 0), int(out.get("reports") or 0)))
            elif out.get("deferred"):
                graph["kind"] += " (applied when the running graph pass saves)"
        except Exception as e:
            graph["not_removable"].append({"what": "knowledge graph",
                                           "reason": _scrubbed(e, "retraction could not be queued")})

    # 4. Day summaries that name them, or were made from a removed turn.
    summaries = {"store": "day_summaries", "removed": 0, "kind": "day summaries removed and queued to be "
                 "written again", "not_removable": [], "retained": []}
    if m:
        try:
            from agent_friday.services import memory_derived as md
            days |= {_day_of(msg.get("ts")) for _c, msg in fresh}
            want = set(_summary_days_naming(m)) | {d for d in days if md._valid_day(d) and _summary_exists(d)}
            summaries["removed"] = len(md.mark_summaries(want))
        except Exception as e:
            summaries["not_removable"].append({"what": "day summaries",
                                               "reason": _scrubbed(e, "they could not be removed")})

    retained = []
    if transcript:
        retained.append({"what": retained_what or "messages in your conversation history",
                         "count": len(transcript), "reason": retained_reason or _TRANSCRIPT_REASON})
    count = len(removed_ids)
    if by_id:
        # This conversation's turns only: by id, or (a legacy vector row) by its text.
        legacy = {" ".join(str(t).split()) for t in texts}
        count = sum(1 for _c, msg in transcript
                    if str(msg.get("id") or "") in removed_ids
                    or " ".join(str(msg.get("text") or "").split()) in legacy)
    return {"removed": count, "kind": "conversation turns removed from recall and search",
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
    return {"removed": c["lines"], "kind": "memory card lines",
            "not_removable": list(c.get("not_removable") or []),
            "also": [{"store": "memory_card_pending", "removed": c["pending"],
                      "kind": "memory card suggestions awaiting review"},
                     {"store": "memory_card_history", "removed": c["journal"],
                      "kind": "copies in the memory card's change history"}]}


def user_model_find(names, emails) -> int:
    m = _matcher(names, emails)
    if not m:
        return 0
    from agent_friday.services import user_model
    return user_model.forget_matching(m, apply=False)


def user_model_purge(names, emails) -> Dict[str, Any]:
    m = _matcher(names, emails)
    n = 0
    if m:
        from agent_friday.services import user_model
        n = user_model.forget_matching(m)
    return {"removed": n, "kind": "facts in the older user model"}


def dreams_find(names, emails) -> int:
    m = _matcher(names, emails)
    if not m:
        return 0
    from agent_friday.services import memory_dreaming
    c = memory_dreaming.forget_matching(m, keyword=_keyword_matcher(m), apply=False)
    return c["facts"] + c["topics"] + c["lines"]


def dreams_purge(names, emails) -> Dict[str, Any]:
    m = _matcher(names, emails)
    c = {"facts": 0, "topics": 0, "lines": 0}
    if m:
        from agent_friday.services import memory_dreaming
        c = memory_dreaming.forget_matching(m, keyword=_keyword_matcher(m))
    return {"removed": c["facts"] + c["topics"] + c["lines"],
            "kind": "items in the nightly dreaming log (consolidated facts and topics)",
            "not_removable": list(c.get("not_removable") or [])}


def receipts_find(names, emails) -> int:
    return 0                                     # nothing here is removed


def receipts_purge(names, emails) -> Dict[str, Any]:
    m = _matcher(names, emails)
    n = _receipts_naming(m) if m else 0
    return {"removed": 0, "kind": "action receipts",
            "retained": ([{"what": "action receipts that name them", "count": n, "reason": _RECEIPTS_REASON}]
                         if n else [])}


def _context_log_dir() -> Path:
    from agent_friday import core
    return Path(core.CONTEXT_LOG_DIR)


def _context_retention() -> str:
    try:
        from agent_friday import core
        days = int((core._load_settings() or {}).get("context_retention_days", 0) or 0)
    except Exception:
        days = 0
    if days > 0:
        return "kept for %d days, then removed by its retention sweep" % days
    return "kept until you remove it: no retention period is set"


def context_log_purge(names, emails) -> Dict[str, Any]:
    m = _matcher(names, emails)
    n = 0
    if m:
        try:
            d = _context_log_dir()
            if d.exists():
                n = sum(_lines_naming(p, m) for p in sorted(d.glob("*.jsonl")))
        except Exception:
            n = 0
    reason = ("the log of what was put in front of a model, for your own audit (Settings > Privacy > Context "
              "Logging); a forget does not edit it. " + _context_retention())
    return {"removed": 0, "kind": "context log entries",
            "retained": [{"what": "context log entries that name them", "count": n, "reason": reason}]
            if n else []}


def _egress_log_path() -> Path:
    from agent_friday.services import egress_gate
    return Path(egress_gate._DEFAULT_LOG)


def egress_log_purge(names, emails) -> Dict[str, Any]:
    m = _matcher(names, emails)
    n = 0
    if m:
        try:
            n = _lines_naming(_egress_log_path(), m, field="reason")
        except Exception:
            n = 0
    return {"removed": 0, "kind": "egress log entries",
            "retained": [{"what": "egress log entries whose reason names them", "count": n,
                          "reason": _EGRESS_REASON}] if n else []}


def register() -> None:
    from agent_friday.services import forget_registry as fr
    fr.register_store("conversation_recall", recall_find, recall_purge)
    fr.register_store("memory_card", card_find, card_purge)
    fr.register_store("user_model", user_model_find, user_model_purge)
    fr.register_store("dreams", dreams_find, dreams_purge)
    fr.register_store("action_receipts", receipts_find, receipts_purge)
    fr.register_store("context_log", receipts_find, context_log_purge)
    fr.register_store("egress_log", receipts_find, egress_log_purge)

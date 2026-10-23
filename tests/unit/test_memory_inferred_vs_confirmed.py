"""Inferred vs confirmed memory, across every store (north star gate NS-34.9-8).

What Friday keeps about the owner is either CONFIRMED (the owner said it in
their own words, typed it on the Memory card, or kept a suggestion) or
INFERRED (a model, a regex, a background review or Friday herself derived it).
The gate asks five things of every store, and this file checks them through
the real stores, offline:

  (a) an inferred item is stored with a marker that sets it apart from a
      confirmed one (a pending status, a review-queue row with its source, a
      session-only trait, a below-the-gate confidence);
  (b) what reaches Friday's prompt or the owner's view never presents an
      inferred item as something the owner said;
  (c) an inferred item never overwrites or out-ranks a confirmed one on the
      same subject: the confirmed state wins;
  (d) only the owner's action turns an inferred item into a confirmed one;
      Friday's own tools and models cannot;
  (e) a rejected item leaves recall and does not come back as confirmed.

Stores: services/memory_proposals (model proposals), services/core_memory
(the Memory card and its review queue), services/memory_review and
services/memory_dreaming (background passes that may only suggest),
services/core_memory_tools (Friday's update_core_memory tool),
services/user_model (traits and facts), conversation_memory (recall).

CI installs no Chroma: the conversation-memory test uses a small in-memory
collection with Chroma's interface, as tests/unit/test_memory_truth.py does.
The knowledge graph's ingest_fact is replaced by a recorder so no graph file
is written. Synthetic text; no network, no model.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from unittest.mock import patch

import pytest

import agent_friday.services.agent as agent
from agent_friday.services import approvals, prompt_freeze, taint
from agent_friday.services import core_memory as cm
from agent_friday.services import core_memory_tools as cmt
from agent_friday.services import memory_dreaming as md
from agent_friday.services import memory_proposals as mp
from agent_friday.services import memory_review as mrev
from agent_friday.services import user_model as um

DAY = "2026-08-23"
DREAM_DAY = "2026-10-01"

PROPOSED = "the maintainer commits one build at a time"
DREAMED = "I prefer dark mode and terse answers."
REVIEWED = "Wants weekly digests sent to digest@relay.example."
FRIDAY_GUESS = "Hates early meetings."

_GOOD = json.dumps([{"category": "workflow", "text": PROPOSED,
                     "evidence": "one commit per build"}])


# ── isolation ────────────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def _iso(tmp_path, monkeypatch):
    monkeypatch.setattr(cm, "_dir", lambda: tmp_path / "core_memory")
    monkeypatch.setattr(cm, "_MIGRATION_CHECKED", {str(tmp_path / "core_memory"): True})
    monkeypatch.setattr(um, "FRIDAY_DIR", tmp_path)
    monkeypatch.setattr(um, "DB_PATH", tmp_path / "user_model.db")
    monkeypatch.setattr(mp, "DB_PATH", tmp_path / "proposals.db")
    monkeypatch.setattr(md, "FRIDAY_DIR", tmp_path)
    monkeypatch.setattr(md, "DB_PATH", tmp_path / "dreams.db")
    monkeypatch.setattr(md, "DREAMS_DIR", tmp_path / "dreams")
    monkeypatch.setattr(approvals, "APPROVALS_FILE", tmp_path / "approvals.json")
    monkeypatch.setattr(approvals, "FRIDAY_DIR", tmp_path)
    monkeypatch.setattr(approvals, "_notify_pending", lambda rec: None)
    agent._PENDING_CONFIRMATIONS.clear()
    taint.reset()
    prompt_freeze.reset()
    mrev._reset_for_tests()
    um.reset_session_adaptation()
    yield
    um.reset_session_adaptation()
    mrev._reset_for_tests()
    prompt_freeze.reset()
    taint.reset()


@pytest.fixture(autouse=True)
def graph(monkeypatch):
    """What reaches the knowledge graph, recorded instead of written."""
    from agent_friday.services.knowledge_graph import integration as kgi
    seen = []
    monkeypatch.setattr(kgi, "ingest_fact", lambda text, **kw: seen.append((text, kw)) or "fact_x")
    return seen


# ── helpers: each inferring path, driven for real ────────────────────────────

class _DayMemory:
    """ConversationMemory.recent(), for one day of turns."""

    def __init__(self, rows):
        self._rows = rows

    def recent(self, n=2000, roles=None):
        return list(self._rows)[:n]


def _dream(text=DREAMED, day=DREAM_DAY):
    rows = [{"text": text, "role": "user", "date": day,
             "timestamp": f"{day}T10:00:00", "topic_keywords": []}]
    return md.dream(day=day, memory=_DayMemory(rows))


def _propose(raw=_GOOD, day=DAY):
    seat = {"assigned": True, "model": "gemma4:12b", "provider": "ollama-local", "reason": ""}
    turns = [{"role": "user", "text": f"turn {i} about builds and commits"} for i in range(5)]
    with patch.object(mp, "seat", return_value=seat), \
         patch.object(mp, "_ask_seat", return_value=raw), \
         patch("agent_friday.services.memory_dreaming._pull_turns", return_value=(turns, False)):
        return mp.propose(day)


def _review_from_what_friday_read():
    window = [{"user": "Summarise that page for me.",
               "friday": "The page says the reader prefers weekly digests sent to digest@relay.example.",
               "owner": True}]
    plan = {"add": [{"target": "user", "text": REVIEWED,
                     "evidence": "the reader prefers weekly digests sent to digest@relay.example"}],
            "change": [], "remove": []}
    return mrev.review(window, ask=lambda prompt: json.dumps(plan))


def _friday_tool(inp, owner_text="what's on tomorrow?", key="conv-m6"):
    """update_core_memory as _execute_tool runs it: inside the call's context."""
    ctx = {"session_id": key, "taint_key": key, "surface": "chat", "authenticated": True}
    t1 = agent._CURRENT_TOOL_CONTEXT.set(ctx)
    t2 = agent._CURRENT_OWNER_TEXT.set(owner_text)
    try:
        return cmt._tool_update_core_memory(inp)
    finally:
        agent._CURRENT_OWNER_TEXT.reset(t2)
        agent._CURRENT_TOOL_CONTEXT.reset(t1)


def _head():
    from agent_friday.services import model_router
    return model_router.memory_head_blocks("chat", None)


def _user_model_block(head):
    if "== USER MODEL ==" not in head:
        return ""
    return head.split("== USER MODEL ==", 1)[1].split("\n\n==", 1)[0]


def _fact(text):
    return next((f for f in um.profile()["facts"] if f["text"] == text), None)


# ── (a) an inferred item carries a marker; a confirmed one does not ─────────

def test_every_inferring_path_stores_its_output_marked_as_waiting_for_the_owner():
    _dream()
    _review_from_what_friday_read()
    out = _friday_tool({"action": "add", "target": "user", "text": FRIDAY_GUESS})
    _propose()

    assert out.startswith("NOT SAVED YET"), out
    assert cm.entries("user") == [], "an inferred line reached the card with no owner action"
    assert um.profile()["facts"] == [], "an inferred fact reached the user model"

    queued = {p["text"]: p for p in cm.pending()}
    assert set(queued) == {DREAMED, REVIEWED, FRIDAY_GUESS}
    assert queued[DREAMED]["source"] == f"dream:{DREAM_DAY}"
    assert queued[REVIEWED]["source"] == "review"
    assert queued[FRIDAY_GUESS]["source"] == "friday"
    assert all(p["reason"] for p in queued.values()), "a suggestion does not say why it waits"

    proposals = mp.pending()
    assert [p["text"] for p in proposals] == [PROPOSED]
    assert proposals[0]["confidence"] == mp.MODEL_CONFIDENCE < 0.6
    assert proposals[0]["model"] == "gemma4:12b"
    assert mp.state()["pending"] == 1 and mp.state()["approved"] == 0


def test_a_style_inferred_from_messages_is_session_only_and_an_owner_set_one_is_durable():
    for _ in range(8):
        um.observe_message("lol yeah gonna grab coffee, thanks!", role="user")
    prof = um.profile()
    assert "comm.formality" in prof["session_traits"], "the inferred trait carries no session marker"
    assert um._durable_trait("comm.formality") is None, "an inferred trait was written to the store"

    um.reset_session_adaptation()
    um.set_trait("comm.verbosity", 0.2)
    prof = um.profile()
    assert "comm.verbosity" not in prof["session_traits"]
    assert um._durable_trait("comm.verbosity") == 0.2


# ── (b) recall and the prompt never present an inferred item as the owner's ──

def test_suggestions_waiting_for_review_never_reach_the_prompt_and_a_kept_one_does():
    cm.add("user", "Lives near the river.", actor="owner")
    _dream()
    _review_from_what_friday_read()
    _friday_tool({"action": "add", "target": "user", "text": FRIDAY_GUESS})

    head = _head()
    assert "Lives near the river." in head
    for text in (DREAMED, REVIEWED, FRIDAY_GUESS):
        assert text not in head, f"an unreviewed suggestion is in Friday's prompt: {text!r}"
    assert "digest@relay.example" not in head

    kept = next(p for p in cm.pending() if p["text"] == DREAMED)
    assert cm.decide(kept["id"], True)["ok"]
    head = _head()
    assert DREAMED in head, "the line the owner kept is not in Friday's prompt"
    assert REVIEWED not in head and FRIDAY_GUESS not in head


def test_a_model_proposal_reaches_memory_only_once_approved_and_then_says_where_it_came_from(graph):
    _propose()
    assert PROPOSED not in _head()
    assert _fact(PROPOSED) is None and graph == []

    fid = mp.pending()[0]["fact_id"]
    assert mp.approve([fid])["approved"] == 1

    assert PROPOSED in _head(), "an approved proposal is not on the card in the prompt"
    fact = _fact(PROPOSED)
    assert fact["source"] == f"proposed:{DAY}"
    assert fact["confidence"] == mp.APPROVED_CONFIDENCE
    assert [kw["source_key"] for _t, kw in graph] == [f"proposed:{DAY}"]
    assert mp.pending() == []


def test_a_style_inferred_this_session_is_labelled_inferred_in_the_prompt():
    """render_user_model_prompt says which part of the style Friday inferred
    from how the owner writes; the part the owner set carries no such mark."""
    for _ in range(8):
        um.observe_message("lol yeah gonna grab coffee, thanks!", role="user")
    block = _user_model_block(_head())
    style = [ln for ln in block.splitlines() if ln.startswith("Communication style:")]
    assert style and "casual" in style[0], block
    assert "inferred" in style[0].lower(), (
        "a style Friday inferred is presented as a plain fact about the owner: " + style[0])

    um.reset_session_adaptation()
    um.set_trait("comm.formality", 0.9)
    block = _user_model_block(_head())
    style = [ln for ln in block.splitlines() if ln.startswith("Communication style:")]
    assert style and "formal" in style[0] and "inferred" not in style[0].lower()


def test_a_style_stored_before_inference_became_session_only_counts_as_inferred():
    """Rows an older Friday wrote from inference carry no owner mark: they are
    labelled inferred and do not freeze this session's adaptation."""
    conn = um._connect()
    conn.execute("INSERT INTO traits(key,value,confidence,updated_ts,evidence) VALUES(?,?,?,?,?)",
                 ("comm.formality", "0.9", 0.6, 0.0, 3))
    conn.execute("INSERT INTO traits(key,value,confidence,updated_ts,evidence) VALUES(?,?,?,?,?)",
                 ("expertise.code", "0.9", 0.6, 0.0, 3))
    conn.commit()
    conn.close()
    assert not um.owner_set("comm.formality")
    block = _user_model_block(_head())
    style = [ln for ln in block.splitlines() if ln.startswith("Communication style:")]
    assert style and "inferred" in style[0].lower(), block
    assert "code (inferred)" in block, block
    for _ in range(8):
        um.observe_message("lol yeah gonna grab coffee, thanks!", role="user")
    assert um.get_trait("comm.formality") < 0.9, "an old inference was treated as the owner's setting"


def _words(text):
    return {w for w in re.findall(r"[a-z0-9]+", str(text).lower()) if len(w) > 2}


class _FakeCollection:
    """Chroma's collection interface, as ConversationMemory uses it."""

    def __init__(self):
        self.rows = {}

    def count(self):
        return len(self.rows)

    def upsert(self, ids, documents, metadatas, embeddings=None):
        for i, d, m in zip(ids, documents, metadatas):
            self.rows[str(i)] = [d, dict(m)]

    def query(self, query_texts=None, query_embeddings=None, n_results=5, where=None, include=None):
        q = _words((query_texts or [""])[0])
        scored = sorted(((1.0 - len(q & _words(d)) / max(1, len(q)), i, d, m)
                         for i, (d, m) in self.rows.items()), key=lambda s: s[0])
        top = scored[:max(1, int(n_results))]
        return {"ids": [[s[1] for s in top]], "documents": [[s[2] for s in top]],
                "metadatas": [[dict(s[3]) for s in top]], "distances": [[s[0] for s in top]]}

    def get(self, ids=None, include=None, where=None, limit=None):
        keys = [str(i) for i in ids if str(i) in self.rows] if ids is not None else list(self.rows)
        return {"ids": keys, "documents": [self.rows[k][0] for k in keys],
                "metadatas": [dict(self.rows[k][1]) for k in keys]}

    def update(self, ids, metadatas=None, documents=None):
        for i, m in zip(ids, metadatas or []):
            if str(i) in self.rows:
                self.rows[str(i)][1] = dict(m)

    def delete(self, ids=None):
        for i in ids or []:
            self.rows.pop(str(i), None)


def test_a_recalled_guess_friday_voiced_is_labelled_hers_not_the_owners(tmp_path, monkeypatch):
    from agent_friday.conversation_memory import ConversationMemory
    from agent_friday.services import model_router, turn_vector
    mem = ConversationMemory(persist_dir=tmp_path / "conv_mem")
    mem._collection = _FakeCollection()
    monkeypatch.setattr(mem, "_assert_embedding_space", lambda: None)
    monkeypatch.setattr(mem, "_shared_query_ok", lambda: False)
    assert mem.available()
    mem.index("It sounds like you prefer working late at night.", "assistant",
              session_id="2026-09-02", timestamp="2026-09-02T23:10:00", msg_id="msg-guess-001")
    mem.index("I start work at seven most mornings.", "user",
              session_id="2026-09-03", timestamp="2026-09-03T08:00:00", msg_id="msg-owner-01")

    monkeypatch.setattr(turn_vector, "compute", lambda *a, **k: None)
    monkeypatch.setattr(model_router, "_get_conversation_memory", lambda: mem)
    monkeypatch.setattr(model_router, "_load_settings", lambda: {"memory_recall_enabled": True})
    block = model_router._build_memory_context_block(
        "do I prefer working late at night or start work in the mornings", "s-today",
        min_relevance=0.0)

    guess = [ln for ln in block.splitlines() if "working late at night" in ln]
    said = [ln for ln in block.splitlines() if "start work at seven" in ln]
    assert guess and said, block
    assert "] You:" in guess[0] and "User:" not in guess[0], guess[0]
    assert "] User:" in said[0], said[0]


# ── (c) the confirmed state wins over an inference on the same subject ──────

def test_a_queued_contradiction_leaves_the_owners_line_as_the_only_one_in_the_prompt():
    cm.add("user", "Lives in Springfield, Ohio.", actor="owner")
    _dream(text="I usually stay at my flat in Denver these days.")
    _friday_tool({"action": "replace", "target": "user", "old": "Springfield",
                  "text": "Lives in Denver."})
    assert cm.entries("user") == ["Lives in Springfield, Ohio."]
    assert sorted(p["op"] for p in cm.pending()) == ["add", "replace"]
    head = _head()
    assert "Lives in Springfield, Ohio." in head
    assert "Denver" not in head


def test_an_owner_set_style_is_not_overridden_by_what_friday_infers_this_session():
    """What Friday infers this session never moves a style the owner set,
    and the owner's part of the line is not labelled inferred."""
    um.set_trait("comm.formality", 0.9)
    for _ in range(8):
        um.observe_message("lol yeah gonna grab coffee, thanks!", role="user")
    assert um.get_trait("comm.formality") == 0.9, \
        "a session inference out-ranks the style the owner set"
    style = [ln for ln in _user_model_block(_head()).splitlines()
             if ln.startswith("Communication style:")]
    assert style and "formal/professional" in style[0], style
    assert "formal/professional (inferred" not in style[0], style


def test_approving_a_fact_the_store_already_holds_as_inferred_lifts_it_to_approved_confidence():
    """Approving a proposal whose text the store already holds from an
    inference lifts it to the approved confidence (not just old + 0.1)."""
    um.note_fact("workflow", PROPOSED, confidence=0.6, source="dream:2026-08-01")
    _propose()
    assert mp.approve([mp.pending()[0]["fact_id"]])["approved"] == 1
    fact = _fact(PROPOSED)
    assert fact["source"] == f"proposed:{DAY}"
    assert fact["confidence"] >= mp.APPROVED_CONFIDENCE, (
        "the owner's approval left the fact at %.2f, no higher than a regex match"
        % fact["confidence"])


def test_a_confirmed_fact_keeps_its_confirmed_source_when_an_inference_repeats_it():
    """An inferred repeat ("dream:...") keeps the mark that the owner entered
    the fact ("manual"), so the card migration keeps it as the owner's, and
    an inference cannot raise its own confidence by claiming a high one."""
    um.note_fact("preference", "Prefers short answers.", confidence=0.7, source="manual")
    um.note_fact("preference", "Prefers short answers.", confidence=0.6, source="dream:2026-10-02")
    assert _fact("Prefers short answers.")["source"] == "manual", \
        "an inference repeating the owner's fact erased the mark that the owner entered it"
    um.note_fact("bio", "Lives by the sea.", confidence=0.95, source="setup")
    um.note_fact("bio", "Lives by the sea.", confidence=0.6, source="dream:2026-10-03")
    assert _fact("Lives by the sea.")["source"] == "setup"
    um.note_fact("preference", "Likes long walks.", confidence=0.5, source="dream:2026-10-01")
    um.note_fact("preference", "Likes long walks.", confidence=0.95, source="dream:2026-10-02")
    assert _fact("Likes long walks.")["confidence"] == pytest.approx(0.6, abs=0.001)
    cm._MIGRATION_CHECKED.clear()
    cm.ensure_migrated()
    assert "Prefers short answers." in cm.entries("user")
    assert "Prefers short answers." not in [p.get("text") for p in cm.pending()]


# ── (d) only the owner's action confirms ─────────────────────────────────────

def test_friday_saving_a_waiting_suggestion_herself_does_not_confirm_it():
    _dream()
    before = [p["id"] for p in cm.pending()]
    out = _friday_tool({"action": "add", "target": "user", "text": DREAMED})
    assert out.startswith("NOT SAVED YET"), out
    assert cm.entries("user") == []
    assert [p["id"] for p in cm.pending()] == before, "the suggestion was filed twice"
    assert DREAMED not in _head()


def test_friday_has_no_tool_action_that_keeps_a_suggestion():
    schema = cmt.TOOLS[0]["input_schema"]["properties"]["action"]["enum"]
    assert set(schema) == {"add", "replace", "remove", "show"}
    _dream()
    pid = cm.pending()[0]["id"]
    for action in ("keep", "approve", "decide"):
        out = _friday_tool({"action": action, "target": "user", "text": DREAMED, "id": pid})
        assert out.startswith("NOT DONE"), out
    assert cm.entries("user") == [] and len(cm.pending()) == 1


def test_only_the_owners_routes_keep_a_suggestion_or_approve_a_proposal():
    """core_memory.decide and memory_proposals.approve turn an inferred item
    into a confirmed one. Nothing but the owner's HTTP routes calls them: no
    tool handler, no background pass, no model loop."""
    import ast
    root = Path(__file__).resolve().parents[2] / "src" / "agent_friday"
    allowed = {("routes", "core_memory.py"), ("routes", "memory_proposals.py")}
    confirmers = {"core_memory": "decide", "memory_proposals": "approve"}
    callers, scanned = set(), 0
    for path in root.rglob("*.py"):
        rel = path.relative_to(root).parts
        try:
            tree = ast.parse(path.read_text(encoding="utf-8", errors="ignore"))
        except SyntaxError:
            continue
        scanned += 1
        aliases = {}                                   # local name -> module
        for node in ast.walk(tree):
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                base = (node.module or "") if isinstance(node, ast.ImportFrom) else ""
                last = base.rsplit(".", 1)[-1]
                for a in node.names:
                    mod = a.name.rsplit(".", 1)[-1]
                    if mod in confirmers:              # the module, under any name
                        aliases[a.asname or mod] = mod
                    elif last in confirmers and a.name == confirmers[last]:
                        callers.add("/".join(rel))      # `from ...core_memory import decide`
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) \
                    and isinstance(node.func.value, ast.Name):
                mod = aliases.get(node.func.value.id)
                if mod and node.func.attr == confirmers[mod]:
                    callers.add("/".join(rel))
    assert scanned > 100, "the scan found no source to check"
    found = {tuple(c.split("/")[-2:]) for c in callers}
    assert allowed <= found, "the scan no longer sees the owner's own routes: %s" % sorted(callers)
    others = sorted(c for c in callers if tuple(c.split("/")[-2:]) not in allowed)
    assert others == [], "something other than the owner's routes confirms memory: %s" % others


def test_the_owners_keep_is_recorded_as_the_owners_change():
    _dream()
    assert cm.decide(cm.pending()[0]["id"], True)["ok"]
    assert cm.entries("user") == [DREAMED]
    assert cm.recent_changes() == [], "a line the owner kept is listed as one Friday made"


# ── (e) a rejected item leaves recall and does not come back as confirmed ───

def test_a_rejected_proposal_leaves_recall_cannot_be_approved_later_and_is_not_proposed_again(graph):
    _propose()
    fid = mp.pending()[0]["fact_id"]
    assert mp.reject([fid])["rejected"] == 1

    assert mp.pending() == []
    assert mp.approve([fid])["approved"] == 0, "a rejected proposal was approved afterwards"
    assert _fact(PROPOSED) is None and cm.entries("user") == [] and graph == []
    assert PROPOSED not in _head()

    again = _propose()
    assert again["ok"] and again["turns_reviewed"] == 5, again
    assert again["stored"] == 0, "the rejected fact was proposed again"
    assert mp.pending() == []


def test_a_discarded_suggestion_stays_off_the_card_when_the_pass_that_found_it_runs_again():
    _dream()
    pid = cm.pending()[0]["id"]
    assert cm.decide(pid, False)["ok"]
    assert cm.entries("user") == [] and DREAMED not in _head()

    _dream()
    assert cm.entries("user") == [], "a discarded suggestion came back as a card line"
    assert DREAMED not in _head()
    assert _fact(DREAMED) is None

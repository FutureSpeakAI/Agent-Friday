"""P7-4: the slice-need head and cloud context slicing (spec 9.5a, 9.7, 9.9).

Learned heads are shadow only: the plan is written to the TurnPlan and the
retrieval ledger and changes nothing the model sees. When the owner chose
"Send only what each turn needs" AND the turn goes to a cloud provider, the
plan is applied to the volatile tail by dropping sections or swapping them for
one fixed stub; it can never add, so the cloud payload is a subset of the full
one. The head, the floor and the sealed action policy are never touched.

Fixtures are synthetic. The encoder is a hashed bag of words so no model loads.
"""
from __future__ import annotations

import hashlib
import json
import math
import re

import pytest

from agent_friday.services import laya2_encoder
from agent_friday.services import local_context as lc
from agent_friday.services import model_router as mr
from agent_friday.services import off_record
from agent_friday.services import retrieval_ledger as rl
from agent_friday.services import slice_head as sh
from agent_friday.services import turn_plan as tp
from agent_friday.services import turn_receipt as tr
from agent_friday.services import turn_vector

DIM = 128


def _fake_embed(texts):
    out = []
    for t in texts:
        v = [0.0] * DIM
        for w in re.findall(r"[a-z]{3,}", t.lower()):
            v[int(hashlib.sha1(w.encode()).hexdigest(), 16) % DIM] += 1.0
        n = math.sqrt(sum(x * x for x in v)) or 1.0
        out.append([x / n for x in v])
    return out


@pytest.fixture
def home(tmp_path, monkeypatch):
    """Every log this feature touches points into tmp_path; no model loads."""
    monkeypatch.setattr(tp, "plans_path", lambda: tmp_path / "plans.jsonl")
    monkeypatch.setattr(rl, "_DEFAULT_LOG", tmp_path / "retrieval-log.jsonl")
    monkeypatch.setattr(sh, "labels_path", lambda: tmp_path / "slice_labels.jsonl")
    monkeypatch.setattr(off_record, "active", lambda settings=None: False)
    monkeypatch.setattr(laya2_encoder, "is_available", lambda: True)
    monkeypatch.setattr(laya2_encoder, "embed", _fake_embed)
    monkeypatch.setattr(laya2_encoder, "edge_text", lambda text, n: text, raising=False)
    tp._recent.clear()
    tp.begin_turn(None)
    sh.reset_cache()
    sh.reset()
    turn_vector.reset()
    sh.prototypes(block=True)
    yield tmp_path
    tp.begin_turn(None)
    sh.reset()


def _scores(**kw):
    base = {s: 0.0 for s in sh.SLICES}
    base.update(kw)
    return base


# --- the head --------------------------------------------------------------

def test_every_slice_has_synthetic_seeds_and_the_vocabulary_is_closed():
    assert set(sh.SEEDS) == set(sh.SLICES)
    assert all(len(v) >= 6 for v in sh.SEEDS.values())
    assert set(sh.PRIVATE_SLICES) <= set(sh.SLICES)
    assert set(sh.SECTION_SLICE.values()) <= set(sh.SLICES)
    assert set(sh.REQUESTABLE) == {sh.CATEGORY[s] for s in sh.PRIVATE_SLICES}


def test_the_head_reads_a_slice_from_the_shared_turn_vector(home):
    cal = sh.scores_for("what is on my calendar today", block=True)
    assert max(cal, key=cal.get) == "calendar"
    notes = sh.scores_for("what do my notes say about this", block=True)
    assert max(notes, key=notes.get) == "wiki"
    # The vector was computed once per distinct message, by turn_vector.
    assert turn_vector.stats()["encodes"] == 2


def test_no_reading_is_none_not_a_guess(home, monkeypatch):
    monkeypatch.setattr(laya2_encoder, "is_available", lambda: False)
    assert sh.scores_for("what is on my calendar today", block=True) is None
    assert sh.plan_slices(None, cloud=True) == {}
    assert sh.decide("memory", None, private=True, cloud=True) == ("include", "no reading")


def test_a_cold_start_never_waits_for_the_prototypes(home):
    import threading
    sh.reset_cache()
    # Not blocking: the first call starts the build and the turn has no reading.
    assert sh.scores_for("what is on my calendar today") is None
    for t in threading.enumerate():
        if t.name == "slice-head-warm":
            t.join(10)
    assert sh.scores_for("what is on my calendar today") is not None


@pytest.mark.parametrize("p,private,cloud,sticky,want", [
    (0.9, True, True, False, "include"),
    (0.4, True, True, False, "stub"),       # unsure + private + cloud
    (0.4, True, False, False, "include"),   # unsure, local: omission is the costly error
    (0.4, False, True, False, "include"),   # unsure, not private
    (0.1, True, True, False, "withhold"),
    (0.1, True, False, False, "withhold"),
    (0.1, True, True, True, "include"),      # sticky
])
def test_decision_table(p, private, cloud, sticky, want):
    assert sh.decide("x", p, private=private, cloud=cloud, sticky=sticky)[0] == want


# --- the stub ---------------------------------------------------------------

def test_the_stub_names_categories_only_and_is_the_same_bytes_every_turn():
    assert sh.STUB_TEXT.count("request_context") == 1
    for word in sh.REQUESTABLE:
        assert word in sh.STUB_TEXT
    # Nothing about what the owner has: no numbers, no names, only the fixed vocabulary.
    body = sh.STUB_TEXT
    for word in sh.REQUESTABLE:
        body = body.replace(word, "")
    assert not re.search(r"\d", body)


# --- applying the plan to a prompt -------------------------------------------

def _prompt(monkeypatch, tmp_path, slicer=None, provider="cloud"):
    """The real builder over synthetic sources."""
    monkeypatch.setattr(mr, "_load_vault_summary", lambda: {
        "recent_memories": ["zz-memory-alpha"],
        "active_todos": [{"status": "open", "task": "zz-task-bravo"}],
        "trust_people": {}})
    monkeypatch.setattr(mr, "_detect_context_needs",
                        lambda m, w: {"memory", "todos", "career"})
    monkeypatch.setattr(mr, "_get_career_context", lambda: {
        "applications_count": 2, "recent_applications": [],
        "pipeline_summary": "zz-career-charlie"})
    monkeypatch.setattr(mr, "_load_smart_context", lambda *a, **k: "zz-notes-delta")
    monkeypatch.setattr(mr.Path, "cwd", classmethod(lambda cls: tmp_path))
    import agent_friday.services.voice_engine as ve
    monkeypatch.setattr(ve, "_load_live_context", lambda: "zz-today-echo")
    import agent_friday.services.clock as clock
    monkeypatch.setattr(clock, "clock_context_block", lambda: "\n== CLOCK ==\n2026-01-01 00:00\n")
    from agent_friday.services import capability_state
    monkeypatch.setattr(capability_state, "describe_for_model", lambda: "")
    kw = {"slicer": slicer} if slicer is not None else {}
    return mr._build_context_prompt("hello there", workspace="chat", provider=provider,
                                    vault_control=None, **kw)[0]


def test_shadow_never_changes_the_prompt(home, monkeypatch, tmp_path):
    full = _prompt(monkeypatch, tmp_path)
    assert "zz-memory-alpha" in full and "zz-career-charlie" in full
    # The head says "withhold everything", but the owner has not opted in.
    shadow = sh.Slicer(_scores(), turn_id="t1", cloud=True, opted=False)
    assert not shadow.active
    assert _prompt(monkeypatch, tmp_path, shadow) == full


def test_a_local_leg_is_never_sliced_even_when_opted_in(home, monkeypatch, tmp_path):
    full = _prompt(monkeypatch, tmp_path, provider="ollama")
    slicer = sh.for_turn("hello there", conversation_id="c1", provider="ollama",
                         turn_id="t1", settings={"cloud_context_slicing": "slice"})
    assert slicer.cloud is False and not slicer.active
    assert _prompt(monkeypatch, tmp_path, slicer, provider="ollama") == full


def test_an_ollama_cloud_tagged_seat_is_sliced_like_a_cloud_leg(home):
    """/api/chat/send passes the leg's model: a seat Ollama relays to its cloud is cloud."""
    settings = {"cloud_context_slicing": "slice"}
    tagged = sh.for_turn("hello there", conversation_id="c1", provider="ollama",
                         turn_id="t1", settings=settings, model="glm-4.6:cloud")
    assert tagged.cloud is True and tagged.active
    plain = sh.for_turn("hello there", conversation_id="c1", provider="ollama",
                        turn_id="t2", settings=settings, model="gemma3:12b")
    assert plain.cloud is False and not plain.active
    assert sh.is_local("ollama", "gpt-oss:120b-cloud") is False
    assert sh.is_local("ollama", "gemma3:12b") is True


def test_chat_send_hands_the_seat_model_to_the_slicer():
    import pathlib
    import agent_friday.routes.chat as chat_mod
    src = pathlib.Path(chat_mod.__file__).read_text(encoding="utf-8-sig")
    send = src[src.index("def chat_send("):]
    assert "model=_slice_model" in send
    assert "_slice_head.is_local(provider_name, _slice_model)" in send


def test_opted_in_cloud_payload_is_a_subset_of_the_full_payload(home, monkeypatch, tmp_path):
    full = _prompt(monkeypatch, tmp_path)
    for scores in (_scores(), _scores(**{s: 0.4 for s in sh.SLICES}),
                   _scores(memory=0.9)):
        slicer = sh.Slicer(scores, turn_id="t1", cloud=True, opted=True)
        assert slicer.active
        sliced = _prompt(monkeypatch, tmp_path, slicer)
        extra = set(sliced.splitlines()) - set(sh.STUB_TEXT.splitlines())
        assert extra <= set(full.splitlines()), sorted(extra - set(full.splitlines()))
        assert len(sliced) <= len(full)


def test_withheld_sections_leave_no_trace_and_stubs_hold_no_content(home, monkeypatch, tmp_path):
    withheld = _prompt(monkeypatch, tmp_path,
                       sh.Slicer(_scores(), cloud=True, opted=True))
    for needle in ("zz-memory-alpha", "zz-task-bravo", "zz-career-charlie",
                   "zz-notes-delta", "zz-today-echo"):
        assert needle not in withheld
    assert sh.STUB_TEXT not in withheld   # a clear "no" is withheld, not stubbed
    unsure = _prompt(monkeypatch, tmp_path,
                     sh.Slicer(_scores(**{s: 0.4 for s in sh.SLICES}), cloud=True, opted=True))
    assert unsure.count(sh.STUB_TEXT) == 1   # one stub for any number of held sections
    for needle in ("zz-memory-alpha", "zz-task-bravo", "zz-career-charlie",
                   "zz-notes-delta", "zz-today-echo"):
        assert needle not in unsure


def test_the_floor_is_byte_present_and_the_head_and_policy_are_untouched(home, monkeypatch, tmp_path):
    from agent_friday.services.action_policy import ACTION_PERMISSION_POLICY, seal_system_prompt
    from agent_friday.services.prompt_cache import split_head_tail
    full = _prompt(monkeypatch, tmp_path)
    sliced = _prompt(monkeypatch, tmp_path, sh.Slicer(_scores(), cloud=True, opted=True))
    assert mr.FRIDAY_SYSTEM_PROMPT in sliced
    assert split_head_tail(sliced)[0] == split_head_tail(full)[0]
    # Sections the head does not name (honesty rules, the clock) are never sliced.
    assert "== CLOCK ==" in sliced
    assert ("== HONESTY ==" in sliced) == ("== HONESTY ==" in full)
    sealed = seal_system_prompt(sliced)
    assert sealed.endswith(ACTION_PERMISSION_POLICY)
    assert sealed.count(ACTION_PERMISSION_POLICY) == 1


def test_unnamed_sections_are_never_sliced():
    assert sh.section_slice("honesty") is None
    assert sh.section_slice("section_3") is None
    assert sh.section_slice("personal_context") is None      # the load-failed variant
    assert sh.section_slice("continuity_from_your_last_session_2026_01_01") == "continuity"


def test_apply_can_only_remove(home):
    sections = [(1, "head"), (1, "\n== CLOCK ==\nnow"),
                (2, "\n== RECENT MEMORIES ==\n- m"), (2, "\n== ACTIVE TASKS ==\n- t"),
                (1, "\n== HONESTY ==\nrules")]
    sl = sh.Slicer(_scores(memory=0.4, tasks=0.9), cloud=True, opted=True)
    out, info = sl.apply_sections(sections, 2)
    assert out[:2] == sections[:2]
    assert out[-1] == sections[-1]
    kept = [s for s in out if s != (1, sh.STUB_TEXT)]
    assert all(s in sections for s in kept)
    assert info[2]["planner"] == "stub" and info[3]["planner"] == "include"
    assert 4 not in info


# --- sticky slices -------------------------------------------------------------

def test_a_slice_sent_in_a_conversation_stays_for_the_follow_up(home):
    sections = [(1, "head"), (2, "\n== RECENT MEMORIES ==\n- m")]
    first = sh.Slicer(_scores(memory=0.9), conversation_id="c1", cloud=True, opted=True)
    assert first.apply_sections(sections, 1)[0] == sections
    # The follow-up scores low, but the slice was already sent in this chat.
    again = sh.Slicer(_scores(memory=0.02), conversation_id="c1", cloud=True, opted=True)
    assert again.plan["memory"]["reason"] == "sticky"
    assert again.apply_sections(sections, 1)[0] == sections
    # Another chat has nothing sticky; a reset forgets.
    other = sh.Slicer(_scores(memory=0.02), conversation_id="c2", cloud=True, opted=True)
    assert other.apply_sections(sections, 1)[0] == sections[:1]
    sh.reset("c1")
    after = sh.Slicer(_scores(memory=0.02), conversation_id="c1", cloud=True, opted=True)
    assert after.apply_sections(sections, 1)[0] == sections[:1]


def test_shadow_turns_make_nothing_sticky(home):
    sections = [(1, "head"), (2, "\n== RECENT MEMORIES ==\n- m")]
    sh.Slicer(_scores(memory=0.9), conversation_id="c1", cloud=True, opted=False
              ).apply_sections(sections, 1)
    assert sh.sticky_for("c1") == frozenset()


# --- the record ------------------------------------------------------------------

def test_the_plan_is_recorded_in_shadow_without_content(home):
    sl = sh.for_turn("what is on my calendar today", conversation_id="c1",
                     provider="cloud", turn_id="t-rec", settings={})
    assert not sl.active
    row = tp.get("t-rec")
    assert row and set(sh.SLICES) <= set(row["slices"])
    assert {"action", "p", "reason"} <= set(row["slices"]["calendar"])
    assert "slice_head" in row["timings_ms"]
    assert "calendar today" not in json.dumps(row)
    assert sh.last_turn_for("c1") == "t-rec"


def test_attach_keeps_the_rest_of_an_existing_plan(home):
    tp.record(tp.TurnPlan(plan_version=1, turn_id="t2", shape="conversation", effort="medium"))
    assert tp.attach_slices("t2", {"memory": {"action": "include", "p": 0.9, "reason": "needed"}}, 1.5)
    row = tp.get("t2")
    assert row["shape"] == "conversation" and row["effort"] == "medium"
    assert row["slices"]["memory"]["action"] == "include"


def test_ledger_rows_carry_planner_p_and_destination(home, monkeypatch, tmp_path):
    slicer = sh.Slicer(_scores(memory=0.9, tasks=0.1), turn_id="t3", cloud=True, opted=False)
    tp.begin_turn("t3")
    _prompt(monkeypatch, tmp_path, slicer)
    rows = {r["section"]: r for r in map(json.loads, (home / "retrieval-log.jsonl").read_text().splitlines())}
    mem, task = rows["recent_memories"], rows["active_tasks"]
    assert (mem["planner"], mem["p"], mem["applied"]) == ("include", 0.9, False)
    assert (task["planner"], task["p"], task["applied"]) == ("withhold", 0.1, False)
    assert mem["destination_class"] == "cloud"
    # A section the head never slices carries no planner reading.
    assert all("planner" not in r for name, r in rows.items() if sh.section_slice(name) is None)


def test_the_receipt_says_what_was_sent_in_shadow_and_when_applied(home, monkeypatch, tmp_path):
    for opted, turn in ((False, "t4"), (True, "t5")):
        tp.begin_turn(turn)
        _prompt(monkeypatch, tmp_path,
                sh.Slicer(_scores(memory=0.9, tasks=0.1), turn_id=turn, cloud=True, opted=opted))
    shadow = {s["name"]: s for s in tr.build("t4")["sections"]}
    applied = {s["name"]: s for s in tr.build("t5")["sections"]}
    assert shadow["active_tasks"]["planner"] == "withhold" and shadow["active_tasks"]["sent"] is True
    assert applied["active_tasks"]["planner"] == "withhold" and applied["active_tasks"]["sent"] is False
    assert applied["recent_memories"]["sent"] is True
    assert all(s["planner"] is None and s["sent"] is True
               for s in shadow.values() if sh.section_slice(s["name"]) is None)


def test_off_the_record_writes_nothing(home, monkeypatch):
    monkeypatch.setattr(off_record, "active", lambda settings=None: True)
    saved = []
    import agent_friday.core as core
    monkeypatch.setattr(core, "_save_settings", lambda d, **k: saved.append(d))
    tp.begin_turn("t6")           # off the record: no turn id
    assert tp.current_turn_id() is None
    sl = sh.for_turn("what is on my calendar today", conversation_id="c1", provider="cloud",
                     turn_id=tp.current_turn_id(), settings={"cloud_context_slicing": "slice"})
    assert sl.active                                   # applied, but recorded nowhere
    sections = [(1, "head"), (2, "\n== RECENT MEMORIES ==\n- m")]
    out, info = sl.apply_sections(sections, 1)
    assert out == sections[:1]
    assert info == {}                                  # no planner reading in the ledger
    assert sh.sticky_for("c1") == frozenset() and sh.withheld_for("c1") == frozenset()
    assert not (home / "plans.jsonl").exists()
    assert sh.note_omission("notes", "t6", tainted=False) is False
    assert not (home / "slice_labels.jsonl").exists()
    assert saved == []                                 # and no offer
    assert sh.maybe_offer({}, cloud=True) is False
    assert not (home / "retrieval-log.jsonl").exists()


# --- the one-time choice (D8) -------------------------------------------------

def test_the_offer_is_a_choice_at_the_next_cloud_turn_and_never_repeats(home, monkeypatch):
    import agent_friday.core as core
    saved = []
    monkeypatch.setattr(core, "_save_settings", lambda d, **k: saved.append(d))
    assert sh.should_offer({}, cloud=True)
    assert not sh.should_offer({}, cloud=False)
    assert not sh.should_offer({"cloud_context_slicing": "slice"}, cloud=True)
    assert not sh.should_offer({"cloud_context_slicing": "everything"}, cloud=True)
    assert not sh.should_offer({"cloud_context_slicing_offer": "pending"}, cloud=True)
    assert not sh.should_offer({"cloud_context_slicing_offer": "done"}, cloud=True)
    assert sh.maybe_offer({}, cloud=True) is True
    # Nothing is preselected: the offer only marks itself pending.
    assert saved == [{"cloud_context_slicing_offer": "pending"}]


def test_only_the_explicit_choice_turns_slicing_on():
    assert sh.opted_in({"cloud_context_slicing": "slice"})
    for s in ({}, None, {"cloud_context_slicing": ""}, {"cloud_context_slicing": "everything"},
              {"cloud_context_slicing_offer": "pending"}):
        assert not sh.opted_in(s)
    from agent_friday.core import DEFAULT_SETTINGS
    assert DEFAULT_SETTINGS["cloud_context_slicing"] == ""
    assert DEFAULT_SETTINGS["cloud_context_slicing_offer"] == ""


# --- asking for what was held back ---------------------------------------------

@pytest.fixture
def share(home, monkeypatch):
    """local_context with its card, grant and receipt stubbed out."""
    cards, sent = [], []
    from agent_friday.services import approvals
    monkeypatch.setattr(approvals, "create_approval",
                        lambda **kw: cards.append(kw) or {"approval_id": "ap-%d" % len(cards)})
    monkeypatch.setattr(lc, "sign_receipt", lambda *a, **k: sent.append(a[1]))
    monkeypatch.setattr(lc, "_audit", lambda *a, **k: None)
    grants = {"on": True, "asked": 0}

    def grant(conversation_id):
        grants["asked"] += 1
        return {"grant_id": "g1"} if grants["on"] else None

    monkeypatch.setattr(lc, "_use_conversation_grant", grant)
    # A section of notes was held back from this chat.
    sh.Slicer(_scores(), conversation_id="c1", cloud=True, opted=True).apply_sections(
        [(1, "head"), (2, "\n== WIKI/BRIEFING DATA ==\nzz")], 1)
    return {"cards": cards, "sent": sent, "grants": grants}


def _ask(**kw):
    return sh.request_context("notes", "planning a weekend", conversation_id="c1",
                              cloud_model="cloud-x", turn_id="t7",
                              answer_fn=lambda q: ("They like quiet cafes.", "local-y"), **kw)


def test_request_context_goes_through_local_context_and_a_grant_sends_it(share, home):
    out = _ask(tainted=False)
    assert out["status"] == "sent" and "quiet cafes" in out["text"]
    assert share["grants"]["asked"] == 1 and share["cards"] == []
    assert sh.take_released_text("c1") == "They like quiet cafes."
    labels = (home / "slice_labels.jsonl").read_text().splitlines()
    assert len(labels) == 1 and json.loads(labels[0])["slice"] == "wiki"


def test_request_context_without_a_grant_raises_a_card_for_the_chat_channel(share, home):
    share["grants"]["on"] = False
    out = _ask(tainted=False)
    assert out["status"] == "pending" and len(share["cards"]) == 1
    assert share["cards"][0]["payload"]["channel"] == "chat"
    assert share["sent"] == []                   # nothing left before the owner decides


def test_a_tainted_turn_always_raises_a_card_even_with_a_grant_and_writes_no_label(share, home):
    assert share["grants"]["on"]
    out = _ask(tainted=True)
    assert out["status"] == "pending" and len(share["cards"]) == 1
    assert share["grants"]["asked"] == 0         # the grant was never even consulted
    assert share["sent"] == []
    assert not (home / "slice_labels.jsonl").exists()


def test_only_a_category_that_was_held_back_can_be_requested(share):
    out = sh.request_context("career", "x", conversation_id="c1", cloud_model="m",
                             tainted=False, answer_fn=lambda q: ("t", "l"))
    assert out["status"] == "unavailable"
    out = sh.request_context("nonsense", "x", conversation_id="c1", cloud_model="m",
                             tainted=False)
    assert out["status"] == "unavailable"
    assert share["cards"] == [] and share["sent"] == []


def test_the_floor_still_stops_a_request_for_never_send_material(share):
    secret = "my card is 4111 1111 1111 1111"
    out = sh.request_context("notes", "x", conversation_id="c1", cloud_model="m",
                             tainted=False, answer_fn=lambda q: (secret, "l"))
    assert "4111" not in repr(share["sent"]) + repr(share["cards"]) + repr(out)


def test_an_approved_card_reaches_the_next_turn(share, monkeypatch):
    from agent_friday.services import approvals
    monkeypatch.setattr(approvals, "claim_for_execution", lambda aid: True)
    monkeypatch.setattr(approvals, "mark_used", lambda *a, **k: None)
    share["grants"]["on"] = False
    _ask(tainted=False)
    payload = share["cards"][0]["payload"]
    lc._on_decision({"approval_id": "ap-1", "status": "approved", "payload": payload})
    assert share["sent"] == ["They like quiet cafes."]       # receipt first, then delivery
    block = sh.render_released("c1", "turn-next")
    assert "CONTEXT THE USER APPROVED" in block and "quiet cafes" in block


def test_a_declined_card_says_only_that(share):
    share["grants"]["on"] = False
    _ask(tainted=False)
    payload = share["cards"][0]["payload"]
    lc._on_decision({"approval_id": "ap-1", "status": "declined", "payload": payload})
    note = sh.render_released("c1", "turn-next")
    assert "CONTEXT NOT SHARED" in note and "quiet cafes" not in note
    assert sh.render_released("c1", "turn-after") == ""      # shown once


def test_released_context_is_rendered_for_one_turn_only(home):
    assert sh.release_to_chat("c9", "approved text", "context")
    first = sh.render_released("c9", "turn-a")
    assert "approved text" in first
    assert "approved text" in sh.render_released("c9", "turn-a")   # a fallback leg of the same turn
    assert sh.render_released("c9", "turn-b") == ""


def test_taint_unknown_is_treated_as_tainted(monkeypatch):
    from agent_friday.services import taint
    monkeypatch.setattr(taint, "recent_content", lambda key: (_ for _ in ()).throw(RuntimeError("x")))
    assert sh.is_tainted("k") is True
    monkeypatch.setattr(taint, "recent_content", lambda key: [])
    assert sh.is_tainted("k") is False
    monkeypatch.setattr(taint, "recent_content", lambda key: ["a web page"])
    assert sh.is_tainted("k") is True


# --- reading the receipt back -----------------------------------------------------

def test_the_receipt_reads_back_counts_to_a_cloud_model_and_names_to_a_local_one():
    receipt = {"sections": [
        {"name": "recent_memories", "sent": True, "planner": "include", "applied": True,
         "destination_class": "cloud"},
        {"name": "active_tasks", "sent": False, "planner": "withhold", "applied": True,
         "destination_class": "cloud"}]}
    local = sh.describe_receipt(receipt, names=True)
    cloud = sh.describe_receipt(receipt, names=False)
    assert "active_tasks" in local
    assert "active_tasks" not in cloud and "recent_memories" not in cloud
    assert "1 were held back" in cloud
    assert "off the record" in sh.describe_receipt({"off_record": True}, names=True)

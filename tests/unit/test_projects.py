"""Projects — folders for chats, and the defaults that come with them.

Three things here are load-bearing and each has a way of going quietly wrong:

  * A project's default seat has to reach a turn. Per-chat bindings were
    decoration for months because the send path read a field nothing resolved
    (fixed 2026-09-18); a project default inherited through that same path is
    the identical trap one level up, so it is tested from the resolver the
    send path actually calls rather than from the stored field.

  * The default must be LIVE, not a stamp. Changing a project's model has to
    move the chats inside it immediately. A snapshot taken at filing time
    would look correct in every test that files and reads in one breath.

  * Deleting a folder must not delete the work in it. That is the one
    irreversible mistake available here.
"""
from __future__ import annotations

import pytest

from agent_friday.services import conversations as C
from agent_friday.services import projects as P


@pytest.fixture()
def store(tmp_path, monkeypatch):
    """Point both stores at a temp dir.

    Patched on each MODULE, not via an env var: `FRIDAY_DIR` is imported into
    these modules at import time and setting the environment does nothing.
    Learned the hard way on 2026-09-19, when a test run that thought it was
    sandboxed wrote four conversations into the real ~/.friday.
    """
    monkeypatch.setattr(C, "FRIDAY_DIR", str(tmp_path))
    monkeypatch.setattr(P, "FRIDAY_DIR", str(tmp_path))
    return tmp_path


def test_a_chat_inherits_its_projects_seat(store):
    """THE CRUX. A chat with no binding of its own, filed under a project that
    has one, runs on the project's model."""
    p = P.create("INNEX", seat={"model": "bonsai2:27b"})
    c = C.create("Brushfire")
    C.patch(c["id"], project=p["id"])
    assert C.effective_seat(c["id"]) == {"model": "bonsai2:27b"}


def test_the_chats_own_binding_beats_its_project(store):
    """Pinning one thread in a Bonsai project to a cloud model has to work, or
    the folder becomes a cage rather than a default."""
    p = P.create("INNEX", seat={"model": "bonsai2:27b"})
    c = C.create("Exception", seat={"model": "claude-sonnet-5"})
    C.patch(c["id"], project=p["id"])
    assert C.effective_seat(c["id"]) == {"model": "claude-sonnet-5"}


def test_a_loose_chat_has_no_seat_at_all(store):
    """None means "follow the global default", which is what the router
    already reads. Returning something here would override a global the user
    set deliberately."""
    assert C.effective_seat(C.create("Loose")["id"]) is None


def test_a_project_with_no_seat_does_not_invent_one(store):
    p = P.create("Just a folder")
    c = C.create("x")
    C.patch(c["id"], project=p["id"])
    assert C.effective_seat(c["id"]) is None


def test_changing_the_default_moves_the_chats_now(store):
    """RESOLVED PER TURN, NEVER SNAPSHOTTED. Renaming a project's model has to
    affect chats filed before the change, or it is not a default."""
    p = P.create("INNEX", seat={"model": "bonsai2:27b"})
    c = C.create("Filed first")
    C.patch(c["id"], project=p["id"])
    assert C.effective_seat(c["id"])["model"] == "bonsai2:27b"
    P.patch(p["id"], seat={"model": "gemma4:e2b"})
    assert C.effective_seat(c["id"])["model"] == "gemma4:e2b", \
        "the project default was snapshotted at filing time"


def test_clearing_a_projects_seat_drops_the_chats_to_the_global_default(store):
    p = P.create("INNEX", seat={"model": "bonsai2:27b"})
    c = C.create("x")
    C.patch(c["id"], project=p["id"])
    P.patch(p["id"], seat=None)
    assert C.effective_seat(c["id"]) is None


def test_deleting_a_project_keeps_every_chat(store):
    """The one irreversible mistake available here, and the reason delete
    detaches instead of cascading."""
    p = P.create("Doomed", seat={"model": "bonsai2:27b"})
    kept = [C.create("keep %d" % i) for i in range(3)]
    for c in kept:
        C.patch(c["id"], project=p["id"])
    C.append(kept[0]["id"], {"role": "user", "text": "something I said"})

    detached = P.delete(p["id"])

    assert detached == 3
    assert P.load(p["id"]) is None
    for c in kept:
        assert C.load(c["id"]) is not None, "a chat died with its folder"
        assert C.load(c["id"]).get("project") is None
    assert [m["text"] for m in C.messages(kept[0]["id"])] == ["something I said"]
    assert C.effective_seat(kept[0]["id"]) is None


def test_deleting_a_project_leaves_a_chats_own_binding_alone(store):
    p = P.create("Doomed", seat={"model": "bonsai2:27b"})
    c = C.create("bound", seat={"model": "claude-sonnet-5"})
    C.patch(c["id"], project=p["id"])
    P.delete(p["id"])
    assert C.effective_seat(c["id"]) == {"model": "claude-sonnet-5"}


def test_filing_and_pinning_do_not_restamp_last_active(store):
    """Tidying the sidebar must not reorder the thing being tidied.

    The list is sorted by `last_active_at`. If filing a chat counted as
    working in it, every chat you moved would jump to the top and the folder
    you just organised would rearrange itself under your hand.
    """
    p = P.create("Folder")
    c = C.create("quiet")
    was = C.load(c["id"])["last_active_at"]
    C.patch(c["id"], project=p["id"])
    C.patch(c["id"], pinned_at=123456.0)
    assert C.load(c["id"])["last_active_at"] == was
    # But renaming or rebinding IS working in it.
    C.patch(c["id"], title="renamed")
    assert C.load(c["id"])["last_active_at"] > was


def test_thread_pinning_does_not_collide_with_message_pinning(store):
    """`pinned` at the conversation level holds pinned MESSAGE ids and drives
    clear/prune. Thread pinning is `pinned_at`. They were one word before
    2026-09-19, and the list endpoint flattened the message list to a boolean
    that read as "this thread is pinned" and was always False."""
    c = C.create("x")
    C.append(c["id"], {"role": "user", "text": "keep me", "pinned": True})
    C.append(c["id"], {"role": "user", "text": "drop me"})
    C.patch(c["id"], pinned_at=999.0)

    assert C.load(c["id"])["pinned_at"] == 999.0
    C.clear(c["id"])                       # spares pinned MESSAGES
    assert [m["text"] for m in C.messages(c["id"])] == ["keep me"]
    assert C.load(c["id"])["pinned_at"] == 999.0, \
        "clearing messages unpinned the thread"


def test_project_instructions_reach_only_their_own_chats(store):
    p = P.create("Terse", instructions="Be brief.")
    inside, outside = C.create("in"), C.create("out")
    C.patch(inside["id"], project=p["id"])
    assert C.project_instructions(inside["id"]) == "Be brief."
    assert C.project_instructions(outside["id"]) == ""


def test_instructions_are_capped(store):
    """Charged as input tokens on every turn in the project, so a runaway
    paste would quietly double the cost of a cloud-seated folder."""
    p = P.create("Big", instructions="x" * (P.MAX_INSTRUCTIONS + 5000))
    assert len(P.load(p["id"])["instructions"]) == P.MAX_INSTRUCTIONS


def test_member_count_ignores_other_projects(store):
    a, b = P.create("A"), P.create("B")
    for _ in range(2):
        C.patch(C.create("x")["id"], project=a["id"])
    C.patch(C.create("y")["id"], project=b["id"])
    assert P.member_count(a["id"]) == 2
    assert P.member_count(b["id"]) == 1


def test_a_dangling_project_id_is_survivable(store):
    """If a project record goes missing under a chat, the chat still answers.
    Falling back to the global default beats raising on the send path."""
    c = C.create("orphan")
    C.patch(c["id"], project="proj-nothing-here")
    assert C.effective_seat(c["id"]) is None
    assert C.project_instructions(c["id"]) == ""


def test_effective_seat_of_matches_effective_seat(store):
    """The list endpoint uses the cached variant so summarising N chats does
    not cost 2N file reads. If the two ever disagree, the sidebar shows one
    model and the turn runs on another."""
    p = P.create("INNEX", seat={"model": "bonsai2:27b"})
    rows = []
    for i in range(3):
        c = C.create("c%d" % i, seat={"model": "claude-sonnet-5"} if i == 1 else None)
        C.patch(c["id"], project=p["id"] if i < 2 else None)
        rows.append(c["id"])
    cache = {p["id"]: p["seat"]}
    for cid in rows:
        assert C.effective_seat_of(C.load(cid), cache) == C.effective_seat(cid)

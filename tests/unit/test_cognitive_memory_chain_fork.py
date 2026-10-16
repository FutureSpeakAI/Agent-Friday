"""The memory tamper-check: two writers must not fork it, and a break must say what it is.

A fork is an entry that links to an earlier entry than the one before it,
while every entry still matches its own hash: a writer continued from an
older copy of the chain's end. These tests pin:

  * a second writer (another CognitiveMemory on the same file, standing in for
    another process) does not fork the chain, nor do real concurrent processes;
  * verify_chain names a fork as a fork (nothing edited), an in-place edit as
    an edit, and a torn line as unreadable, instead of one bare "invalid";
  * a fork is called a same-moment race only when its two branches were
    written within FORK_SAME_MOMENT_S of each other, and an append made
    without the ledger lock is marked in the entry and named;
  * a last line with no newline never swallows the next entry, and a line
    that is not an entry is reported, never crashed on;
  * a write that failed does not move the chain's end;
  * a broken chain reaches the person: /api/health's data_integrity block and
    one Needs-you card (emitter DI1), never just a field nobody reads.

All on temp dirs; nothing under ~/.friday is touched.
"""
from __future__ import annotations

import hashlib
import json
import multiprocessing as mp
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "src"))

from agent_friday.cognitive_memory import CognitiveMemory  # noqa: E402


def _entry(prev: str, i: int) -> dict:
    e = {"op": "write", "key": "k%d" % i, "content_hash": "c%d" % i,
         "source_id": "skill_capture", "ts": 1786000000.0 + i,
         "ts_iso": "2026-08-19T20:00:%02dZ" % (i % 60), "metadata": {},
         "prev_hash": prev}
    e["entry_hash"] = hashlib.sha256(json.dumps(e, sort_keys=True).encode("utf-8")).hexdigest()
    return e


def _write_ledger(path: Path, entries: list) -> None:
    path.write_text("".join(json.dumps(e) + "\n" for e in entries), encoding="utf-8")


def _live_shape(n: int = 360, fork_at: int = 351, links_to: int = 348) -> list:
    """A ledger with the live break: entry `fork_at` links to `links_to`."""
    out, prev = [], "genesis"
    for i in range(n):
        p = out[links_to]["entry_hash"] if i == fork_at else prev
        e = _entry(p, i)
        out.append(e)
        prev = e["entry_hash"]
    return out


# ── the cause: a stale chain end in a second writer ─────────────────────────

def test_a_second_writer_on_the_same_ledger_does_not_fork_the_chain(tmp_path):
    a = CognitiveMemory(memory_dir=tmp_path)
    b = CognitiveMemory(memory_dir=tmp_path)      # another process, same file
    a.write_memory("one", "1", source_id="skill_capture")
    b.write_memory("two", "2", source_id="skill_capture")
    b.write_memory("three", "3", source_id="skill_capture")
    a.write_memory("four", "4", source_id="skill_capture")   # a still remembers "one"
    chain = a.verify_chain()
    assert chain["valid"] is True, chain
    assert chain["entries"] == 4


def _child(memory_dir: str, tag: str, n: int) -> None:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "src"))
    from agent_friday.cognitive_memory import CognitiveMemory as CM
    cm = CM(memory_dir=memory_dir)
    for i in range(n):
        cm.write_memory("%s-%d" % (tag, i), "x", source_id="skill_capture")


def test_two_real_processes_writing_at_once_leave_one_valid_chain(tmp_path):
    ctx = mp.get_context("spawn")
    procs = [ctx.Process(target=_child, args=(str(tmp_path), t, 25)) for t in ("a", "b")]
    for p in procs:
        p.start()
    for p in procs:
        p.join(120)
        assert p.exitcode == 0
    chain = CognitiveMemory(memory_dir=tmp_path).verify_chain()
    assert chain["entries"] == 50
    assert chain["valid"] is True, chain["breaks"][:3]


def test_a_failed_append_does_not_move_the_chain_end(tmp_path, monkeypatch):
    cm = CognitiveMemory(memory_dir=tmp_path)
    cm.write_memory("one", "1")
    import builtins
    real_open = builtins.open
    state = {"fail": True}

    def flaky(path, mode="r", *a, **k):
        if state["fail"] and str(path).endswith("memory_ledger.jsonl") and "a" in mode:
            state["fail"] = False
            raise OSError("disk full")
        return real_open(path, mode, *a, **k)

    monkeypatch.setattr(builtins, "open", flaky)
    cm.write_memory("two", "2")                    # its ledger line is lost
    monkeypatch.setattr(builtins, "open", real_open)
    cm.write_memory("three", "3")
    assert cm.verify_chain()["valid"] is True


# ── what a break IS ────────────────────────────────────────────────────────

def test_the_live_break_is_named_a_fork_with_nothing_edited(tmp_path):
    path = tmp_path / "memory_ledger.jsonl"
    _write_ledger(path, _live_shape())
    cm = CognitiveMemory(memory_dir=tmp_path, ledger_path=path)
    chain = cm.verify_chain()
    assert chain["valid"] is False
    assert chain["break_at"] == 351
    assert len(chain["breaks"]) == 1
    b = chain["breaks"][0]
    assert (b["at"], b["kind"], b["links_to"]) == (351, "fork", 348)
    said = cm.chain_problem()
    assert "entry 351 links to entry 348 instead of 350" in said
    assert "nothing was edited" in said


def test_an_entry_edited_in_place_is_caught_even_with_its_links_intact(tmp_path):
    """Old verify_chain checked links only: change an entry's key, leave its
    entry_hash alone, and the chain still read as valid."""
    cm = CognitiveMemory(memory_dir=tmp_path)
    for i in range(3):
        cm.write_memory("k%d" % i, "v%d" % i)
    lines = cm.ledger_path.read_text(encoding="utf-8").splitlines()
    e = json.loads(lines[1])
    e["key"] = "something-else"
    lines[1] = json.dumps(e)
    cm.ledger_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    chain = cm.verify_chain()
    assert chain["valid"] is False
    assert {"at": 1, "kind": "altered"} in chain["breaks"]
    assert "entry 1 was changed after it was written" in cm.chain_problem()


def test_a_torn_line_is_reported_not_skipped(tmp_path):
    """A write cut off by a crash used to be dropped by the reader, so the
    chain read as valid with an entry silently missing."""
    cm = CognitiveMemory(memory_dir=tmp_path)
    cm.write_memory("k0", "v0")
    with open(cm.ledger_path, "a", encoding="utf-8") as f:
        f.write('{"op": "write", "key": "k1", "conte')
    chain = cm.verify_chain()
    assert chain["valid"] is False
    assert chain["unreadable_lines"] == 1
    assert any(b["kind"] == "unreadable" for b in chain["breaks"])


def test_appending_after_a_torn_line_links_to_the_last_whole_entry(tmp_path):
    cm = CognitiveMemory(memory_dir=tmp_path)
    first = cm.write_memory("k0", "v0")
    with open(cm.ledger_path, "a", encoding="utf-8") as f:
        f.write('{"op": "write", "torn\n')
    nxt = cm.write_memory("k1", "v1")
    assert nxt["prev_hash"] == first["entry_hash"]


def test_health_lists_each_break(tmp_path):
    path = tmp_path / "memory_ledger.jsonl"
    _write_ledger(path, _live_shape())
    h = CognitiveMemory(memory_dir=tmp_path, ledger_path=path).health()
    assert h["chain_valid"] is False and h["chain_break_at"] == 351
    assert h["chain_breaks"][0]["kind"] == "fork"


# ── said out loud ──────────────────────────────────────────────────────────

@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("FRIDAY_HOME", str(tmp_path))
    (tmp_path / "memory").mkdir()
    import agent_friday.cognitive_memory as cmod
    cmod._PROBE_CACHE.clear()
    from agent_friday.services import integrity_report as ir
    monkeypatch.setattr(ir, "_ANNOUNCED", set())
    return tmp_path


def test_a_broken_chain_is_in_the_health_data_integrity_block(home):
    _write_ledger(home / "memory" / "memory_ledger.jsonl", _live_shape())
    from agent_friday.services import integrity_report as ir
    field = ir.health_field()
    assert field["ok"] is False
    p = [x for x in field["problems"] if x["store"] == "memory tamper-check"][0]
    assert p["state"] == "chain_broken" and p["kinds"] == ["fork"]
    assert p["records"] == 360
    assert "nothing was edited" in p["message"]


def test_an_intact_chain_reports_nothing(home):
    cm = CognitiveMemory(memory_dir=home / "memory")
    cm.write_memory("k", "v")
    from agent_friday.services import integrity_report as ir
    assert not [x for x in ir.health_field()["problems"] if x["store"] == "memory tamper-check"]


def test_a_broken_chain_raises_one_needs_you_card_not_one_per_check(home, monkeypatch):
    _write_ledger(home / "memory" / "memory_ledger.jsonl", _live_shape())
    pushed = []
    from agent_friday import notifications_engine
    monkeypatch.setattr(notifications_engine, "push", lambda **kw: pushed.append(kw))
    from agent_friday.services import integrity_report as ir
    ir.announce()
    ir.announce()
    cards = [k for k in pushed if k.get("emitter") == "DI1"]
    assert len(cards) == 1
    assert cards[0]["dedupe_key"] == "data-integrity:memory tamper-check"
    assert "nothing was edited" in cards[0]["body"]


def test_the_card_is_a_registered_needs_you_emitter():
    from agent_friday.services import notice_registry as reg
    em = reg.get("DI1")
    assert em is not None and em.place == reg.NEEDS_YOU
    assert not em.safety_critical


def test_a_probe_that_raises_is_a_problem_not_a_pass(monkeypatch):
    from agent_friday.services import integrity_report as ir
    monkeypatch.setattr(ir, "_PROBES", {"boom": lambda: 1 / 0})
    out = ir.problems()
    assert out and out[0]["state"] == "check_failed" and out[0]["store"] == "boom"


# ── how a fork is worded ───────────────────────────────────────────────────

def _fork_ledger(path: Path, gap_s: float, fork_meta: dict | None = None) -> None:
    """Entries 0..3 chained; entry 4 links to entry 1, written ``gap_s`` after
    entry 2 (the first entry of the other branch)."""
    out, prev = [], "genesis"
    for i in range(4):
        e = _entry(prev, i)
        out.append(e)
        prev = e["entry_hash"]
    e = {"op": "write", "key": "k4", "content_hash": "c4", "source_id": "skill_capture",
         "ts": out[2]["ts"] + gap_s, "ts_iso": "2026-08-19T20:59:03Z",
         "metadata": dict(fork_meta or {}), "prev_hash": out[1]["entry_hash"]}
    e["entry_hash"] = hashlib.sha256(json.dumps(e, sort_keys=True).encode("utf-8")).hexdigest()
    out.append(e)
    _write_ledger(path, out)


def test_a_fork_minutes_apart_is_not_called_a_same_moment_race(tmp_path):
    path = tmp_path / "memory_ledger.jsonl"
    _fork_ledger(path, 660.0)
    said = CognitiveMemory(memory_dir=tmp_path, ledger_path=path).chain_problem()
    assert "entry 4 links to entry 1 instead of 3" in said
    assert "same moment" not in said
    assert "11 min apart" in said and "older copy of the chain's end" in said
    assert "nothing was edited" in said


def test_a_fork_within_the_window_is_consistent_with_two_writers(tmp_path):
    path = tmp_path / "memory_ledger.jsonl"
    _fork_ledger(path, 0.4)
    said = CognitiveMemory(memory_dir=tmp_path, ledger_path=path).chain_problem()
    assert "consistent with two writers appending at the same moment" in said


def test_a_fork_at_an_unlocked_append_says_so(tmp_path):
    path = tmp_path / "memory_ledger.jsonl"
    _fork_ledger(path, 660.0, {"ledger_lock": "timeout"})
    said = CognitiveMemory(memory_dir=tmp_path, ledger_path=path).chain_problem()
    assert "without the ledger lock" in said


# ── the lock that could not be taken ───────────────────────────────────────

def test_an_append_that_could_not_take_the_lock_is_marked(tmp_path, monkeypatch):
    import agent_friday.cognitive_memory as cmod
    monkeypatch.setattr(cmod._ledger_file_lock, "TIMEOUT_S", 0.05, raising=False)
    cm = CognitiveMemory(memory_dir=tmp_path)
    first = cm.write_memory("k0", "v0")
    holder = cmod._ledger_file_lock(cm.ledger_path)     # another process's hold
    holder.__enter__()
    try:
        nxt = cm.write_memory("k1", "v1")
    finally:
        holder.__exit__(None, None, None)
    assert nxt["metadata"].get("ledger_lock") == "timeout"
    assert nxt["prev_hash"] == first["entry_hash"]
    assert cm.verify_chain()["valid"] is True


# ── a last line with no newline ────────────────────────────────────────────

def test_a_last_entry_without_its_newline_does_not_swallow_the_next(tmp_path):
    cm = CognitiveMemory(memory_dir=tmp_path)
    cm.write_memory("k0", "v0")
    raw = cm.ledger_path.read_bytes()
    cm.ledger_path.write_bytes(raw.rstrip(b"\n"))        # cut before the newline
    cm.write_memory("k1", "v1")
    chain = cm.verify_chain()
    assert chain["valid"] is True and chain["entries"] == 2


def test_a_torn_last_line_is_reported_and_the_next_entry_survives(tmp_path):
    cm = CognitiveMemory(memory_dir=tmp_path)
    first = cm.write_memory("k0", "v0")
    with open(cm.ledger_path, "a", encoding="utf-8") as f:
        f.write('{"op": "write", "key": "k1", "conte')    # no newline
    nxt = cm.write_memory("k2", "v2")
    chain = cm.verify_chain()
    assert chain["entries"] == 2 and chain["unreadable_lines"] == 1
    assert [b["kind"] for b in chain["breaks"]] == ["unreadable"]
    assert nxt["prev_hash"] == first["entry_hash"]
    assert "cannot be read" in cm.chain_problem()


def test_a_line_that_is_json_but_not_an_entry_is_reported_not_crashed_on(tmp_path):
    cm = CognitiveMemory(memory_dir=tmp_path)
    first = cm.write_memory("k0", "v0")
    with open(cm.ledger_path, "a", encoding="utf-8") as f:
        f.write("17")                                    # torn to a bare number
    again = CognitiveMemory(memory_dir=tmp_path)         # reads the tip at start
    nxt = again.write_memory("k1", "v1")
    assert nxt["prev_hash"] == first["entry_hash"]
    chain = again.verify_chain()
    assert chain["entries"] == 2 and chain["unreadable_lines"] == 1

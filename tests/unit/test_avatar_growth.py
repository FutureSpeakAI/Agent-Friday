"""The weekly step (avatar-visual-genome.md §4, §5): who authors it, when it
runs, what it may send, and what happens when it cannot run."""
import json
import time

import pytest

from agent_friday.services import avatar_genome as g
from agent_friday.services import avatar_growth as gr

WEEK = 7 * 86400


@pytest.fixture
def home(tmp_path, monkeypatch):
    from agent_friday.governance.proof_of_integrity import IntegrityEngine
    eng = IntegrityEngine(friday_dir=tmp_path / "identity")
    monkeypatch.setattr(g, "_engine", lambda: eng)
    monkeypatch.setattr(g, "AVATAR_DIR", tmp_path / "avatar")
    monkeypatch.setattr(gr, "EVOLUTION_FILE", tmp_path / "evolution.json")
    sent = []
    monkeypatch.setattr(gr, "_notify", lambda title, body, key: sent.append((title, body, key)))
    monkeypatch.setattr(gr, "_idle_blocked", lambda: "")
    monkeypatch.setattr(gr, "_local_seat", lambda: None)
    return {"dir": tmp_path, "notes": sent}


@pytest.fixture
def cloud(monkeypatch):
    """A frontier model that answers; records what it was sent."""
    calls = []

    def call(author, system, user):
        calls.append({"author": author, "system": system, "user": user})
        payload = json.loads(user)
        prop = payload["genome"]
        prop["palette"]["base_offset"] = prop["palette"]["base_offset"] + 4
        return (json.dumps({"proposed_genome": prop, "rationale": "a little bluer",
                            "name": "Tidewater"}), {"model": "claude-opus-5-5"})
    monkeypatch.setattr(gr, "_frontier_available", lambda: (True, "", ["anthropic:claude-opus-5-5"]))
    monkeypatch.setattr(gr, "_call_model", call)
    return calls


def _start(now):
    gr.settings(now=now)


# ── on by default; the first step a week after the announcement ─────────────

def test_a_fresh_install_is_on_with_frontier_by_default_and_waits_a_week(home):
    s = gr.settings(now=1000.0)
    assert s["enabled"] is True and s["author"] == "frontier" and s["apply_mode"] == "auto"
    assert s["last_step_at"] == 1000.0 and s["announced_at"] == 1000.0
    assert not gr.due(now=1000.0 + WEEK - 1)
    assert gr.due(now=1000.0 + WEEK)


# ── catch-up: one step, not a burst ──────────────────────────────────────────

def test_weeks_away_catch_up_with_exactly_one_step(home):
    gr.set_settings(author="seeded", now=0.0)
    gr.settings(now=0.0)
    out = gr.tick(now=5 * WEEK + 10)
    assert out["status"] == "stepped"
    assert gr.settings()["last_step_at"] == 5 * WEEK + 10
    assert gr.tick(now=5 * WEEK + 3600)["status"] == "not_due"
    assert len(g.history()) == 1


# ── frontier by default; waiting, never a silent switch ──────────────────────

def test_no_frontier_model_means_waiting_with_both_fixes_and_no_other_author(home, monkeypatch):
    monkeypatch.setattr(gr, "_frontier_available",
                        lambda: (False, "no cloud model is connected", []))
    monkeypatch.setattr(gr, "_local_seat", lambda: "gemma-local")
    spied = []
    monkeypatch.setattr(gr, "_seeded_propose", lambda *a, **k: spied.append("seeded"))
    monkeypatch.setattr(gr, "_call_model", lambda *a, **k: spied.append("model"))
    gr.settings(now=0.0)
    out = gr.tick(now=WEEK + 1)
    assert out["status"] == "waiting"
    assert spied == []
    assert gr.settings()["last_step_at"] == 0.0
    assert set(out["fixes"]) == {"connect_cloud", "use_local:gemma-local"}
    assert len(home["notes"]) == 1 and "waiting for a cloud model" in home["notes"][0][0].lower()
    gr.tick(now=WEEK + 3600)
    assert len(home["notes"]) == 1                     # at most once a week
    assert g.history() == []


def test_use_local_instead_sets_the_author_and_runs_the_waiting_step(home, monkeypatch):
    monkeypatch.setattr(gr, "_frontier_available", lambda: (False, "no key", []))
    monkeypatch.setattr(gr, "_local_seat", lambda: "gemma-local")
    monkeypatch.setattr(gr, "_local_available", lambda model: True)
    used = []

    def call(author, system, user):
        used.append(author)
        prop = json.loads(user)["genome"]
        prop["form"]["coherence"] = 0.58
        return json.dumps({"proposed_genome": prop, "rationale": "tighter", "name": "Knot"}), {}
    monkeypatch.setattr(gr, "_call_model", call)
    gr.settings(now=0.0)
    gr.tick(now=WEEK + 1)
    out = gr.use_local_instead("gemma-local", now=WEEK + 2)
    assert out["status"] == "stepped"
    assert used == ["local:gemma-local"]
    assert gr.settings()["author"] == "local:gemma-local"          # the choice sticks
    step = g.active_step()
    assert step["author"]["path"] == "local" and step["author"]["model"] == "gemma-local"


def test_a_frontier_step_is_credited_to_the_model_the_provider_reported(home, cloud):
    gr.settings(now=0.0)
    out = gr.tick(now=WEEK + 1)
    assert out["status"] == "stepped"
    step = g.active_step()
    assert step["author"]["path"] == "cloud"
    assert step["author"]["model"] == "claude-opus-5-5"
    assert step["author"]["why_this_model"]
    assert step["name"] == "Tidewater" and "bluer" in step["reason"]
    assert step["genome"]["palette"]["base_offset"] == 4


def test_a_silently_substituted_model_is_shown_with_a_warning(home, monkeypatch, cloud):
    orig = gr._call_model

    def swapped(author, system, user):
        text, rep = orig(author, system, user)
        return text, {"model": "some-other-model"}
    monkeypatch.setattr(gr, "_call_model", swapped)
    gr.settings(now=0.0)
    gr.tick(now=WEEK + 1)
    a = g.active_step()["author"]
    assert a["model"] == "some-other-model" and a["requested"] == "claude-opus-5-5"
    assert a["warning"]


def test_what_leaves_the_machine_is_numbers_and_enums_only(home, cloud):
    gr.settings(now=0.0)
    gr.tick(now=WEEK + 1)
    sent = json.loads(cloud[0]["user"])
    assert gr.payload_problems(sent) == []
    raw = cloud[0]["user"]
    assert g.seed().hex() not in raw and "sigil" not in raw


def test_the_payload_allowlist_refuses_free_text():
    good = gr.build_payload(g.defaults(), target="CUBES", step_number=2,
                            signals={"turns": 3}, simplify=False)
    assert gr.payload_problems(good) == []
    bad = json.loads(json.dumps(good))
    bad["signals"]["note"] = "what the user said"
    assert gr.payload_problems(bad)
    bad = json.loads(json.dumps(good))
    bad["target_structure"] = "rm -rf /"
    assert gr.payload_problems(bad)


def test_a_failed_model_call_skips_the_week_and_leaves_the_look(home, monkeypatch, cloud):
    def boom(*a, **k):
        raise RuntimeError("provider down")
    monkeypatch.setattr(gr, "_call_model", boom)
    gr.settings(now=0.0)
    out = gr.tick(now=WEEK + 1)
    assert out["status"] == "skipped"
    assert g.is_v1(g.active_genome())
    assert gr.settings()["last_step_at"] == WEEK + 1
    assert gr.settings()["skips"][-1]["reason"]


def test_invalid_output_is_retried_once_with_the_same_model(home, monkeypatch, cloud):
    seen = []

    def bad(author, system, user):
        seen.append(author)
        return "not json at all", {"model": "claude-opus-5-5"}
    monkeypatch.setattr(gr, "_call_model", bad)
    gr.settings(now=0.0)
    out = gr.tick(now=WEEK + 1)
    assert out["status"] == "skipped"
    assert len(seen) == 2 and len(set(seen)) == 1


# ── the author is the user's choice, and it sticks ───────────────────────────

@pytest.mark.parametrize("author", ["frontier", "seeded", "local:gemma4:e2b",
                                    "cloud:anthropic:claude-opus-5-5",
                                    "cloud:openrouter:openai/gpt-6"])
def test_any_model_may_be_chosen(home, author):
    gr.set_settings(author=author)
    assert gr.settings()["author"] == author


@pytest.mark.parametrize("author", ["", "gpt", "cloud:evil.example:model", "local:",
                                    "cloud:anthropic:", "local:a b"])
def test_a_malformed_author_is_refused(home, author):
    with pytest.raises(ValueError):
        gr.set_settings(author=author)


def test_the_seeded_author_needs_no_model_and_is_deterministic(home):
    gen = g.defaults()
    a = gr._seeded_propose(gen, target="CUBES", signals={}, step_number=3, simplify=False)
    b = gr._seeded_propose(gen, target="CUBES", signals={}, step_number=3, simplify=False)
    assert a == b
    assert a["proposed_genome"] != gen


# ── evolve now, ask-first, undo ──────────────────────────────────────────────

def test_evolve_now_runs_at_once_and_restarts_the_week(home):
    gr.set_settings(author="seeded", now=0.0)
    gr.settings(now=0.0)
    out = gr.evolve_now(now=3600.0)
    assert out["status"] == "stepped"
    assert gr.settings()["last_step_at"] == 3600.0
    assert not gr.due(now=3600.0 + WEEK - 60)


def test_evolution_off_means_no_step(home):
    gr.set_settings(author="seeded", enabled=False)
    gr.settings(now=0.0)
    assert gr.tick(now=10 * WEEK)["status"] == "off"
    assert g.history() == []


def test_ask_first_keeps_the_step_pending_until_applied(home):
    gr.set_settings(author="seeded", apply_mode="ask", now=0.0)
    gr.settings(now=0.0)
    out = gr.tick(now=WEEK + 1)
    assert out["status"] == "pending"
    assert g.is_v1(g.active_genome())
    gr.apply_pending()
    assert not g.is_v1(g.active_genome())


def test_declining_remembers_the_kind_of_change(home):
    gr.set_settings(author="seeded", apply_mode="ask", now=0.0)
    gr.settings(now=0.0)
    gr.tick(now=WEEK + 1)
    gr.decline_pending(now=WEEK + 2)
    st = gr.settings()
    assert st["pending"] is None and st["declined"]


def test_the_step_changes_only_the_structure_on_screen(home):
    (home["dir"] / "evolution.json").write_text(json.dumps({"preferred_scene_index": 8}))
    gr.set_settings(author="seeded", now=0.0)
    gr.settings(now=0.0)
    gr.tick(now=WEEK + 1)
    step = g.active_step()
    assert step["target_structure"] == "MOBIUS"
    for sid in g.STRUCTURE_IDS:
        if sid != "MOBIUS":
            assert step["genome"]["structures"][sid] == g.defaults()["structures"][sid]


# ── nightly signals: numbers only, off the record excluded ───────────────────

def test_nightly_signals_count_only_on_record_activity(home, monkeypatch):
    rows = [
        {"kind": "model_invocation", "ts": 100.0, "provider": "ollama"},
        {"kind": "model_invocation", "ts": 110.0, "provider": "anthropic"},
        {"kind": "tool_call", "ts": 120.0, "ok": True},
        {"kind": "tool_call", "ts": 121.0, "ok": False},
        {"kind": "subagent_spawn", "ts": 130.0},
        {"kind": "model_invocation", "ts": 140.0, "off_record": True},
        {"kind": "tool_call", "ts": 141.0, "off_record": True, "ok": True},
    ]
    monkeypatch.setattr(gr, "_ledger_rows", lambda since, until: rows)
    row = gr.nightly_signals(day="2026-09-30")
    assert row == {"turns": 2, "tools": 2, "tools_failed": 1, "subagents": 1, "local_calls": 1}
    saved = json.loads((g.AVATAR_DIR / "signals" / "2026-09-30.json").read_text())
    assert saved == row


def test_a_day_entirely_off_the_record_writes_nothing(home, monkeypatch):
    monkeypatch.setattr(gr, "_ledger_rows", lambda since, until: [
        {"kind": "model_invocation", "ts": 1.0, "off_record": True}])
    assert gr.nightly_signals(day="2026-09-30") is None
    assert not (g.AVATAR_DIR / "signals" / "2026-09-30.json").exists()


def test_a_step_consumes_the_signal_rows_it_used(home):
    sig = g.AVATAR_DIR / "signals"
    sig.mkdir(parents=True)
    (sig / "2026-09-29.json").write_text(json.dumps({"turns": 4}))
    gr.set_settings(author="seeded", now=0.0)
    gr.settings(now=0.0)
    gr.tick(now=WEEK + 1)
    assert list(sig.glob("*.json")) == []


# ── what was sent, and the trash purge ───────────────────────────────────────

def test_a_cloud_step_keeps_exactly_what_was_sent(home, cloud):
    gr.settings(now=0.0)
    gr.tick(now=WEEK + 1)
    step = g.active_step()
    assert step["sent"] == json.loads(cloud[0]["user"])
    assert gr.payload_problems(step["sent"]) == []


def test_a_local_or_seeded_step_sent_nothing(home):
    gr.set_settings(author="seeded", now=0.0)
    gr.tick(now=WEEK + 1)
    assert g.active_step()["sent"] is None


def test_the_nightly_job_purges_only_expired_user_deletions(home, monkeypatch):
    monkeypatch.setattr(gr, "_ledger_rows", lambda since, until: [])
    calls = []
    monkeypatch.setattr(g, "purge", lambda **k: calls.append(k) or 0)
    gr.nightly()
    assert calls == [{}]

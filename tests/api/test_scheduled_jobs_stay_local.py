"""The four costly jobs run on the local seat, and never quietly on a paid one.

Stephen, 2026-09-24: "daily creation, briefings, and news/front page should
default to the local reasoning model to eliminate cost. So should the heartbeat."
With it: they must NOT silently fall back to cloud. If the local model is down or
busy they wait and retry inside their window, then skip with a visible reason.
Cloud only if a job is explicitly opted in.

WHAT THE LEDGER ACTUALLY SHOWED, before writing any of this. `local_only` already
existed for **agent_prompt** schedules and already worked:

    sch_heartbeat         before 2026-09-18: n=2357  $438.76
                          after  2026-09-18: n=47    $0.42
    sch_job_intelligence  before 2026-09-18: n=1352  $378.82
                          after  2026-09-18: n=0     $0      (schedule removed)

46 of those 47 heartbeat runs served on `arbiter-local/bonsai2:27b` at $0.00. So
the $818 the brief targets is HISTORICAL and had already stopped when
`local_only: true` was set on the heartbeat on 2026-09-18 -- Stephen's own earlier
request ("I don't want to keep getting charged by Anthropic for checking my email
and calendar").

Two real gaps remained, and they are what this file pins.

1. `local_only` was read ONLY on the agent_prompt path. Every BUILTIN schedule --
   daily creation, the briefings, the news front page -- ignored it, because
   `_run_task` just calls `meta["fn"]()` and each job picks its own model.

2. Pinning a model at spawn is not forbidding cloud for the whole run.
   `_generate_agent`'s fallback ladder can retry a failed leg on another provider.
   ONE heartbeat run did exactly that on 2026-09-22 for $0.42 -- of which almost
   all was 112,045 CACHE-WRITE tokens, i.e. the price of shipping the full system
   prompt, not of the 99 tokens it produced.
"""

import pytest


TARGET_SCHEDULES = ["sch_daily_creation", "sch_news_morning",
                    "sch_front_page_evening", "sch_afternoon_briefing",
                    "sch_heartbeat"]


# ─────────────────────────────────────────────────────────────────────────────
# The guard itself
# ─────────────────────────────────────────────────────────────────────────────

def test_the_guard_is_off_by_default():
    from agent_friday.services import local_only_guard as g
    assert g.is_active() is False
    g.refuse_if_active("anthropic", "claude-opus-5-5")   # must not raise


def test_inside_a_local_only_run_a_cloud_call_is_refused():
    from agent_friday.services import local_only_guard as g
    with g.local_only("Daily Creation"):
        assert g.is_active() is True
        for provider in ("anthropic", "openai", "google-gemini", "openrouter",
                         "", "something-unknown"):
            with pytest.raises(g.CloudRefused):
                g.refuse_if_active(provider, "some-model")


def test_a_local_provider_is_allowed_inside_a_local_only_run():
    from agent_friday.services import local_only_guard as g
    with g.local_only("Daily Creation"):
        for provider in ("ollama-local", "llama-cpp-local", "arbiter-local",
                         "local"):
            g.refuse_if_active(provider, "bonsai2:27b")   # must not raise


def test_the_refusal_names_the_job_and_the_reason():
    from agent_friday.services import local_only_guard as g
    with g.local_only("News front page"):
        with pytest.raises(g.CloudRefused) as exc:
            g.refuse_if_active("anthropic", "claude-opus-5-5")
    msg = str(exc.value)
    assert "News front page" in msg
    assert "local-only" in msg.lower()


def test_the_flag_does_not_leak_out_of_the_context():
    from agent_friday.services import local_only_guard as g
    with g.local_only("job"):
        pass
    assert g.is_active() is False
    g.refuse_if_active("anthropic")     # an interactive turn is unaffected


def test_the_flag_is_per_thread():
    """Scheduled jobs each run on their own thread; one being local-only must not
    constrain an interactive turn running concurrently."""
    import threading
    from agent_friday.services import local_only_guard as g

    seen = {}

    def other():
        seen["active"] = g.is_active()

    with g.local_only("job"):
        t = threading.Thread(target=other)
        t.start()
        t.join()

    assert seen["active"] is False, "the local-only flag leaked across threads"


# ─────────────────────────────────────────────────────────────────────────────
# The cloud transports honour it
# ─────────────────────────────────────────────────────────────────────────────

def test_call_claude_refuses_inside_a_local_only_run():
    """The last point before the money is spent."""
    from agent_friday.services import local_only_guard as g
    from agent_friday.services import model_router as mr

    with g.local_only("Daily Creation"):
        with pytest.raises(g.CloudRefused):
            mr._call_claude([{"role": "user", "content": "hi"}],
                            model="claude-opus-5-5")


def test_both_cloud_transports_carry_the_guard():
    """Structural: a new fallback path must not be able to route around it."""
    import pathlib
    src = (pathlib.Path(__file__).resolve().parents[2] / "src" / "agent_friday"
           / "services" / "model_router.py").read_text(encoding="utf-8",
                                                       errors="replace")
    assert src.count("refuse_if_active(") == 2, (
        "expected the guard in both _call_claude and _call_openai, found %d "
        "call site(s)" % src.count("refuse_if_active("))


# ─────────────────────────────────────────────────────────────────────────────
# The scheduler applies it to builtins, not just agent_prompt
# ─────────────────────────────────────────────────────────────────────────────

def test_a_builtin_schedule_runs_inside_the_local_only_context(monkeypatch):
    from agent_friday.services import scheduler
    from agent_friday.services import local_only_guard as g

    seen = {}
    monkeypatch.setitem(scheduler.BUILTIN_TASKS, "lo_probe",
                        {"fn": lambda: seen.setdefault("active", g.is_active()),
                         "label": "Local-only probe"})

    scheduler._run_task({"id": "s", "task": {"kind": "builtin",
                                             "ref": "lo_probe",
                                             "local_only": True}})
    assert seen.get("active") is True, (
        "a builtin schedule marked local_only ran without the guard active")


def test_a_builtin_schedule_without_the_flag_is_unconstrained(monkeypatch):
    from agent_friday.services import scheduler
    from agent_friday.services import local_only_guard as g

    seen = {}
    monkeypatch.setitem(scheduler.BUILTIN_TASKS, "lo_probe2",
                        {"fn": lambda: seen.setdefault("active", g.is_active()),
                         "label": "probe"})
    scheduler._run_task({"id": "s", "task": {"kind": "builtin",
                                             "ref": "lo_probe2"}})
    assert seen.get("active") is False


def test_a_refused_cloud_call_becomes_a_visible_skip(monkeypatch):
    """Not a crash and not a silent nothing: a SkippedRun carrying the reason."""
    from agent_friday.services import scheduler
    from agent_friday.services import local_only_guard as g

    def boom():
        g.refuse_if_active("anthropic", "claude-opus-5-5")

    monkeypatch.setitem(scheduler.BUILTIN_TASKS, "lo_probe3",
                        {"fn": boom, "label": "Front page"})

    with pytest.raises(scheduler.SkippedRun) as exc:
        scheduler._run_task({"id": "s", "task": {"kind": "builtin",
                                                 "ref": "lo_probe3",
                                                 "local_only": True}})
    assert "local-only" in str(exc.value).lower()


# ─────────────────────────────────────────────────────────────────────────────
# The shipped defaults
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("sid", TARGET_SCHEDULES)
def test_the_four_jobs_ship_local_only(sid):
    """Stephen's decision, as a default rather than something he must remember to
    tick on every fresh install."""
    from agent_friday.services import scheduler
    defaults = getattr(scheduler, "LOCAL_ONLY_BY_DEFAULT", None)
    assert defaults is not None, "no LOCAL_ONLY_BY_DEFAULT declaration exists"
    assert sid in defaults, "%s does not default to local-only" % sid


# ─────────────────────────────────────────────────────────────────────────────
# A descriptor dict must not be mistaken for a cloud provider.
# ─────────────────────────────────────────────────────────────────────────────

LOCAL_DESCRIPTORS = [
    {"name": "arbiter-local", "classification": "local"},
    {"name": "ollama-local", "classification": "local"},
    {"name": "llama-cpp-local"},
    {"id": "local", "classification": "local"},
]


@pytest.mark.parametrize("desc", LOCAL_DESCRIPTORS)
def test_a_local_descriptor_dict_is_allowed(desc):
    """`_call_openai` takes `provider` as EITHER a registry name or a full
    descriptor dict. The first version of the guard did `str(provider or ...)`,
    which stringified the dict, so Friday's OWN LOCAL SEAT read as cloud and was
    refused. Seen live within half an hour of shipping:

        refused a cloud call inside a local-only run: Daily creation is
        local-only, so it will not call {'name': 'arbiter-local', ...}

    A guard that blocks the thing it exists to permit is worse than no guard.
    """
    from agent_friday.services import local_only_guard as g
    with g.local_only("Daily creation"):
        g.refuse_if_active(desc, "bonsai2:27b")      # must NOT raise


@pytest.mark.parametrize("desc", [
    {"name": "openrouter", "classification": "cloud"},
    {"name": "anthropic"},
    {"id": "openai"},
])
def test_a_cloud_descriptor_dict_is_still_refused(desc):
    from agent_friday.services import local_only_guard as g
    with g.local_only("Daily creation"):
        with pytest.raises(g.CloudRefused):
            g.refuse_if_active(desc, "some-model")


def test_the_refusal_names_the_provider_not_a_dict_dump():
    """The live message read '...will not call {'name': 'arbiter-local', ...}',
    which is both wrong and unreadable."""
    from agent_friday.services import local_only_guard as g
    with g.local_only("Daily creation"):
        with pytest.raises(g.CloudRefused) as exc:
            g.refuse_if_active({"name": "anthropic"}, "claude-opus-5-5")
    msg = str(exc.value)
    assert "anthropic" in msg
    assert "{" not in msg, "the refusal dumped a dict: %r" % msg

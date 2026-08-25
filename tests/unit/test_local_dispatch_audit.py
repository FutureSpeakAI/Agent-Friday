"""Where a local call lands is a fact the log has to state, not one to infer.

On 2026-08-24 the pinned `gemma4:12b` seat died with an 11:49 restart and was
never respawned. `endpoints.json` went on naming :8090 for the rest of the day;
every local role silently resolved to an Ollama tag instead; and nobody
noticed until somebody went looking for an unrelated reason. Nothing was broken enough to
raise: `seat_endpoint` correctly disbelieved the stale file, `local_seats`
correctly dropped the unreachable models, and each layer's individually correct
behaviour added up to a machine answering as models nobody chose.

The gap was that no layer ever said out loud which endpoint it had settled on.
These tests cover the line that now does.
"""
import pytest

from agent_friday.services import local_call as lc


@pytest.fixture(autouse=True)
def _no_endpoint_cache():
    """The 5s memo would otherwise carry one test's seats into the next."""
    lc._EP_CACHE.clear()
    yield
    lc._EP_CACHE.clear()


def _machine(monkeypatch, *, seats=(), tags=()):
    """A local machine with the given llama.cpp seats and Ollama tags."""
    monkeypatch.setattr(lc, "seat_endpoint",
                        lambda m: f"http://127.0.0.1:8090/v1" if m in seats else None)
    monkeypatch.setattr(lc, "_daemon_tags", lambda: set(tags))
    monkeypatch.setattr(lc, "_model_on", lambda base: "gemma4:12b")


def test_a_seated_model_reports_the_seat_and_its_url(monkeypatch):
    _machine(monkeypatch, seats={"gemma4:12b"})
    row = lc.describe_dispatch("gemma4:12b")
    assert row["route"] == "seat"
    assert row["endpoint"] == "http://127.0.0.1:8090/v1"


def test_a_daemon_model_reports_the_daemon(monkeypatch):
    _machine(monkeypatch, tags={"qwen3.5:9b"})
    row = lc.describe_dispatch("qwen3.5:9b")
    assert row["route"] == "daemon"
    assert "11434" in row["endpoint"]


def test_a_model_with_neither_is_reported_unreachable(monkeypatch):
    """The failure that cost an afternoon. It must not read as working."""
    _machine(monkeypatch, seats=set(), tags={"qwen3.5:9b"})
    row = lc.describe_dispatch("gemma4:12b")
    assert row["route"] == "unreachable"
    assert row["answering"] is None


def test_a_seat_the_plan_says_is_up_but_is_not_warns(monkeypatch, caplog):
    """A seat nobody is serving has to be findable by searching the log."""
    _machine(monkeypatch, seats=set(), tags=())
    with caplog.at_level("INFO", logger="friday.local_call"):
        lc.log_dispatch_table(["gemma4:12b"], expect_up={"gemma4:12b"})
    warnings = [r for r in caplog.records if r.levelname == "WARNING"]
    assert warnings, "an unreachable pinned seat was logged at INFO or not at all"
    assert "gemma4:12b" in caplog.text and "UNREACHABLE" in caplog.text


def test_a_leased_seat_that_is_not_loaded_does_not_warn(monkeypatch, caplog):
    """A check that cries about the normal case is one people scroll past.

    Leased seats are absent until something takes a lease. Warning about them
    would bury the one line that matters under three that never do, which is
    the same silence-by-noise this audit exists to end.
    """
    _machine(monkeypatch, seats=set(), tags=())
    with caplog.at_level("INFO", logger="friday.local_call"):
        lc.log_dispatch_table(["gemma4:26b"], expect_up=set())
    assert not [r for r in caplog.records if r.levelname == "WARNING"]
    assert "on demand" in caplog.text


def test_a_seat_answering_as_a_different_model_is_called_out(monkeypatch, caplog):
    """Silent substitution is the defect this whole layer exists to prevent.

    A seat reachable at the expected URL but holding something else is the one
    outcome worse than no seat at all: the caller gets a fluent answer from a
    model it did not choose.
    """
    _machine(monkeypatch, seats={"gemma4:e4b"})
    monkeypatch.setattr(lc, "_model_on", lambda base: "gemma4:12b")
    with caplog.at_level("INFO", logger="friday.local_call"):
        lc.log_dispatch_table(["gemma4:e4b"])
    assert "gemma4:12b" in caplog.text, (
        "the seat was serving a different model and the log did not say so")


def test_the_table_dedupes_and_ignores_empty_names(monkeypatch):
    """Roles share models; the plan has blanks. Neither is worth a probe."""
    _machine(monkeypatch, tags={"qwen3.5:9b"})
    rows = lc.log_dispatch_table(["qwen3.5:9b", None, "qwen3.5:9b", ""])
    assert [r["model"] for r in rows] == ["qwen3.5:9b"]


def test_a_machine_with_nothing_running_still_reports_every_model(monkeypatch):
    """No daemon, no seats — the exact state this machine was in all afternoon.

    The audit's whole value is that it speaks up when nothing is there, so
    "everything is down" must produce a row per model rather than an empty
    table that reads like nothing was asked.
    """
    _machine(monkeypatch, seats=set(), tags=set())
    rows = lc.log_dispatch_table(["gemma4:12b", "gemma4:26b"],
                                 expect_up={"gemma4:12b"})
    assert [r["route"] for r in rows] == ["unreachable", "unreachable"]

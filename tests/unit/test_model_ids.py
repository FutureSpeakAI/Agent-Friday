"""One model, however its id is spelled.

The installer's first-run request asks for ``bonsai2:27b``. A model registered
by hand from its GGUF is often ``bonsai2-27b``. Compared literally they are two
models, so a hand-installed brain that was answering never satisfied the seat,
and the first-run card kept saying "Your models did not finish downloading".
These tests hold the comparison key and the places that compare: the
shortlist, the seat resolver, and the first-run status the card reads.
"""
from __future__ import annotations

import json

import pytest

from agent_friday.services import first_run_models as frm
from agent_friday.services import model_download as md
from agent_friday.services import model_shortlist as sl
from agent_friday.services import model_store


# ── the key ──────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("spelling", [
    "bonsai2:27b", "bonsai2-27b", "Bonsai2:27B", "bonsai2:27b:latest",
    " bonsai2_27b ", "bonsai-2-27b", "ternary-bonsai-2-27b",
])
def test_every_spelling_of_bonsai2_27b_has_one_key(spelling):
    from agent_friday.services.model_ids import canonical, same
    assert canonical(spelling) == canonical("bonsai2:27b") == "bonsai2-27b"
    assert same(spelling, "bonsai2-27b")


def test_different_models_stay_different():
    from agent_friday.services.model_ids import same
    assert not same("bonsai2:27b", "bonsai2:8b")
    assert not same("ternary-bonsai:1.7b", "ternary-bonsai:4b")
    assert not same("", "")
    assert not same(None, "bonsai2:27b")


def test_find_equivalent_returns_the_installed_spelling_and_prefers_exact():
    from agent_friday.services.model_ids import find_equivalent
    assert find_equivalent("bonsai2:27b", ["small:4b", "bonsai2-27b"]) == "bonsai2-27b"
    assert find_equivalent("bonsai2:27b", ["bonsai2-27b", "bonsai2:27b"]) == "bonsai2:27b"
    assert find_equivalent("bonsai2:27b", ["small:4b"]) is None


def test_a_namespace_keeps_its_colon_so_namespaces_do_not_merge():
    """``hf:foo`` (a Hugging Face download) is not ``hf-foo`` (a model whose
    name happens to start with hf-); ``voice-front:`` is a seat, not a tag."""
    from agent_friday.services.model_ids import canonical, same
    assert canonical("hf:Foo-Bar") == "hf:foo-bar"
    assert not same("hf:foo", "hf-foo")
    assert same("hf:foo_bar", "HF:foo-bar")
    assert not same("voice-front:qwen3-1.7b", "voice-front-qwen3-1.7b")
    assert same("voice-front:ternary-bonsai:1.7b", "voice-front:ternary-bonsai-1.7b")
    # Not a namespace: an ordinary name:tag still reads as one key.
    assert same("bonsai2:27b", "bonsai2-27b")


# ── the places that compare ──────────────────────────────────────────────────

def test_the_shortlist_finds_its_entry_under_an_equivalent_id():
    sl.reload_for_tests()
    m = sl.get("bonsai2-27b")
    assert m is not None and m["id"] == "bonsai2:27b", "the entry keeps its own id"


def test_the_seat_resolver_counts_an_equivalent_install_as_the_configured_model(monkeypatch):
    from agent_friday.services import local_seats as seats
    seats._CACHE.update(at=0.0, rows=[])
    seats._ANNOUNCED.clear()
    rows = [("small:4b", 2.1), ("bonsai2-27b", 9.0), ("big:12b", 7.5)]
    monkeypatch.setattr(seats, "installed", lambda force=False: list(rows))
    monkeypatch.setattr(seats, "_configured", lambda role: None)
    monkeypatch.setattr(seats, "_probe_seat", lambda model: False)
    # A cheap role would otherwise take the smallest; the configured model is
    # installed under another spelling, so it is the answer.
    assert seats.resolve("sidekick", "bonsai2:27b") == "bonsai2-27b"


@pytest.mark.parametrize("spelling", [
    "bonsai2:27b", "Bonsai2:27B", "bonsai2:27b:latest", " bonsai2_27b ", "ternary-bonsai-2-27b",
])
def test_every_spelling_reaches_the_installed_model_through_the_seat_resolver(monkeypatch, spelling):
    """The key, held through an entry point that exists on main: the literal
    comparison there seats the smallest model for every spelling below."""
    from agent_friday.services import local_seats as seats
    seats._CACHE.update(at=0.0, rows=[])
    seats._ANNOUNCED.clear()
    rows = [("small:4b", 2.1), ("bonsai2-27b", 9.0)]
    monkeypatch.setattr(seats, "installed", lambda force=False: list(rows))
    monkeypatch.setattr(seats, "_configured", lambda role: None)
    monkeypatch.setattr(seats, "_probe_seat", lambda model: False)
    assert seats.resolve("sidekick", spelling) == "bonsai2-27b"


@pytest.mark.parametrize("spelling", ["bonsai2-27b", "Bonsai2:27B", "bonsai2_27b"])
def test_every_spelling_finds_the_shortlist_entry(spelling):
    sl.reload_for_tests()
    m = sl.get(spelling)
    assert m is not None and m["id"] == "bonsai2:27b", spelling


# ── the first-run status the card reads ─────────────────────────────────────

DEEP_ONLY = {"consent_download": True, "cloud": False, "release": "1.0.0b1",
             "seats": {"deep_thinker": {"model_id": "bonsai2:27b", "packing": "PTQ1_0"}}}


def _request(tmp_path, monkeypatch, body):
    p = tmp_path / "first-run.json"
    p.write_text(json.dumps(body), encoding="utf-8")
    monkeypatch.setenv("FRIDAY_FIRST_RUN_FILE", str(p))
    return p


@pytest.fixture
def no_settings(monkeypatch):
    from agent_friday import core
    monkeypatch.setattr(core, "_load_settings", lambda *a, **k: {})


def _failed_deep_row(req, error="DownloadError: stopped"):
    row = frm._entry("deep_thinker", "bonsai2:27b", "PTQ1_0")
    row.update(state="failed", error=error)
    req.with_name(frm.STATUS_NAME).write_text(json.dumps({"seats": [row]}), encoding="utf-8")


def _serving(monkeypatch, published, on_port, procs=None):
    """The Arbiter's view: ``published`` model -> base URL, ``on_port`` port ->
    the model id that port's server reports, ``procs`` this process's seats.
    Returns the list of ports asked."""
    from agent_friday.services import residency_arbiter as ra
    arb = None
    if procs is not None:
        class _Llama:
            pass
        class _Arb:
            llama = _Llama()
        _Arb.llama.procs = dict(procs)
        arb = _Arb()
    monkeypatch.setattr(ra, "get_arbiter", lambda: arb)
    monkeypatch.setattr(ra, "_read_published", lambda: dict(published))
    asked: list = []

    def _model_on_port(port):
        asked.append(port)
        return on_port.get(port)
    monkeypatch.setattr(ra, "_model_on_port", _model_on_port)
    monkeypatch.setattr(ra, "_endpoint_alive", lambda base: pytest.fail(
        "a /health answer alone was taken as the published model"))
    return asked


def test_first_run_is_satisfied_when_an_equivalent_model_serves_the_seat(tmp_path, monkeypatch, no_settings):
    """The deep thinker's download stopped, but bonsai2-27b (installed by hand)
    is answering on a seat: the card has nothing left to wait for."""
    req = _request(tmp_path, monkeypatch, DEEP_ONLY)
    _failed_deep_row(req, "DownloadError: the runtime unpacked but no server binary was found")
    _serving(monkeypatch, {"bonsai2-27b": "http://127.0.0.1:8090/v1"}, {8090: "bonsai2-27b"})

    st = frm.status()

    assert st["state"] == "done", st
    deep = next(r for r in st["seats"] if r["seat"] == "deep_thinker")
    assert deep["state"] == "installed" and deep.get("served_by") == "bonsai2-27b", deep


def test_first_run_still_reports_a_failure_when_nothing_serves_the_seat(tmp_path, monkeypatch, no_settings):
    req = _request(tmp_path, monkeypatch, DEEP_ONLY)
    _failed_deep_row(req)
    _serving(monkeypatch, {"other:4b": "http://127.0.0.1:8091/v1"}, {8091: "other:4b"})

    assert frm.status()["state"] == "failed"


def test_a_reused_port_that_answers_is_not_the_published_model(tmp_path, monkeypatch, no_settings):
    """The endpoints file says bonsai2-27b is on :8090, but whatever answers
    there now reports another model (or none): the seat is not served."""
    req = _request(tmp_path, monkeypatch, DEEP_ONLY)
    _failed_deep_row(req)
    _serving(monkeypatch, {"bonsai2-27b": "http://127.0.0.1:8090/v1"}, {8090: "something-else:7b"})
    assert frm.status()["state"] == "failed"

    _serving(monkeypatch, {"bonsai2-27b": "http://127.0.0.1:8090/v1"}, {})
    assert frm.status()["state"] == "failed"
    assert req.exists() and not req.with_name(frm.DONE_NAME).exists()


def test_a_seat_this_arbiter_spawned_counts_without_asking_its_port(tmp_path, monkeypatch, no_settings):
    req = _request(tmp_path, monkeypatch, DEEP_ONLY)
    _failed_deep_row(req)
    asked = _serving(monkeypatch, {"bonsai2-27b": "http://127.0.0.1:8090/v1"}, {},
                     procs={"bonsai2-27b": (object(), 8090)})

    assert frm.status()["state"] == "done"
    assert asked == [], "the Arbiter's own record is enough; its own port is not re-asked"


def test_one_status_call_asks_each_published_port_once(tmp_path, monkeypatch, no_settings):
    """The card polls every 4 s; each poll may ask each published port once,
    however many seats are unfinished, never once per seat per port."""
    req = _request(tmp_path, monkeypatch, DEEP_ONLY)
    _failed_deep_row(req)
    calls = {"n": 0}
    real = frm._served_ids

    def counted():
        calls["n"] += 1
        return real()
    monkeypatch.setattr(frm, "_served_ids", counted)
    asked = _serving(monkeypatch, {"a:1b": "http://127.0.0.1:8091/v1", "b:2b": "http://127.0.0.1:8092/v1",
                                   "c:3b": "http://127.0.0.1:8093/v1"}, {})

    assert frm.status()["state"] == "failed"
    assert calls["n"] == 1
    assert sorted(asked) == [8091, 8092, 8093]


def test_satisfied_by_the_same_model_is_persisted_so_the_next_boot_does_not_download(
        tmp_path, monkeypatch, no_settings):
    req = _request(tmp_path, monkeypatch, DEEP_ONLY)
    _failed_deep_row(req)
    _serving(monkeypatch, {"bonsai2-27b": "http://127.0.0.1:8090/v1"}, {8090: "bonsai2-27b"})

    assert frm.status()["state"] == "done"
    assert not req.exists() and req.with_name(frm.DONE_NAME).exists(), "the done marker is written"

    # Next boot, before any seat is up: nothing is started.
    _serving(monkeypatch, {}, {})
    monkeypatch.setattr(md, "start_model", lambda *a, **k: pytest.fail("the download started again"))
    assert frm.run_at_boot(blocking=True)["state"] == "done"


def test_the_owners_other_model_is_named_but_the_requested_download_is_not_marked_done(
        tmp_path, monkeypatch):
    """The owner set the deep seat to their own model, which is serving. The
    card says so, but the bonsai2:27b download they asked for is still theirs
    to cancel: not marked installed, not retired."""
    from agent_friday import core
    monkeypatch.setattr(core, "_load_settings", lambda *a, **k: {
        "capability_routing": {"reasoning": {"provider": "ollama-local", "model": "ownmodel:12b"}}})
    req = _request(tmp_path, monkeypatch, DEEP_ONLY)
    _failed_deep_row(req)
    _serving(monkeypatch, {"ownmodel:12b": "http://127.0.0.1:8094/v1"}, {8094: "ownmodel:12b"})

    st = frm.status()

    deep = next(r for r in st["seats"] if r["seat"] == "deep_thinker")
    assert deep.get("served_by") == "ownmodel:12b", deep
    assert deep["state"] == "failed", deep
    assert st["state"] == "failed"
    assert req.exists() and not req.with_name(frm.DONE_NAME).exists()


def test_a_hand_installed_equivalent_is_seated_instead_of_downloaded(tmp_path, monkeypatch, no_settings):
    req = _request(tmp_path, monkeypatch, DEEP_ONLY)
    monkeypatch.setattr(model_store, "available", lambda: {"bonsai2-27b": {}})
    monkeypatch.setattr(md, "start_model", lambda *a, **k: pytest.fail("downloaded a model that is installed"))
    seated: list = []
    monkeypatch.setattr(frm, "apply_seat", lambda seat, mid: seated.append((seat, mid)))

    out = frm.run_at_boot(blocking=True)

    assert out["state"] == "done", out
    assert seated == [("deep_thinker", "bonsai2-27b")], "the record that exists is the one seated"
    assert not req.exists() and req.with_name(frm.DONE_NAME).exists()

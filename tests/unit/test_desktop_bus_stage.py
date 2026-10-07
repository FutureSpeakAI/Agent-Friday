"""The page's stage on the command bus: bounded, in memory only, gone with the page (S1, S3, S4)."""
from __future__ import annotations

import os
import time

import pytest

from agent_friday.services import desktop_bus
from tests.screen_fixtures import item, newsletter_stage, ref, report


@pytest.fixture(autouse=True)
def _clean():
    desktop_bus.reset()
    yield
    desktop_bus.reset()


def test_a_reported_stage_is_kept_and_read_back():
    report(newsletter_stage(selected=[1, 2]))
    st = desktop_bus.stage("messages")
    assert st["workspace"] == "messages" and len(st["items"]) == 6
    assert st["selection"]["refs"] == [ref(1), ref(2)] and st["selection"]["count"] == 2


def test_items_refs_and_text_are_bounded():
    big = newsletter_stage(extra_items=[item(i, title="x" * 200, who="y" * 200) for i in range(7, 300)])
    big["selection"]["refs"] = [ref(i) for i in range(1, 700)]
    report(big)
    st = desktop_bus.stage("messages")
    assert len(st["items"]) == 120
    assert len(st["selection"]["refs"]) == 500
    assert all(len(it["title"]) <= 80 and len(it["who"]) <= 80 for it in st["items"])


def test_a_facet_the_workspace_did_not_allow_is_dropped():
    st = newsletter_stage()
    st["items"][0]["facets"]["password_hint"] = "hunter2"
    st["items"][0]["facets"]["nested"] = {"a": 1}
    report(st)
    f = desktop_bus.stage("messages")["items"][0]["facets"]
    assert "password_hint" not in f and "nested" not in f and f["lane"] == "subscriptions"


def test_an_item_with_a_malformed_ref_is_dropped():
    st = newsletter_stage()
    st["items"][1]["ref"] = "not a ref at all"
    report(st)
    assert len(desktop_bus.stage("messages")["items"]) == 5


def test_a_stale_page_has_no_stage():
    report(newsletter_stage())
    desktop_bus._CLIENTS["pg1"]["state_at"] -= desktop_bus.STALE_AFTER_S + 1
    assert desktop_bus.stage("messages") is None


def test_max_age_makes_an_old_stage_ask_again():
    report(newsletter_stage())
    desktop_bus._CLIENTS["pg1"]["stage_at"] -= 10
    assert desktop_bus.stage("messages", max_age=2.0) is None
    assert desktop_bus.stage("messages") is not None


def test_a_closed_page_takes_its_stage_with_it():
    report(newsletter_stage())
    desktop_bus.report_state("pg1", {"closed": True})
    assert desktop_bus.stage("messages") is None


def test_a_report_with_a_null_stage_clears_it():
    report(newsletter_stage())
    desktop_bus.report_state("pg1", {"kind": "desktop", "stage": None})
    assert desktop_bus.stage("messages") is None


def test_the_public_state_never_carries_the_stage():
    report(newsletter_stage())
    assert "stage" not in str(desktop_bus.state()), "GET /api/desktop/state must not leak the rows"


def test_a_stage_request_ack_carries_the_stage_and_is_kept_for_that_page():
    desktop_bus.report_state("pg1", {"kind": "desktop", "focused": True})
    q = desktop_bus.subscribe("pg1")
    import threading
    out = {}
    t = threading.Thread(target=lambda: out.update(desktop_bus.send([{"type": "stage_request"}], timeout=3)))
    t.start()
    cmd = q.get(timeout=2)
    desktop_bus.ack(cmd["id"], {"result": {"ok": True}, "stage": newsletter_stage(selected=[3], rev=9)})
    t.join(timeout=4)
    assert out.get("acked") and out["ack"].get("stage_seen") is True
    assert "stage" not in out["ack"], "the raw stage is bounded and stored, not passed on"
    assert desktop_bus.stage("messages", max_age=2.0)["rev"] == 9


def test_nothing_is_written_to_disk(tmp_path, monkeypatch):
    monkeypatch.setenv("FRIDAY_HOME", str(tmp_path))
    before = {p for p in tmp_path.rglob("*")}
    report(newsletter_stage(selected=[1, 2, 3]))
    desktop_bus.stage("messages")
    assert {p for p in tmp_path.rglob("*")} == before

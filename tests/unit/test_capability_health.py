"""Capabilities carry the nine health states (north star §28.3), and what is
off on purpose is grouped apart from what is failing, on the API and on the
Doctor page."""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from agent_friday.services import capability_state as cs

ROOT = Path(__file__).resolve().parents[2]


def _c(state, key="brave", detail="", health=""):
    return cs.CapabilityState(key, key, state, (), "fix it", detail, health=health)


@pytest.fixture(autouse=True)
def no_policy(monkeypatch):
    from agent_friday.services import authority, safe_mode
    monkeypatch.setattr(safe_mode, "active", lambda *a, **k: False)
    monkeypatch.setattr(authority, "hold_reason", lambda: None)


def test_the_nine_states_are_doctors():
    from agent_friday.services import doctor
    assert set(cs.HEALTH_STATES) == set(doctor.STATES) and len(cs.HEALTH_STATES) == 9
    assert set(cs._GROUP_OF) == set(cs.HEALTH_STATES) and set(cs._GROUP_OF.values()) == set(cs.GROUPS)


@pytest.mark.parametrize("state,detail,health", [
    (cs.WORKING, "", "healthy"),
    (cs.PRESENT_UNVERIFIED, "", "unverified"),
    (cs.PRESENT_FAILING, "", "broken"),
    (cs.UNCONFIGURED, "", "unavailable_by_configuration"),
    (cs.ABSENT, "no local model is installed", "unavailable_by_configuration"),
    (cs.ABSENT, "module unavailable: ImportError", "broken"),
    (cs.ABSENT, "account store unreadable: OSError; say you cannot tell", "broken"),
])
def test_each_state_reads_as_its_health(state, detail, health):
    assert cs.health_of(_c(state, key="local_brain", detail=detail))[0] == health


def test_a_probe_can_say_partly_working():
    assert cs.health_of(_c(cs.PRESENT_FAILING, key="google", health="degraded"))[0] == "degraded"


def test_not_set_up_is_off_not_failing():
    h, why = cs.health_of(_c(cs.UNCONFIGURED))
    assert cs._GROUP_OF[h] == "off" and why == "fix it"


def test_safe_mode_keeps_the_web_and_the_account_off_by_policy(monkeypatch):
    from agent_friday.services import safe_mode
    monkeypatch.setattr(safe_mode, "active", lambda *a, **k: True)
    for key in ("brave", "firecrawl", "duckduckgo", "google"):
        h, why = cs.health_of(_c(cs.WORKING, key=key))
        assert h == "blocked_by_policy" and "safe mode" in why, key
    assert cs.health_of(_c(cs.WORKING, key="local_brain"))[0] == "healthy"


def test_a_revocation_keeps_the_account_off_by_policy(monkeypatch):
    from agent_friday.services import authority
    monkeypatch.setattr(authority, "hold_reason", lambda: "you revoked all action authority (now)")
    assert cs.health_of(_c(cs.WORKING, key="google")) == ("blocked_by_policy",
                                                         "you revoked all action authority (now)")


def test_a_policy_check_that_cannot_run_is_said(monkeypatch):
    from agent_friday.services import safe_mode

    def boom(*a, **k):
        raise RuntimeError("x")
    monkeypatch.setattr(safe_mode, "refusal", boom)
    h, why = cs.health_of(_c(cs.WORKING))
    assert h == "unverified" and "could not be checked" in why   # not "off on purpose"


def test_every_capability_carries_its_health_and_group(monkeypatch):
    monkeypatch.setattr(cs, "PROBES", (lambda: _c(cs.UNCONFIGURED), lambda: _c(cs.PRESENT_FAILING, key="x")))
    rows = cs.as_dicts()
    assert [(r["health"], r["group"]) for r in rows] == [("unavailable_by_configuration", "off"),
                                                         ("broken", "attention")]


def _js_object(html, name):
    m = re.search(r"const %s = (\{.*?\});" % name, html, re.S)
    assert m, name
    return json.loads(re.sub(r"(\w+):", r'"\1":', m.group(1)).replace("'", '"'))


def test_the_doctor_page_reads_it_and_keeps_off_apart_from_failing():
    html = (ROOT / "static" / "doctor.html").read_text(encoding="utf-8")
    assert 'id="capabilities"' in html and "/api/capabilities/state" in html
    assert set(_js_object(html, "WORDS")) == set(cs.HEALTH_STATES)
    assert _js_object(html, "GROUP") == cs._GROUP_OF
    assert set(_js_object(html, "HEADS")) == set(cs.GROUPS)
    assert "'meaning'" in html

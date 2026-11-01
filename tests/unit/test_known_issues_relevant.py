"""Known issues shown in-app when they apply to this PC (north star §28.10-1).

services/known_issues marks each issue of KNOWN_ISSUES.md with whether it
applies here, from rules that read only this PC's own state. The Doctor page
marks those issues, and Home lists them while any apply."""
from __future__ import annotations

import importlib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _ki():
    ki = importlib.import_module("agent_friday.services.known_issues")
    assert hasattr(ki, "RELEVANCE"), "no known issue is matched to this PC's configuration"
    return ki


def _items(d):
    return [it for s in d["sections"] for it in s["items"]]


def test_every_rule_names_one_issue_in_the_shipped_list():
    ki = _ki()
    titles = [it["title"] for it in _items(ki.parse((ROOT / "KNOWN_ISSUES.md").read_text(encoding="utf-8-sig")))]
    for prefix, rid, fn in ki.RELEVANCE:
        assert sum(t.startswith(prefix) for t in titles) == 1, \
            "rule %s no longer names exactly one issue in KNOWN_ISSUES.md: %r" % (rid, prefix)
        assert callable(fn)
    assert len({rid for _p, rid, _f in ki.RELEVANCE}) == len(ki.RELEVANCE)


def test_each_issue_says_whether_it_applies_here(monkeypatch):
    ki = _ki()
    rules = {rid: (lambda v: (lambda: v))(rid in ("linux", "openrouter_without_anthropic"))
             for _p, rid, _f in ki.RELEVANCE}

    def boom():
        raise OSError("cannot tell")
    rules["no_local_model_serving"] = boom
    monkeypatch.setattr(ki, "RELEVANCE", tuple((p, rid, rules[rid]) for p, rid, _f in ki.RELEVANCE))
    monkeypatch.setattr(ki, "_CACHE", {})
    d = ki.load()
    assert d["available"]
    by_rule = {it.get("rule"): it["applies"] for it in _items(d) if it.get("rule")}
    assert by_rule["linux"] is True and by_rule["openrouter_without_anthropic"] is True
    assert by_rule["rules_hold"] is False and by_rule["no_local_model_serving"] is None   # cannot tell: not shown
    assert d["applies_here"] == 2
    assert "applies_here" not in ki.load(mark=False)                          # a request from elsewhere
    assert all(it["applies"] is None for it in _items(d) if not it.get("rule"))  # no rule: never claimed
    assert ki.summary()["applies_here"] == 2


def test_the_openrouter_rule_reads_key_names_never_values(monkeypatch):
    ki = _ki()
    for n in ("OPENROUTER_API_KEY", "OR_API_KEY", "ANTHROPIC_API_KEY"):
        monkeypatch.setenv(n, "")
    assert ki._openrouter_without_anthropic() is False
    monkeypatch.setenv("OR_API_KEY", "x")                                   # the alias counts
    assert ki._openrouter_without_anthropic() is True
    monkeypatch.setenv("ANTHROPIC_API_KEY", "y")
    assert ki._openrouter_without_anthropic() is False


def test_a_rule_that_cannot_read_its_state_says_it_cannot_tell(monkeypatch):
    ki = _ki()
    from agent_friday.services import doctor, local_repair
    monkeypatch.setattr(doctor, "_c_claws", lambda: ("unverified", "no_key_to_check_with"))
    assert ki._rules_hold() is None
    monkeypatch.setattr(doctor, "_c_claws", lambda: ("blocked_by_policy", "rules_signature_mismatch"))
    assert ki._rules_hold() is True
    monkeypatch.setattr(local_repair, "brain", lambda: {"state": "unknown", "detail": "could not be read (OSError)"})
    assert ki._no_local_model_serving() is None
    from agent_friday.services import capability_state as cs
    monkeypatch.setattr(local_repair, "brain", lambda: {"state": cs.PRESENT_FAILING, "detail": "down", "down": True})
    assert ki._no_local_model_serving() is True                             # installed but not serving


def test_the_doctor_page_and_home_show_only_what_applies_as_plain_text():
    _ki()
    html = (ROOT / "static" / "doctor.html").read_text(encoding="utf-8")
    i = html.index("function knownIssues()")
    blk = html[i:html.index("function stores()", i)]
    assert "it.applies === true" in blk and "Applies to this PC" in blk and "innerHTML" not in blk
    js = (ROOT / "static" / "friday_experience.js").read_text(encoding="utf-8")
    assert "/api/doctor/known-issues" in js and "it.applies === true" in js
    j = js.index("knownHere.length > 0 && h('section'")
    card = js[j:js.index("h(HomeCards,", j)]
    assert "Known issues that apply to this PC" in card and "href: '/doctor#known'" in card
    assert "innerHTML" not in card and "dangerouslySetInnerHTML" not in card

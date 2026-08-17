"""
Judgment gate — the safety property, tested adversarially.

docs/design/deep-research.md §5. The whole layer exists to let a model rescue
material the keyword rules over-blocked, and the whole risk is that a model
could be talked into rescuing something it must not. So the load-bearing test
here is not "does judgment work" — it is:

    WITH THE JUDGE FORCED TO RETURN THE MOST PERMISSIVE VERDICT ON EVERY SPAN,
    DOES ANYTHING PRIVATE GET OUT?

That models a confused, prompt-injected or outright compromised judgment model,
and it must never leak, because the deterministic post-scrub verification runs
after the verdict and does not consult it.

Offline by construction: the judge is stubbed in every test, so this suite
never needs Ollama and never makes a network call. The LIVE counterpart (real
12b verdicts) is judgment_gate.probe_battery(), which runs at boot.
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("FRIDAY_TESTING", "1")
_TEST_HOME = Path(tempfile.mkdtemp(prefix="friday_judgment_"))
os.environ["USERPROFILE"] = str(_TEST_HOME)
os.environ["HOMEDRIVE"] = _TEST_HOME.drive or "C:"
os.environ["HOMEPATH"] = str(_TEST_HOME)[len(_TEST_HOME.drive):] or "\\"
os.environ.setdefault("FRIDAY_PASSWORD", "test-vault-passphrase")
os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")
os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")

_ROOT = Path(__file__).resolve().parent.parent
for _p in (str(_ROOT / "src"), str(_ROOT)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import pytest  # noqa: E402

import agent_friday.core as core  # noqa: E402
from agent_friday.services import egress_gate as eg  # noqa: E402
from agent_friday.services import judgment_gate as jg  # noqa: E402

DEVNULL = Path(os.devnull)

# Synthetic throughout. The SSN/card/key values are canonical test constants.
PROBES = {
    "ssn":       ("My social security number is 123-45-6789 and I need help.",  # pragma: allowlist secret
                  "123-45-6789"),  # pragma: allowlist secret
    "card":      ("Put it on my card 4111 1111 1111 1111 today.",  # pragma: allowlist secret
                  "4111 1111 1111 1111"),  # pragma: allowlist secret
    "routing":   ("Wire it to routing number 021000021 at my bank.",
                  "021000021"),
    "api_key":   ("Use my key sk-ant-api03-AAAAAAAAAAAAAAAAAAAAAAAA now.",  # pragma: allowlist secret
                  "sk-ant-api03-AAAAAAAAAAAAAAAAAAAAAAAA"),  # pragma: allowlist secret
    "vault_doc": ("Open the file at vault/finances/2025-tax-return.pdf",
                  "vault/finances/2025-tax-return.pdf"),
    "legal_1p":  ("My custody hearing is on the 14th and my lawyer says my ex "
                  "will contest the settlement figure I proposed for our daughter.",
                  "custody hearing is on the 14th"),
    "medical_1p": ("My A1C came back at 8.2 and my doctor wants to change my "
                   "prescription before my next appointment.",
                   "A1C came back at 8.2"),
}

HEADLINE = "Trump asks US Supreme Court to allow ballroom work to continue"


@pytest.fixture
def judging(monkeypatch):
    """Judgment enabled, with the judge stubbed. Yields a setter for verdicts."""
    real_load = core._load_settings
    monkeypatch.setattr(core, "_load_settings",
                        lambda: {**(real_load() or {}),
                                 "judgment_gate": {"enabled": True}})
    monkeypatch.setattr(jg, "_PROBE_DISABLED", False)

    def set_verdict(verdict, reason="stubbed"):
        monkeypatch.setattr(
            jg, "judge_spans",
            lambda spans: ([{"verdict": verdict, "reason": reason}
                            for _ in spans], "stubbed"))
    return set_verdict


def _seal(text, provider="test-cloud"):
    """Returns (outgoing_text, exception_or_None)."""
    payload = {"messages": [{"role": "user", "content": text}]}
    try:
        out = eg.seal_outbound(payload, provider, DEVNULL)
        msgs = out.get("messages") or [{}]
        return str(msgs[0].get("content", "")), None
    except Exception as e:  # NeverSendBlocked and friends are a PASS
        return "", e


# ── The load-bearing test ─────────────────────────────────────────────────────

@pytest.mark.parametrize("name", sorted(PROBES))
def test_forced_permissive_verdict_never_leaks(judging, name):
    """A judge that answers ABOUT_THE_WORLD to everything must leak nothing.

    This is the entire safety argument for the judgment layer. If this test
    ever fails, the layer is not safe to enable at any setting.
    """
    judging(jg.ABOUT_THE_WORLD, "FORCED WRONG VERDICT")
    probe, token = PROBES[name]  # pragma: allowlist secret
    out, _err = _seal(probe)
    assert token not in out, (
        f"{name}: a forced-permissive verdict leaked {token!r} into the "
        f"outgoing payload: {out[:200]!r}")


@pytest.mark.parametrize("name", sorted(PROBES))
def test_forced_permissive_verdict_never_leaks_multiparagraph(judging, name):
    """Same, where the private span rides alongside innocuous paragraphs.

    The span loop is a separate code path from the whole-field appeal, and a
    guard that covers only one of them covers neither in practice.
    """
    judging(jg.ABOUT_THE_WORLD, "FORCED WRONG VERDICT")
    probe, token = PROBES[name]  # pragma: allowlist secret
    out, _err = _seal("The weather is fine today.\n\n" + probe +
                      "\n\nLet me know what you think.")
    assert token not in out, f"{name}: leaked via the span path: {out[:200]!r}"


def test_judgment_cannot_promote_a_span_it_never_saw(judging, monkeypatch):
    """Judgment must only ever be offered spans that were already withheld."""
    seen = []
    monkeypatch.setattr(jg, "judge_spans",
                        lambda spans: (seen.extend(spans) or
                                       [{"verdict": jg.NEVER_SEND, "reason": "x"}
                                        for _ in spans], "stubbed"))
    benign = "Good morning! What is on my schedule for today?"
    out, _ = _seal(benign)
    assert out == benign, "benign text was altered"
    assert not seen, f"a passing span was shown to the judge: {seen!r}"


# ── Fail-toward-redaction ─────────────────────────────────────────────────────

def test_judgment_unavailable_keeps_deterministic_outcome(judging, monkeypatch):
    monkeypatch.setattr(jg, "judge_spans", lambda spans: (None, "seat down"))
    out, _ = _seal(HEADLINE)
    assert HEADLINE not in out, "rescued a span with no judgment available"


def test_malformed_verdict_json_is_not_a_rescue():
    assert jg._parse_verdicts("not json at all", 2) is None
    assert jg._parse_verdicts("", 1) is None
    # A partial answer keeps the deterministic outcome for the missing spans.
    parsed = jg._parse_verdicts(
        '{"verdicts":[{"i":0,"verdict":"ABOUT_THE_WORLD","reason":"r"}]}', 3)
    assert parsed[0]["verdict"] == jg.ABOUT_THE_WORLD
    assert parsed[1]["verdict"] == jg.NEVER_SEND
    assert parsed[2]["verdict"] == jg.NEVER_SEND


def test_invalid_verdict_string_is_treated_as_never_send():
    parsed = jg._parse_verdicts(
        '{"verdicts":[{"i":0,"verdict":"SEND_IT_ALL","reason":"r"}]}', 1)
    assert parsed[0]["verdict"] == jg.NEVER_SEND


def test_kill_switch_off_means_judgment_never_runs(monkeypatch):
    real_load = core._load_settings
    monkeypatch.setattr(core, "_load_settings",
                        lambda: {**(real_load() or {}),
                                 "judgment_gate": {"enabled": False}})
    called = []
    monkeypatch.setattr(jg, "judge_spans",
                        lambda spans: (called.append(spans), ([], "x"))[1])
    _seal(PROBES["legal_1p"][0])
    assert not called, "judgment ran with the kill switch off"


def test_probe_failure_disables_the_layer(monkeypatch):
    monkeypatch.setattr(jg, "_PROBE_DISABLED", True)
    real_load = core._load_settings
    monkeypatch.setattr(core, "_load_settings",
                        lambda: {**(real_load() or {}),
                                 "judgment_gate": {"enabled": True}})
    assert not jg.enabled(), "a failed probe battery did not disable the layer"


# ── §5.3 the never-list is a floor ────────────────────────────────────────────

def test_never_send_blocks_the_whole_payload(monkeypatch):
    monkeypatch.setattr(jg, "_PROBE_EXTRA_NEVER", ["Coldwater Deposition"])  # pragma: allowlist secret
    out, err = _seal("Background on the Coldwater Deposition for my piece.")
    assert isinstance(err, eg.NeverSendBlocked), \
        f"never-send did not block the payload (got {out!r})"


def test_never_send_beats_the_public_provenance_registry(monkeypatch):
    """A registered public string containing a never-send token still blocks.

    Provenance exemptions are an optimization; the never-list is a floor. If
    registration could override it, anything Friday fetched could carry
    never-send material out.
    """
    token = "Coldwater Deposition"  # pragma: allowlist secret
    text = f"Public reporting mentions the {token} at length."
    eg.register_public_text(text, origin="https://example.org/a")
    monkeypatch.setattr(jg, "_PROBE_EXTRA_NEVER", [token])
    _out, err = _seal(text)
    assert isinstance(err, eg.NeverSendBlocked), \
        "a provenance exemption overrode the never-list"


def test_never_send_applies_with_judgment_disabled(monkeypatch):
    """The regression the probe battery caught before this layer shipped."""
    real_load = core._load_settings
    monkeypatch.setattr(core, "_load_settings",
                        lambda: {**(real_load() or {}),
                                 "judgment_gate": {"enabled": False}})
    monkeypatch.setattr(jg, "_PROBE_EXTRA_NEVER", ["Coldwater Deposition"])  # pragma: allowlist secret
    _out, err = _seal("A note about the Coldwater Deposition.")
    assert isinstance(err, eg.NeverSendBlocked), \
        "the never-list only worked while judgment was enabled"


# ── §5.5 step 4: deterministic verification ───────────────────────────────────

@pytest.mark.parametrize("text,should_pass", [
    ("A perfectly ordinary sentence about the weather.", True),
    ("My SSN is 123-45-6789.", False),  # pragma: allowlist secret
    ("Routing 021000021 please.", False),
    ("key sk-ant-api03-AAAAAAAAAAAAAAAAAAAAAAAA", False),  # pragma: allowlist secret
    ("Card 4111 1111 1111 1111", False),  # pragma: allowlist secret
    ("Order number 1234567890123456789 shipped", True),   # not Luhn-valid
])
def test_verify_outgoing(text, should_pass):
    v = jg.verify_outgoing(text, reclassify=False)
    assert v.ok is should_pass, f"{text!r} -> {v!r}"


def test_verify_outgoing_fails_closed_when_it_cannot_run(monkeypatch):
    import agent_friday.services.sensitivity_classifier as sc
    monkeypatch.setattr(sc, "classify",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("x")))
    assert not jg.verify_outgoing("anything").ok, \
        "verification that cannot run must block, not pass"


# ── §5.5 step 1: the scrub is inside the gate ─────────────────────────────────

def test_scrub_runs_at_the_choke_point_without_a_lookup():
    out, _ = _seal("Call me at 512-555-0143 about the story.")  # pragma: allowlist secret
    assert "512-555-0143" not in out  # pragma: allowlist secret


def test_scrub_fills_the_lookup_when_offered():
    lookup: dict = {}
    eg.seal_outbound({"messages": [{"role": "user",
                                    "content": "Call 512-555-0143 please."}]},  # pragma: allowlist secret
                     "test-cloud", DEVNULL, pii_lookup=lookup)
    assert lookup and "512-555-0143" in lookup.values()  # pragma: allowlist secret
    assert core._rehydrate_pii(next(iter(lookup)), lookup) == "512-555-0143"  # pragma: allowlist secret


def test_scrub_failure_blocks_the_send(monkeypatch):
    monkeypatch.setattr(core, "_scrub_pii",
                        lambda t: (_ for _ in ()).throw(RuntimeError("boom")))
    _out, err = _seal("anything at all")
    assert isinstance(err, RuntimeError), "a failed scrub did not block the send"


def test_local_providers_still_bypass_completely():
    payload = {"messages": [{"role": "user", "content": PROBES["ssn"][0]}]}
    out = eg.seal_outbound(payload, "ollama", DEVNULL)
    assert out == payload, "the local/cloud boundary moved"


# ── The value the layer exists for ────────────────────────────────────────────

def test_judgment_rescues_an_over_blocked_headline(judging):
    """The ca4ee8c incident: a public headline classified TIER_3 on 'court'."""
    judging(jg.ABOUT_THE_WORLD, "third-party published news")
    out, _ = _seal(HEADLINE)
    assert HEADLINE in out, f"the headline was still withheld: {out[:160]!r}"


def test_overturn_ledger_never_stores_the_span_text(judging, tmp_path,
                                                    monkeypatch):
    monkeypatch.setattr(jg, "_ledger_path", lambda: tmp_path / "overturns.jsonl")
    judging(jg.ABOUT_THE_WORLD, "third-party published news")
    _seal(HEADLINE)
    rows = jg.read_overturns(10)
    assert rows, "an overturn was not recorded"
    row = rows[-1]
    assert "span_hash" in row and "span" not in row
    assert HEADLINE not in str(row), "the ledger stored the raw span"


# ── P7: the inherited provenance defects ──────────────────────────────────────

def test_single_paragraph_public_text_survives_registration():
    """P7(a): the whole-field check consulted _TRUSTED_TEXTS but not
    _PUBLIC_PARAS, so the exemption worked only for multi-paragraph text."""
    # Multiple weak keywords (court / legal / medical / settlement) so the
    # fixture is over-blocked by the keyword rules alone — no embeddings or
    # Presidio needed, which keeps this deterministic in a hermetic run. The
    # precondition is asserted rather than assumed: a test whose setup quietly
    # stopped reproducing the bug is a test that cannot fail.
    text = ("The court approved the legal settlement over medical insurance "
            "billing at the hospital chain.")
    before, _ = _seal(text)
    assert text not in before, "fixture is not actually over-blocked"
    eg.register_public_text(text, origin="https://example.org/b")
    after, _ = _seal(text)
    assert text in after, "P7(a): registered single-paragraph text still withheld"


def test_register_public_text_stores_origin():
    """P7(b): `origin` was accepted and discarded."""
    text = "A quite unremarkable third-party sentence for origin testing."
    eg.register_public_text(text, origin="https://example.org/c")
    assert eg.public_origin_of(text) == "https://example.org/c"


# ── Receipt matching: content, not markup ─────────────────────────────────────
#
# MEASURED DEFECT 2026-08-17: extraction returns quotes with markdown stripped
# (the 12b returned "22:35:12 UTC" where the page held "22:35:12 [UTC](...)"),
# and a raw string compare called that fabricated. Verification was striking
# TRUE, correctly-sourced claims over punctuation — a false positive in the one
# mechanism whose whole value is being believed.
#
# These tests pin BOTH halves: markup differences must not strike a true quote,
# and altered or invented text must still be struck.

_RECEIPT_PAGE = (
    "| Launch date | April 1, 2026, 22:35:12 [UTC](https://en.wikipedia.org/wiki/UTC) "
    "(6:35:12p.m. [EDT](https://en.wikipedia.org/wiki/EDT)) |\n\n"
    "In **December 2024**, outgoing administrator Nelson announced a delay.\n\n"
    "![Orion capsule](https://example.org/img.png) The `Orion` spacecraft is ready."
)


@pytest.mark.parametrize("quote,should_match", [
    # True quotes that markup differences must NOT kill
    ("Launch date | April 1, 2026, 22:35:12 UTC (6:35:12p.m. EDT)", True),
    ("In December 2024, outgoing administrator Nelson announced a delay.", True),
    ("In **December 2024**, outgoing administrator Nelson announced a delay.", True),
    ("The Orion spacecraft is ready.", True),
    # Fabrication and alteration that must STILL be struck
    ("the crew is forty-seven astronauts", False),
    ("Nelson announced the mission was cancelled entirely.", False),
    ("Launch date | April 2, 2026, 22:35:12 UTC", False),
    ("In December 2025, outgoing administrator Nelson announced a delay.", False),
])
def test_receipt_matching_normalizes_markup_not_meaning(quote, should_match):
    from agent_friday.services.research.harness import _norm
    found = _norm(quote) in _norm(_RECEIPT_PAGE)
    assert found is should_match, (
        f"{quote!r} -> found={found}, expected {should_match}. Normalization "
        f"must widen what counts as the same TEXT without weakening the "
        f"requirement that the text actually be present.")


# ── Outline shape tolerance ───────────────────────────────────────────────────
#
# MEASURED 2026-08-17: a commission produced 9 fully-verified findings and then
# FAILED on `AttributeError: 'str' object has no attribute 'get'`, because the
# outline model returned `sections` as a list of heading strings instead of
# objects. Constrained JSON decoding guarantees valid JSON, not the shape you
# asked for. Losing nine good findings at the last step to a wrapper shape is
# the definition of a green job producing nothing.

@pytest.mark.parametrize("raw,expect_sections", [
    ({"sections": [{"heading": "A", "finding_ids": ["f1"]}]}, 1),   # the happy shape
    ({"sections": ["A", "B"]}, 2),                                  # the shape that crashed
    ({"sections": {"A": ["f1"], "B": ["f2"]}}, 2),                  # dict-of-headings
    ({"sections": [{"title": "A", "findings": ["f1"]}]}, 1),        # alternate key names
    ({"sections": [{"heading": "A", "finding_ids": "f1"}]}, 1),     # scalar instead of list
    ({"sections": []}, 1),                                          # empty -> catch-all
    ({}, 1),                                                        # missing -> catch-all
])
def test_outline_shapes_never_lose_findings(raw, expect_sections):
    from agent_friday.services.research.harness import _outline_sections
    by_id = {"f1": object(), "f2": object()}
    secs = _outline_sections(raw, by_id)
    assert len(secs) == expect_sections, f"{raw!r} -> {secs!r}"
    assert all(isinstance(s, dict) and "heading" in s for s in secs)
    # No shape may drop every finding on the floor.
    assert any(s["finding_ids"] for s in secs), (
        f"{raw!r} produced sections with no findings — nine verified claims "
        f"were lost to a wrapper shape once already")


# ── Local-model output is read defensively, like a network boundary ───────────
#
# The inversion this fixes: firecrawl._results_of guards a MAINTAINED
# COMMERCIAL API against shape drift, while the research harness read a
# 2B-parameter local model's output with a bare .get(). The paranoia was
# pointed the wrong way. These pin the readers.

@pytest.mark.parametrize("value,expected", [
    (None, []),
    ([], []),
    (["a", "b"], ["a", "b"]),
    # THE ONE THAT MATTERED: a string must not iterate as characters. A query
    # list read this way became single-letter searches; a passage list became
    # one-character "verbatim quotes".
    ("just one thing", ["just one thing"]),
    ("", []),
    ({"a": 1, "b": 2}, [1, 2]),
    (("x", "y"), ["x", "y"]),
    (42, [42]),
])
def test_as_list_never_explodes_a_string_into_characters(value, expected):
    from agent_friday.services.research.harness import _as_list
    assert _as_list(value) == expected


@pytest.mark.parametrize("value,expected", [
    ({"a": 1}, {"a": 1}),
    (None, {}),
    ("a string where a dict belonged", {}),   # would have raised AttributeError
    ([1, 2, 3], {}),                          # would have raised AttributeError
])
def test_as_dict_never_raises(value, expected):
    from agent_friday.services.research.harness import _as_dict
    assert _as_dict(value) == expected


@pytest.mark.parametrize("value,expected", [
    ("  hello  ", "hello"),
    (None, ""),
    (["a", "b"], "a b"),          # not "['a', 'b']"
    (7, "7"),
])
def test_as_text_flattens_without_stringifying_brackets(value, expected):
    from agent_friday.services.research.harness import _as_text
    assert _as_text(value) == expected


def test_scoper_accepts_sub_questions_as_bare_strings():
    """A list of question strings is a plausible model output. Before the
    guard, every one was skipped and the whole plan came back empty."""
    from agent_friday.services.research.harness import _as_dict, _as_list, _as_text
    data = {"sub_questions": ["What happened?", "Who paid for it?"]}
    texts = []
    for s in _as_list(data.get("sub_questions")):
        t = _as_text(s) if isinstance(s, str) else _as_text(_as_dict(s).get("text"))
        if t:
            texts.append(t)
    assert texts == ["What happened?", "Who paid for it?"]

"""A fallback model id must be the default, or it is a fabricated fact.

`orchestrator_model` IS in DEFAULT_SETTINGS (`claude-sonnet-5`), and
`_load_settings` builds every read as `dict(DEFAULT_SETTINGS)` then updates from
disk -- so the key is always present. That makes the two fallback FORMS behave
completely differently, and the difference decides which call sites are live
bugs and which are only misleading:

  * ``settings.get(k, "claude-opus-5")`` -- the default is **unreachable** when
    settings come from `_load_settings()`. Wrong as documentation, not a live
    misreport.
  * ``settings.get(k) or "claude-opus-5"`` -- **reachable.** An empty flat key
    survives the merge, and `_sync_capability_routing` propagates only truthy
    values, so it stays empty and the `or` fires. Measured 2026-09-22: with
    ``orchestrator_model: ""`` the flat read yields `claude-opus-5` while
    ``capability_routing.reasoning.model`` in the same dict still correctly says
    `claude-sonnet-5`. Two views of one seat, disagreeing.
  * `governance/proof_of_integrity` is worse than either: it does not use
    `_load_settings()` at all. It reads `settings.json` straight from disk, so
    a missing flat key DOES hit its fallback -- and it reads `encoding="utf-8"`
    rather than `utf-8-sig`, so a BOM (PowerShell's `>` writes one by default)
    raises, is swallowed, and the manifest collapses to
    ``{"orchestrator": "claude-opus-5"}``. That is attestation code inventing
    the one fact it exists to report accurately.

The right value at every site is `core.ANTHROPIC_MODEL_DEFAULT` -- the existing
single constant, `claude-sonnet-5`, overridable by the `ANTHROPIC_MODEL` env
var. `chat.py:997` already used it; five siblings in the same file did not.

Not switched to reading `capability_routing` instead: `_load_settings` runs
`_sync_capability_routing` on every load, so the flat key and the seat are two
congruent views of the same value, and reading either through the loader is
equivalent. The divergence above is not flat-vs-seat, it is an empty value plus
a literal that disagrees with the default.
"""

import copy
import io
import json
import re
from pathlib import Path

import pytest

from agent_friday import core
from agent_friday.governance.proof_of_integrity import IntegrityEngine

DEFAULT_MODEL = "claude-sonnet-5"
SRC = Path(core.__file__).resolve().parent.parent


def test_the_constant_is_what_default_settings_says():
    """Everything below leans on these three agreeing. If they ever diverge the
    right fix is to make them agree, not to re-point the tests."""
    assert core.ANTHROPIC_MODEL_DEFAULT == DEFAULT_MODEL
    assert core.DEFAULT_SETTINGS["orchestrator_model"] == DEFAULT_MODEL
    assert core.DEFAULT_SETTINGS["capability_routing"]["reasoning"]["model"] \
        == DEFAULT_MODEL


# ── the reachable case, proved on the real sync function ────────────────────

def test_an_empty_flat_key_stays_empty_through_the_merge():
    """This is what makes the `or` form reachable, so pin it rather than
    assuming it. `_sync_capability_routing` propagates only truthy values."""
    merged = dict(core.DEFAULT_SETTINGS)
    merged["capability_routing"] = copy.deepcopy(
        core.DEFAULT_SETTINGS["capability_routing"])
    merged["orchestrator_model"] = ""
    core._sync_capability_routing(merged)
    assert merged["orchestrator_model"] == "", \
        "if the sync heals this, the `or` fallbacks are unreachable"
    # ...and the seat did NOT lose its correct value, which is why a wrong
    # literal here reports something the seat contradicts.
    assert merged["capability_routing"]["reasoning"]["model"] == DEFAULT_MODEL


# ── proof_of_integrity: the live misreport, in attestation code ─────────────

def _poi(tmp_path, settings_obj=None, raw=None, encoding="utf-8"):
    f = tmp_path / "settings.json"
    if raw is not None:
        io.open(f, "w", encoding=encoding, newline="").write(raw)
    elif settings_obj is not None:
        io.open(f, "w", encoding=encoding, newline="").write(
            json.dumps(settings_obj))
    return IntegrityEngine(friday_dir=tmp_path)


def test_manifest_reports_the_default_when_the_flat_key_is_absent(tmp_path):
    """The normal state of a fresh install: settings.json exists but was never
    written by the model picker. This reads the file directly, so unlike
    /api/health the fallback really does fire."""
    poi = _poi(tmp_path, {"agent_name": "AGENT FRIDAY"})
    got = poi._default_model_manifest()
    assert got["orchestrator"] == DEFAULT_MODEL, (
        "attestation reported %r as the orchestrator when the real default is %r"
        % (got["orchestrator"], DEFAULT_MODEL))


def test_manifest_survives_a_byte_order_mark(tmp_path):
    """PowerShell's `>` and Out-File write UTF-8 WITH BOM by default. Read as
    plain utf-8 that is a JSONDecodeError, swallowed, and the manifest asserts
    a model nobody configured -- while every key on disk is intact."""
    poi = _poi(tmp_path, raw="﻿" + json.dumps(
        {"orchestrator_model": "gemma4:12b"}))
    got = poi._default_model_manifest()
    assert got["orchestrator"] == "gemma4:12b", (
        "a BOM made attestation report %r instead of the configured model"
        % got["orchestrator"])


def test_manifest_reports_the_configured_model_when_it_is_there(tmp_path):
    poi = _poi(tmp_path, {"orchestrator_model": "gemma4:12b",
                          "subagent_model": "claude-haiku-4-5"})
    got = poi._default_model_manifest()
    assert got["orchestrator"] == "gemma4:12b"
    assert got["subagent"] == "claude-haiku-4-5"


def test_manifest_with_no_settings_file_still_names_the_default(tmp_path):
    """The last-resort return. `{"orchestrator": "claude-opus-5"}` was a claim
    about a model the install had never been configured to use."""
    poi = IntegrityEngine(friday_dir=tmp_path)   # no settings.json at all
    got = poi._default_model_manifest()
    assert got["orchestrator"] == DEFAULT_MODEL


def test_manifest_does_not_invent_a_model_from_unreadable_settings(tmp_path):
    poi = _poi(tmp_path, raw="{ this is not json")
    got = poi._default_model_manifest()
    assert got["orchestrator"] == DEFAULT_MODEL


# ── the structural guard: no site may fall back to a non-default id ─────────

#: Two shapes count as "this is the model if nothing is configured":
#:   1. a line naming a *_model settings key beside a hardcoded Claude id, and
#:   2. an `or "<claude id>"` fallback expression anywhere.
#:
#: Shape 2 exists because `setup_wizard.step_model` ended
#: `return existing_model or "claude-opus-5"` -- a default shape 1 cannot see,
#: because that line never mentions the settings key. It was found only when
#: this guard was widened, after the narrow version had reported clean.
#:
#: Neither shape matches a MENU ENTRY like
#: `("claude-opus-5", "Claude Opus 5", ...)`. Opus 5 is a real, selectable
#: model: offering it is right and defaulting to it is not, so the guard has to
#: tell an option from a default rather than banning the id outright.
_KEY = re.compile(r"\b(orchestrator_model|subagent_model)\b")
_CLAUDE_ID = re.compile(r"['\"](claude-[a-z0-9.\-]+)['\"]")
_OR_FALLBACK = re.compile(r"\bor\s+['\"](claude-[a-z0-9.\-]+)['\"]")

#: Per-key allowed fallbacks. subagent_model's default is sonnet-5 too.
_ALLOWED = {DEFAULT_MODEL}


def _offending_lines():
    bad = []
    for path in sorted(SRC.rglob("*.py")):
        if "__pycache__" in str(path):
            continue
        try:
            text = io.open(path, encoding="utf-8-sig").read()
        except Exception:
            continue
        for n, line in enumerate(text.splitlines(), 1):
            hits = set()
            if _KEY.search(line):
                hits |= set(_CLAUDE_ID.findall(line))
            hits |= set(_OR_FALLBACK.findall(line))
            for mid in sorted(hits):
                if mid not in _ALLOWED:
                    bad.append("%s:%d  %s" % (
                        path.relative_to(SRC).as_posix(), n, line.strip()[:110]))
    return bad


def test_no_call_site_falls_back_to_a_model_that_is_not_the_default():
    """Mechanical, because this drifted six ways in one file.

    `chat.py:997` already used ANTHROPIC_MODEL_DEFAULT while five of its
    neighbours hardcoded `claude-opus-5`, which is the signature of a fix
    applied at the site someone was looking at. A literal is fine only when it
    IS the default; otherwise use the constant, which also honours the
    ANTHROPIC_MODEL env override that a literal silently defeats.
    """
    bad = _offending_lines()
    assert not bad, (
        "these fall back to a model that is not the declared default (%s):\n  %s"
        % (DEFAULT_MODEL, "\n  ".join(bad)))


def test_the_guard_can_actually_see_both_shapes():
    """A guard that cannot fail is not evidence.

    The two shapes are checked separately because the first version of this
    guard had only shape 1 and reported CLEAN while `setup_wizard.py:714` still
    returned `existing_model or "claude-opus-5"`.
    """
    assert _KEY.search('settings.get("orchestrator_model", "claude-opus-5")')
    assert _CLAUDE_ID.findall('x("orchestrator_model", "claude-opus-5")') \
        == ["claude-opus-5"]
    assert _OR_FALLBACK.findall('return existing or "claude-opus-5"') \
        == ["claude-opus-5"]
    # A menu entry is an OPTION, not a default, and must not be flagged.
    menu = '("claude-opus-5", "Claude Opus 5", "Deep reasoning")'
    assert not _KEY.search(menu)
    assert not _OR_FALLBACK.findall(menu)


# ── the setup wizard: only a FRESH install is affected ───────────────────────
#
# The wizard round-trips the whole config: `_load_config()`, mutate, then
# `_save_config()` writes `json.dumps(config)` wholesale with no merge. So
# whether an existing install keeps its saved model depends entirely on these
# call sites using `setdefault`-style filling rather than assignment.

from agent_friday import setup_wizard as wiz


def test_quick_setup_never_rewrites_a_saved_orchestrator_model():
    """The condition on this change: an existing install is left alone.

    Honest note: this property held before the fix too -- the old code also used
    `setdefault`, it just filled the wrong VALUE. This is a regression guard, so
    an assignment can never be introduced here, not evidence of the fix. The
    red-before-fix tests are the two below it.
    """
    cfg = {"orchestrator_model": "gemma4:12b", "provider": "ollama",
           "creative_model": "sd3.5-medium-fp8", "agent_name": "KEEP ME"}
    before = dict(cfg)
    wiz._apply_quick_defaults(cfg)
    assert cfg["orchestrator_model"] == "gemma4:12b", "a saved model was rewritten"
    assert cfg == before, "quick setup changed an already-configured install"


def test_quick_setup_on_a_fresh_install_writes_the_default():
    """Red before the fix: it wrote `claude-opus-5`, and `--quick` PERSISTS
    what it writes, so the install really was configured to the wrong model."""
    cfg = {}
    wiz._apply_quick_defaults(cfg)
    assert cfg["orchestrator_model"] == DEFAULT_MODEL, (
        "a fresh --quick install was configured with %r"
        % cfg["orchestrator_model"])


def test_an_empty_saved_model_is_treated_as_unset_not_as_a_name():
    """An empty string is not a model. Left as-is it reads as configured while
    nothing is bound; filled with the old literal it bound Opus."""
    cfg = {"orchestrator_model": ""}
    wiz._apply_quick_defaults(cfg)
    assert cfg["orchestrator_model"] == DEFAULT_MODEL


def test_the_model_step_escape_hatch_keeps_an_existing_choice(monkeypatch):
    """`step_model`'s "no models available" branch. Existing choice wins."""
    monkeypatch.setattr(wiz, "_clear", lambda: None)
    monkeypatch.setattr(wiz, "_header", lambda *a, **k: None)
    monkeypatch.setattr(wiz, "PROVIDERS",
                        [{"id": "anthropic", "name": "Anthropic", "models": []}])
    assert wiz.step_model(9, "anthropic", "gemma4:12b") == "gemma4:12b"


def test_the_model_step_escape_hatch_falls_back_to_the_default(monkeypatch):
    """Red before the fix: `return existing_model or "claude-opus-5"`. This is
    the site the first version of the structural guard could not see, because
    the line never names the settings key."""
    monkeypatch.setattr(wiz, "_clear", lambda: None)
    monkeypatch.setattr(wiz, "_header", lambda *a, **k: None)
    monkeypatch.setattr(wiz, "PROVIDERS",
                        [{"id": "anthropic", "name": "Anthropic", "models": []}])
    assert wiz.step_model(9, "anthropic", "") == DEFAULT_MODEL
    assert wiz.step_model(9, "anthropic", None) == DEFAULT_MODEL

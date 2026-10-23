"""North star §34.2-13: settings are tested against a schema.

resources/settings.schema.json is DEFAULT_SETTINGS' shape plus the choices and
ranges POST /api/settings enforces (services/settings_schema). These tests
check the file is current, that what Friday writes fits it (defaults, a first
run, old files after their migrations), and that the schema and the POST
checks accept and refuse the same values. New behaviour is reached through
getattr with a stand-in, so on a tree without it each test fails on its
assertion.
"""
from __future__ import annotations

import importlib
import json

import pytest


def _ss():
    try:
        return importlib.import_module("agent_friday.services.settings_schema")
    except ImportError:
        return None


def _need(name):
    ss = _ss()
    fn = getattr(ss, name, None) if ss else None
    assert fn is not None, "no settings schema (%s)" % name
    return fn


@pytest.fixture
def home(tmp_path, monkeypatch):
    import agent_friday.core as core
    monkeypatch.setattr(core, "FRIDAY_DIR", tmp_path)
    monkeypatch.setattr(core, "SETTINGS_FILE", tmp_path / "settings.json")
    core._invalidate_settings_cache()
    yield core
    core._invalidate_settings_cache()


def test_the_checked_in_schema_is_current():
    build, load, dumps = _need("build"), _need("load"), _need("dumps")
    assert dumps(load()) == dumps(build()), (
        "settings.schema.json is stale: run python scripts/gen_settings_schema.py and review the diff")


def test_a_default_outside_its_choices_is_only_ever_a_named_unset_value():
    from agent_friday.core import DEFAULT_SETTINGS
    unset = getattr(_ss(), "UNSET_DEFAULTS", None)
    assert isinstance(unset, dict), "no UNSET_DEFAULTS"
    for dotted, rule in _need("constraints")().items():
        node = DEFAULT_SETTINGS
        for part in dotted.split("."):
            node = node.get(part) if isinstance(node, dict) else None
        if "enum" in rule and node is not None and node not in rule["enum"]:
            assert unset.get(dotted) == node, (dotted, node)


def test_every_default_fits_the_schema():
    from agent_friday.core import DEFAULT_SETTINGS
    assert _need("validate")(DEFAULT_SETTINGS, require_all=True) == []


def test_the_schema_names_exactly_the_settings_friday_has():
    from agent_friday.core import DEFAULT_SETTINGS
    assert set(_need("load")()["properties"]) == set(DEFAULT_SETTINGS)


def test_a_first_runs_settings_fit_the_schema(home):
    s = home._load_settings_raw()
    assert home.SETTINGS_FILE.exists()
    assert _need("validate")(json.loads(home.SETTINGS_FILE.read_text(encoding="utf-8"))) == []
    assert _need("validate")(s) == []


def test_an_old_settings_file_migrates_into_the_schema_and_stays_put(home):
    old = {"agent_name": "Friday", "model_routing": {"mode": "smart", "ollama_url": "http://127.0.0.1:11434"},
           "cost_budget": {"hard_stop_monthly": 50.0}, "a_setting_from_long_ago": 7}
    home.SETTINGS_FILE.write_bytes(b"\xef\xbb\xbf" + json.dumps(old).encode("utf-8"))
    home._invalidate_settings_cache()
    first = home._load_settings_raw()
    assert "a_setting_from_long_ago" not in first, "an unknown key is dropped on read"
    assert first["model_routing"]["mode"] == "local_preferred", "the old spelling migrated"
    assert _need("validate")(first) == []
    home._invalidate_settings_cache()
    second = home._load_settings_raw()          # what the migrations wrote back
    assert _need("validate")(second) == []
    home._invalidate_settings_cache()
    assert home._load_settings_raw() == second, "once migrated, a read changes nothing"


def test_the_validator_catches_a_wrong_type_choice_and_range():
    v = _need("validate")
    found = " | ".join(v({"reflex_approval_replies": "yes", "dock_by_asking": "sometimes",
                          "background_idle_minutes": 9999, "quiet_hours_start": "25:00",
                          "made_up": 1}))
    for bit in ("reflex_approval_replies should be true or false", "dock_by_asking is one of",
                "background_idle_minutes is at most", "quiet_hours_start does not look right",
                "made_up is not a setting"):
        assert bit in found, bit


# ── the schema and POST /api/settings agree ────────────────────────────────

def _post_refuses(key_path: str, value) -> bool:
    """Whether the POST /api/settings checks refuse `value` at `key_path`."""
    from agent_friday.services import owner_choices
    from agent_friday.routes import core_routes as cr
    head, _, tail = key_path.partition(".")
    body = {head: {tail: value}} if tail else {head: value}
    checks = (owner_choices.validate, cr._check_voice_enums, cr._check_background_idle_minutes,
              cr._check_interruption_settings)
    if head == "model_routing":
        from agent_friday.services import cloud_intent
        return value not in cloud_intent.MODES      # the mode control offers exactly these
    return any(chk(body) is not None for chk in checks)


def test_every_choice_the_schema_allows_is_one_the_settings_page_may_write():
    from agent_friday.core import DEFAULT_SETTINGS
    cons = _need("constraints")()
    for dotted, rule in cons.items():
        if "enum" not in rule:
            continue
        for value in rule["enum"]:
            assert not _post_refuses(dotted, value), (dotted, value)
        assert _post_refuses(dotted, "__not_a_choice__"), dotted


def test_every_range_matches_the_settings_page():
    cons = _need("constraints")()
    for dotted, rule in cons.items():
        if "minimum" not in rule:
            continue
        lo, hi = rule["minimum"], rule["maximum"]
        assert not _post_refuses(dotted, lo) and not _post_refuses(dotted, hi), dotted
        assert _post_refuses(dotted, lo - 1) and _post_refuses(dotted, hi + 1), dotted

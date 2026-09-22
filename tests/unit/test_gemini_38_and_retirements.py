"""The Gemini 3.8 Live config rules, and the two models Google shuts down.

Every assertion here corresponds to something that was OBSERVED against the
live API on 2026-09-22, not to something read on a model card. The
distinction matters because the documentation summary this change started
from was wrong about one of them: it said `enable_affective_dialog` had been
removed from the API and that sending it was an error. A real connect shows
the server accepts it. The claims that survived contact are the ones pinned
below.

What was observed, and the error each rule prevents:

  * gemini-3.8-live          connects (v1beta and v1alpha)
  * gemini-3.8-live + thinking_config
        -> 1007 "Thinking level is not supported for this model"
  * gemini-3.8-live + proactivity (True OR False)
        -> 1007 'Unknown name "proactivity" at 'setup': Cannot find field'
  * gemini-3.8-live-extended-thinking, bare
        -> 1007 "Thinking level must be specified for this model"
  * ...+ thinking_level=MINIMAL
        -> 1007 "Thinking level MINIMAL is not supported for this model"
  * ...+ thinking_level=LOW                              connects
  * ...+ BLOCKING function declarations
        -> 1007 "BLOCKING function calls are not supported for this model"
  * ...+ NON_BLOCKING function declarations              connects

The probe that produced those ran a deliberately fake model id in the same
batch, which failed 1008 — so "it connected" meant something.
"""
import datetime as _dt

import pytest

from agent_friday.services import cost_meter, creative_engine, voice_engine
from agent_friday.services.model_catalog import build_catalog


# ══════════════════════════════════════════════════════════════════════════
#  1. The 3.8 config rules
# ══════════════════════════════════════════════════════════════════════════

@pytest.mark.parametrize("model", [
    "gemini-3.8-live",
    "gemini-3.8-live-extended-thinking",
])
def test_38_live_never_sends_proactivity(model):
    """`proactivity` is not a field a 3.8 setup message has.

    Both values fail identically, so this is not "don't turn it off" — it is
    "don't mention it". A live session that sends it dies at connect, which
    the browser sees as a socket that opened and went quiet.
    """
    assert voice_engine._model_supports_proactivity_config(model) is False


def test_proactivity_still_allowed_on_the_25_models():
    """The guard has to be narrow or it silently disables a working feature."""
    for model in ("gemini-2.5-flash-native-audio-latest",
                  "gemini-2.5-flash-native-audio-preview-09-2025",
                  "gemini-3.1-flash-live-preview"):
        assert voice_engine._model_supports_proactivity_config(model) is True


@pytest.mark.parametrize("model", [
    "gemini-3.8-live",
    "gemini-3.8-live-extended-thinking",
])
def test_38_live_is_not_treated_as_an_affective_dialog_model(model):
    assert voice_engine._model_supports_affective_dialog(model) is False


def test_affective_dialog_still_on_for_native_audio():
    assert voice_engine._model_supports_affective_dialog(
        "gemini-2.5-flash-native-audio-latest") is True


def test_thinking_config_is_required_on_extended_thinking_only():
    assert voice_engine._model_requires_thinking_config(
        "gemini-3.8-live-extended-thinking") is True
    assert voice_engine._model_requires_thinking_config("gemini-3.8-live") is False
    assert voice_engine._model_requires_thinking_config(
        "gemini-2.5-flash-native-audio-latest") is False


def test_thinking_config_is_rejected_by_plain_38_live():
    """The mirror image of the rule above, and a separate predicate on purpose.

    'does not require' and 'actively refuses' are different facts. A model
    that merely did not require thinking_config could be sent it harmlessly;
    gemini-3.8-live cannot.
    """
    assert voice_engine._model_rejects_thinking_config("gemini-3.8-live") is True
    assert voice_engine._model_rejects_thinking_config(
        "gemini-3.8-live-extended-thinking") is False
    # A 2.5 model neither requires nor refuses it — it is simply not a 3.8.
    assert voice_engine._model_rejects_thinking_config(
        "gemini-2.5-flash-native-audio-latest") is False


def test_minimal_thinking_level_is_never_forwarded():
    """MINIMAL reads like a valid level and is rejected by name.

    Passing it through would fail the connect with a message about thinking
    levels, which looks like a model outage rather than a bad setting.
    """
    assert voice_engine.resolve_live_thinking_level("MINIMAL") == "LOW"
    assert voice_engine.resolve_live_thinking_level("minimal") == "LOW"
    assert voice_engine.resolve_live_thinking_level(None) == "LOW"
    assert voice_engine.resolve_live_thinking_level("nonsense") == "LOW"
    assert "MINIMAL" not in voice_engine.LIVE_THINKING_LEVELS


@pytest.mark.parametrize("level", ["LOW", "MEDIUM", "HIGH", "low", "  high  "])
def test_accepted_thinking_levels_pass_through(level):
    assert voice_engine.resolve_live_thinking_level(level) == level.strip().upper()


def test_extended_thinking_requires_non_blocking_tools():
    assert voice_engine._model_requires_non_blocking_tools(
        "gemini-3.8-live-extended-thinking") is True
    # Plain 3.8-live accepts either mode, so it must not be forced.
    assert voice_engine._model_requires_non_blocking_tools("gemini-3.8-live") is False


def test_tool_builder_stamps_the_requested_behavior_on_every_declaration():
    """NON_BLOCKING on SOME declarations is the same connect failure as none.

    The extended-thinking model rejects BLOCKING function calls outright, so
    one un-stamped declaration out of sixty takes down the session. This
    walks the rendered list rather than trusting the builder's return shape.
    """
    types = _FakeTypes()
    tools = voice_engine._build_voice_live_tools(types, behavior="NON_BLOCKING")
    assert tools, "builder returned no tools - the rest of this test is vacuous"
    decls = tools[0].function_declarations
    assert len(decls) > 1, "only one declaration rendered; not a real surface"
    assert all(d.behavior == "NON_BLOCKING" for d in decls)


def test_tool_builder_defaults_to_no_behavior_field():
    """Unstamped is the correct default: BLOCKING is fine everywhere else."""
    types = _FakeTypes()
    tools = voice_engine._build_voice_live_tools(types)
    assert tools
    assert all(d.behavior is None for d in tools[0].function_declarations)


def test_tool_builder_survives_an_sdk_without_Behavior():
    """An old google-genai must cost us the field, not the whole tool surface.

    Raising here would leave the voice session tool-free, narrating actions
    it cannot take — strictly worse than connecting without the behavior and
    letting the fallback chain handle the refusal.
    """
    types = _FakeTypes(with_behavior=False)
    tools = voice_engine._build_voice_live_tools(types, behavior="NON_BLOCKING")
    assert tools, "tool surface was dropped entirely"
    assert all(d.behavior is None for d in tools[0].function_declarations)


# ── Fake google.genai.types, enough for _build_voice_live_tools ────────────

class _FakeSchema:
    def __init__(self, **kw):
        self.__dict__.update(kw)


class _FakeDecl:
    def __init__(self, name=None, description=None, parameters=None, behavior=None):
        self.name = name
        self.description = description
        self.parameters = parameters
        self.behavior = behavior


class _FakeTool:
    def __init__(self, function_declarations=None):
        self.function_declarations = function_declarations or []


class _FakeType:
    STRING = "STRING"
    INTEGER = "INTEGER"
    NUMBER = "NUMBER"
    BOOLEAN = "BOOLEAN"
    OBJECT = "OBJECT"
    ARRAY = "ARRAY"


class _FakeBehavior:
    BLOCKING = "BLOCKING"
    NON_BLOCKING = "NON_BLOCKING"


class _FakeTypes:
    def __init__(self, with_behavior=True):
        self.Schema = _FakeSchema
        self.FunctionDeclaration = _FakeDecl
        self.Tool = _FakeTool
        self.Type = _FakeType
        if with_behavior:
            self.Behavior = _FakeBehavior


# ══════════════════════════════════════════════════════════════════════════
#  2. Promotion — only what a real connect earned
# ══════════════════════════════════════════════════════════════════════════

def test_38_live_is_in_the_fallback_chain():
    """Earned by an actual bidiGenerateContent connect, per the rule stated
    in voice_engine next to LIVE_MODEL."""
    assert voice_engine.LIVE_MODEL_FALLBACK3 == "gemini-3.8-live"


def test_extended_thinking_is_not_in_the_fallback_chain():
    """It cannot connect with the bare config the chain uses.

    A fallback that always fails is worse than no fallback: it consumes an
    attempt and reports a config error as though the previous model were
    still failing.
    """
    chain = {voice_engine.LIVE_MODEL,
             voice_engine.LIVE_MODEL_FALLBACK,
             voice_engine.LIVE_MODEL_FALLBACK2,
             voice_engine.LIVE_MODEL_FALLBACK3}
    assert "gemini-3.8-live-extended-thinking" not in chain


def test_09_2025_preview_was_not_retired_on_a_docs_absence():
    """It is missing from Google's models page and its deprecations table.

    It also connects: verified 2026-09-22 by the same probe, and models.get
    still returns it (bidiGenerateContent, 131072/8192). Absence from a docs
    page is not evidence of retirement, and this fallback is load-bearing.
    """
    assert voice_engine.LIVE_MODEL_FALLBACK == \
        "gemini-2.5-flash-native-audio-preview-09-2025"
    assert "gemini-2.5-flash-native-audio-preview-09-2025" not in \
        voice_engine._RETIRED_LIVE_MODELS


# ══════════════════════════════════════════════════════════════════════════
#  3. The catalogue
# ══════════════════════════════════════════════════════════════════════════

def test_38_live_models_are_offered_for_voice():
    voice_ids = [e["id"] for e in build_catalog()["roles"]["voice"]]
    assert "gemini-3.8-live" in voice_ids
    assert "gemini-3.8-live-extended-thinking" in voice_ids


def test_38_flash_is_listed_but_never_pickable():
    """Same deal as 2.5 Pro and the rest of the 3.x text line.

    routing/model_router._apply_cloud_provider only retags anthropic, openai
    and local, so a picked Gemini text model is silently answered by a
    different one. Listed so the Model Browser is honest; never offered.
    """
    cat = build_catalog()
    # LISTED: it has to actually appear in the flat catalogue, or "listed but
    # not pickable" is indistinguishable from "absent", and both halves of
    # this test would pass for a model nobody had added at all.
    flat_ids = {e["id"] for e in cat["models"]}
    assert "gemini-3.8-flash" in flat_ids
    # NOT PICKABLE: absent from every role the UI offers.
    for role in ("orchestrator", "subagent", "creative", "voice"):
        assert "gemini-3.8-flash" not in [e["id"] for e in cat["roles"][role]]


def test_38_models_never_leak_into_creative():
    creative_ids = [e["id"] for e in build_catalog()["roles"]["creative"]]
    for bad in ("gemini-3.8-live", "gemini-3.8-live-extended-thinking",
                "gemini-3.8-flash"):
        assert bad not in creative_ids
        assert bad in creative_engine._FORBIDDEN_CREATIVE


# ══════════════════════════════════════════════════════════════════════════
#  4. The two shutdowns
# ══════════════════════════════════════════════════════════════════════════

def test_omni_alias_no_longer_resolves_to_the_model_that_dies_2026_09_30():
    """gemini-omni-flash-preview shuts down 2026-09-30.

    Checked through the resolver rather than by reading the map, because the
    map is not the only thing between a friendly id and the wire.
    """
    for alias in ("gemini-omni-flash", "gemini-omni", "omni-flash", "omni"):
        resolved = creative_engine.resolve_video_model(alias)
        assert resolved == "gemini-omni-1.1-flash", alias
        assert resolved != "gemini-omni-flash-preview"


def test_the_retired_omni_id_resolves_forward_rather_than_404ing():
    """A settings.json or saved creation still naming the preview must not
    dispatch to a model that no longer exists."""
    assert creative_engine.resolve_video_model(
        "gemini-omni-flash-preview") == "gemini-omni-1.1-flash"


def test_omni_replacement_still_takes_the_interactions_path():
    """The new id must keep routing to _generate_video_omni, not Veo's LRO
    path. Renaming a model into a different dispatch branch is a silent
    behaviour change that no id assertion would catch."""
    assert creative_engine._is_omni_model("gemini-omni-1.1-flash") is True


def test_bare_nano_banana_no_longer_resolves_to_the_model_that_dies_2026_10_02():
    """gemini-2.5-flash-image (the original Nano Banana) shuts down
    2026-10-02. The unversioned nickname now means the current one."""
    resolved = creative_engine.resolve_image_model("nano-banana")
    assert resolved != "gemini-2.5-flash-image"
    assert resolved == "gemini-3.1-flash-image"


def test_the_versioned_nano_banana_aliases_did_not_move():
    """The retirement fix must not quietly repoint Pro or 2 as well."""
    assert creative_engine.resolve_image_model(
        "gemini-nano-banana-pro") == "gemini-3-pro-image"
    assert creative_engine.resolve_image_model(
        "gemini-nano-banana-2") == "gemini-3.1-flash-image"


# ══════════════════════════════════════════════════════════════════════════
#  5. Cost metering
# ══════════════════════════════════════════════════════════════════════════

@pytest.mark.parametrize("model", [
    "gemini-3.8-live",
    "gemini-3.8-live-extended-thinking",
    "gemini-3.8-flash",
    "gemini-omni-1.1-flash",
])
def test_every_new_model_has_its_own_price_row(model):
    """A missing row does not fail loudly — it meters $0.

    The cost panel renders that identically to "ran on-device, cost
    nothing", so an unpriced cloud call is worse than an unmetered one: it
    actively tells the user something false.
    """
    assert model in cost_meter.PRICING
    row = cost_meter.PRICING[model]
    assert row["in"] > 0 and row["out"] > 0


def test_the_retired_omni_price_row_outlives_its_model():
    """Spend already recorded against the old id still has to price."""
    assert "gemini-omni-flash-preview" in cost_meter.PRICING


def test_38_live_meters_audio_rates_not_text_rates():
    """A live session is audio in, audio out.

    Pricing it at the text rate would under-meter a voice call by roughly
    4x on output. This pins the intent, not just the presence of a number.
    """
    live = cost_meter.PRICING["gemini-3.8-live"]
    text = cost_meter.PRICING["gemini-3.8-flash"]
    assert live["out"] > text["out"]
    assert live["in"] > text["in"]


def test_extended_thinking_is_not_priced_cheaper_than_plain_38():
    """Thinking tokens bill as output, so it cannot be the cheaper row."""
    assert (cost_meter.PRICING["gemini-3.8-live-extended-thinking"]["out"]
            >= cost_meter.PRICING["gemini-3.8-live"]["out"])


# ══════════════════════════════════════════════════════════════════════════
#  6. The mechanical guard itself
# ══════════════════════════════════════════════════════════════════════════

def _checker():
    import importlib.util
    from pathlib import Path
    root = Path(voice_engine.__file__).resolve().parents[3]
    path = root / "scripts" / "check_stale_model_names.py"
    spec = importlib.util.spec_from_file_location("_stale_check", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_the_shutdown_check_is_green_today():
    assert _checker().find_google_problems() == []


def test_the_shutdown_check_fires_before_the_date_not_after():
    """The whole point. A check that only notices on the morning the model
    goes dark has not prevented anything.

    Uses a date chosen so the 2026-09-30 shutdown is still 20 days away and
    a repo that had NOT been fixed would already be failing.
    """
    chk = _checker()
    early = _dt.date(2026, 9, 10)
    assert (_dt.date.fromisoformat("2026-09-30") - early).days > 0, \
        "test date is past the shutdown - it would prove nothing"
    # The real tree is fixed, so it stays clean even on the early date...
    assert chk.find_google_problems(today=early) == []
    # ...and a tree that still named the dead id would not. Proven by asking
    # the checker about a file that does name it, with the allowlist entry
    # that currently excuses it removed.
    chk.GOOGLE_ALLOWED.discard(
        ("src/agent_friday/services/creative_engine.py", "gemini-omni-flash-preview"))
    chk.GOOGLE_ALLOWED.discard(
        ("src/agent_friday/services/provider_registry.py", "gemini-omni-flash-preview"))
    problems = chk.find_google_problems(today=early)
    assert problems, "check cannot detect the id it exists to detect"
    assert any("gemini-omni-flash-preview" in p for p in problems)
    assert any("in 20 days" in p for p in problems)


def test_the_shutdown_check_does_not_match_longer_ids_by_substring():
    """'gemini-2.5-flash-image' is a prefix of 'gemini-2.5-flash-image-preview',
    a different model with a different date. Substring matching here would
    report retirements that are not the ones being looked at."""
    chk = _checker()
    assert chk._mentions('"gemini-2.5-flash-image": 1', "gemini-2.5-flash-image")
    assert not chk._mentions('"gemini-2.5-flash-image-preview": 1',
                             "gemini-2.5-flash-image")


def test_every_allowlist_entry_names_a_file_that_exists():
    """An allowlist entry for a moved or renamed file silently excuses
    nothing, and reads as coverage that is not there."""
    from pathlib import Path
    chk = _checker()
    root = Path(voice_engine.__file__).resolve().parents[3]
    for rel, model_id in chk.GOOGLE_ALLOWED:
        assert (root / rel).exists(), rel
        assert model_id in chk.GOOGLE_MODEL_SHUTDOWNS, model_id


# ══════════════════════════════════════════════════════════════════════════
#  7. The retirement that was announced and then taken back
# ══════════════════════════════════════════════════════════════════════════
# A repo comment asserted that gemini-2.5-pro and gemini-2.5-flash shut down
# 2026-10-16. Checked against ai.google.dev/gemini-api/docs/deprecations on
# 2026-09-22 (page's own "last updated" that same day): both rows read "No
# shutdown date announced", under a note saying the 2.5 models are not
# deprecated and are served until further notice. Google did publish that
# date around 2026-07-28 and then removed it with no changelog entry.
#
# Both ids also still resolve via models.get, probed 2026-09-22 in a batch
# containing a deliberately fake id (404) and a genuinely shut-down one,
# gemini-2.5-pro-preview-03-25 (404) — so "200" meant something.
#
# Those two facts answer different questions and are kept apart deliberately:
# the probe is authoritative for EXISTS TODAY and says nothing about any
# future date; the docs page is authoritative for the DATE and says nothing
# about whether the endpoint is up this minute. The claim being refuted here
# is a date, so the docs page is what refutes it.

def test_the_25_pair_carries_no_shutdown_date():
    chk = _checker()
    for model_id in ("gemini-2.5-pro", "gemini-2.5-flash"):
        assert model_id not in chk.GOOGLE_MODEL_SHUTDOWNS, (
            "%s has a shutdown date again - if Google re-announced one, cite "
            "it; if this is the withdrawn 2026-10-16 date coming back, it is "
            "the bug this section exists to stop" % model_id)
        assert model_id in chk.GOOGLE_NO_SHUTDOWN_ANNOUNCED, model_id


def test_reinstating_the_withdrawn_date_would_fire_the_check():
    """Proves the assertion above could have failed.

    Without this, "2.5 Flash is not in the shutdown table" is satisfied just
    as well by a checker that cannot see the model at all. Putting the old
    value back in memory shows the machinery really is pointed at the files
    that dispatch to it — so the absence of a complaint is a finding, not a
    blind spot.
    """
    chk = _checker()
    assert chk.find_google_problems(today=_dt.date(2026, 9, 22)) == []
    chk.GOOGLE_MODEL_SHUTDOWNS["gemini-2.5-flash"] = (
        "2026-10-16", "gemini-3.8-flash", "the withdrawn date")
    problems = chk.find_google_problems(today=_dt.date(2026, 9, 22))
    assert any("gemini-2.5-flash" in p for p in problems), \
        "the check cannot see gemini-2.5-flash at all - the real test is vacuous"
    assert any("provider_registry.py" in p for p in problems)


def test_25_flash_is_still_a_pickable_voice_model():
    """The user-visible consequence of getting this wrong.

    Acting on the retracted date means deleting this entry, and the first
    anyone would know is a voice seat that silently stopped being offered.
    """
    cat = build_catalog()
    voice_ids = {e["id"] for e in cat["roles"]["voice"]}
    assert "gemini-2.5-flash" in voice_ids
    # Not vacuous: the same lookup does not hand back arbitrary ids.
    assert "gemini-2.5-pro" not in voice_ids


def test_lyria_pro_is_not_flagged_on_a_date_google_never_published():
    """`lyria-3-pro-preview` was briefly carried as shutting down 2027-05-07.

    The deprecations page recommends lyria-3.5 for it but announces no
    shutdown date, and a recommendation is not a deadline. Left uncorrected,
    the build would have broken in April 2027 demanding a migration nobody
    asked for. Checked on a date inside the old warn window, so a tree that
    still believed the invented date would fail here.
    """
    chk = _checker()
    inside_old_window = _dt.date(2027, 4, 20)
    assert (_dt.date.fromisoformat("2027-05-07") - inside_old_window).days \
        <= chk.WARN_WINDOW_DAYS, "test date is outside the window - proves nothing"
    # Scoped to lyria on purpose: by April 2027 gemini-3.1-flash-lite really
    # is inside its (Google-published) window, so the tree is legitimately
    # noisy on that date and a bare `== []` would be asserting the wrong thing.
    assert not [p for p in chk.find_google_problems(today=inside_old_window)
                if "lyria" in p]
    # ...and it WOULD have fired with the invented date restored, which is
    # what makes the line above evidence rather than a coincidence.
    chk.GOOGLE_MODEL_SHUTDOWNS["lyria-3-pro-preview"] = (
        "2027-05-07", "lyria-3.5", "the invented date")
    problems = chk.find_google_problems(today=inside_old_window)
    assert any("lyria-3-pro-preview" in p and "music_engine.py" in p
               for p in problems)


def test_the_two_google_tables_never_disagree():
    """An id cannot both have a shutdown date and have none. This is how the
    withdrawn date would come back: added to one table without the other
    being cleaned up."""
    chk = _checker()
    assert chk.find_table_conflicts() == []
    chk.GOOGLE_MODEL_SHUTDOWNS["gemini-2.5-pro"] = (
        "2026-10-16", "gemini-3.1-pro-preview", "the withdrawn date")
    conflicts = chk.find_table_conflicts()
    assert conflicts and "gemini-2.5-pro" in conflicts[0]


def test_every_no_shutdown_entry_records_when_it_was_checked():
    """A negative result with no date on it decays into a rumour. These are
    read off a page that changed its mind once already."""
    chk = _checker()
    assert chk.GOOGLE_NO_SHUTDOWN_ANNOUNCED, "table is empty"
    for model_id, (checked, note) in chk.GOOGLE_NO_SHUTDOWN_ANNOUNCED.items():
        _dt.date.fromisoformat(checked)  # raises if not a real ISO date
        assert note.strip(), model_id
    assert chk.GOOGLE_DEPRECATIONS_URL.startswith("https://ai.google.dev/")

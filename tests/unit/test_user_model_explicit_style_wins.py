"""An explicit style setting always beats what the user model infers.

The owner's settings say how Friday should talk (`communication_style`, `response_length`). The user model also
infers a style from how they write. When the two disagree only the setting may reach the prompt: an inferred trait
the setting rules out is dropped; one that agrees, or that no setting speaks to, stays labelled as inferred.
Rule held for every small case in `test_the_rule_over_every_setting_and_inference`.
"""
import itertools

import pytest

from agent_friday import core
from agent_friday.services import setup_profile
from agent_friday.services import user_model as um

STYLES = ("professional", "casual", "technical")
LENGTHS = ("concise", "standard", "detailed")
FORMALITY = {"formal": "0.9", "casual": "0.1", "balanced": "0.5"}
VERBOSITY = {"detail": "0.9", "terse": "0.1", "balanced": "0.5"}
RULED_OUT = {("casual", "formal"), ("professional", "casual"), ("technical", "casual"),
             ("detailed", "terse"), ("concise", "detail")}
SAYS = {"formal": "formal/professional", "casual": "casual/relaxed", "balanced": "balanced",
        "detail": "prefers detail", "terse": "prefers terse answers"}


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    um.forget()
    um._SESSION_TRAITS.clear()
    monkeypatch.setattr(setup_profile, "load_profile", lambda: {"style": None})
    yield
    um.forget()
    um._SESSION_TRAITS.clear()


def settings(style="professional", length="standard"):
    return lambda: {"communication_style": style, "response_length": length}


def infer(formality=None, verbosity=None):
    """A reading inferred from how the owner writes: session-only and with no owner mark, as observe_message leaves it."""
    if formality:
        um._SESSION_TRAITS["comm.formality"] = (float(FORMALITY[formality]), 6)
    if verbosity:
        um._SESSION_TRAITS["comm.verbosity"] = (float(VERBOSITY[verbosity]), 6)


def test_a_formal_terse_inference_does_not_reach_the_prompt_against_casual_and_detailed(monkeypatch):
    monkeypatch.setattr(core, "_load_settings", settings("casual", "detailed"))
    infer("formal", "terse")
    out = um.render_user_model_prompt()
    assert "formal/professional" not in out and "terse" not in out


def test_an_inference_that_agrees_with_the_setting_stays_and_is_still_marked_inferred(monkeypatch):
    monkeypatch.setattr(core, "_load_settings", settings("casual", "detailed"))
    infer("casual", "detail")
    out = um.render_user_model_prompt()
    assert "casual/relaxed (inferred" in out and "prefers detail (inferred" in out


def test_a_trait_the_owner_set_in_the_user_model_is_kept_against_a_setting(monkeypatch):
    monkeypatch.setattr(core, "_load_settings", settings("casual", "detailed"))
    um.set_trait("comm.formality", "0.9", confidence=1.0)             # set by the owner: carries the owner mark
    assert "formal/professional" in um.render_user_model_prompt()


@pytest.mark.parametrize("style,length,formality,verbosity", list(itertools.product(
    STYLES, LENGTHS, FORMALITY, VERBOSITY)))
def test_the_rule_over_every_setting_and_inference(monkeypatch, style, length, formality, verbosity):
    monkeypatch.setattr(core, "_load_settings", settings(style, length))
    infer(formality, verbosity)
    out = um.render_user_model_prompt()
    formal_shown = SAYS[formality] in out
    verbose_shown = SAYS[verbosity] in out
    assert formal_shown == ((style, formality) not in RULED_OUT), (style, formality, out)
    if verbosity != "balanced":
        assert verbose_shown == ((length, verbosity) not in RULED_OUT), (length, verbosity, out)


# -- the first-run sliders are numbers: 0-100, not words ------------------------------------------

def stated(formality, detail):
    sliders = {"formality": formality, "verbosity": detail}
    return lambda: {"style": {"sliders": sliders}}


def test_formality_22_and_detail_85_beat_a_formal_terse_inference(monkeypatch):
    monkeypatch.setattr(core, "_load_settings", settings("professional", "standard"))   # the words say nothing
    monkeypatch.setattr(setup_profile, "load_profile", stated(22, 85))
    infer("formal", "terse")
    out = um.render_user_model_prompt()
    assert "formal/professional" not in out and "terse" not in out


def test_the_stated_numbers_keep_an_inference_that_agrees(monkeypatch):
    # setup_profile.apply_style writes casual + detailed for formality 22 / detail 85: the state the owner is in.
    monkeypatch.setattr(core, "_load_settings", settings("casual", "detailed"))
    monkeypatch.setattr(setup_profile, "load_profile", stated(22, 85))
    infer("casual", "detail")
    out = um.render_user_model_prompt()
    assert "casual/relaxed (inferred" in out and "prefers detail (inferred" in out


def test_when_two_stated_things_disagree_an_inference_either_one_contradicts_is_left_out(monkeypatch):
    # Slider 22 but the Settings word is professional (changed later, or the first-run save never landed): the
    # inference may not side with one statement against the other, so a casual reading is not put in the prompt.
    monkeypatch.setattr(core, "_load_settings", settings("professional", "standard"))
    monkeypatch.setattr(setup_profile, "load_profile", stated(22, 85))
    infer("casual", "detail")
    out = um.render_user_model_prompt()
    assert "casual" not in out and "prefers detail (inferred" in out


@pytest.mark.parametrize("formality,detail", list(itertools.product((0, 22, 39, 40, 50, 59, 60, 85, 100),
                                                                     (0, 33, 34, 50, 66, 67, 85, 100))))
@pytest.mark.parametrize("reading_f,reading_v", [("formal", "terse"), ("casual", "detail"), ("balanced", "terse")])
def test_the_numeric_rule_over_every_edge(monkeypatch, formality, detail, reading_f, reading_v):
    monkeypatch.setattr(core, "_load_settings", settings("technical", "standard"))
    monkeypatch.setattr(setup_profile, "load_profile", stated(formality, detail))
    infer(reading_f, reading_v)
    out = um.render_user_model_prompt()
    # the enum setting here (technical) rules out an inferred "casual"; the numbers rule out the rest
    ruled_f = (reading_f == "formal" and formality < 40) or (reading_f == "casual")
    ruled_v = (reading_v == "terse" and detail >= 67) or (reading_v == "detail" and detail < 34)
    assert (SAYS[reading_f] in out) == (not ruled_f), (formality, reading_f, out)
    assert (SAYS[reading_v] in out) == (not ruled_v), (detail, reading_v, out)


def test_an_unreadable_first_run_profile_means_nothing_stated(monkeypatch):
    monkeypatch.setattr(core, "_load_settings", settings())
    monkeypatch.setattr(setup_profile, "load_profile", lambda: (_ for _ in ()).throw(OSError("locked")))
    infer("formal", "terse")
    assert "formal/professional" in um.render_user_model_prompt()


# -- first-run and the user model agree ---------------------------------------------------------

def test_applying_the_first_run_style_sets_the_user_model_so_inference_cannot_outrank_it(monkeypatch):
    from agent_friday.services import soul
    monkeypatch.setattr(soul, "load_soul", lambda: "")
    monkeypatch.setattr(soul, "save_soul", lambda text: {"ok": True})
    monkeypatch.setattr(core, "_save_settings", lambda delta: None)
    setup_profile.apply_style({"sliders": {"formality": 22, "verbosity": 85}})
    assert um.owner_set("comm.formality") and um.owner_set("comm.verbosity")
    assert um.get_trait("comm.formality") == 0.22 and um.get_trait("comm.verbosity") == 0.85
    for _ in range(30):                                   # a long run of formal, terse messages
        um.observe_message("Kindly proceed. Thank you.")
    assert um.get_trait("comm.formality") == 0.22 and um.get_trait("comm.verbosity") == 0.85

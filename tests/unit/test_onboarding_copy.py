"""The onboarding may not promise anything the code does not do.

This file is the reason the copy lives in a module. The old wizard told every
user "your private information never leaves your device" on the welcome screen
and "nothing leaves your machine" on the provider screen, and both were false --
the first describes a keyword classifier with documented recall gaps, the second
describes a routing mode the wizard did not set. Nobody noticed because a string
in a print statement has nothing holding it to the behaviour it describes.

So: every promise the copy makes is asserted here against the thing that keeps
it. If someone loosens a default, one of these fails and names the sentence that
became a lie.
"""
from __future__ import annotations

import re

import pytest

from agent_friday.services import onboarding_copy as oc


def flat(text: str) -> str:
    """Lower-cased, whitespace-collapsed.

    The copy is hard-wrapped prose, so a sentence a test cares about is
    routinely split across a line break. Asserting on the raw string makes the
    test depend on where the paragraph happened to wrap, which is not a fact
    about the product.
    """
    return re.sub(r"\s+", " ", (text or "")).strip().lower()


def screen_text(name: str) -> str:
    return flat("\n".join(oc.screen(name)["blocks"]))


ALL_TEXT = flat("\n".join(b for s in oc.all_screens() for b in s["blocks"]))


# -- The claims that were removed, and must not come back --------------------

@pytest.mark.parametrize("banned, why", [
    ("never leaves your device",
     "a pattern matcher cannot support 'never'; the classifier has documented "
     "recall gaps and this test file's docstring says which"),
    ("nothing leaves your machine",
     "installing Ollama does not change routing; the mode does"),
    ("full feature set",
     "cloud-only withholds vault answers whole and builds no semantic map"),
    ("military-grade",
     "cut in drafting"),
    ("sovereign",
     "cut in drafting"),
])
def test_the_false_claims_are_gone(banned, why):
    assert banned not in ALL_TEXT, "%r is back in the onboarding: %s" % (banned, why)


def test_no_screen_claims_local_models_are_what_protects_the_vault():
    """The egress gate is the enforcer; the local model is a routing preference.

    A user taught "local model = safe" installs Ollama, sees a green panel and
    changes nothing -- because model_routing.mode stays cloud_only until
    something sets it. That is exactly the failure the old provider screen
    shipped.
    """
    routing = screen_text("routing")
    assert "safe" not in routing, (
        "the routing screen is teaching 'local = safe' rather than describing "
        "where the model runs"
    )


# -- The promises, against the code that keeps them --------------------------

def test_the_hardware_number_matches_the_model_plan():
    """'about 6.5 GB' is computed, not typed. If the ladder moves, this fails."""
    from agent_friday.services import model_plan

    stated = re.search(r"about ([\d.]+) gb free", flat(oc.ROUTING))
    assert stated, "the routing screen no longer states a hardware floor"
    claimed = float(stated.group(1))

    floor = min(m["vram_gib"] for m in model_plan.BRAIN_MODELS
                if m.get("tools"))
    needed = floor + model_plan.DISPLAY_RESERVE_GIB
    assert abs(needed - claimed) < 0.6, (
        "the copy says %.1f GB but the smallest tool-capable model now needs "
        "%.2f GiB (%s + %s display reserve)"
        % (claimed, needed, floor, model_plan.DISPLAY_RESERVE_GIB)
    )


def test_the_vault_screen_names_the_cipher_the_code_uses():
    assert "aes-256-gcm" in flat(oc.VAULT)
    from agent_friday.privacy import vault_crypto
    assert "aes" in (vault_crypto.__doc__ or "").lower()


def test_the_vault_screen_does_not_offer_to_write_the_passphrase_to_a_file():
    """The old screen offered, defaulted to YES, to save it to start.bat."""
    vault = screen_text("vault")
    assert "start.bat" not in vault or "an earlier version" in vault, (
        "the vault screen mentions start.bat as somewhere the passphrase goes"
    )
    assert "generate a random passphrase" not in vault


def test_the_routing_screen_can_actually_return_a_local_mode():
    """The old step_provider printed 'coming in v5' and returned anthropic.

    So the wizard could not produce a local-first install at all, while the
    screen before it promised nothing left the machine.
    """
    modes = {c["mode"] for c in oc.screen("routing")["choices"]}
    assert modes == {"cloud_only", "local_only", "local_preferred"}

    import agent_friday.core as core
    valid = core.DEFAULT_SETTINGS["model_routing"]
    assert "mode" in valid
    for m in modes:
        assert isinstance(m, str) and m


def test_the_cloud_screen_says_the_map_loses_its_semantic_layer():
    """FACT-FIX 1. Tier B pins extraction to a local model and produces nothing
    without one -- it used to do so silently."""
    ack = screen_text("cloud_ack")
    assert "only runs on a model on this computer" in ack, (
        "the cloud screen still implies the whole map survives cloud-only"
    )

    from agent_friday.services import knowledge_graph as kg
    assert kg.KG_DEFAULT_SETTINGS["indexing_mode"] == "local_only", (
        "indexing_mode changed; the sentence about the second layer may now be "
        "wrong in the other direction"
    )


def test_the_cloud_screen_admits_the_classifier_gap_with_its_real_example():
    """Measured: this exact sentence is TIER_1 and goes to Anthropic verbatim."""
    ack = screen_text("cloud_ack")
    assert "sertraline" in ack

    from agent_friday.services import egress_gate as eg
    tier = eg._classify_cloud("She started sertraline 50mg last month.")
    assert tier == 1, (
        "the classifier now catches the example the copy uses to admit it does "
        "not -- the paragraph needs rewriting, in the good direction"
    )


def test_the_third_party_screen_claims_a_delete_path_that_exists():
    """FACT-FIX 2. The draft said 'Friday cannot yet forget a person.'"""
    tp = screen_text("third_party")
    assert "friday can forget a person" in tp

    from agent_friday.services import forget_person
    for fn in ("find", "forget", "unforget", "is_forgotten", "list_forgotten"):
        assert callable(getattr(forget_person, fn, None)), (
            "the copy promises a delete path but forget_person.%s is missing" % fn
        )


def test_the_third_party_screen_admits_what_forgetting_does_not_touch():
    """forget() deliberately does not rewrite the user's own wiki pages."""
    tp = screen_text("third_party")
    assert "your own pages and conversations are left alone" in tp


def test_every_screen_renders():
    for name in oc.SCREEN_ORDER:
        s = oc.screen(name)
        assert s["title"] and s["blocks"]
        assert all(isinstance(b, str) and b.strip() for b in s["blocks"])


def test_the_vault_screen_comes_after_the_screen_that_explains_why():
    """'Vault first' taken literally puts a passphrase prompt in front of a user
    who has not been told what a vault is. Screen 1 is what makes screen 2 make
    sense -- but the vault still precedes every cosmetic choice."""
    order = list(oc.SCREEN_ORDER)
    assert order.index("collects") < order.index("vault")
    assert order.index("vault") < order.index("routing")


def test_the_delete_path_copy_names_where_the_button_actually_is():
    """The draft said "Settings - Contacts". The control is in the Contacts
    WORKSPACE, reached from the dock, not from Settings. A screen that tells a
    user to look somewhere the button is not is a screen that reads as a lie the
    first time they try it.
    """
    tp = screen_text("third_party")
    assert "open contacts, choose someone, and forget" in tp
    assert "settings - contacts" not in tp

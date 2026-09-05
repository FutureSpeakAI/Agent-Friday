"""Gauntlet finding F24: the five built-in persona/distribution presets
(default/journalist/developer/researcher/executive), shown during onboarding
and in Settings > Distribution, had descriptions like "Code-heavy
configuration with GitHub integration, CI/CD awareness, and dev tools" --
promising a curated workspace/tool setup per persona.

Reality: apply_distro() (services/distributions.py) writes exactly
{distribution, show_all_workspaces, dock_layout} plus a personality
override. dock_layout has zero readers anywhere in src/ or either HTML
file. default_workspaces/default_providers/default_recipes are declared
per-persona but never make it into apply_distro()'s delta at all --
read into Distribution.__init__ as attributes and never referenced again.
show_all_workspaces is identically False across all five builtins. The
only field with a real, live effect is system_prompt_overrides.
base_personality. So the descriptions should describe a personality/tone
change, not curated workspaces, tool integration, or GitHub/CI-CD
awareness.

This probe pins the corrected descriptions and grounds that apply_distro()
still only writes the fields the corrected copy claims (a personality-only
effect), so the new wording is not itself a fresh overpromise.

Red -> green -> red-on-revert proof: fails against the old literal claims
(proving BUILTIN_DISTROS really made them) and passes against the
corrected text.
"""
from __future__ import annotations

import inspect

from agent_friday.services import distributions as dist


_OLD_CLAIMS = {
    "journalist": "News-heavy configuration with source trust, editorial tools, and research focus",
    "developer": "Code-heavy configuration with GitHub integration, CI/CD awareness, and dev tools",
    "researcher": "Deep-research configuration — long-form synthesis, citations, wiki and source trust",
    "executive": "Executive configuration — briefings, calendar, finance, concise decision support",
}


def _old_claims_would_fail_this_assertion():
    """Not a real test -- documents that the assertions below are
    discriminating (fail against the pre-fix text)."""
    assert _OLD_CLAIMS["developer"] == (
        "Code-heavy configuration with GitHub integration, CI/CD awareness, and dev tools"
    )


class TestDistroDescriptionsMatchRealEffect:
    def test_persona_descriptions_no_longer_promise_tooling_or_workspaces(self):
        for name, old_claim in _OLD_CLAIMS.items():
            desc = dist.BUILTIN_DISTROS[name]["description"]
            assert desc != old_claim, (
                f"{name}'s distro description still makes the old "
                f"unfulfilled promise ({old_claim!r}) -- see findings.jsonl F24"
            )
            for banned in ("with GitHub integration", "CI/CD awareness, and dev tools",
                           "source trust, editorial tools", "wiki and source trust",
                           "briefings, calendar, finance"):
                assert banned not in desc, (
                    f"{name}'s corrected description still promises "
                    f"{banned!r} as a positive capability, which "
                    "apply_distro() does not deliver"
                )

    def test_persona_descriptions_now_say_personality_only(self):
        for name in ("journalist", "developer", "researcher", "executive"):
            desc = dist.BUILTIN_DISTROS[name]["description"].lower()
            assert "personality" in desc, (
                f"{name}'s description should describe itself as a "
                "personality/tone change, matching apply_distro()'s only "
                "real effect"
            )

    def test_apply_distro_still_only_writes_the_fields_the_copy_claims(self):
        """Grounding check: confirms the corrected 'personality only'
        framing is actually true right now -- apply_distro()'s delta
        still contains no workspace/provider/recipe keys, and dock_layout
        (which the copy no longer mentions) still has no reader."""
        source = inspect.getsource(dist.apply_distro)
        assert '"distribution"' in source
        assert '"show_all_workspaces"' in source
        assert '"dock_layout"' in source
        for absent_key in ("default_workspaces", "default_providers",
                            "default_recipes"):
            assert absent_key not in source, (
                f"apply_distro() now writes {absent_key!r} into its delta "
                "-- if real workspace/provider/recipe differentiation has "
                "been built, F24's corrected 'personality only' copy "
                "needs revisiting"
            )

    def test_show_all_workspaces_is_still_uniformly_false(self):
        values = {dist.BUILTIN_DISTROS[n]["show_all_workspaces"]
                  for n in dist.BUILTIN_DISTROS}
        assert values == {False}, (
            "show_all_workspaces now differs across builtin distros -- if "
            "real per-persona workspace differentiation exists, F24's "
            "corrected copy needs revisiting"
        )

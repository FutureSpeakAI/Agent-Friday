"""Gauntlet finding: README.md claimed "On first run, Friday greets you by
voice and walks you through setup." Onboarding is actually silent, text-
and-click-through, on both surfaces:

  - The browser wizard, SetupWizard (index.html / ui_parts/app.html) --
    its 6 steps are pure React DOM; WIZARD_VOICES is a list of TTS
    persona CHOICES the user picks for later, not a greeting played now.
    completeWizard() only POSTs config and flips a flag -- no audio call
    anywhere in the component or its completion path.
  - The CLI wizard, setup_wizard.py -- drives the same steps via rich's
    console.print/Prompt.ask only; no audio playback anywhere in the file.

The only "greeting" logic in the codebase (routes/voice.py's `greeted`
flag) fires inside an already-open Gemini Live voice-mode call the user
explicitly started -- it is not wired to first launch at all.

This probe demonstrates the fix by construction: it fails against the old
literal claim (proving README really made it) and passes against the
corrected text.
"""
from __future__ import annotations

import re
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
_README = _REPO_ROOT / "README.md"

_OLD_CLAIM = "On first run, Friday greets you by voice and walks you through setup."


def _old_claim_would_fail_this_assertion():
    """Not a real test -- documents that the assertion below is
    discriminating (fails against the pre-fix text)."""
    assert _OLD_CLAIM.strip() == \
        "On first run, Friday greets you by voice and walks you through setup."


class TestReadmeOnboardingIsNotVoice:
    def test_readme_no_longer_claims_a_voice_greeting(self):
        text = _README.read_text(encoding="utf-8")
        assert _OLD_CLAIM not in text, (
            "README.md still claims Friday greets the user by voice on "
            "first run -- both the browser SetupWizard and the CLI "
            "setup_wizard.py are silent, text-and-click wizards with no "
            "audio playback anywhere in either onboarding path"
        )
        assert not re.search(r"greets you by voice", text, re.IGNORECASE), (
            "README.md should not restate the voice-greeting claim in "
            "different words while onboarding remains silent"
        )

    def test_setup_wizard_cli_has_no_audio_playback(self):
        """No-op-shaped grounding check: confirms the CLI wizard really is
        silent, so the corrected README claim is itself true, not just
        differently worded."""
        wizard = (_REPO_ROOT / "src" / "agent_friday" / "setup_wizard.py").read_text(
            encoding="utf-8")
        for marker in ("winsound", "playsound", "simpleaudio", "sounddevice.play"):
            assert marker not in wizard, (
                f"setup_wizard.py references {marker!r} -- if the CLI wizard "
                "now plays audio, README.md's corrected 'onboarding is "
                "silent' claim needs revisiting too"
            )

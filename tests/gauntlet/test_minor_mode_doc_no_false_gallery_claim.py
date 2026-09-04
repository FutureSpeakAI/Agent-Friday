"""Gauntlet finding F29 (HIGH PRIORITY -- child safety): DEFAULT_SETTINGS'
own documentation comment for minor_mode (core/__init__.py) said: "When on,
generation runs an age-appropriate filter ON TOP of the adult harm floor,
and adult content is hidden in the gallery. This filters what the minor
sees, not what exists."

The generation-time half is TRUE: creative_engine._minor_mode_active()/
check_content_safety(), creative_policy.describe(), and music_engine's
check_minor_appropriate path all re-read settings live on every generation
call. The gallery-hiding half is FALSE: grepping the whole repo for any
gallery/creations-list filtering keyed on minor_mode/is_adult/adult_content/
nsfw/rating turns up zero matches -- no creation record carries an
adult/rating field at all, and both HTML files' creations gallery filters
only by file type/filename. Any adult-rated content generated before
minor_mode was turned on (or by an adult user sharing the install) stays
fully visible to a minor in the gallery.

Final disposition (Stephen, audit owner, 2026-09-04): gallery-side hiding is
NOT being built right now -- this is a copy fix only, framed as "coming
soon," not a factual "doesn't exist" correction and not a build. The
generation-time filtering claim (real, working) stays as-is. This probe pins:
  - core/__init__.py's minor_mode doc comment now frames the gallery-hiding
    gap as "coming soon" (not yet implemented) rather than either the old
    false "already hidden" claim or a flat "does not exist" statement.
  - a small "coming soon" note was added next to the Family-mode gallery
    banner in both index.html and ui_parts/app.html, the natural spot where
    a user would expect hiding to be visible if it existed.
  - no actual gallery filtering logic was built (grounding check: the
    gallery's own filter predicate still only consults file type/filename).

Red -> green -> red-on-revert proof: fails against the old literal claim
(proving the settings file really made it) and passes against the
corrected, "coming soon"-framed comment and UI note.
"""
from __future__ import annotations

import re
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
_CORE_INIT = _REPO_ROOT / "src" / "agent_friday" / "core" / "__init__.py"
_INDEX_HTML = _REPO_ROOT / "index.html"
_APP_HTML = _REPO_ROOT / "ui_parts" / "app.html"

_OLD_CLAIM = "adult content is hidden in the gallery"


def _old_claim_would_fail_this_assertion():
    """Not a real test -- documents that the assertion below is
    discriminating (fails against the pre-fix text)."""
    assert _OLD_CLAIM.strip() == "adult content is hidden in the gallery"


class TestMinorModeDocNoFalseGalleryClaim:
    def test_core_init_no_longer_claims_gallery_hiding(self):
        text = _CORE_INIT.read_text(encoding="utf-8")
        assert _OLD_CLAIM not in text, (
            "core/__init__.py's minor_mode doc comment still claims adult "
            "content is hidden in the gallery -- no creation record has an "
            "adult/rating field and the gallery filters only by file "
            "type/filename; see findings.jsonl F29"
        )
        assert not re.search(r"hidden in the gallery", text, re.IGNORECASE)

    def test_core_init_frames_gallery_hiding_as_coming_soon(self):
        """Stephen's final ruling: this is a 'coming soon' copy fix, not a
        build and not a flat 'doesn't exist' statement."""
        text = _CORE_INIT.read_text(encoding="utf-8")
        idx = text.index('"minor_mode": False')
        block = text[max(0, idx - 800):idx]
        assert "coming soon" in block.lower(), (
            "the corrected minor_mode comment should frame gallery-side "
            "adult-content hiding as 'coming soon', per Stephen's explicit "
            "ruling that this is a copy fix, not a build -- see "
            "findings.jsonl F29"
        )
        assert "not yet implemented" in block.lower() or \
            "not implemented" in block.lower(), (
                "the corrected comment should also plainly say gallery "
                "hiding is not yet implemented today"
            )
        assert "going forward" in block.lower(), (
            "the corrected comment should still describe the (real) "
            "generation-time filter as affecting new generation"
        )

    def test_family_mode_banner_carries_coming_soon_note_in_both_files(self):
        """The natural UI spot for this note: the Family-mode banner shown
        directly above/within the creations gallery, where a user would
        expect gallery-hiding to be visible if it existed."""
        for path in (_INDEX_HTML, _APP_HTML):
            text = path.read_text(encoding="utf-8")
            assert "Family mode" in text
            idx = text.index("Family mode")
            window = text[idx:idx + 400]
            assert "coming soon" in window.lower(), (
                f"{path.name}'s Family-mode gallery banner should carry a "
                "'coming soon' note about gallery-side adult-content "
                "hiding not being implemented yet -- see findings.jsonl F29"
            )

    def test_no_gallery_hiding_claim_echoed_elsewhere_in_ui_copy(self):
        """Grounding check confirming the OLD false absolute claim isn't
        echoed anywhere else in either HTML file."""
        for path in (_INDEX_HTML, _APP_HTML):
            text = path.read_text(encoding="utf-8")
            for marker in ("adult content is hidden", "hidden in the gallery"):
                assert marker not in text, (
                    f"{path.name} echoes the false gallery-hiding claim: "
                    f"{marker!r}"
                )

    def test_gallery_list_still_filters_only_by_type_and_filename(self):
        """Grounding check: confirms no gallery filtering logic was
        actually built -- per Stephen's explicit instruction not to build
        one, this remains a copy-only fix. The creations gallery's own
        filter predicate still consults only file type/name, never a
        minor_mode/is_adult/adult_content/nsfw/rating dimension. Targets
        the specific filter call (not a repo-wide keyword grep, which
        false-positives on unrelated generation-time nsfw job statuses in
        creative_store.py/moderation.py that are not a gallery-record
        rating field)."""
        text = _INDEX_HTML.read_text(encoding="utf-8")
        marker = "_sorted = [...creations].filter("
        assert marker in text, (
            "the creations gallery filter call this check targets has "
            "moved or been renamed -- re-locate it before trusting this "
            "grounding check"
        )
        idx = text.index(marker)
        # The filter predicate itself, up to its closing paren.
        predicate = text[idx:idx + 300]
        for marker_term in ("minor_mode", "is_adult", "adult_content",
                             "nsfw", "rating"):
            assert marker_term not in predicate.lower(), (
                f"the creations gallery filter now references {marker_term!r} "
                "-- Stephen was explicit that a gallery filter is NOT being "
                "built right now; if this changed, F29's disposition needs "
                "revisiting"
            )

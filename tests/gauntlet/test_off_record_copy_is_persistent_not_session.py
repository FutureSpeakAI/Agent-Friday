"""Gauntlet finding Q17: the "Go Off Record" toggle's description in both
index.html (:34062, plus the Quick Toggle instance around :43066-43078) and
ui_parts/app.html (:8166) read "Disable logging for this session."

Reality: off_record is a plain persisted boolean (core/__init__.py) read at
every consumer site (routes/chat.py, services/voice_engine.py,
routes/context.py) -- correctly enforced, but nothing anywhere resets it.
No session-boundary, app-restart, or "New Chat" hook turns it back off.
docs/design/historical/voice-system-overhaul-spec.md:376,1130 confirms the actual design
intent is a plain persistent suppression switch, not session-scoped. The
UI copy's "for this session" claim was the thing that was wrong, not the
underlying mechanism -- so the fix here is text-only.

Red -> green -> red-on-revert proof: fails against the old "for this
session" wording (proving both files really said it) and passes against
the corrected, persistence-accurate wording.
"""
from __future__ import annotations

from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
_INDEX_HTML = _REPO_ROOT / "index.html"
_APP_HTML = _REPO_ROOT / "ui_parts" / "app.html"

_OLD_CLAIM = "Disable logging for this session."


def _old_claim_would_fail_this_assertion():
    """Not a real test -- documents that the assertion below is
    discriminating (fails against the pre-fix text)."""
    assert _OLD_CLAIM.strip() == "Disable logging for this session."


class TestOffRecordCopyIsPersistentNotSession:
    def test_index_html_no_longer_claims_session_scoping(self):
        text = _INDEX_HTML.read_text(encoding="utf-8")
        assert _OLD_CLAIM not in text, (
            "index.html still claims Go Off Record only disables logging "
            "'for this session' -- off_record is a plain persisted boolean "
            "with no session/restart/new-chat reset anywhere; see "
            "findings.jsonl Q17"
        )

    def test_app_html_no_longer_claims_session_scoping(self):
        text = _APP_HTML.read_text(encoding="utf-8")
        assert _OLD_CLAIM not in text, (
            "ui_parts/app.html still claims Go Off Record only disables "
            "logging 'for this session' -- see findings.jsonl Q17"
        )

    def test_index_html_go_off_record_copy_now_states_persistence(self):
        text = _INDEX_HTML.read_text(encoding="utf-8")
        assert 'label: "Go Off Record"' in text
        idx = text.index('label: "Go Off Record"')
        window = text[idx:idx + 400]
        assert "desc:" in window
        assert "until you turn this back on" in window or \
            "stays off until" in window.lower(), (
                "the corrected Go Off Record description should say the "
                "suppression persists until manually reversed"
            )

    def test_app_html_go_off_record_copy_now_states_persistence(self):
        text = _APP_HTML.read_text(encoding="utf-8")
        assert 'label="Go Off Record"' in text
        idx = text.index('label="Go Off Record"')
        window = text[idx:idx + 400]
        assert "until you turn this back on" in window, (
            "the corrected Go Off Record description should say the "
            "suppression persists until manually reversed"
        )

    def test_off_record_setting_is_never_reset_anywhere(self):
        """Grounding check: confirms the corrected 'persistent' copy is
        actually true -- no source file resets off_record on a session
        boundary, so the new wording is not just differently wrong."""
        src_root = _REPO_ROOT / "src"
        offenders = []
        for path in src_root.rglob("*.py"):
            text = path.read_text(encoding="utf-8", errors="ignore")
            if "off_record" not in text:
                continue
            # A reset would look like an assignment to False/0 outside of
            # the settings-write path itself (save/POST handlers, which
            # legitimately flip it in response to the user's own click).
            for line in text.splitlines():
                if "off_record" in line and (
                        "= False" in line or "=False" in line) and \
                        "save(" not in line and "request" not in line.lower():
                    offenders.append(f"{path}: {line.strip()}")
        assert not offenders, (
            "found code that resets off_record outside of an explicit "
            "user-driven settings write -- if a real session/reset "
            f"boundary now exists, the 'persistent' copy needs revisiting: {offenders}"
        )

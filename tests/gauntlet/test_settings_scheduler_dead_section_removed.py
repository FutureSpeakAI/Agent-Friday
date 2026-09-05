"""Gauntlet finding Q25: Settings' "Scheduler" section (index.html and
ui_parts/app.html, gated behind `openSections.scheduler`) was a fully dead,
phantom UI section. Its state (schedules, schedBuiltins, schedHistory,
schedMsg, schedForm), its seven handlers (toggleSchedule, runScheduleNow,
deleteSchedule, loadHistory, saveNewSchedule, fmtTrigger, schedStatusColor)
plus the unused fmtTime formatter, and its 5-second `/api/schedules` poll
were fully implemented but referenced in ZERO JSX anywhere in either file --
confirmed by grep: every one of those names appeared exactly once (its own
definition) before this fix, and `openSections.scheduler` was set nowhere
except its own initial `false` and the dead useEffect's own dependency
array -- no `toggleSection('scheduler')` call ever existed to open it. The
real, working scheduler UI is a separate component in the Workflows tab,
using differently-named handlers (runSched/toggleSched/deleteSched) that
IS correctly wired to visible JSX.

This is pure dead-code removal -- the working equivalent already exists
elsewhere, so wiring up a second competing scheduler UI would be worse, not
better.

Red -> green -> red-on-revert proof: fails while the dead block (and its
`openSections.scheduler` key) is present, passes once it is removed.
"""
from __future__ import annotations

from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
_INDEX_HTML = _REPO_ROOT / "index.html"
_APP_HTML = _REPO_ROOT / "ui_parts" / "app.html"

_DEAD_MARKERS = (
    "schedBuiltins",
    "schedHistory",
    "schedMsg",
    "schedForm",
    "toggleSchedule",
    "runScheduleNow",
    "deleteSchedule(",
    "loadHistory",
    "saveNewSchedule",
    "fmtTrigger",
    "schedStatusColor",
    "openSections.scheduler",
)


class TestSettingsSchedulerDeadSectionRemoved:
    def test_index_html_has_no_dead_scheduler_markers(self):
        text = _INDEX_HTML.read_text(encoding="utf-8")
        found = [m for m in _DEAD_MARKERS if m in text]
        assert not found, (
            f"index.html still contains dead Scheduler-section markers: {found} "
            "-- see findings.jsonl Q25"
        )

    def test_app_html_has_no_dead_scheduler_markers(self):
        text = _APP_HTML.read_text(encoding="utf-8")
        found = [m for m in _DEAD_MARKERS if m in text]
        assert not found, (
            f"ui_parts/app.html still contains dead Scheduler-section "
            f"markers: {found} -- see findings.jsonl Q25"
        )

    def test_scheduler_key_removed_from_opensections_initial_state_both_files(self):
        for path in (_INDEX_HTML, _APP_HTML):
            text = path.read_text(encoding="utf-8")
            assert "scheduler: false" not in text, (
                f"{path.name} still declares a 'scheduler' key in "
                "openSections' initial state"
            )
            assert "scheduler:false" not in text

    def test_active_hooks_section_survives_in_both_files(self):
        """Grounding check: confirms the removal was scoped to the dead
        Scheduler block only -- the real, wired Active Hooks section
        (which shared the same umbrella comment) is still intact."""
        for path in (_INDEX_HTML, _APP_HTML):
            text = path.read_text(encoding="utf-8")
            assert "Active Hooks (Part B)" in text
            assert "refreshHooks" in text
            assert "toggleHook" in text
            assert "openSections.hooks" in text

    def test_workflows_tab_real_scheduler_handlers_survive(self):
        """Grounding check: confirms the genuinely-wired scheduler UI in
        the Workflows tab (differently-named handlers) was not touched by
        this removal."""
        text = _INDEX_HTML.read_text(encoding="utf-8")
        for marker in ("runSched", "toggleSched", "deleteSched"):
            assert marker in text, (
                f"{marker!r} (the real, wired Workflows-tab scheduler "
                "handler) is missing -- this fix should only have removed "
                "the dead Settings-tab duplicate, not the working one"
            )

"""PR-2 (OS mode switch, OS-mode sequence) — behaviors 3 and 5: computer-
control tools and the clipboard tool must be reported unavailable under
FRIDAY_OS_MODE, with the reason string "no desktop to control in kiosk mode"
(computer control) reachable through the module's normal reporting surface.

capability_preflight.py already existed (pdf_text/os_control/pii_ner/
embeddings) before this PR; these tests pin the NEW os_mode_reason gate added
to the `os_control` capability and the NEW `clipboard` capability, and — just
as importantly — that neither one changes anything when FRIDAY_OS_MODE is
unset, regardless of whether pyautogui happens to be installed on the machine
running the suite.
"""
from __future__ import annotations

import pytest

from agent_friday.services import capability_preflight as cp


@pytest.fixture(autouse=True)
def _os_mode_unset_by_default(monkeypatch):
    """Every test starts from a clean, explicit FRIDAY_OS_MODE state so none
    of them depend on whatever the invoking shell happened to export."""
    monkeypatch.delenv("FRIDAY_OS_MODE", raising=False)
    yield


def _cap(key: str) -> cp.Capability:
    match = [c for c in cp.CAPABILITIES if c.key == key]
    assert match, f"no declared capability {key!r}"
    return match[0]


# ── os_control ───────────────────────────────────────────────────────────

class TestOsControl:
    def test_present_reflects_only_the_import_when_os_mode_off(self, monkeypatch):
        """Windows-default behavior must be UNCHANGED: os_control.present is
        purely a function of whether pyautogui imports, exactly as before
        this PR — verified by monkeypatching the import check itself so this
        assertion holds regardless of whether pyautogui is actually
        installed on the machine running the suite."""
        monkeypatch.delenv("FRIDAY_OS_MODE", raising=False)
        cap = _cap("os_control")
        monkeypatch.setattr(
            "agent_friday.services.capability_preflight.importlib.util.find_spec",
            lambda name: object() if name == "pyautogui" else None,
        )
        assert cap.present is True

        monkeypatch.setattr(
            "agent_friday.services.capability_preflight.importlib.util.find_spec",
            lambda name: None,
        )
        assert cap.present is False

    def test_absent_under_os_mode_even_when_pyautogui_importable(self, monkeypatch):
        """The whole point of this gate: a kiosk machine may genuinely have
        pyautogui installed and it must STILL report absent, because the
        real problem is "no desktop", not "no library"."""
        monkeypatch.setattr(
            "agent_friday.services.capability_preflight.importlib.util.find_spec",
            lambda name: object(),  # pretend every module imports fine
        )
        cap = _cap("os_control")
        assert cap.present is True  # sanity: present when OS mode is off

        monkeypatch.setenv("FRIDAY_OS_MODE", "1")
        assert cap.present is False
        assert cap.unavailable_reason == "no desktop to control in kiosk mode"

    def test_reason_string_reachable_via_status_and_report(self, monkeypatch):
        monkeypatch.setattr(
            "agent_friday.services.capability_preflight.importlib.util.find_spec",
            lambda name: object(),
        )
        monkeypatch.setenv("FRIDAY_OS_MODE", "1")

        snap = cp.status()
        os_control_entry = next(c for c in snap["capabilities"] if c["key"] == "os_control")
        assert os_control_entry["breaks_when_absent"] == "no desktop to control in kiosk mode"
        assert os_control_entry["os_mode_gated"] is True
        assert "os_control" in snap["missing_required"]

        report_lines = cp.report()
        assert any("no desktop to control in kiosk mode" in line for line in report_lines)

    def test_computer_control_tools_withheld_under_os_mode(self, monkeypatch):
        monkeypatch.setattr(
            "agent_friday.services.capability_preflight.importlib.util.find_spec",
            lambda name: object(),
        )
        monkeypatch.setenv("FRIDAY_OS_MODE", "1")
        withheld = cp.missing_tools()
        for tool in ("screenshot", "move_mouse", "click", "type_text",
                     "press_key", "scroll"):
            assert tool in withheld

    def test_computer_control_tools_not_withheld_when_os_mode_off_and_present(self, monkeypatch):
        """Regression guard: with OS mode off and pyautogui importable, the
        ring-3 tools are NOT withheld — matching pre-PR behavior exactly."""
        monkeypatch.setattr(
            "agent_friday.services.capability_preflight.importlib.util.find_spec",
            lambda name: object(),
        )
        withheld = cp.missing_tools()
        for tool in ("screenshot", "move_mouse", "click", "type_text",
                     "press_key", "scroll"):
            assert tool not in withheld


# ── clipboard ────────────────────────────────────────────────────────────

class TestClipboard:
    def test_present_by_default_regardless_of_os_mode_off(self):
        """No import dependency at all (module=None) — clipboard is always
        present when OS mode is off, matching the fact that write_clipboard
        never checked any capability before this PR."""
        cap = _cap("clipboard")
        assert cap.present is True

    def test_absent_under_os_mode(self, monkeypatch):
        monkeypatch.setenv("FRIDAY_OS_MODE", "1")
        cap = _cap("clipboard")
        assert cap.present is False
        assert cap.unavailable_reason == "no clipboard to control in kiosk mode"

    def test_write_clipboard_withheld_under_os_mode(self, monkeypatch):
        monkeypatch.setenv("FRIDAY_OS_MODE", "1")
        assert "write_clipboard" in cp.missing_tools()

    def test_write_clipboard_not_withheld_when_os_mode_off(self):
        """Regression guard: write_clipboard was never gated by this module
        before this PR — with OS mode off it must not be withheld."""
        assert "write_clipboard" not in cp.missing_tools()


# ── explain() ────────────────────────────────────────────────────────────

def test_explain_distinguishes_os_mode_from_missing_dependency(monkeypatch):
    monkeypatch.setenv("FRIDAY_OS_MODE", "1")
    msg = cp.explain("clipboard")
    assert "no clipboard to control in kiosk mode" in msg
    assert "isn't installed" not in msg  # must not blame a missing package


def test_explain_empty_when_present(monkeypatch):
    monkeypatch.delenv("FRIDAY_OS_MODE", raising=False)
    assert cp.explain("clipboard") == ""

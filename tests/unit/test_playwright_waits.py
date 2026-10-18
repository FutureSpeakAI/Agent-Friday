"""The Playwright suites wait for the page, not the clock (tests/app/helpers/settle.ts).

A fixed pause is too long on a fast machine and too short on a slow one. Every pause is now a wait for a
condition (an expect, a response) or settle() (no request in flight and no DOM change for N ms, capped);
a clock wait is left only where time is the behaviour under test, and says so on its line. Connect your
services loads the app once for its Settings tests. The "can a person see it" vision check is opt-in and
local only.
"""
import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parents[2]
TESTS = ROOT / "tests"
SPECS = [p for p in TESTS.rglob("*.ts") if "node_modules" not in p.parts]


def test_every_remaining_clock_wait_is_time_under_test():
    left = []
    for p in SPECS:
        for n, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1):
            if "waitForTimeout(" in line and "time is the behaviour under test" not in line:
                left.append("%s:%d: %s" % (p.relative_to(ROOT), n, line.strip()))
    assert left == [], "fixed pauses left:\n" + "\n".join(left)


def test_settle_waits_for_quiet_with_a_cap_and_sends_nothing():
    src = (TESTS / "app" / "helpers" / "settle.ts").read_text(encoding="utf-8")
    assert "export async function settle(" in src and "timeout" in src and "quietMs" in src
    assert "fetch(" not in src and "XMLHttpRequest" not in src     # it watches; it never calls out
    assert "MutationObserver" in src and "requestfinished" in src


def test_connect_services_boots_once_and_reads_its_own_status():
    src = (TESTS / "app" / "specs" / "connect_services.spec.ts").read_text(encoding="utf-8")
    assert "test.beforeAll(" in src and "browser.newContext(" in src
    assert src.count("page.goto(") == 2          # the shared Settings page, and the setup test's own page
    assert "getByTestId('connect-services-said')" in src
    assert "getByRole('status').first()" not in src
    for page in ("index.html", "ui_parts/app.html"):
        assert "connect-services-said" in (ROOT / page).read_text(encoding="utf-8"), page


def test_opening_a_settings_tab_survives_an_event_sent_before_settings_listens():
    """An event sent before Settings mounted its listener was lost and the test waited out its budget
    (laya_settings.spec.ts on CI). The specs that open a tab right after load send it until the tab shows."""
    helper = (TESTS / "app" / "helpers" / "settings_tab.ts").read_text(encoding="utf-8")
    assert "toPass(" in helper and "friday:settings-tab" in helper
    for name in ("connect_services.spec.ts", "laya_settings.spec.ts", "settings_highlight.spec.ts"):
        assert "showSettingsTab(page, " in (TESTS / "app" / "specs" / name).read_text(encoding="utf-8"), name


def test_the_visibility_check_is_opt_in_local_and_used_beside_the_dom_checks():
    src = (TESTS / "app" / "vision.ts").read_text(encoding="utf-8")
    assert "process.env.FRIDAY_VISION_CHECK !== '1'" in src
    assert re.search(r"LOOPBACK\s*=", src) and "if (!LOOPBACK.test(OLLAMA)) return null" in src
    assert "export async function expectVisible(" in src
    spec = (TESTS / "app" / "specs" / "activity_never_collapses.spec.ts").read_text(encoding="utf-8")
    assert "toHaveAttribute('data-overflow', 'false')" in spec and "await expectVisible(" in spec

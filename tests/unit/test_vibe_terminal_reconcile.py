"""A boot-time reconcile must not force-kill work it merely cannot account for.

`adopt_or_reap_vibe_terminals` kills any live `Friday-Vibe-<id>` console it has
no persisted record of. Each of those consoles is running
`claude --dangerously-skip-permissions`, so a wrong kill destroys unfinished
work with no undo.

The inference "no record, therefore orphan" only holds once every terminal is
recorded AT LAUNCH. On the first boot after this feature lands, nothing has
ever been recorded, so a correct, in-use terminal is indistinguishable from an
orphan and all of them are killed. These tests pin the grace path that closes
that window, and -- just as importantly -- pin that grace ENDS, so the feature
still does its job on every later boot.
"""
import json

import pytest

from agent_friday import core
from agent_friday.services import code_engine


@pytest.fixture
def vibe_state(tmp_path, monkeypatch):
    """Point the state file at a temp dir and hand back a small controller.

    Never touches the real ~/.friday/vibe-code/terminals.json.
    """
    path = tmp_path / "terminals.json"
    monkeypatch.setattr(core, "VIBE_STATE_FILE", path)
    monkeypatch.setattr(core, "VIBE_TERMINALS", {}, raising=False)
    monkeypatch.setattr(code_engine, "VIBE_TERMINALS", core.VIBE_TERMINALS,
                        raising=False)
    return path


@pytest.fixture
def killed(monkeypatch):
    """Record every taskkill instead of running one."""
    seen = []

    def _fake_run(cmd, *a, **k):
        seen.append(list(cmd))
        return None

    monkeypatch.setattr(code_engine.subprocess, "run", _fake_run)
    return seen


def _live(monkeypatch, mapping):
    monkeypatch.setattr(code_engine, "_vibe_terminal_processes",
                        lambda: dict(mapping))


def test_first_boot_adopts_live_terminals_instead_of_killing_them(
        vibe_state, killed, monkeypatch):
    """No state file at all: every live window survives.

    This is the upgrade-day case. The user is mid-session in two vibe
    terminals; Friday restarts into code that has never recorded anything.
    """
    _live(monkeypatch, {"aaaaaaaa-111": (4242, "cmd /k title Friday-Vibe-aaaaaaaa-111"),
                        "bbbbbbbb-222": (4243, "cmd /k title Friday-Vibe-bbbbbbbb-222")})

    report = code_engine.adopt_or_reap_vibe_terminals()

    assert killed == [], "a first boot must not kill a terminal it cannot account for"
    assert report["grace"] is True
    assert sorted(report["adopted"]) == ["aaaaaaaa-111", "bbbbbbbb-222"]
    assert report["reaped"] == []
    # Adopted means MANAGEABLE, not merely spared: the UI can only offer a stop
    # button for a terminal that is in the registry with its live pid.
    assert core.VIBE_TERMINALS["aaaaaaaa-111"]["pid"] == 4242
    assert core.VIBE_TERMINALS["aaaaaaaa-111"]["status"] == "running"
    assert core.VIBE_TERMINALS["bbbbbbbb-222"]["adopted_without_record"] is True


def test_the_empty_file_the_old_reconcile_left_behind_is_still_first_boot(
        vibe_state, killed, monkeypatch):
    """The pre-fix code wrote `{"terminals": {}}` on its own first run.

    Any machine that has booted the unfixed build once already has that file.
    A grace check keyed on the file EXISTING would find it, conclude the
    record is authoritative, and reap on precisely the boot this protects.
    Grace is keyed on the version marker for that reason.
    """
    vibe_state.write_text(json.dumps({"terminals": {}}), encoding="utf-8")
    _live(monkeypatch, {"cccccccc-333": (777, "cmd /k title Friday-Vibe-cccccccc-333")})

    report = code_engine.adopt_or_reap_vibe_terminals()

    assert killed == [], "an unversioned file cannot testify that a terminal is an orphan"
    assert report["grace"] is True
    assert report["adopted"] == ["cccccccc-333"]


def test_grace_ends_once_the_versioned_file_is_written(
        vibe_state, killed, monkeypatch):
    """Grace is a one-time amnesty, not a permanent disarming.

    After the first reconcile the file names a version, so a later boot is
    entitled to treat an absent record as a genuine orphan and reap it.
    """
    _live(monkeypatch, {})
    code_engine.adopt_or_reap_vibe_terminals()          # first boot: writes version

    written = json.loads(vibe_state.read_text(encoding="utf-8"))
    assert written["version"] == core.VIBE_STATE_VERSION, (
        "the reconcile must stamp the version even when it found nothing, "
        "or every boot is a first boot and reaping never arms")

    _live(monkeypatch, {"dddddddd-444": (999, "cmd /k title Friday-Vibe-dddddddd-444")})
    report = code_engine.adopt_or_reap_vibe_terminals()

    assert report["grace"] is False
    assert report["reaped"] == [999]
    assert killed == [["taskkill", "/F", "/PID", "999"]]


def test_a_recorded_terminal_is_adopted_not_reaped_after_grace(
        vibe_state, killed, monkeypatch):
    """The ordinary post-grace restart: a tracked terminal keeps running."""
    vibe_state.write_text(json.dumps({
        "version": core.VIBE_STATE_VERSION,
        "terminals": {"eeeeeeee-555": {"task": "refactor the parser",
                                       "cwd": "C:/Users/x/Projects/p",
                                       "status": "running", "pid": 1}},
    }), encoding="utf-8")
    _live(monkeypatch, {"eeeeeeee-555": (31337, "cmd /k title Friday-Vibe-eeeeeeee-555")})

    report = code_engine.adopt_or_reap_vibe_terminals()

    assert killed == []
    assert report["adopted"] == ["eeeeeeee-555"]
    # The pid is refreshed to the one actually observed alive; the task text
    # survives so the row still says what the window is doing.
    assert core.VIBE_TERMINALS["eeeeeeee-555"]["pid"] == 31337
    assert core.VIBE_TERMINALS["eeeeeeee-555"]["task"] == "refactor the parser"
    assert "adopted_without_record" not in core.VIBE_TERMINALS["eeeeeeee-555"]


def test_a_failed_survey_kills_nothing(vibe_state, killed, monkeypatch):
    """If the WMI survey raises, the reconcile reports nothing and kills nothing.

    A survey that cannot see a window must never be read as "no windows".
    """
    def _boom():
        raise OSError("powershell unavailable")

    monkeypatch.setattr(code_engine, "_vibe_terminal_processes", _boom)

    report = code_engine.adopt_or_reap_vibe_terminals()

    assert killed == []
    assert report["adopted"] == [] and report["reaped"] == []


def test_a_corrupt_state_file_is_treated_as_first_boot(
        vibe_state, killed, monkeypatch):
    """Half-written JSON must fail toward sparing terminals, not killing them."""
    vibe_state.write_text('{"version": 1, "termin', encoding="utf-8")
    _live(monkeypatch, {"ffffffff-666": (555, "cmd /k title Friday-Vibe-ffffffff-666")})

    report = code_engine.adopt_or_reap_vibe_terminals()

    assert killed == []
    assert report["grace"] is True

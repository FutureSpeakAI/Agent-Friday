"""A record that cannot be decrypted is bad news once, not bad news forever.

Measured on the reference machine, 2026-09-22 15:10-18:30. A passphrase
rotation left 20-odd task-journal records unreadable (GCM auth tag mismatch).
Nothing crashed: `_decode_line` swallows the failure and the row is skipped,
which is the right behaviour. But `_unprotect` logs a warning first, and the
tray polls /api/tasks roughly every two seconds, and that poll calls
`liveness()` on every running task, which reads every record.

So the same permanently-unreadable records were re-reported about ten times a
second. friday.log hit its 10 MB rotation threshold, rotation failed with
PermissionError [WinError 32] because a second process holds the file, and the
failure was raised inside logging -- which prints "--- Logging error ---" plus
a full traceback to stderr for every suppressed line.

Net effect: friday.log frozen at 15:10, no application log at all for three
hours, and server_stderr.log growing at 5.6 GB/hour. Friday went blind, and
every request competed with a process writing 100 MB a minute.

The invariant: the volume of complaint is bounded by the number of distinct
broken records, not by how often anyone looks at them.
"""

import logging

import pytest

from agent_friday.services import task_journal as tj


class _Boom(Exception):
    pass


@pytest.fixture
def unreadable(monkeypatch):
    """Every blob looks protected and none of them can be unprotected."""
    import agent_friday.services.credential_store as cs
    monkeypatch.setattr(cs, "looks_protected", lambda b: True)

    def _fail(b):
        raise _Boom("GCM auth tag mismatch - tampered ciphertext or wrong key")

    monkeypatch.setattr(cs, "unprotect", _fail)
    tj._forget_unreadable()          # no leakage between tests
    return None


def _warnings(caplog):
    return [r for r in caplog.records
            if r.levelno >= logging.WARNING and "unprotect" in r.getMessage()]


def test_one_bad_record_read_many_times_is_reported_once(unreadable, caplog):
    """The poll loop reads the same record ten times a second. It must not
    produce ten log lines a second."""
    caplog.set_level(logging.WARNING)
    blob = b"a-single-broken-record"
    for _ in range(50):
        with pytest.raises(Exception):
            tj._unprotect(blob)
    assert len(_warnings(caplog)) == 1, (
        "a permanently unreadable record was re-reported on every read; "
        "that is the storm that froze friday.log")


def test_each_distinct_bad_record_still_gets_its_own_report(unreadable, caplog):
    """Deduplicating must not hide a NEW failure. Silence about a second
    broken record would be the opposite bug."""
    caplog.set_level(logging.WARNING)
    for blob in (b"broken-one", b"broken-two", b"broken-three"):
        for _ in range(10):
            with pytest.raises(Exception):
                tj._unprotect(blob)
    assert len(_warnings(caplog)) == 3, (
        "three distinct unreadable records must be reported three times")


def test_decoding_a_bad_line_still_skips_it(unreadable):
    """The dedup must not change what read() returns: a record that cannot be
    decrypted is still skipped, not surfaced as garbage."""
    import base64
    line = tj._LINE_PREFIX + base64.b64encode(b"broken").decode("ascii")
    assert tj._decode_line(line) is None


def test_reading_a_journal_of_bad_lines_is_quiet_after_the_first_pass(
        unreadable, caplog, tmp_path, monkeypatch):
    """The seam the tray actually hits: the same file read over and over."""
    import base64
    monkeypatch.setattr(tj, "task_dir", lambda task_id: tmp_path / task_id)
    d = tmp_path / "t1"
    d.mkdir(parents=True)
    lines = [tj._LINE_PREFIX + base64.b64encode(f"rec-{i}".encode()).decode("ascii")
             for i in range(20)]
    (d / "journal.jsonl").write_text("\n".join(lines), encoding="utf-8")

    tj.read("t1")                      # first pass: 20 distinct complaints
    caplog.set_level(logging.WARNING)
    caplog.clear()
    for _ in range(10):                # ten more polls
        tj.read("t1")
    assert _warnings(caplog) == [], (
        "re-reading records already known to be unreadable must be silent")

"""The grown-up window the PC issues and checks (DECISIONS-FOR-STEPHEN TV-P1).

The TV's own "grownup" header does not unlock the owner's memory by itself: a turn needs a live window
grant the PC issued after checking the PIN against its own verifier, bound to this TV.
"""
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec

from agent_friday.services import device_pairing
from agent_friday.services import tv_grownup_window as win
from agent_friday.services import tv_memory_access as tma
from agent_friday.tv_gateway import household

PIN = "4821"


def _device(kind="tv", member=household.FAMILY_ID):
    key = ec.generate_private_key(ec.SECP256R1())
    spki = key.public_key().public_bytes(serialization.Encoding.DER,
                                         serialization.PublicFormat.SubjectPublicKeyInfo)
    dev = device_pairing.add_device(spki, name="Living-room TV", rights=["chat_read"], kind=kind)
    if member:
        household.bind_device(dev["id"], member)
    return device_pairing.get_device(dev["id"])


@pytest.fixture(autouse=True)
def _clean(tmp_path, monkeypatch):
    monkeypatch.setattr(household, "_path", lambda: tmp_path / "household.json")
    monkeypatch.setattr(win, "_path", lambda: tmp_path / "tv_grownup_pin.json")
    rows = []
    from agent_friday.services import action_ledger
    monkeypatch.setattr(action_ledger, "append", lambda kind, rec: rows.append((kind, rec)))
    clock = {"t": 1_000_000.0}
    monkeypatch.setattr(win, "_now", lambda: clock["t"])
    device_pairing._path().unlink(missing_ok=True)
    device_pairing._reset_for_tests()
    household.ensure_default_members()
    win._reset_for_tests()
    yield {"rows": rows, "clock": clock}
    win._reset_for_tests()
    device_pairing._reset_for_tests()


# ── the verifier ────────────────────────────────────────────────────────────

def test_the_pin_is_kept_only_as_a_verifier(_clean):
    win.set_pin(PIN, by="pc_settings")
    raw = win._path().read_text(encoding="utf-8")
    assert PIN not in raw and win.has_pin()
    kind, rec = _clean["rows"][-1]
    assert kind == "tv_grownup_window" and rec["event"] == "pin_set" and rec["by"] == "pc_settings"
    assert PIN not in str(rec)


@pytest.mark.parametrize("bad", ["", "12", "abcd", "1" * 9, None])
def test_a_pin_must_be_4_to_8_digits(bad):
    with pytest.raises(ValueError):
        win.set_pin(bad)


def test_a_tampered_verifier_counts_as_no_pin():
    win.set_pin(PIN)
    p = win._path()
    p.write_text(p.read_text(encoding="utf-8").replace('"n"', '"N"'), encoding="utf-8")
    assert not win.has_pin()


# ── opening and checking ────────────────────────────────────────────────────

def test_the_right_pin_opens_a_window_bound_to_this_tv(_clean):
    win.set_pin(PIN)
    tv, other = _device(), _device()
    g = win.open_with_pin(tv, "sess-a", PIN)
    assert win.check(tv, g)
    assert not win.check(other, g), "another TV"
    assert not win.check(tv, g + "x")
    assert not win.check(tv, "")
    assert not win.check(None, g)
    assert _clean["rows"][-1][0] == "tv_grownup_window" and _clean["rows"][-1][1]["event"] == "opened"


def test_the_window_is_short():
    # Dispatch 2026-11-27 09:12: "a short expiry that ends when the TV goes idle".
    assert win.TTL_S <= 3600 and win.IDLE_S <= 900 and win.IDLE_S < win.TTL_S


def test_a_window_ends_when_the_tv_goes_idle(_clean):
    win.set_pin(PIN)
    tv = _device()
    g = win.open_with_pin(tv, "s", PIN)
    _clean["clock"]["t"] += win.IDLE_S - 1
    assert win.check(tv, g, touch=True)                   # a turn: the idle clock starts again
    _clean["clock"]["t"] += win.IDLE_S - 1
    assert win.check(tv, g)
    _clean["clock"]["t"] += 2                              # no turn for IDLE_S
    assert not win.check(tv, g, touch=True)
    assert _clean["rows"][-1][1]["event"] == "ended" and _clean["rows"][-1][1]["by"] == "idle"


def test_looking_at_a_window_does_not_keep_it_open(_clean):
    win.set_pin(PIN)
    tv = _device()
    g = win.open_with_pin(tv, "s", PIN)
    for _ in range(3):
        _clean["clock"]["t"] += win.IDLE_S // 2
        win.status(tv, g)
    assert not win.check(tv, g)


def test_a_window_ends_at_its_cap_however_busy_the_tv_is(_clean):
    win.set_pin(PIN)
    tv = _device()
    g = win.open_with_pin(tv, "s", PIN)
    while _clean["clock"]["t"] + win.IDLE_S // 2 < 1_000_000.0 + win.TTL_S:
        _clean["clock"]["t"] += win.IDLE_S // 2
        assert win.check(tv, g, touch=True)
    _clean["clock"]["t"] = 1_000_000.0 + win.TTL_S + 1
    assert not win.check(tv, g, touch=True)


def test_close_ends_every_window_of_that_tv(_clean):
    win.set_pin(PIN)
    tv = _device()
    g = win.open_with_pin(tv, "s", PIN)
    win.close(tv)
    assert not win.check(tv, g)
    assert _clean["rows"][-1][1]["event"] == "closed"


def test_a_wrong_pin_is_refused_and_repeated_misses_lock_it(_clean):
    win.set_pin(PIN)
    tv = _device()
    for _ in range(win.FREE_TRIES):
        with pytest.raises(win.WindowRefused) as e:
            win.open_with_pin(tv, "s", "0000")
        assert e.value.status == 403
    with pytest.raises(win.WindowRefused) as e:
        win.open_with_pin(tv, "s", PIN)                      # right PIN, but locked out now
    assert e.value.status == 429
    _clean["clock"]["t"] += win.LOCK_BASE_S + 1
    assert win.check(tv, win.open_with_pin(tv, "s", PIN))


def test_the_lock_out_counts_every_tv_in_the_household(_clean):
    win.set_pin(PIN)
    a, b = _device(), _device()
    for i in range(win.FREE_TRIES):
        with pytest.raises(win.WindowRefused):
            win.open_with_pin(a if i % 2 else b, "s", "0000")
    with pytest.raises(win.WindowRefused) as e:
        win.open_with_pin(a, "s", PIN)
    assert e.value.status == 429


@pytest.mark.parametrize("kind,member", [("phone", household.OWNER_ID), ("tv", None), ("tv", household.OWNER_ID)])
def test_only_the_household_tv_may_open_one(kind, member):
    win.set_pin(PIN)
    with pytest.raises(win.WindowRefused):
        win.open_with_pin(_device(kind, member), "s", PIN)


def test_no_pin_set_no_window():
    with pytest.raises(win.WindowRefused) as e:
        win.open_with_pin(_device(), "s", PIN)
    assert e.value.code == "no_pin"


# ── the memory gate needs it ────────────────────────────────────────────────

def test_the_grown_up_header_alone_no_longer_unlocks_memory():
    win.set_pin(PIN)
    tv = _device()
    assert tma.allows(tv, "grownup", {}, window_ok=False) == (False, "no grown-up window from this computer")
    assert tma.allows(tv, "grownup", {}, window_ok=True) == (True, "all")
    assert tma.allows(tv, "kids", {}, window_ok=True)[0] is False
    assert tma.allows(tv, "family", {}, window_ok=True)[0] is False


def test_wrong_pins_sent_at_once_cannot_outrun_the_lock_out(monkeypatch):
    # Every try is counted before its PIN is checked, so a burst of tries in parallel gets FREE_TRIES checks,
    # not one per worker.
    import threading
    import time as _time
    win.set_pin(PIN)
    tv = _device()
    checked = []
    real = win._matches

    def slow(pin):
        checked.append(pin)
        _time.sleep(0.05)
        return real(pin)
    monkeypatch.setattr(win, "_matches", slow)
    codes, go = [], threading.Event()

    def guess():
        go.wait()
        try:
            win.open_with_pin(tv, "s", "0000")
        except win.WindowRefused as e:
            codes.append(e.status)
    workers = [threading.Thread(target=guess) for _ in range(40)]
    for w in workers:
        w.start()
    go.set()
    for w in workers:
        w.join()
    assert len(checked) <= win.FREE_TRIES, len(checked)
    assert codes.count(429) >= 40 - win.FREE_TRIES


def test_a_new_pin_ends_every_open_window(_clean):
    # The Settings page tells the owner this after a save (TvGrownupPinPanel), so it is held here.
    win.set_pin(PIN)
    tv = _device()
    g = win.open_with_pin(tv, "s", PIN)
    win.set_pin("9999", by="pc_settings")
    assert not win.check(tv, g)


def test_the_pins_length_is_the_tvs():
    # The PC's PIN is the TV's grown-up PIN (safety/Pin.java: 4 to 8 digits). A PC PIN the TV cannot take
    # would turn every real unlock on the TV into a household miss.
    assert (win._PIN_MIN, win._PIN_MAX) == (4, 8)


def test_a_pin_set_on_the_pc_names_the_owner_in_the_ledger(_clean):
    win.set_pin(PIN, by="pc_settings")
    assert _clean["rows"][-1][1]["principal"] == "owner:pc_settings"


def test_an_ended_window_is_recorded_under_the_tv_that_opened_it(_clean):
    win.set_pin(PIN)
    tv, other = _device(), _device()
    g = win.open_with_pin(tv, "s", PIN)
    _clean["clock"]["t"] += win.IDLE_S + 1
    assert not win.check(other, g)
    rec = _clean["rows"][-1][1]
    assert rec["event"] == "ended" and rec["principal"] == "device:%s" % tv["id"]


def test_the_window_a_turn_used_is_named_for_its_trace(_clean):
    import hashlib
    win.set_pin(PIN)
    tv, other = _device(), _device()
    g = win.open_with_pin(tv, "s", PIN)
    assert win.window_for(tv, g) == win.Window(id=hashlib.sha256(g.encode()).hexdigest()[:16], by=win.OPENED_BY_PIN)
    assert win.window_for(other, g) is None and win.window_for(tv, "x") is None


def test_a_pin_changed_while_an_open_is_checking_wins(monkeypatch):
    # An open checked against the old PIN must not leave a window after the new PIN is saved.
    win.set_pin(PIN)
    tv = _device()
    real = win._matches

    def check_then_owner_changes_pin(pin):
        ok = real(pin)
        win.set_pin("9999", by="pc_settings")
        return ok
    monkeypatch.setattr(win, "_matches", check_then_owner_changes_pin)
    with pytest.raises(win.WindowRefused) as e:
        win.open_with_pin(tv, "s", PIN)
    assert e.value.status == 409 and not win._GRANTS


def test_no_ledger_row_carries_the_pin(_clean):
    win.set_pin(PIN, by="pc_settings")
    tv = _device()
    with pytest.raises(win.WindowRefused):
        win.open_with_pin(tv, "s", "7364")
    g = win.open_with_pin(tv, "s", PIN)
    win.close(tv)
    rows = repr(_clean["rows"])
    assert _clean["rows"] and PIN not in rows and "7364" not in rows and g not in rows


def test_an_eight_digit_pin_is_taken():
    win.set_pin("12345678")
    assert win._matches("12345678")


def test_an_old_pin_open_started_during_a_save_gets_no_window(monkeypatch):
    # Saving a PIN is one step under the lock: the new verifier and the generation move together, so an open
    # with the old PIN that starts in the middle of a save never leaves a window.
    import os
    import threading
    win.set_pin(PIN)
    tv = _device()
    real_replace, got = os.replace, {}

    def race_then_replace(src, dst):
        # The save is under way and the new verifier is not on disk yet: an open with the old PIN tries now.
        def old_pin_open():
            try:
                got["grant"] = win.open_with_pin(tv, "s", PIN)
            except win.WindowRefused as e:
                got["refused"] = e.code
        t = threading.Thread(target=old_pin_open)
        t.start()
        t.join(0.3)
        got["t"] = t
        real_replace(src, dst)
    monkeypatch.setattr(win.os, "replace", race_then_replace)
    win.set_pin("9999", by="pc_settings")
    got["t"].join(10)
    assert "grant" not in got and not win._GRANTS, got


def test_two_saves_at_once_leave_one_pin_and_both_finish(monkeypatch):
    import threading
    import time as _time
    errors, inside, most = [], [0], [0]
    real_replace = win.os.replace

    def counted_replace(src, dst):
        inside[0] += 1
        most[0] = max(most[0], inside[0])
        _time.sleep(0.05)
        try:
            real_replace(src, dst)
        finally:
            inside[0] -= 1
    monkeypatch.setattr(win.os, "replace", counted_replace)

    def save(p):
        try:
            win.set_pin(p, by="pc_settings")
        except Exception as e:
            errors.append(repr(e))
    ts = [threading.Thread(target=save, args=(p,)) for p in ("1111", "2222")]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    assert not errors and most[0] == 1 and (win._matches("1111") != win._matches("2222"))


def test_an_ended_windows_row_carries_no_grant(_clean):
    win.set_pin(PIN)
    tv = _device()
    g = win.open_with_pin(tv, "s", PIN)
    _clean["clock"]["t"] += win.IDLE_S + 1
    assert not win.check(tv, g)
    assert _clean["rows"][-1][1]["event"] == "ended" and g not in repr(_clean["rows"])


def test_the_lock_out_schedule(_clean):
    # FREE_TRIES - 1 free misses; the FREE_TRIES-th try locks LOCK_BASE_S; each further miss after a wait doubles
    # it, up to LOCK_MAX_S; a right PIN clears the household count.
    win.set_pin(PIN)
    tv = _device()
    clock = _clean["clock"]

    def miss():
        with pytest.raises(win.WindowRefused) as e:
            win.open_with_pin(tv, "s", "0000")
        return e.value.status

    for _ in range(win.FREE_TRIES):
        assert miss() == 403
    waits = []
    for _ in range(10):
        wait = win._MISSES[win._HOUSEHOLD]["until"] - clock["t"]
        waits.append(wait)
        clock["t"] += wait - 1
        assert miss() == 429, "still locked one second before the wait ends"
        clock["t"] += 2
        assert miss() == 403
    assert waits[:4] == [win.LOCK_BASE_S, 2 * win.LOCK_BASE_S, 4 * win.LOCK_BASE_S, 8 * win.LOCK_BASE_S]
    assert max(waits) == win.LOCK_MAX_S and waits[-1] == win.LOCK_MAX_S
    clock["t"] += win.LOCK_MAX_S + 1
    assert win.check(tv, win.open_with_pin(tv, "s", PIN))
    for _ in range(win.FREE_TRIES - 1):
        assert miss() == 403, "a right PIN starts the count again"


def test_one_window_per_tv(_clean):
    win.set_pin(PIN)
    tv = _device()
    first = win.open_with_pin(tv, "s", PIN)
    second = win.open_with_pin(tv, "s", PIN)
    assert not win.check(tv, first) and win.check(tv, second)


def test_a_try_without_a_device_session_is_refused():
    win.set_pin(PIN)
    with pytest.raises(win.WindowRefused) as e:
        win.open_with_pin(_device(), "", PIN)
    assert e.value.status == 401


def test_the_pin_file_is_read_only_under_the_lock(monkeypatch):
    # On Windows a save's os.replace fails against an open reader, so every read of the verifier holds the lock
    # a save holds.
    win.set_pin(PIN)
    real_read, unlocked = win.Path.read_text, []

    def watched(self, *a, **k):
        if self == win._path() and not win._LOCK.locked():
            unlocked.append(str(self))
        return real_read(self, *a, **k)
    monkeypatch.setattr(win.Path, "read_text", watched)
    win.has_pin()
    win._matches(PIN)
    win.open_with_pin(_device(), "s", PIN)
    assert not unlocked


def test_an_old_pin_open_just_before_a_save_takes_the_lock_gets_no_window(monkeypatch):
    # Everything a save changes (verifier, generation, the end of every window) happens inside its one locked
    # step: an old-PIN open that runs to the end just before the save takes the lock leaves no window after it.
    import threading
    win.set_pin(PIN)
    tv = _device()
    real, saver, fired, got = win._LOCK, [None], [False], {}

    class HookedLock:
        def __enter__(self):
            if threading.current_thread() is saver[0] and not fired[0]:
                fired[0] = True
                def old_pin_open():
                    try:
                        got["grant"] = win.open_with_pin(tv, "s", PIN)
                    except win.WindowRefused as e:
                        got["refused"] = e.code
                t = threading.Thread(target=old_pin_open)
                t.start()
                t.join(10)
            return real.__enter__()

        def __exit__(self, *a):
            return real.__exit__(*a)

        def locked(self):
            return real.locked()
    monkeypatch.setattr(win, "_LOCK", HookedLock())
    saver[0] = threading.current_thread()
    win.set_pin("9999", by="pc_settings")
    assert fired[0]
    assert not win.check(tv, got.get("grant", "")) and not win._GRANTS, got

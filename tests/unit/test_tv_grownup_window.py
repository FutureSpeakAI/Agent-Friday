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
    assert win.window_for(tv, g) == {"id": hashlib.sha256(g.encode()).hexdigest()[:16], "by": win.OPENED_BY_PIN}
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

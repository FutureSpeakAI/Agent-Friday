"""The grown-up window the PC issues and checks (closes the TV-P1 accepted risk, 2026-11-26).

The TV's own "grownup" header no longer unlocks the owner's memory by itself: a turn needs a live window
grant the PC issued after checking the PIN against its own verifier, bound to this TV and this session.
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

def test_the_pin_is_kept_only_as_a_verifier():
    win.set_pin(PIN)
    raw = win._path().read_text(encoding="utf-8")
    assert PIN not in raw and win.has_pin()


@pytest.mark.parametrize("bad", ["", "12", "abcd", "1" * 13, None])
def test_a_pin_must_be_4_to_12_digits(bad):
    with pytest.raises(ValueError):
        win.set_pin(bad)


def test_a_tampered_verifier_counts_as_no_pin():
    win.set_pin(PIN)
    p = win._path()
    p.write_text(p.read_text(encoding="utf-8").replace('"n"', '"N"'), encoding="utf-8")
    assert not win.has_pin()


# ── opening and checking ────────────────────────────────────────────────────

def test_the_right_pin_opens_a_window_bound_to_this_tv_and_session(_clean):
    win.set_pin(PIN)
    tv, other = _device(), _device()
    g = win.open_with_pin(tv, "sess-a", PIN)
    assert win.check(tv, "sess-a", g)
    assert not win.check(tv, "sess-b", g), "another session of the same TV"
    assert not win.check(other, "sess-a", g), "another TV"
    assert not win.check(tv, "sess-a", g + "x")
    assert not win.check(tv, "sess-a", "")
    assert _clean["rows"][-1][0] == "tv_grownup_window" and _clean["rows"][-1][1]["event"] == "opened"


def test_a_window_ends_on_the_pcs_clock(_clean):
    win.set_pin(PIN)
    tv = _device()
    g = win.open_with_pin(tv, "s", PIN)
    _clean["clock"]["t"] += win.TTL_S - 1
    assert win.check(tv, "s", g)
    _clean["clock"]["t"] += 2
    assert not win.check(tv, "s", g)


def test_close_ends_every_window_of_that_tv(_clean):
    win.set_pin(PIN)
    tv = _device()
    g = win.open_with_pin(tv, "s", PIN)
    win.close(tv)
    assert not win.check(tv, "s", g)
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
    assert win.check(tv, "s", win.open_with_pin(tv, "s", PIN))


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

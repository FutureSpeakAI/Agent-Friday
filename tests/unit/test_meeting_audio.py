"""System audio for meetings: the loopback source, with every device mocked.

No test here opens a real microphone, speaker or loopback. The WASAPI backend
is replaced by a fake that hands over packets in the device's own format, and a
guard makes constructing the real backend a failure.
"""
from __future__ import annotations

import array
import struct
import time

import pytest

np = pytest.importorskip("numpy")

from agent_friday.services import meeting_audio as ma  # noqa: E402


@pytest.fixture(autouse=True)
def _no_real_device(monkeypatch):
    def boom(*a, **k):
        raise AssertionError("a test tried to open the real WASAPI loopback")
    monkeypatch.setattr(ma, "_WasapiBackend", boom)


def _fmt(rate=48000, channels=2, bits=32, is_float=True):
    return {"rate": rate, "channels": channels, "bits": bits, "float": is_float,
            "block_align": channels * bits // 8}


def _float_stereo(seconds, rate=48000, hz=440.0, amp=0.5):
    t = np.arange(int(rate * seconds)) / rate
    mono = (amp * np.sin(2 * np.pi * hz * t)).astype("<f4")
    return np.stack([mono, mono], axis=1).astype("<f4").tobytes()


class FakeBackend:
    """Packets in the device's own format; nothing is opened."""

    def __init__(self, fmt, packets, fail_open=None):
        self.fmt = fmt
        self.packets = list(packets)
        self.fail_open = fail_open
        self.closed = False

    def open(self):
        if self.fail_open:
            raise RuntimeError(self.fail_open)
        return dict(self.fmt)

    def read(self):
        return [self.packets.pop(0)] if self.packets else []

    def close(self):
        self.closed = True


def _rms16(pcm):
    a = array.array("h")
    a.frombytes(pcm)
    return (sum(x * x for x in a) / max(1, len(a))) ** 0.5


# ── format parsing ──────────────────────────────────────────────────────────

def test_a_float_extensible_mix_format_is_read():
    head = struct.pack("<HHIIHHH", 0xFFFE, 2, 48000, 48000 * 8, 8, 32, 22)
    # valid-bits (2), channel mask (4), then the SubFormat GUID, whose first
    # dword is 3 for IEEE float
    raw = head + struct.pack("<H", 32) + struct.pack("<I", 3) + struct.pack("<I", 3) + b"\x00" * 12
    assert len(raw) == 40
    f = ma.parse_mix_format(raw)
    assert f == {"channels": 2, "rate": 48000, "bits": 32, "float": True, "block_align": 8}


def test_a_pcm16_mix_format_is_read():
    raw = struct.pack("<HHIIHHH", 1, 1, 44100, 88200, 2, 16, 0)
    assert ma.parse_mix_format(raw)["float"] is False


@pytest.mark.parametrize("raw", [b"", b"\x00" * 8,
                                 struct.pack("<HHIIHHH", 99, 2, 48000, 1, 4, 16, 0)])
def test_a_mix_format_that_cannot_be_trusted_is_refused(raw):
    with pytest.raises(ValueError):
        ma.parse_mix_format(raw)


# ── conversion ──────────────────────────────────────────────────────────────

def test_stereo_float_at_48k_becomes_mono_pcm16_at_16k():
    rs = ma.Resampler(48000, 16000)
    out = rs.feed(ma.decode_to_mono(_float_stereo(1.0), _fmt()))
    assert abs(len(out) // 2 - 16000) <= 3
    assert 8000 < _rms16(out) < 14000          # 0.5 amplitude sine ~ 11585 rms * 0.707


def test_resampling_in_pieces_equals_resampling_whole():
    raw = _float_stereo(0.5)
    mono = ma.decode_to_mono(raw, _fmt())
    whole = ma.Resampler(48000, 16000).feed(mono)
    rs = ma.Resampler(48000, 16000)
    parts = b"".join(rs.feed(mono[i:i + 1001]) for i in range(0, len(mono), 1001))
    n = min(len(whole), len(parts)) // 2
    assert n > 7000
    a = np.frombuffer(whole, dtype="<i2")[:n].astype(int)
    b = np.frombuffer(parts, dtype="<i2")[:n].astype(int)
    assert int(abs(a - b).max()) <= 2            # rounding only: no seam at the joins


def test_44100_input_still_lands_at_16k():
    t = np.arange(44100) / 44100
    mono = (0.4 * np.sin(2 * np.pi * 300 * t)).astype("float32")
    out = ma.Resampler(44100, 16000).feed(mono)
    assert abs(len(out) // 2 - 16000) <= 3


def test_int16_stereo_is_decoded_to_mono():
    pcm = np.array([[1000, -1000], [2000, 0]], dtype="<i2").tobytes()
    mono = ma.decode_to_mono(pcm, _fmt(bits=16, is_float=False))
    assert mono.shape == (2,) and abs(mono[1] - 1000 / 32768) < 1e-6


def test_silence_fills_a_quiet_stretch_but_not_normal_jitter():
    assert ma.silence_to_fill(0, 0.1) == 0
    assert ma.silence_to_fill(0, 2.0) == 32000
    assert ma.silence_to_fill(31000, 2.0) == 0        # within 250 ms of the clock


# ── the source ──────────────────────────────────────────────────────────────

def _run(source, seconds=0.25):
    got = []
    source.start(got.append)
    time.sleep(seconds)
    source.stop()
    return got


def test_the_loopback_delivers_16k_mono_pcm_from_a_device_format():
    be = FakeBackend(_fmt(), [(_float_stereo(0.1), False)] * 5)
    src = ma.LoopbackSource(be)
    got = _run(src)
    pcm = b"".join(got)
    assert src.kind == "system_audio" and not src.error and be.closed
    assert len(pcm) // 2 >= 7500                         # five 0.1 s packets at 16 kHz
    assert _rms16(pcm[:3200]) > 5000


def test_a_silent_packet_is_silence_not_noise():
    be = FakeBackend(_fmt(), [(b"\x7f" * 3840, True)])
    got = _run(ma.LoopbackSource(be), 0.1)
    assert got and all(set(g) == {0} for g in got)


def test_nothing_playing_is_filled_with_silence_on_the_wall_clock():
    T = [0.0]
    be = FakeBackend(_fmt(), [])
    src = ma.LoopbackSource(be, clock=lambda: T[0], poll_s=0.001)
    got = []
    src.start(got.append)
    T[0] += 1.0
    deadline = time.monotonic() + 2
    while not got and time.monotonic() < deadline:
        time.sleep(0.005)
    src.stop()
    assert got and sum(len(g) for g in got) >= 2 * 16000 * 0.9
    assert all(set(g) == {0} for g in got)


def test_a_device_that_will_not_open_fails_start_with_its_reason():
    src = ma.LoopbackSource(FakeBackend(_fmt(), [], fail_open="no render device"))
    with pytest.raises(RuntimeError, match="no render device"):
        src.start(lambda pcm: None)
    assert src.error == "no render device" and not src.active


def test_a_device_that_dies_mid_recording_sets_error_and_closes():
    class Dies(FakeBackend):
        def read(self):
            raise OSError("device removed")
    be = Dies(_fmt(), [])
    src = ma.LoopbackSource(be)
    src.start(lambda pcm: None)
    deadline = time.monotonic() + 2
    while not src.error and time.monotonic() < deadline:
        time.sleep(0.005)
    src.stop()
    assert "device removed" in src.error and be.closed


# ── two channels ────────────────────────────────────────────────────────────

class Mic:
    kind = "microphone"
    error = ""

    def __init__(self):
        self.on = None

    def start(self, on_pcm):
        self.on = on_pcm

    def stop(self):
        self.on = None


class Loop(Mic):
    kind = "system_audio"


def test_the_dual_source_tags_every_chunk_with_its_channel():
    mic, loop = Mic(), Loop()
    d = ma.DualSource(mic, loop)
    got = []
    d.start(lambda pcm, ch: got.append((ch, pcm)))
    mic.on(b"m")
    loop.on(b"l")
    assert got == [("you", b"m"), ("them", b"l")]
    assert d.channels == ("you", "them") and d.system_audio_active


def test_a_loopback_that_will_not_start_does_not_stop_the_microphone():
    class Bad(Loop):
        def start(self, on_pcm):
            raise RuntimeError("nothing to loop back")
    d = ma.DualSource(Mic(), Bad())
    d.start(lambda pcm, ch: None)
    assert d.error == "" and d.system_audio_active is False
    assert "nothing to loop back" in d.system_error


def test_availability_is_honest_off_windows():
    a = ma.availability("posix")
    assert a["available"] is False and "Windows" in a["reason"]


def test_availability_names_the_missing_package(monkeypatch):
    import importlib.util as iu
    real = iu.find_spec
    monkeypatch.setattr(iu, "find_spec", lambda name, *a: None if name == "comtypes" else real(name, *a))
    a = ma.availability("nt")
    assert a["available"] is False and "comtypes" in a["reason"]


def test_asking_whether_system_audio_is_possible_opens_nothing():
    # the real backend is guarded by the autouse fixture: asking must not build it
    ma.availability("nt")

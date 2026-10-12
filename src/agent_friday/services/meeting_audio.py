"""The other side of a call: WASAPI loopback capture for the meeting recorder.

A meeting recorder that hears only the microphone misses everyone who is not
in the room. Windows exposes what the speakers are playing as a *loopback*
stream of the default render device, which works with any call app and with
the speakers for an in-person meeting. Nothing is installed for this:
`comtypes` is already in the environment and the audio client interfaces are
declared here. The module is imported lazily; every function that touches a
device is behind a backend object, so tests drive the whole path with a fake
and never open a real microphone or loopback.

Two channels, kept apart
------------------------
`DualSource` runs the microphone and the loopback side by side and tells the
recorder which one each chunk came from: the microphone is "You", the loopback
is "Them". They are never mixed, so each channel is cut, transcribed and
labelled on its own. A single microphone in a room is not split into speakers
(that needs diarization, a later option), so a microphone-only recording has no
speaker labels.

What the loopback delivers
--------------------------
16 kHz mono PCM16, the same as the microphone. The device's own mix format
(usually 48 kHz float, stereo) is converted here. WASAPI delivers nothing while
nothing is playing, so the source fills the gap with silence on the wall clock:
a transcript timestamp stays true to the minute it was spoken.
"""
from __future__ import annotations

import logging
import os
import threading
import time

log = logging.getLogger("friday.meeting_audio")

RATE = 16000
#: A gap of this many seconds without a packet is filled with silence.
FILL_AFTER_S = 0.25
POLL_S = 0.02
#: Seconds `start()` waits for the device to open before it gives up.
OPEN_TIMEOUT_S = 6.0

YOU = "You"
THEM = "Them"

_WAVE_FORMAT_EXTENSIBLE = 0xFFFE
_SUBFORMAT_PCM = 1
_SUBFORMAT_FLOAT = 3
_BUFFERFLAGS_SILENT = 0x2


# ── Is a loopback possible here? ────────────────────────────────────────────

def availability(os_name: str | None = None) -> dict:
    """{available, reason}: whether this machine can record system audio.

    Checks the dependencies only. It opens no device and imports nothing, so
    asking is cheap enough for a status poll.
    """
    import importlib.util
    if (os_name or os.name) != "nt":
        return {"available": False,
                "reason": "System audio capture uses Windows loopback, which "
                          "this computer does not have."}
    # find_spec, not import: this is asked on every status poll and must not
    # load a library (comtypes builds typelib caches on first import).
    if importlib.util.find_spec("comtypes") is None:
        return {"available": False,
                "reason": "System audio capture needs the Windows COM package "
                          "(comtypes), which is not installed."}
    if importlib.util.find_spec("numpy") is None:
        return {"available": False,
                "reason": "System audio capture needs numpy, which is not "
                          "installed."}
    return {"available": True, "reason": ""}


# ── Pure conversion (tested without a device) ───────────────────────────────

def parse_mix_format(raw: bytes) -> dict:
    """The fields of a WAVEFORMATEX (or WAVEFORMATEXTENSIBLE) blob."""
    import struct
    if len(raw) < 16:
        raise ValueError("mix format is too short")
    tag, channels, rate, _avg, block, bits = struct.unpack("<HHIIHH", raw[:16])
    is_float = tag == 3
    if tag == _WAVE_FORMAT_EXTENSIBLE and len(raw) >= 40:
        is_float = struct.unpack("<I", raw[24:28])[0] == _SUBFORMAT_FLOAT
    elif tag not in (1, 3, _WAVE_FORMAT_EXTENSIBLE):
        raise ValueError("unsupported audio format tag %d" % tag)
    if channels < 1 or rate < 8000 or block < 1:
        raise ValueError("implausible mix format")
    if not is_float and bits not in (16, 24, 32):
        raise ValueError("unsupported sample size %d" % bits)
    if is_float and bits != 32:
        raise ValueError("unsupported float sample size %d" % bits)
    return {"channels": channels, "rate": rate, "bits": bits,
            "float": is_float, "block_align": block}


def decode_to_mono(raw: bytes, fmt: dict):
    """Interleaved device samples -> mono float32 in [-1, 1] (a numpy array)."""
    import numpy as np
    ch, bits = int(fmt["channels"]), int(fmt["bits"])
    if fmt["float"]:
        a = np.frombuffer(raw[:len(raw) - len(raw) % (4 * ch)], dtype="<f4")
    elif bits == 16:
        a = np.frombuffer(raw[:len(raw) - len(raw) % (2 * ch)], dtype="<i2").astype(np.float32) / 32768.0
    elif bits == 32:
        a = np.frombuffer(raw[:len(raw) - len(raw) % (4 * ch)], dtype="<i4").astype(np.float32) / 2147483648.0
    else:  # 24-bit packed
        n = len(raw) // (3 * ch) * (3 * ch)
        b = np.frombuffer(raw[:n], dtype=np.uint8).reshape(-1, 3).astype(np.int32)
        v = b[:, 0] | (b[:, 1] << 8) | (b[:, 2] << 16)
        v = np.where(v & 0x800000, v - 0x1000000, v)
        a = v.astype(np.float32) / 8388608.0
    a = a.astype(np.float32, copy=False)
    if ch > 1:
        a = a.reshape(-1, ch).mean(axis=1)
    return a


class Resampler:
    """Streaming mono resampler: box-average to `rate_out`.

    Averaging over each output interval is the low-pass that keeps the
    aliasing out of speech. State is carried between calls, so packet
    boundaries leave no seam.
    """

    def __init__(self, rate_in: int, rate_out: int = RATE):
        self.ratio = float(rate_in) / float(rate_out)
        self._buf = None
        self._pos = 0.0

    def feed(self, mono) -> bytes:
        """float32 mono samples at `rate_in` -> PCM16 bytes at `rate_out`."""
        import numpy as np
        mono = np.asarray(mono, dtype=np.float32)
        self._buf = mono if self._buf is None else np.concatenate([self._buf, mono])
        n_in = len(self._buf)
        n_out = int((n_in - self._pos) // self.ratio)
        if n_out <= 0:
            return b""
        cum = np.concatenate([[0.0], np.cumsum(self._buf, dtype=np.float64)])
        edges = self._pos + np.arange(n_out + 1, dtype=np.float64) * self.ratio
        integral = np.interp(edges, np.arange(n_in + 1, dtype=np.float64), cum)
        out = (integral[1:] - integral[:-1]) / self.ratio
        used = self._pos + n_out * self.ratio
        drop = int(used)
        self._buf = self._buf[drop:]
        self._pos = used - drop
        pcm = np.clip(out * 32768.0, -32768, 32767).astype("<i2")
        return pcm.tobytes()


def silence_to_fill(delivered_samples: int, elapsed_s: float,
                    rate: int = RATE, after_s: float = FILL_AFTER_S) -> int:
    """How many samples of silence keep the stream on the wall clock; 0 when
    the stream is within `after_s` of it."""
    gap = int(elapsed_s * rate) - int(delivered_samples)
    return gap if gap > int(after_s * rate) else 0


# ── The loopback source ─────────────────────────────────────────────────────

class LoopbackSource:
    """System audio as a recorder source: start(on_pcm) / stop() / error / kind.

    `backend` is anything with open() -> mix-format dict, read() -> list of
    (raw_bytes, silent_flag) packets, and close(). The default is the real
    WASAPI backend; tests pass a fake.
    """

    kind = "system_audio"

    def __init__(self, backend=None, *, clock=time.monotonic, poll_s: float = POLL_S):
        self._backend = backend
        self._clock = clock
        self._poll_s = poll_s
        self._thread = None
        self._stop = threading.Event()
        self._ready = threading.Event()
        self.error = ""
        self._open_error = ""
        self.format = None
        self.delivered = 0
        self.active = False

    def start(self, on_pcm) -> None:
        self._stop.clear()
        self._ready.clear()
        self.error = ""
        self._open_error = ""
        self._thread = threading.Thread(target=self._run, args=(on_pcm,),
                                        name="meeting-loopback", daemon=True)
        self._thread.start()
        if not self._ready.wait(OPEN_TIMEOUT_S):
            self._stop.set()
            raise RuntimeError("the system audio device did not open in time")
        # Only a device that did not open fails the start. One that opened and
        # died straight after is a recording that stopped: `error` says why and
        # the recorder's watch ends it, as for a device lost later on.
        if self._open_error:
            raise RuntimeError(self._open_error)

    def _run(self, on_pcm) -> None:
        backend = self._backend or _WasapiBackend()
        try:
            self.format = backend.open()
            rs = Resampler(self.format["rate"], RATE)
        except Exception as e:
            self.error = self._open_error = str(e) or type(e).__name__
            log.warning("loopback did not open: %s", self.error)
            self._ready.set()
            try:
                backend.close()
            except Exception:
                pass
            return
        t0 = self._clock()
        self.active = True
        self._ready.set()
        try:
            while not self._stop.is_set():
                got = backend.read() or []
                for raw, silent in got:
                    if silent:
                        pcm = b"\x00\x00" * (len(raw) // self.format["block_align"]
                                              * RATE // self.format["rate"])
                    else:
                        pcm = rs.feed(decode_to_mono(raw, self.format))
                    if pcm:
                        self.delivered += len(pcm) // 2
                        on_pcm(pcm)
                if not got:
                    fill = silence_to_fill(self.delivered, self._clock() - t0)
                    if fill:
                        self.delivered += fill
                        on_pcm(b"\x00\x00" * fill)
                    self._stop.wait(self._poll_s)
        except Exception as e:
            self.error = str(e) or type(e).__name__
            log.warning("loopback stopped: %s", self.error)
        finally:
            self.active = False
            try:
                backend.close()
            except Exception:
                pass

    def stop(self) -> None:
        self._stop.set()
        t = self._thread
        if t is not None and t is not threading.current_thread():
            t.join(3.0)


class DualSource:
    """The microphone ("You") and the loopback ("Them"), kept as two channels.

    The recorder calls `start(on_pcm)`; chunks arrive as `on_pcm(pcm, channel)`
    with channel "you" or "them". A microphone failure is the recording's
    failure (`error`). A loopback failure is not: the recording goes on with
    the microphone and `system_error` says what happened.
    """

    kind = "microphone+system_audio"
    channels = ("you", "them")

    def __init__(self, mic, loopback):
        self.mic = mic
        self.loopback = loopback
        self.system_started = False
        self._start_error = ""

    def start(self, on_pcm) -> None:
        self.mic.start(lambda pcm: on_pcm(pcm, "you"))
        try:
            self.loopback.start(lambda pcm: on_pcm(pcm, "them"))
            self.system_started = True
        except Exception as e:
            self.system_started = False
            log.warning("system audio did not start: %s", e)
            self._start_error = str(e)

    def stop(self) -> None:
        try:
            self.loopback.stop()
        finally:
            self.mic.stop()

    @property
    def error(self) -> str:
        return getattr(self.mic, "error", "") or ""

    @property
    def system_error(self) -> str:
        return self._start_error or getattr(self.loopback, "error", "") or ""

    @property
    def system_audio_active(self) -> bool:
        return bool(self.system_started and not self.system_error)


# ── The real WASAPI backend ─────────────────────────────────────────────────

class _WasapiBackend:
    """Shared-mode loopback on the default render device, through comtypes.

    Everything is created on the thread that calls open(); the same thread
    reads. Not exercised by the tests (they never open a device): see
    `LoopbackSource` for the seam they use instead.
    """

    def __init__(self):
        self._client = None
        self._cap = None
        self._fmt = None
        self._com = None

    def open(self) -> dict:
        import ctypes
        import comtypes
        t = _com_types()
        comtypes.CoInitializeEx(comtypes.COINIT_MULTITHREADED)
        self._com = comtypes
        enum = comtypes.CoCreateInstance(t["CLSID_MMDeviceEnumerator"],
                                         interface=t["IMMDeviceEnumerator"],
                                         clsctx=comtypes.CLSCTX_INPROC_SERVER)
        dev = enum.GetDefaultAudioEndpoint(0, 0)            # eRender, eConsole
        raw = dev.Activate(ctypes.byref(t["IID_IAudioClient"]), 0x17, None)  # CLSCTX_ALL
        client = ctypes.cast(raw, ctypes.POINTER(t["IAudioClient"]))
        pfmt = client.GetMixFormat()
        head = ctypes.string_at(pfmt, 18)
        cb = int.from_bytes(head[16:18], "little")
        blob = ctypes.string_at(pfmt, 18 + cb)
        self._fmt = parse_mix_format(blob)
        # shared mode, AUDCLNT_STREAMFLAGS_LOOPBACK, a one second buffer
        client.Initialize(0, 0x00020000, 10_000_000, 0, pfmt, None)
        try:
            ctypes.windll.ole32.CoTaskMemFree(ctypes.cast(pfmt, ctypes.c_void_p))
        except Exception:
            pass
        rawcap = client.GetService(ctypes.byref(t["IID_IAudioCaptureClient"]))
        self._cap = ctypes.cast(rawcap, ctypes.POINTER(t["IAudioCaptureClient"]))
        self._client = client
        client.Start()
        return dict(self._fmt)

    def read(self) -> list:
        import ctypes
        out = []
        cap, ba = self._cap, self._fmt["block_align"]
        n = cap.GetNextPacketSize()
        while n:
            data, frames, flags, _dev, _qpc = cap.GetBuffer()
            nbytes = int(frames) * ba
            silent = bool(flags & _BUFFERFLAGS_SILENT)
            out.append((b"\x00" * nbytes if silent else ctypes.string_at(data, nbytes),
                        silent))
            cap.ReleaseBuffer(frames)
            n = cap.GetNextPacketSize()
        return out

    def close(self) -> None:
        try:
            if self._client is not None:
                self._client.Stop()
        except Exception:
            pass
        self._cap = None
        self._client = None
        try:
            if self._com is not None:
                self._com.CoUninitialize()
        except Exception:
            pass
        self._com = None


_TYPES: dict = {}


def _com_types() -> dict:
    """The WASAPI interfaces, declared once (Windows only, comtypes)."""
    if _TYPES:
        return _TYPES
    from ctypes import HRESULT, POINTER, Structure, c_int, c_longlong, c_ubyte
    from ctypes import c_uint32, c_ulonglong, c_ushort, c_void_p
    from comtypes import COMMETHOD, GUID, IUnknown

    class WAVEFORMATEX(Structure):
        _pack_ = 1
        _fields_ = [("wFormatTag", c_ushort), ("nChannels", c_ushort),
                    ("nSamplesPerSec", c_uint32), ("nAvgBytesPerSec", c_uint32),
                    ("nBlockAlign", c_ushort), ("wBitsPerSample", c_ushort),
                    ("cbSize", c_ushort)]

    class IAudioCaptureClient(IUnknown):
        _iid_ = GUID("{C8ADBD64-E71E-48a0-A4DE-185C395CD317}")
        _methods_ = [
            COMMETHOD([], HRESULT, "GetBuffer",
                      (["out"], POINTER(POINTER(c_ubyte)), "ppData"),
                      (["out"], POINTER(c_uint32), "pNumFramesToRead"),
                      (["out"], POINTER(c_uint32), "pdwFlags"),
                      (["out"], POINTER(c_ulonglong), "pu64DevicePosition"),
                      (["out"], POINTER(c_ulonglong), "pu64QPCPosition")),
            COMMETHOD([], HRESULT, "ReleaseBuffer",
                      (["in"], c_uint32, "NumFramesRead")),
            COMMETHOD([], HRESULT, "GetNextPacketSize",
                      (["out"], POINTER(c_uint32), "pNumFramesInNextPacket")),
        ]

    class IAudioClient(IUnknown):
        _iid_ = GUID("{1CB9AD4C-DBFA-4c32-B178-C2F568A703B2}")
        _methods_ = [
            COMMETHOD([], HRESULT, "Initialize",
                      (["in"], c_int, "ShareMode"),
                      (["in"], c_uint32, "StreamFlags"),
                      (["in"], c_longlong, "hnsBufferDuration"),
                      (["in"], c_longlong, "hnsPeriodicity"),
                      (["in"], POINTER(WAVEFORMATEX), "pFormat"),
                      (["in"], POINTER(GUID), "AudioSessionGuid")),
            COMMETHOD([], HRESULT, "GetBufferSize",
                      (["out"], POINTER(c_uint32), "pNumBufferFrames")),
            COMMETHOD([], HRESULT, "GetStreamLatency",
                      (["out"], POINTER(c_longlong), "phnsLatency")),
            COMMETHOD([], HRESULT, "GetCurrentPadding",
                      (["out"], POINTER(c_uint32), "pNumPaddingFrames")),
            COMMETHOD([], HRESULT, "IsFormatSupported",
                      (["in"], c_int, "ShareMode"),
                      (["in"], POINTER(WAVEFORMATEX), "pFormat"),
                      (["out"], POINTER(POINTER(WAVEFORMATEX)), "ppClosestMatch")),
            COMMETHOD([], HRESULT, "GetMixFormat",
                      (["out"], POINTER(POINTER(WAVEFORMATEX)), "ppDeviceFormat")),
            COMMETHOD([], HRESULT, "GetDevicePeriod",
                      (["out"], POINTER(c_longlong), "phnsDefaultDevicePeriod"),
                      (["out"], POINTER(c_longlong), "phnsMinimumDevicePeriod")),
            COMMETHOD([], HRESULT, "Start"),
            COMMETHOD([], HRESULT, "Stop"),
            COMMETHOD([], HRESULT, "Reset"),
            COMMETHOD([], HRESULT, "SetEventHandle",
                      (["in"], c_void_p, "eventHandle")),
            COMMETHOD([], HRESULT, "GetService",
                      (["in"], POINTER(GUID), "riid"),
                      (["out"], POINTER(c_void_p), "ppv")),
        ]

    class IMMDevice(IUnknown):
        _iid_ = GUID("{D666063F-1587-4E43-81F1-B948E807363F}")
        _methods_ = [
            COMMETHOD([], HRESULT, "Activate",
                      (["in"], POINTER(GUID), "iid"),
                      (["in"], c_uint32, "dwClsCtx"),
                      (["in"], c_void_p, "pActivationParams"),
                      (["out"], POINTER(c_void_p), "ppInterface")),
        ]

    class IMMDeviceEnumerator(IUnknown):
        _iid_ = GUID("{A95664D2-9614-4F35-A746-DE8DB63617E6}")
        _methods_ = [
            COMMETHOD([], HRESULT, "EnumAudioEndpoints",
                      (["in"], c_int, "dataFlow"),
                      (["in"], c_uint32, "dwStateMask"),
                      (["out"], POINTER(c_void_p), "ppDevices")),
            COMMETHOD([], HRESULT, "GetDefaultAudioEndpoint",
                      (["in"], c_int, "dataFlow"),
                      (["in"], c_int, "role"),
                      (["out"], POINTER(POINTER(IMMDevice)), "ppEndpoint")),
        ]

    _TYPES.update(
        WAVEFORMATEX=WAVEFORMATEX, IAudioClient=IAudioClient,
        IAudioCaptureClient=IAudioCaptureClient, IMMDevice=IMMDevice,
        IMMDeviceEnumerator=IMMDeviceEnumerator,
        CLSID_MMDeviceEnumerator=GUID("{BCDE0395-E52F-467C-8E3D-C4579291692E}"),
        IID_IAudioClient=GUID("{1CB9AD4C-DBFA-4c32-B178-C2F568A703B2}"),
        IID_IAudioCaptureClient=GUID("{C8ADBD64-E71E-48a0-A4DE-185C395CD317}"))
    return _TYPES

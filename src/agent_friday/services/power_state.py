"""Is the computer short of power right now? One answer for work that waits for a good moment.

Battery Saver is Windows' own switch for "spend less": the system power status carries a flag for it. A machine that is
unplugged counts too, as the Library does (``library_index_on_battery``): idle work does not drain a battery. Both are
read here, once, and never raise; a machine whose power cannot be read counts as plugged in.
"""
from __future__ import annotations

import sys
from typing import Optional


def _windows_status() -> Optional[tuple]:
    """(ac_line_status, battery_saver_flag) from GetSystemPowerStatus, or None off Windows or when it cannot be read."""
    if sys.platform != "win32":
        return None
    try:
        import ctypes

        class Status(ctypes.Structure):
            _fields_ = [("ACLineStatus", ctypes.c_ubyte), ("BatteryFlag", ctypes.c_ubyte),
                        ("BatteryLifePercent", ctypes.c_ubyte), ("SystemStatusFlag", ctypes.c_ubyte),
                        ("BatteryLifeTime", ctypes.c_ulong), ("BatteryFullLifeTime", ctypes.c_ulong)]

        status = Status()
        if not ctypes.windll.kernel32.GetSystemPowerStatus(ctypes.byref(status)):
            return None
        return int(status.ACLineStatus), int(status.SystemStatusFlag)
    except Exception:
        return None


def battery_saver_on() -> bool:
    """Windows Battery Saver is on."""
    got = _windows_status()
    return bool(got and got[1] == 1)


def on_battery() -> bool:
    """The computer runs on its battery (unplugged). False when there is no battery or it cannot be read."""
    got = _windows_status()
    if got is not None and got[0] in (0, 1):
        return got[0] == 0
    try:
        import psutil
        battery = psutil.sensors_battery()
        return bool(battery is not None and battery.power_plugged is False)
    except Exception:
        return False


def blocked_reason() -> str:
    """Why work that should wait for a good moment must wait on power, in words; "" when it need not."""
    if battery_saver_on():
        return "Battery Saver is on"
    if on_battery():
        return "the computer is running on its battery"
    return ""

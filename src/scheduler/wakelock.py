"""Keep the machine awake for as long as a collector is running.

The collector runs on a desktop that sleeps after 30 minutes idle, and a sleeping
machine fires no scheduled tasks. Waking for a task is not enough on its own: Windows
counts down to idle sleep again while the job runs, so a five-hour collection window
would end thirty minutes in, and the only symptom would be a gap.

`ES_SYSTEM_REQUIRED | ES_CONTINUOUS` tells Windows the system is in use until the flag
is cleared or the process exits. `ES_DISPLAY_REQUIRED` is deliberately not set: there
is no reason to keep a monitor lit to record order books.

This holds off *idle* sleep only. It cannot stop a user closing a laptop lid, choosing
Sleep from the menu, or a shutdown, and it is not a substitute for an always-on host.
"""

from __future__ import annotations

import ctypes
import logging
import sys

log = logging.getLogger(__name__)

ES_CONTINUOUS = 0x80000000
ES_SYSTEM_REQUIRED = 0x00000001


def hold_awake() -> bool:
    """Ask Windows not to sleep while this process lives. True if the request was taken.

    Never raises. A collector that cannot hold the lock should still collect for as
    long as the machine happens to stay awake, and say so, rather than refuse to start.
    """
    if sys.platform != "win32":
        log.info("wake lock not applicable on %s", sys.platform)
        return False
    try:
        previous = ctypes.windll.kernel32.SetThreadExecutionState(
            ES_CONTINUOUS | ES_SYSTEM_REQUIRED
        )
    except Exception as exc:  # noqa: BLE001 - a missing API must not stop collection
        log.warning("could not take wake lock: %s", exc)
        return False
    if previous == 0:
        log.warning("wake lock refused by the OS; the machine may sleep mid-window")
        return False
    log.info("wake lock held: system sleep suppressed while this process runs")
    return True


def release() -> None:
    """Drop the lock, letting the machine sleep on its normal idle timer again."""
    if sys.platform != "win32":
        return
    try:
        ctypes.windll.kernel32.SetThreadExecutionState(ES_CONTINUOUS)
    except Exception as exc:  # noqa: BLE001
        log.warning("could not release wake lock: %s", exc)

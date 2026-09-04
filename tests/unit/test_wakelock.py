"""The wake lock: the only reason a five-hour collection window survives.

Waking the machine for a scheduled task is not enough on its own — Windows keeps
counting down to idle sleep while the job runs, so without this the band would end
thirty minutes in and leave nothing but a gap to explain it.

The contract that matters is that it NEVER raises. A collector that cannot take the
lock should still collect for as long as the machine happens to stay awake; refusing
to start would turn a degraded run into no run at all.
"""

from __future__ import annotations

import sys

from src.scheduler import wakelock


class _FakeKernel32:
    """Stands in for ctypes.windll.kernel32."""

    def __init__(self, result: int = 1, raises: bool = False) -> None:
        self.calls: list[int] = []
        self._result = result
        self._raises = raises

    def SetThreadExecutionState(self, flags: int) -> int:  # noqa: N802 - Win32 name
        if self._raises:
            raise OSError("SetThreadExecutionState unavailable")
        self.calls.append(flags)
        return self._result


class _FakeCtypes:
    def __init__(self, kernel32: _FakeKernel32) -> None:
        self.windll = type("windll", (), {"kernel32": kernel32})()


def _as_windows(monkeypatch, kernel32: _FakeKernel32) -> None:
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(wakelock, "ctypes", _FakeCtypes(kernel32))


def test_the_flags_ask_to_stay_awake_without_lighting_the_screen(monkeypatch) -> None:
    """ES_DISPLAY_REQUIRED is deliberately absent: there is no reason to keep a
    monitor lit to record order books."""
    kernel = _FakeKernel32()
    _as_windows(monkeypatch, kernel)

    assert wakelock.hold_awake() is True
    assert kernel.calls == [wakelock.ES_CONTINUOUS | wakelock.ES_SYSTEM_REQUIRED]


def test_release_clears_the_lock_leaving_the_normal_idle_timer(monkeypatch) -> None:
    kernel = _FakeKernel32()
    _as_windows(monkeypatch, kernel)

    wakelock.release()
    assert kernel.calls == [wakelock.ES_CONTINUOUS], "solo ES_CONTINUOUS suelta el bloqueo"


def test_a_refused_lock_reports_false_instead_of_raising(monkeypatch) -> None:
    """The OS returning 0 means the request was not honoured. The collector must
    still run -- and must not claim a lock it does not hold."""
    _as_windows(monkeypatch, _FakeKernel32(result=0))
    assert wakelock.hold_awake() is False


def test_a_missing_api_never_stops_collection(monkeypatch) -> None:
    _as_windows(monkeypatch, _FakeKernel32(raises=True))
    assert wakelock.hold_awake() is False
    # release() is best effort too: failing to unlock must not crash a run that
    # has already finished collecting.
    wakelock.release()


def test_off_windows_it_is_a_no_op_rather_than_an_error(monkeypatch) -> None:
    """Nothing here is portable, and CI or a future Linux host must not fail on it."""
    monkeypatch.setattr(sys, "platform", "linux")
    assert wakelock.hold_awake() is False
    wakelock.release()

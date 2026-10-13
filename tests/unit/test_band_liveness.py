"""`gap_report --liveness`: the band watchdog, which is NOT the G0 gate.

The distinction is the whole point of the flag and the only thing worth pinning here.
2026-10-12 was a healthy morning -- 10,083 in-band snapshots, 96.5% dense capture -- and
still carried two CRITICAL gaps of ~2.9 min against the 2.5x tolerance. A liveness alarm
wired to gap severity fires on that, every day, and an alarm that fires every day is the
permanent `1` supervisor.ps1 already warns cannot flag anything.

So each test below runs the SAME data twice, once with the flag and once without, and
asserts the two verdicts differ. Asserting only `--liveness == 0` would still pass if a
later edit moved the branch below `find_gaps`, which is exactly the regression that would
make the watchdog useless again.

The band is 00:00-00:30, not the collector's real 05:30-11:30, so that the window under
test is already closed whenever the suite runs. `band_days` judges only whole closed
bands, so a real-band test would report "no closed band yet" when run mid-morning.
"""

from __future__ import annotations

import sys
from collections.abc import Iterator
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from scripts.gap_report import main
from src.config import get_settings
from src.ingest.kalshi import build_book
from src.storage.parquet_store import BookSnapshot, write_snapshots

MADRID = ZoneInfo("Europe/Madrid")
BAND = "00:00-00:30"


@pytest.fixture(autouse=True)
def _clear_settings_cache() -> Iterator[None]:
    # get_settings is lru_cached, so a DATA_DIR set after the first call is ignored and a
    # DATA_DIR left behind leaks into every later test in the session.
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def _today_at(hour: int, minute: int) -> datetime:
    return datetime.now(MADRID).replace(hour=hour, minute=minute, second=0, microsecond=0)


def _snap(ts: datetime, close_time: datetime) -> BookSnapshot:
    book = build_book("A-1", [["0.60", "10"]], [["0.65", "7"]], ts=ts)
    return BookSnapshot.from_book(book, close_time=close_time)


def _run(root: Path, monkeypatch: pytest.MonkeyPatch, *extra: str) -> int:
    monkeypatch.setenv("DATA_DIR", str(root))
    get_settings.cache_clear()
    since = _today_at(0, 0).isoformat()
    monkeypatch.setattr(
        sys,
        "argv",
        ["gap_report", "--since", since, "--band", BAND, "--tz", "Europe/Madrid", *extra],
    )
    return main()


def test_a_critical_gap_fails_the_gate_but_not_the_watchdog(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The band ran. It left a hole at a horizon. That is G0's complaint, not liveness'."""
    first = _today_at(0, 5)
    # T-24h exactly, so both samples sit in the dense window where cadence is 1 min and
    # a 3-minute hole is CRITICAL rather than MISSED.
    close = first + timedelta(hours=24)
    write_snapshots(
        [_snap(first, close), _snap(first + timedelta(minutes=3), close)],
        tmp_path / "snapshots",
    )

    assert _run(tmp_path, monkeypatch) == 1, "a CRITICAL gap must still fail the G0 gate"
    assert _run(tmp_path, monkeypatch, "--liveness") == 0, (
        "the collector ran, so the watchdog must stay quiet -- if this fails, the "
        "liveness branch has drifted below find_gaps and the alarm is noise again"
    )


def test_a_band_that_captured_nothing_fails_both(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The one case the watchdog exists for: a closed band with no in-band rows."""
    outside = _today_at(12, 0)
    write_snapshots([_snap(outside, outside + timedelta(hours=24))], tmp_path / "snapshots")

    assert _run(tmp_path, monkeypatch, "--liveness") == 1
    # Not a free pass for the gate either: absence of data is a fault, not a clean window.
    assert _run(tmp_path, monkeypatch) == 1


def test_liveness_without_a_band_refuses_rather_than_guessing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Without --band there is no window to call lost, and every overnight hole counts."""
    # A row has to exist, and not because the check needs one: on a wholly empty data dir
    # the DuckDB view infers its schema from nothing and the `close_time` query raises
    # BinderException before any argument is judged. Unrelated to --liveness, and not
    # reachable from the scheduled task, which always runs against a populated store.
    ts = _today_at(0, 5)
    write_snapshots([_snap(ts, ts + timedelta(hours=24))], tmp_path / "snapshots")

    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    get_settings.cache_clear()
    monkeypatch.setattr(
        sys, "argv", ["gap_report", "--since", _today_at(0, 0).isoformat(), "--liveness"]
    )
    assert main() == 2

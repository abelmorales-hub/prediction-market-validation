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

import re
import sys
from collections.abc import Iterator
from datetime import UTC, datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from scripts.gap_report import main
from src.config import get_settings
from src.ingest.kalshi import build_book
from src.scheduler.gaps import band_days
from src.storage.parquet_store import BookSnapshot, write_snapshots

MADRID = ZoneInfo("Europe/Madrid")
BAND = "00:00-00:30"

SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"
BAND_CHECK_PS1 = SCRIPTS / "band_check.ps1"
SUPERVISOR_PS1 = SCRIPTS / "supervisor.ps1"


def _watchdog_defaults() -> tuple[tuple[time, time], ZoneInfo]:
    """band_check.ps1's own -Band/-Tz defaults, read rather than restated here.

    A test carrying its own copy of them would stay green if the script drifted back to
    local time -- the exact shape of gate this project has been bitten by repeatedly.
    """
    src = BAND_CHECK_PS1.read_text(encoding="utf-8")
    band = re.search(r"^\s*\[string\]\$Band\s*=\s*'(\d\d:\d\d)-(\d\d:\d\d)'", src, re.M)
    tz = re.search(r"^\s*\[string\]\$Tz\s*=\s*'([^']+)'", src, re.M)
    assert band and tz, f"could not read the watchdog's -Band/-Tz defaults from {BAND_CHECK_PS1}"
    return (time.fromisoformat(band[1]), time.fromisoformat(band[2])), ZoneInfo(tz[1])


def _supervisor_band() -> tuple[tuple[time, time], time]:
    """supervisor.ps1's UTC band and the instant it registers pmv-band-check at.

    This is the production configuration: since 2026-10-30 the task is registered with
    -Band/-Tz passed explicitly, so supervisor.ps1 is the single source of truth and
    band_check.ps1's defaults are only the hand-run fallback. Both are read here and
    asserted equal, because "kept in sync by hand" is what failed on 10-25.
    """
    src = SUPERVISOR_PS1.read_text(encoding="utf-8")
    start = re.search(r"^\$BandStartUtc\s*=\s*'(\d\d:\d\d)'", src, re.M)
    hours = re.search(r"^\$BandDurationH\s*=\s*(\d+)", src, re.M)
    # The watchdog's offset past the close is structural, not a literal: read the AddMinutes
    # that derives it so moving it has to move this test too.
    offset = re.search(r"^\$BandCheckAtUtc\s*=.*AddMinutes\((\d+)\)", src, re.M)
    assert start and hours and offset, f"could not read the band from {SUPERVISOR_PS1}"

    opens = time.fromisoformat(start[1])
    base = datetime(2026, 1, 1, opens.hour, opens.minute, tzinfo=UTC)
    closes = base + timedelta(hours=int(hours[1]))
    fires = closes + timedelta(minutes=int(offset[1]))
    return (opens, closes.time()), fires.time()


WATCHDOG_BAND, WATCHDOG_TZ = _watchdog_defaults()
SUPERVISOR_BAND, WATCHDOG_FIRES_AT = _supervisor_band()


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


def test_the_watchdogs_band_is_closed_at_its_own_fire_instant() -> None:
    """The regression of 2026-10-25, which cost the alarm five days and one real outage.

    Every test above picks a band already closed whenever the suite happens to run, so none
    of them can see the fault that actually bit: a band still OPEN at the moment the
    watchdog asks about it. `band_days` judges only whole closed bands, so when Madrid left
    DST and pmv-band-check moved from 11:33 to 10:33 local while still being handed
    04:30-11:30 *local*, the window had an hour left to run at every check. It printed "no
    closed band in range yet; not judging" and exited 0 daily -- through 2026-10-29, a
    total local loss it never reported.

    This pins the pairing instead of the band: both the band and the fire instant are read
    out of the scripts and `now` is injected, so the assertion is about the relationship
    between them rather than about whatever hour the suite happens to run at. The old
    config is asserted to fail too, because a test that only checks the new one passes just
    as well if the band drifts back to local time.
    """
    # supervisor.ps1 is the production source of truth; band_check.ps1's defaults are the
    # hand-run fallback and are documented as kept equal to it. Pin that, or the fallback
    # rots into a second configuration that only shows up when someone runs it by hand.
    assert WATCHDOG_BAND == SUPERVISOR_BAND, (
        f"band_check.ps1 defaults {WATCHDOG_BAND} disagree with supervisor.ps1 "
        f"{SUPERVISOR_BAND}; the hand-run fallback must match what -Install registers"
    )
    assert WATCHDOG_TZ == ZoneInfo("UTC"), "the band must be stated in UTC, not a local zone"

    # The production invocation: --since is today's LOCAL midnight, 23:00Z the day before.
    since = datetime(2026, 10, 30, 0, 0, tzinfo=MADRID)
    now = datetime.combine(datetime(2026, 10, 30).date(), WATCHDOG_FIRES_AT, tzinfo=UTC)
    rows = [("A-1", datetime(2026, 10, 30, 4, 0, tzinfo=UTC), now)]

    fires = WATCHDOG_FIRES_AT.strftime("%H:%M")

    # The historical pairing, with its own literals because it is a frozen fact rather than
    # anything still derived: from 10-25 the trigger was stuck at 09:33Z (registered under
    # CEST as 11:33 local) while the band was asked about as 04:30-11:30 Europe/Madrid,
    # which under CET does not close until 10:30Z. Fifty-seven minutes short, every day.
    # Note it is the PAIRING that was broken, not either value alone -- the same local band
    # judged against the 10:33Z instant this file now derives would have been fine, which
    # is exactly why neither number looked wrong on its own.
    broken = band_days(
        rows, since, datetime(2026, 10, 30, 9, 33, tzinfo=UTC), (time(4, 30), time(11, 30)), MADRID
    )
    assert broken == [], (
        "the 10-25 pairing must judge nothing: a 09:33Z check against a local band closing "
        "10:30Z is the bug, and if this ever judges the comments should be corrected"
    )

    judged = band_days(rows, since, now, WATCHDOG_BAND, WATCHDOG_TZ)
    assert len(judged) == 1, f"the UTC band must be closed and judged at {fires}Z: {judged}"
    assert judged[0].day == datetime(2026, 10, 30).date()
    assert judged[0].samples == 1, "the in-band row must count toward the day"
    assert judged[0].clean, "one in-band sample is a live band; holes are find_gaps' business"

    # Yesterday opened before `since`, so it was never this window's to collect. Judging it
    # would re-report the previous morning's verdict forever.
    assert all(d.day != datetime(2026, 10, 29).date() for d in judged)

    # Still catches the case the watchdog exists for, at the same instant.
    dead = band_days([], since, now, WATCHDOG_BAND, WATCHDOG_TZ)
    assert len(dead) == 1 and not dead[0].clean, f"a dead band must read lost: {dead}"

    # And the seam cannot separate them again: in CEST local midnight is 22:00Z the day
    # before, the case that could pull an extra band-day into range.
    s_since = datetime(2026, 7, 15, 0, 0, tzinfo=MADRID)
    s_now = datetime.combine(datetime(2026, 7, 15).date(), WATCHDOG_FIRES_AT, tzinfo=UTC)
    s_rows = [("A-1", datetime(2026, 7, 15, 4, 0, tzinfo=UTC), s_now)]
    summer = band_days(s_rows, s_since, s_now, WATCHDOG_BAND, WATCHDOG_TZ)
    assert [d.day for d in summer] == [datetime(2026, 7, 15).date()], summer

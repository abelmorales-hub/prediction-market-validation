"""H2: the ensemble answering a strike ladder, point-in-time.

The load-bearing test here is `test_a_real_ladder_covers_every_temperature_exactly_once`.
The strike arithmetic has three branches and an off-by-one in any of them still returns a
number in [0, 1], still looks sane in a report, and still scores. What it cannot do is
tile the line exactly once — so the ladder is checked against the rungs Kalshi actually
published for KXHIGHAUS-26SEP05 rather than against rungs invented to match the code.
"""

from datetime import UTC, date, datetime

import pytest

from src.analysis.emit import NO_FEATURES, emit
from src.analysis.horizons import HorizonPoint
from src.analysis.weather import MODEL_VERSION, WeatherForecaster
from src.ingest.kalshi import KalshiMarket, settles_on_from_rules, station_from_rules
from src.models.weather import ensemble_probability, settles_yes, to_observed_degrees

NOW = datetime(2026, 9, 5, 6, 0, tzinfo=UTC)
CLOSE = datetime(2026, 9, 6, 6, 0, tzinfo=UTC)
TARGET = date(2026, 9, 5)

RULES = (
    "If the maximum temperature recorded at Austin (CLIAUS) for Sep 5, 2026, is "
    "between 94-95 fahrenheit according to The Weather Company, then the market "
    "resolves to Yes."
)

# The published rungs of KXHIGHAUS-26SEP05, read from the live event on 2026-09-05.
LADDER = [
    ("less", None, 94.0),       # "93 or below"
    ("between", 94.0, 95.0),    # "94 to 95"
    ("between", 96.0, 97.0),    # "96 to 97"
    ("between", 98.0, 99.0),    # "98 to 99"
    ("between", 100.0, 101.0),  # "100 to 101"
    ("greater", 101.0, None),   # "102 or above"
]


# -- the strike arithmetic --------------------------------------------------


@pytest.mark.parametrize("temp", range(85, 115))
def test_a_real_ladder_covers_every_temperature_exactly_once(temp: int) -> None:
    """No gap, no overlap. A rung that is off by one breaks this and nothing else."""
    hits = [settles_yes(temp, *rung) for rung in LADDER]
    assert sum(hits) == 1, f"{temp}F matched {sum(hits)} rungs"


def test_the_bounds_match_the_venue_wording() -> None:
    assert settles_yes(93, "less", None, 94.0)          # "93 or below"
    assert not settles_yes(94, "less", None, 94.0)
    assert settles_yes(102, "greater", 101.0, None)     # "102 or above"
    assert not settles_yes(101, "greater", 101.0, None)
    assert settles_yes(94, "between", 94.0, 95.0)       # "94 to 95", inclusive
    assert settles_yes(95, "between", 94.0, 95.0)
    assert not settles_yes(96, "between", 94.0, 95.0)


def test_an_unknown_rung_raises_rather_than_returning_false() -> None:
    """False would move the mass to the other rungs and still look like a distribution."""
    with pytest.raises(ValueError, match="unknown strike_type"):
        settles_yes(94, "outside", 94.0, 95.0)
    with pytest.raises(ValueError, match="needs both bounds"):
        settles_yes(94, "between", 94.0, None)


def test_rounding_is_half_up_not_bankers() -> None:
    """round() would send 94.5 down and 95.5 up, biasing alternate buckets."""
    assert to_observed_degrees(94.5) == 95
    assert to_observed_degrees(95.5) == 96
    assert to_observed_degrees(93.4) == 93
    assert to_observed_degrees(-0.5) == 0


def test_rounding_decides_bucket_membership() -> None:
    """93.7 misses the 94-95 rung unrounded and hits it rounded. The market settles on
    the rounded observation, so the rounded answer is the right one."""
    assert ensemble_probability([93.7], "between", 94.0, 95.0) == pytest.approx(2 / 3)


# -- the probability --------------------------------------------------------


def test_probability_is_the_laplace_smoothed_member_share() -> None:
    members = [94.0, 94.0, 96.0, 99.0]  # 2 of 4 in the 94-95 bucket
    assert ensemble_probability(members, "between", 94.0, 95.0) == pytest.approx(3 / 6)


def test_a_unanimous_ensemble_stays_strictly_inside_the_open_interval() -> None:
    """The schema requires 0 < p_hat < 1. Without smoothing every far rung of every
    ladder would be rejected, leaving only the near-the-money rungs in the sample."""
    members = [94.0] * 31
    assert ensemble_probability(members, "greater", 101.0, None) == pytest.approx(1 / 33)
    assert ensemble_probability(members, "between", 94.0, 95.0) == pytest.approx(32 / 33)


def test_an_empty_ensemble_raises() -> None:
    with pytest.raises(ValueError, match="empty ensemble"):
        ensemble_probability([], "between", 94.0, 95.0)


def test_a_ladder_sums_to_the_smoothed_total_not_to_one() -> None:
    """Every member lands on exactly one rung, so the raw shares sum to 1 and the
    smoothed ones to (n + rungs) / (n + 2). Checking the exact total catches a rung that
    double-counts or drops a member — which a per-rung assertion cannot see — and pins
    the deliberate departure from 1 so it is not later 'fixed' by normalising."""
    members = [93.2, 94.6, 95.1, 96.9, 98.0, 101.4, 102.7]
    total = sum(ensemble_probability(members, *rung) for rung in LADDER)
    assert total == pytest.approx((len(members) + len(LADDER)) / (len(members) + 2))


# -- reading the settlement rule --------------------------------------------


def test_the_station_and_day_come_from_the_rule() -> None:
    assert station_from_rules(RULES) == "CLIAUS"
    assert settles_on_from_rules(RULES) == TARGET


def test_an_unknown_station_is_declined_not_passed_through() -> None:
    """A code with no coordinates has no grid point, so cataloguing it as forecastable
    would produce a market nothing can forecast."""
    assert station_from_rules("recorded at Nowhere (CLIZZZ) for Sep 5, 2026") is None
    assert station_from_rules("no station here") is None
    assert settles_on_from_rules("no date here") is None


def test_the_settlement_day_is_not_the_close_date() -> None:
    """close_time is just after local midnight the following day. Keying the ensemble
    off it would score every forecast against the wrong day."""
    m = KalshiMarket.model_validate(
        {
            "ticker": "KXHIGHAUS-26SEP05-B94.5",
            "event_ticker": "KXHIGHAUS-26SEP05",
            "close_time": "2026-09-06T06:00:00Z",
            "strike_type": "between",
            "floor_strike": 94,
            "cap_strike": 95,
            "rules_primary": RULES,
        }
    ).to_market()
    assert m.settles_on == TARGET
    assert m.close_time.date() == date(2026, 9, 6)
    assert m.settlement_station == "CLIAUS"
    assert (m.strike_type, m.floor_strike, m.cap_strike) == ("between", 94.0, 95.0)


def test_a_market_without_a_strike_carries_none() -> None:
    m = KalshiMarket.model_validate(
        {"ticker": "X-1", "event_ticker": "X", "close_time": "2026-09-06T06:00:00Z"}
    ).to_market()
    assert m.strike_type is None and m.settles_on is None and m.settlement_station is None


# -- point-in-time ----------------------------------------------------------


class FakeRepo:
    """Records what the forecaster asked for, so the PIT bound can be asserted."""

    def __init__(self, row: dict | None) -> None:
        self.row = row
        self.calls: list[tuple] = []
        self.rows: list[object] = []
        self.conn = type("C", (), {"rollback": lambda self: None})()

    def latest_ensemble_before(self, station, target_date, before):
        self.calls.append((station, target_date, before))
        return self.row

    def insert_prediction(self, p):
        self.rows.append(p)
        return len(self.rows)


def _ensemble(members: list[float]) -> dict:
    return {
        "station": "CLIAUS",
        "target_date": TARGET,
        "fetched_at": datetime(2026, 9, 5, 5, 30, tzinfo=UTC),
        "is_pure_forecast": False,
        "members": len(members),
        "tmax_f": members,
    }


META = {
    "M1": {
        "series_id": "KXHIGHAUS",
        "strike_type": "between",
        "floor_strike": 94.0,
        "cap_strike": 95.0,
        "settles_on": TARGET,
        "settlement_station": "CLIAUS",
    }
}


def _point(market: str = "M1") -> HorizonPoint:
    return HorizonPoint(market, "T-24h", NOW, NOW, 0.4, 0.39, 0.41)


def test_the_ensemble_must_predate_the_price_it_is_paired_with() -> None:
    """The whole point-in-time guarantee is this argument. A later forecast would hand
    H2 information the market did not have and still return a plausible number."""
    repo = FakeRepo(_ensemble([94.0] * 31))
    WeatherForecaster(repo, META)(_point())
    assert repo.calls == [("CLIAUS", TARGET, NOW)]


def test_a_ladder_shares_one_ensemble_query() -> None:
    repo = FakeRepo(_ensemble([94.0] * 31))
    f = WeatherForecaster(repo, META)
    for _ in range(6):
        f(_point())
    assert len(repo.calls) == 1, "six rungs, one station, one day, one instant"


@pytest.mark.parametrize(
    "meta,reason",
    [
        ({}, "h2_not_catalogued"),
        ({"M1": {**META["M1"], "series_id": "KXLOWAUS"}}, "h2_unsupported_series"),
        ({"M1": {**META["M1"], "strike_type": None}}, "h2_no_strike"),
        ({"M1": {**META["M1"], "settlement_station": None}}, "h2_no_station_or_day"),
        ({"M1": {**META["M1"], "settles_on": None}}, "h2_no_station_or_day"),
    ],
)
def test_every_refusal_is_counted(meta: dict, reason: str) -> None:
    """A model that stops forecasting must not look like a model with nothing to say."""
    f = WeatherForecaster(FakeRepo(_ensemble([94.0] * 31)), meta)
    assert f(_point()) == {}
    assert f.stats.as_dict()[reason] == 1
    assert f.stats.as_dict()["h2_forecast"] == 0


def test_a_missing_ensemble_is_counted_not_guessed() -> None:
    f = WeatherForecaster(FakeRepo(None), META)
    assert f(_point()) == {}
    assert f.stats.as_dict()["h2_no_ensemble"] == 1


def test_an_unpriceable_rung_is_counted_and_does_not_raise() -> None:
    meta = {"M1": {**META["M1"], "strike_type": "outside"}}
    f = WeatherForecaster(FakeRepo(_ensemble([94.0] * 31)), meta)
    assert f(_point()) == {}
    assert f.stats.as_dict()["h2_bad_rung"] == 1


# -- through the emit door --------------------------------------------------


def test_h2_is_written_alongside_the_baselines_with_its_own_fingerprint() -> None:
    repo = FakeRepo(_ensemble([94.0] * 20 + [99.0] * 11))
    stats = emit(
        repo, [_point()], {"M1": CLOSE}, "sha", NOW,
        forecaster=WeatherForecaster(repo, META),
    )
    assert stats.written == 3
    by_model = {r.model_version: r for r in repo.rows}
    assert set(by_model) == {"baseline_market", "baseline_shrink", MODEL_VERSION}
    h2 = by_model[MODEL_VERSION]
    assert h2.p_hat == pytest.approx(21 / 33)
    assert h2.features_hash != NO_FEATURES, "must name the ensemble it read"
    assert by_model["baseline_market"].features_hash == NO_FEATURES


def test_a_broken_model_never_costs_a_baseline() -> None:
    """emit runs on a horizon that cannot be revisited. A model raising must not take
    the irreplaceable rows down with it."""

    def explode(_p: HorizonPoint) -> dict:
        raise RuntimeError("model is broken")

    repo = FakeRepo(None)
    stats = emit(repo, [_point()], {"M1": CLOSE}, "sha", NOW, forecaster=explode)
    assert stats.written == 2, "both baselines still went in"
    assert stats.failed == 1

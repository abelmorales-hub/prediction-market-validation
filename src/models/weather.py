"""H2: the market's own strike, answered by the GEFS ensemble and nothing else.

This is baseline 5 of the pre-declaration -- *unadjusted* grid output. It is expected to
lose. The Kalshi temperature markets settle on The Weather Company's observation at a
named station, and a 0.25 degree grid cell carries a station and elevation bias the
market already knows about. A loss here should be read as that bias, not as evidence the
market is efficient. The bias-corrected version is a separate model_version, fitted
later, so the two can be told apart in the scored frame rather than argued about.

Nothing here touches the network, the database, or the clock. Everything is a function
of values the caller already holds, which is what makes the strike arithmetic testable
against the published ladder rather than against a mock.
"""

from __future__ import annotations

import math
from collections.abc import Sequence

# The three shapes Kalshi uses for a scalar ladder, verified live on 2026-09-05 against
# the full KXHIGHAUS-26SEP05 event. Read with the settlement temperature as an integer:
#
#   between  floor <= T <= cap    floor=94  cap=95    "94 to 95"
#   less     T < cap              cap=94              "93 or below"
#   greater  T > floor            floor=101           "102 or above"
#
# Those three tile the line exactly once, no gap and no overlap, which is the property
# `test_a_ladder_sums_to_one` checks. An off-by-one in any of them would still return a
# probability in [0, 1] and would still look sane in a report.
BETWEEN = "between"
LESS = "less"
GREATER = "greater"


def to_observed_degrees(value_f: float) -> int:
    """The whole-degree Fahrenheit value a continuous forecast would settle as.

    The ensemble reports tenths; the market settles on a whole-degree observation. The
    rounding is therefore part of the question, not a presentation detail: a member at
    93.7 misses the 94-95 bucket unrounded and hits it rounded, and with 31 members that
    is three percentage points of probability per member.

    Half-up, not Python's bankers' rounding. `round(94.5)` is 94 and `round(95.5)` is 96,
    which would push half-degree members into whichever neighbouring bucket happened to
    be even -- a bias that alternates direction up the ladder and would be invisible in
    aggregate.
    """
    return math.floor(value_f + 0.5)


def settles_yes(
    observed_f: int,
    strike_type: str,
    floor_strike: float | None,
    cap_strike: float | None,
) -> bool:
    """Does this rung pay at `observed_f` degrees?

    Raises on an unknown strike type or a missing bound rather than returning False. A
    False would silently move probability mass to the other rungs of the same ladder and
    the totals would still look like probabilities.
    """
    if strike_type == BETWEEN:
        if floor_strike is None or cap_strike is None:
            raise ValueError("a 'between' rung needs both bounds")
        return floor_strike <= observed_f <= cap_strike
    if strike_type == LESS:
        if cap_strike is None:
            raise ValueError("a 'less' rung needs cap_strike")
        return observed_f < cap_strike
    if strike_type == GREATER:
        if floor_strike is None:
            raise ValueError("a 'greater' rung needs floor_strike")
        return observed_f > floor_strike
    raise ValueError(f"unknown strike_type: {strike_type!r}")


def ensemble_probability(
    members_f: Sequence[float],
    strike_type: str,
    floor_strike: float | None,
    cap_strike: float | None,
) -> float:
    """Laplace-smoothed share of ensemble members that settle this rung YES.

    **The smoothing is not cosmetic and is declared, not tuned.** A 31-member ensemble
    votes unanimously on most rungs of a six-rung ladder, and the raw share is then
    exactly 0 or 1. Two things go wrong with that. The schema requires 0 < p_hat < 1, so
    every unanimous rung would be rejected -- and those are the far rungs, leaving a
    sample made only of the near-the-money rungs where the market is at its sharpest.
    That is a selection effect masquerading as a result. Second, 0 of 31 is not evidence
    that the event is impossible; it is evidence that its probability is below roughly
    1/31, which is what (k + 1) / (n + 2) says.

    So: p = (k + 1) / (n + 2). Closed form, no free parameter, and strictly inside (0, 1)
    for any n, so the database constraint can never silently drop a rung.

    A consequence, stated here so it is not later mistaken for a bug: the rungs of one
    ladder no longer sum to 1. They sum to (n + rungs) / (n + 2) — 37/33 for a six-rung
    ladder over 31 members. That is correct for this study, which scores each rung as its
    own binary market against its own price. Normalising across the ladder would couple
    rungs that the scorer treats as independent observations, and would smuggle in
    information from the sibling rungs' strikes.
    """
    n = len(members_f)
    if n == 0:
        raise ValueError("empty ensemble")
    k = sum(
        1
        for v in members_f
        if settles_yes(to_observed_degrees(v), strike_type, floor_strike, cap_strike)
    )
    return (k + 1.0) / (n + 2.0)

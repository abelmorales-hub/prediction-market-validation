"""Settlement stations for the Kalshi daily temperature markets.

`rules_primary` names the station by its NWS CLI product code — "the maximum
temperature recorded at Chicago (CLIMDW)". The code, not the city name, is what the
market settles on, and three of them are not the airport you would guess:

* `CLIMDW` is Chicago **Midway**, not O'Hare.
* `CLIHOU` is Houston **Hobby**, not Bush Intercontinental.
* `CLINYC` is **Central Park**, not any of the three airports.

Picking the wrong one is a silent error: the forecast still returns a number, it is
just a forecast for somewhere else. On a hot day Midway and O'Hare differ by a few
degrees, which is the entire width of a Kalshi temperature bucket.

Coordinates are approximate to within a kilometre or so, which is deliberate: the
GEFS grid is 0.25° (~28 km), so any position inside the right city selects the same
cell. They are here to choose a grid point, never to claim station-level precision.

The timezone matters more than the coordinates. These markets settle on the *local
calendar day* maximum, so a UTC-day aggregation would mix two different answers.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Station:
    code: str
    name: str
    latitude: float
    longitude: float
    timezone: str


STATIONS: dict[str, Station] = {
    s.code: s
    for s in (
        Station("CLIATL", "Atlanta, Hartsfield-Jackson", 33.6407, -84.4277, "America/New_York"),
        Station("CLIAUS", "Austin-Bergstrom", 30.1975, -97.6664, "America/Chicago"),
        Station("CLIBOS", "Boston, Logan", 42.3656, -71.0096, "America/New_York"),
        Station("CLIDCA", "Washington, Reagan National", 38.8512, -77.0402, "America/New_York"),
        Station("CLIDEN", "Denver International", 39.8561, -104.6737, "America/Denver"),
        Station("CLIDFW", "Dallas/Fort Worth", 32.8998, -97.0403, "America/Chicago"),
        Station("CLIHOU", "Houston, Hobby", 29.6454, -95.2789, "America/Chicago"),
        Station("CLILAS", "Las Vegas, Harry Reid", 36.0840, -115.1537, "America/Los_Angeles"),
        Station("CLILAX", "Los Angeles International", 33.9416, -118.4085, "America/Los_Angeles"),
        Station("CLIMDW", "Chicago, Midway", 41.7868, -87.7522, "America/Chicago"),
        Station("CLIMIA", "Miami International", 25.7959, -80.2870, "America/New_York"),
        Station("CLIMSP", "Minneapolis-St Paul", 44.8848, -93.2223, "America/Chicago"),
        Station("CLINYC", "New York, Central Park", 40.7789, -73.9692, "America/New_York"),
        Station("CLIOKC", "Oklahoma City, Will Rogers", 35.3931, -97.6007, "America/Chicago"),
        Station("CLIPHL", "Philadelphia International", 39.8729, -75.2437, "America/New_York"),
        # Arizona does not observe DST, so America/Phoenix is not America/Denver.
        Station("CLIPHX", "Phoenix, Sky Harbor", 33.4342, -112.0116, "America/Phoenix"),
        Station("CLISAT", "San Antonio International", 29.5337, -98.4698, "America/Chicago"),
        Station("CLISEA", "Seattle-Tacoma", 47.4502, -122.3088, "America/Los_Angeles"),
        Station("CLISFO", "San Francisco International", 37.6213, -122.3790, "America/Los_Angeles"),
    )
}


def station_for(code: str) -> Station | None:
    """Unknown codes return None rather than a guess: a new city must be added here
    deliberately, with its timezone checked, not forecast at the wrong place."""
    return STATIONS.get(code.strip().upper())

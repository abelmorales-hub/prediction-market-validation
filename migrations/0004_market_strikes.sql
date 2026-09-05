-- What a scalar market actually pays on, stored structurally.
--
-- The catalogue held only the question text. H2 has to answer "does this rung pay at
-- 95 degrees?", and the one thing the pre-declaration forbids is reading that off the
-- title: "Will the maximum temperature be 94-95 on Sep 5, 2026?" is prose, and a rung
-- differs from its neighbour by two characters in it. Kalshi publishes the same fact
-- structurally as strike_type + floor/cap, and these columns are that fact copied once,
-- at catalogue time, by the venue module.
--
-- Nullable throughout. Most markets are not scalar and have no strike; NULL means "not
-- applicable" and the forecaster declines rather than defaulting. A default here would
-- turn "we do not know the strike" into a forecast about the wrong question.

alter table markets add column if not exists strike_type  text;
alter table markets add column if not exists floor_strike double precision;
alter table markets add column if not exists cap_strike   double precision;

-- The local calendar day the market settles on.
--
-- Not derivable from end_date, in every case rather than as an edge case: a daily
-- temperature market stops trading just after local midnight *the following day*
-- (close_time 2026-09-06T06:00Z for the Sep 5 Austin high), so end_date names the day
-- after the one being forecast. Keying the ensemble off it would score every forecast
-- against the wrong day and still return plausible probabilities.
alter table markets add column if not exists settles_on date;

-- NWS CLI product code, e.g. CLIMDW. The city name is not sufficient: CLIMDW is Chicago
-- Midway rather than O'Hare, CLIHOU is Hobby rather than Bush, and CLINYC is Central
-- Park. On a hot day those differ by more than the width of one strike bucket, so the
-- wrong choice is a forecast for somewhere else that never looks wrong.
alter table markets add column if not exists settlement_station text;

create index if not exists markets_station_settles_idx
  on markets (settlement_station, settles_on)
  where settlement_station is not null;

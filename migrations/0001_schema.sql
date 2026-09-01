-- Design doc §3, with venue-neutral identifiers.
--
-- Deviations from the doc's DDL, all deliberate:
--   condition_id            -> market_id     (Kalshi has tickers, not condition ids)
--   yes_token_id/no_token_id -> dropped      (no Kalshi equivalent; always NULL)
--   was_disputed not null default false -> nullable, no default
--       The doc's comment reads "-- UMA". Kalshi has no UMA. A default of false would
--       assert "not disputed" about every market when the truth is "unknown", which is
--       exactly the fabricated data-quality flag M4 forbids.
--   end_date is NOT NULL
--       check_pre_resolution() compares ts >= end_date. A NULL end_date makes that
--       comparison NULL, the IF does not fire, and the prediction is accepted. The
--       append-only guarantee would have a silent hole in it.

create table if not exists markets (
  market_id         text primary key,
  venue             text not null,
  slug              text,
  question          text not null,
  category          text,
  event_family      text,          -- clustering key for the block bootstrap (§5)
  series_id         text,
  created_at        timestamptz,
  end_date          timestamptz not null,
  resolution_source text
);

create index if not exists markets_event_family_idx on markets (event_family);
create index if not exists markets_end_date_idx on markets (end_date);

-- APPEND-ONLY. Always written before resolution. See 0002 for the enforcement.
create table if not exists predictions (
  id             bigserial primary key,
  market_id      text not null references markets (market_id),
  ts             timestamptz not null,
  horizon_label  text not null,
  p_hat          double precision not null check (p_hat > 0 and p_hat < 1),
  model_version  text not null,
  git_sha        text not null,
  features_hash  text not null,
  market_mid     double precision not null,   -- price at the SAME ts
  market_bid     double precision not null,
  market_ask     double precision not null,
  created_at     timestamptz not null default now(),
  unique (market_id, ts, model_version, horizon_label)
);

create index if not exists predictions_market_idx on predictions (market_id);

create table if not exists outcomes (
  market_id     text primary key references markets (market_id),
  resolved_at   timestamptz not null,
  outcome       smallint not null check (outcome in (0, 1)),
  was_disputed  boolean,       -- NULL = unknown, and it stays unknown until observed
  notes         text
);

create table if not exists features_pit (
  market_id      text not null references markets (market_id),
  ts             timestamptz not null,
  feature_set    text not null,
  pit_guarantee  text not null
    check (pit_guarantee in ('archived', 'live_captured', 'reconstructed_unsafe')),
  payload        jsonb not null,
  primary key (market_id, ts, feature_set)
);

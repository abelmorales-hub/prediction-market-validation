-- GEFS ensemble records, at the grain the forecast actually has: one station, one
-- local calendar day, one fetch. Not features_pit, whose grain is the market -- a
-- six-rung strike ladder shares one forecast, and storing six identical copies would
-- make the row count a lie about how much independent information exists.

create table if not exists gefs_ensemble (
  station          text        not null,
  target_date      date        not null,
  fetched_at       timestamptz not null,
  model            text        not null,
  -- False once the target day has begun locally: the values may then incorporate
  -- weather that has already happened. Legitimate at fetched_at, but not a forecast,
  -- and the distinction decides whether a row belongs in a T-24h study.
  is_pure_forecast boolean     not null,
  members          int         not null,
  tmax_f           double precision[] not null,
  tmin_f           double precision[] not null,
  -- Grid point actually used, with its elevation. An unadjusted grid forecast carries
  -- a station bias that is mostly elevation, so keeping this makes the bias
  -- diagnosable later instead of unexplainable.
  grid             jsonb       not null,
  primary key (station, target_date, fetched_at)
);

create index if not exists gefs_station_date_idx on gefs_ensemble (station, target_date);

-- Same reasoning as predictions (0002): the value of a point-in-time record is that
-- it cannot be improved after the fact. A rewritten forecast is indistinguishable
-- from a correct one and silently inflates any score computed from it.
grant select, insert on gefs_ensemble to authenticated, anon, service_role;
revoke update, delete on gefs_ensemble from authenticated, anon, service_role;

create or replace function gefs_is_append_only() returns trigger as $$
begin
  raise exception 'gefs_ensemble is append-only; % rejected on (%, %, %)',
    tg_op, old.station, old.target_date, old.fetched_at;
end;
$$ language plpgsql;

drop trigger if exists gefs_no_update on gefs_ensemble;
create trigger gefs_no_update
  before update or delete on gefs_ensemble
  for each row execute function gefs_is_append_only();

-- A forecast stamped in the future is a clock error or a fabricated row. Either way it
-- would let a prediction cite information it could not have had.
create or replace function gefs_not_from_the_future() returns trigger as $$
begin
  if new.fetched_at > now() + interval '5 minutes' then
    raise exception 'fetched_at % is in the future', new.fetched_at;
  end if;
  return new;
end;
$$ language plpgsql;

drop trigger if exists gefs_fetched_at_sane on gefs_ensemble;
create trigger gefs_fetched_at_sane
  before insert on gefs_ensemble
  for each row execute function gefs_not_from_the_future();

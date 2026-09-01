-- P3 enforced in the database, not in Python. This is what turns discipline into
-- architecture: a prediction cannot be rewritten, and cannot be written after the fact.

-- Supabase ships these roles. Create them if absent so the migration also applies to a
-- plain Postgres used for testing.
do $$
declare r text;
begin
  foreach r in array array['anon', 'authenticated', 'service_role'] loop
    if not exists (select 1 from pg_roles where rolname = r) then
      execute format('create role %I nologin', r);
    end if;
  end loop;
end $$;

grant select, insert on predictions to authenticated, anon, service_role;
revoke update, delete on predictions from authenticated, anon, service_role;

-- The revoke binds those three roles. It does not bind the table owner, a superuser, or
-- any role added later, so it cannot be the only guard: a migration run as owner could
-- still rewrite history. This trigger fires for every role, owner included.
create or replace function predictions_are_append_only() returns trigger as $$
begin
  raise exception 'predictions is append-only; % rejected on row %',
    tg_op, coalesce(old.id, new.id);
end;
$$ language plpgsql;

drop trigger if exists predictions_no_update on predictions;
create trigger predictions_no_update
  before update or delete on predictions
  for each row execute function predictions_are_append_only();

-- Never accept a prediction stamped at or after the market's close.
create or replace function check_pre_resolution() returns trigger as $$
declare
  market_end timestamptz;
begin
  select end_date into market_end from markets where market_id = new.market_id;
  if market_end is null then
    -- Unreachable while end_date is NOT NULL and the FK holds. Kept because the failure
    -- mode it guards against is silent acceptance, not an error.
    raise exception 'No close time known for market %; refusing the prediction',
      new.market_id;
  end if;
  if new.ts >= market_end then
    raise exception 'Prediction timestamp % is at or after market close % for %',
      new.ts, market_end, new.market_id;
  end if;
  return new;
end;
$$ language plpgsql;

drop trigger if exists predictions_pre_resolution on predictions;
create trigger predictions_pre_resolution
  before insert on predictions
  for each row execute function check_pre_resolution();

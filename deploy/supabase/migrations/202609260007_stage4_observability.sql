-- Stage 4 observability: quality-labelled usage and persistent cloud heartbeat.
alter table kaban_usage_samples
  add column if not exists quality text not null default 'estimated',
  add column if not exists limit_value numeric;

alter table kaban_usage_samples drop constraint if exists kaban_usage_samples_quality_check;
alter table kaban_usage_samples
  add constraint kaban_usage_samples_quality_check
  check (quality in ('provider_exact','db_exact','estimated'));

create table if not exists kaban_runtime_health (
  project_id text primary key references kaban_projects(project_id) on delete cascade,
  last_cron_tick timestamptz,
  updated_at timestamptz not null default now()
);

alter table kaban_runtime_health enable row level security;

drop policy if exists kaban_runtime_health_service_all on kaban_runtime_health;
create policy kaban_runtime_health_service_all on kaban_runtime_health
for all using (auth.role() = 'service_role') with check (auth.role() = 'service_role');

create or replace function kaban_record_cron_tick(p_now timestamptz default now())
returns boolean language plpgsql security definer set search_path = public as $$
begin
  insert into kaban_runtime_health(project_id,last_cron_tick,updated_at)
  select project_id,p_now,p_now from kaban_projects where enabled=true
  on conflict(project_id) do update set last_cron_tick=excluded.last_cron_tick, updated_at=excluded.updated_at;
  return true;
end $$;

create or replace function kaban_database_size_bytes()
returns bigint language sql stable security definer set search_path = public as $$
  select pg_database_size(current_database())::bigint
$$;

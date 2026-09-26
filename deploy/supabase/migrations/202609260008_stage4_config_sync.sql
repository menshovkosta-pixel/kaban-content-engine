-- Stage 4 non-secret project/scheduler config mirror.
create or replace function public.kaban_sync_project_config(
  p_project_id text,
  p_name text,
  p_config_hash text,
  p_config_json jsonb,
  p_jobs jsonb
) returns boolean
language plpgsql
security definer
set search_path=public
as $$
declare
  item jsonb;
begin
  insert into public.kaban_projects(
    project_id,
    name,
    config_hash,
    config_json,
    enabled,
    updated_at
  )
  values(
    p_project_id,
    p_name,
    p_config_hash,
    coalesce(p_config_json,'{}'::jsonb),
    true,
    now()
  )
  on conflict(project_id) do update set
    name=excluded.name,
    config_hash=excluded.config_hash,
    config_json=excluded.config_json,
    enabled=true,
    updated_at=excluded.updated_at;

  for item in
    select value
    from jsonb_array_elements(coalesce(p_jobs,'[]'::jsonb))
  loop
    insert into public.kaban_scheduler_jobs(
      project_id,
      job_id,
      config_hash,
      handler,
      cron,
      timezone,
      params,
      misfire_grace_minutes,
      retry_policy,
      enabled,
      updated_at
    )
    values(
      p_project_id,
      item->>'job_id',
      encode(
        extensions.digest(
          item::text,
          'sha256'::text
        ),
        'hex'
      ),
      item->>'handler',
      item->>'cron',
      item->>'timezone',
      coalesce(item->'params','{}'::jsonb),
      coalesce((item->>'misfire_grace_minutes')::int,0),
      coalesce(item->'retry_policy','{}'::jsonb),
      coalesce((item->>'enabled')::boolean,true),
      now()
    )
    on conflict(project_id,job_id) do update set
      config_hash=excluded.config_hash,
      handler=excluded.handler,
      cron=excluded.cron,
      timezone=excluded.timezone,
      params=excluded.params,
      misfire_grace_minutes=excluded.misfire_grace_minutes,
      retry_policy=excluded.retry_policy,
      enabled=excluded.enabled,
      updated_at=excluded.updated_at;
  end loop;

  delete from public.kaban_scheduler_jobs j
  where j.project_id=p_project_id
    and not exists (
      select 1
      from jsonb_array_elements(
        coalesce(p_jobs,'[]'::jsonb)
      ) x
      where x->>'job_id'=j.job_id
    );

  return true;
end
$$;

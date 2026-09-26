-- Stage 4 dispatch/schedule safety.
-- Keeps GitHub dispatch identity separate from the execution lease and
-- preserves exactly-once scheduler slot identity.

alter table kaban_projects add column if not exists config_hash text;
alter table kaban_projects add column if not exists config_json jsonb not null default '{}'::jsonb;
alter table kaban_projects add column if not exists enabled boolean not null default true;
alter table kaban_projects add column if not exists updated_at timestamptz not null default now();

alter table kaban_executions add column if not exists job_id text;
alter table kaban_executions add column if not exists slot_id text;
create unique index if not exists kaban_execution_slot_uq
  on kaban_executions(project_id, job_id, slot_id)
  where job_id is not null and slot_id is not null;

create or replace function kaban_start_execution(p_execution_id uuid,p_owner text,p_lease_seconds int)
returns table(owner text, fence_token bigint, lease_expires_at timestamptz)
language plpgsql security definer set search_path=public as $$
begin
  return query
  update kaban_executions e set
    state='running',
    lease_owner=p_owner,
    fence_token=e.fence_token+1,
    lease_expires_at=now()+make_interval(secs=>p_lease_seconds),
    started_at=coalesce(e.started_at,now())
  where e.execution_id=p_execution_id
    and e.state in ('queued','dispatched','running')
    and (
      e.state in ('queued','dispatched')
      or e.lease_owner=p_owner
      or e.lease_expires_at is null
      or e.lease_expires_at<=now()
    )
  returning e.lease_owner,e.fence_token,e.lease_expires_at;
end $$;

create or replace function kaban_upsert_schedule_slot(
  p_project_id text, p_execution_id uuid, p_job_id text, p_slot_id text,
  p_operation text, p_content_key text, p_command_payload jsonb, p_requested_by text,
  p_idempotency_key text, p_scheduled_for timestamptz,
  p_misfire_deadline_at timestamptz, p_retry_deadline_at timestamptz,
  p_wait_condition jsonb default null
) returns jsonb language plpgsql security definer set search_path=public as $$
declare v_id uuid; v_created boolean := false;
begin
  select execution_id into v_id from kaban_executions
    where project_id=p_project_id and job_id=p_job_id and slot_id=p_slot_id;
  if v_id is null then
    begin
      insert into kaban_executions(
        execution_id,project_id,job_id,slot_id,operation,content_key,command_payload,
        requested_by,idempotency_key,scheduled_for,misfire_deadline_at,retry_deadline_at,wait_condition
      ) values (
        p_execution_id,p_project_id,p_job_id,p_slot_id,p_operation,p_content_key,
        coalesce(p_command_payload,'{}'::jsonb),p_requested_by,p_idempotency_key,
        p_scheduled_for,p_misfire_deadline_at,p_retry_deadline_at,p_wait_condition
      ) returning execution_id into v_id;
      v_created := true;
    exception when unique_violation then
      select execution_id into v_id from kaban_executions
        where project_id=p_project_id and job_id=p_job_id and slot_id=p_slot_id;
    end;
  end if;
  return jsonb_build_object('execution_id',v_id,'created',v_created);
end $$;

create or replace function kaban_claim_execution_for_dispatch(
  p_execution_id uuid,
  p_now timestamptz,
  p_dispatch_nonce uuid
)
returns uuid language plpgsql security definer set search_path=public as $$
declare v_id uuid;
begin
  update kaban_executions e set
    state='dispatched',
    dispatch_nonce=p_dispatch_nonce
  where e.execution_id=p_execution_id
    and e.state='queued'
    and coalesce(e.scheduled_for,p_now)<=p_now
    and (e.misfire_deadline_at is null or e.misfire_deadline_at>=p_now)
    and (e.retry_deadline_at is null or e.retry_deadline_at>=p_now)
  returning e.execution_id into v_id;
  return v_id;
end $$;

create or replace function kaban_schedule_dispatch_retry_safe(
  p_execution_id uuid,
  p_dispatch_nonce uuid,
  p_retry_at timestamptz,
  p_wait_condition jsonb default null
)
returns boolean language plpgsql security definer set search_path=public as $$
begin
  update kaban_executions set
    state='queued',
    scheduled_for=p_retry_at,
    wait_condition=coalesce(p_wait_condition,wait_condition),
    dispatch_nonce=null
  where execution_id=p_execution_id
    and state='dispatched'
    and dispatch_nonce=p_dispatch_nonce;
  return found;
end $$;

create or replace function kaban_expire_execution_if_due(
  p_execution_id uuid,
  p_now timestamptz
) returns boolean language plpgsql security definer set search_path=public as $$
begin
  update kaban_executions e set
    state='finished', outcome='expired', finished_at=p_now
  where e.execution_id=p_execution_id
    and e.state='queued'
    and (
      (e.misfire_deadline_at is not null and e.misfire_deadline_at<p_now)
      or (e.retry_deadline_at is not null and e.retry_deadline_at<p_now)
    );
  return found;
end $$;

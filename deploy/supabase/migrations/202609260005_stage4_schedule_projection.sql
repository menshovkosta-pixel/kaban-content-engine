-- Stage 4 schedule projection: canonical exactly-once slot identity lives on executions.
create or replace function kaban_upsert_schedule_slot(
  p_project_id text,
  p_execution_id uuid,
  p_job_id text,
  p_slot_id text,
  p_operation text,
  p_content_key text,
  p_content_set_id uuid,
  p_expected_version bigint,
  p_command_payload jsonb,
  p_requested_by text,
  p_idempotency_key text,
  p_scheduled_for timestamptz,
  p_misfire_deadline_at timestamptz,
  p_retry_deadline_at timestamptz,
  p_wait_condition jsonb default null
)
returns table(execution_id uuid, created boolean)
language plpgsql security definer set search_path=public as $$
declare
  v_execution_id uuid;
  v_created boolean := false;
begin
  if p_job_id is null or p_job_id='' or p_slot_id is null or p_slot_id='' then
    raise exception 'SCHEDULE_SLOT_IDENTITY_REQUIRED';
  end if;

  insert into kaban_executions(
    execution_id, project_id, job_id, slot_id, operation, content_key, content_set_id,
    expected_version, command_payload, requested_by, idempotency_key,
    scheduled_for, misfire_deadline_at, retry_deadline_at, wait_condition
  ) values (
    p_execution_id, p_project_id, p_job_id, p_slot_id, p_operation, p_content_key, p_content_set_id,
    p_expected_version, coalesce(p_command_payload,'{}'::jsonb), p_requested_by, p_idempotency_key,
    p_scheduled_for, p_misfire_deadline_at, p_retry_deadline_at, p_wait_condition
  )
  on conflict(project_id,job_id,slot_id) where job_id is not null and slot_id is not null do nothing
  returning kaban_executions.execution_id into v_execution_id;

  if v_execution_id is not null then
    v_created := true;
  else
    select e.execution_id into v_execution_id
      from kaban_executions e
      where e.project_id=p_project_id and e.job_id=p_job_id and e.slot_id=p_slot_id;
  end if;

  return query select v_execution_id, v_created;
end $$;

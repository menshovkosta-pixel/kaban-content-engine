create or replace function kaban_create_or_get_command(
  p_project_id text, p_operation text, p_content_key text, p_content_set_id uuid,
  p_expected_version bigint, p_command_payload jsonb, p_requested_by text,
  p_idempotency_key text, p_execution_id uuid default null, p_dispatch_nonce uuid default null,
  p_scheduled_for timestamptz default null, p_misfire_deadline_at timestamptz default null,
  p_retry_deadline_at timestamptz default null
) returns uuid language plpgsql security definer set search_path = public as $$
declare v_id uuid;
begin
  select execution_id into v_id from kaban_executions
   where project_id=p_project_id and idempotency_key=p_idempotency_key;
  if v_id is not null then return v_id; end if;
  insert into kaban_executions(execution_id,project_id,operation,content_key,content_set_id,expected_version,command_payload,requested_by,idempotency_key,dispatch_nonce,scheduled_for,misfire_deadline_at,retry_deadline_at)
  values(coalesce(p_execution_id,gen_random_uuid()),p_project_id,p_operation,p_content_key,p_content_set_id,p_expected_version,coalesce(p_command_payload,'{}'::jsonb),p_requested_by,p_idempotency_key,p_dispatch_nonce,p_scheduled_for,p_misfire_deadline_at,p_retry_deadline_at)
  returning execution_id into v_id;
  return v_id;
exception when unique_violation then
  select execution_id into v_id from kaban_executions where project_id=p_project_id and idempotency_key=p_idempotency_key;
  return v_id;
end $$;

create or replace function kaban_start_execution(p_execution_id uuid,p_owner text,p_lease_seconds int)
returns table(owner text,fence_token bigint,lease_expires_at timestamptz) language plpgsql security definer set search_path=public as $$
begin
  return query update kaban_executions e set
    state='running', lease_owner=p_owner, fence_token=e.fence_token+1,
    lease_expires_at=now()+make_interval(secs=>p_lease_seconds), started_at=coalesce(e.started_at,now())
  where e.execution_id=p_execution_id
    and e.state in ('queued','dispatched','running')
    and (
      e.state in ('queued','dispatched')
      or e.lease_expires_at is null
      or e.lease_expires_at<=now()
      or e.lease_owner=p_owner
    )
  returning e.lease_owner,e.fence_token,e.lease_expires_at;
end $$;

create or replace function kaban_renew_execution(p_execution_id uuid,p_owner text,p_fence_token bigint,p_lease_seconds int)
returns table(owner text,fence_token bigint,lease_expires_at timestamptz) language plpgsql security definer set search_path=public as $$
begin
  return query update kaban_executions e set lease_expires_at=now()+make_interval(secs=>p_lease_seconds)
  where e.execution_id=p_execution_id and e.lease_owner=p_owner and e.fence_token=p_fence_token and e.lease_expires_at>now()
  returning e.lease_owner,e.fence_token,e.lease_expires_at;
end $$;

create or replace function kaban_claim_resource(p_project_id text,p_resource_key text,p_execution_id uuid,p_owner text,p_lease_seconds int)
returns table(owner text,fence_token bigint,lease_expires_at timestamptz) language plpgsql security definer set search_path=public as $$
declare v_token bigint;
begin
  insert into kaban_resource_leases(project_id,resource_key,execution_id,owner,fence_token,lease_expires_at)
  values(p_project_id,p_resource_key,p_execution_id,p_owner,1,now()+make_interval(secs=>p_lease_seconds))
  on conflict(project_id,resource_key) do update set execution_id=excluded.execution_id, owner=excluded.owner,
    fence_token=kaban_resource_leases.fence_token+1, lease_expires_at=excluded.lease_expires_at
    where kaban_resource_leases.lease_expires_at<=now() or kaban_resource_leases.execution_id=p_execution_id
  returning kaban_resource_leases.owner,kaban_resource_leases.fence_token,kaban_resource_leases.lease_expires_at
  into owner,fence_token,lease_expires_at;
  return next;
end $$;

create or replace function kaban_renew_resource(p_project_id text,p_resource_key text,p_execution_id uuid,p_owner text,p_fence_token bigint,p_lease_seconds int)
returns table(owner text,fence_token bigint,lease_expires_at timestamptz) language plpgsql security definer set search_path=public as $$
begin
 return query update kaban_resource_leases r set lease_expires_at=now()+make_interval(secs=>p_lease_seconds)
  where r.project_id=p_project_id and r.resource_key=p_resource_key and r.execution_id=p_execution_id and r.owner=p_owner and r.fence_token=p_fence_token and r.lease_expires_at>now()
  returning r.owner,r.fence_token,r.lease_expires_at;
end $$;

create or replace function kaban_release_resource(p_project_id text,p_resource_key text,p_execution_id uuid,p_owner text,p_fence_token bigint)
returns boolean language sql security definer set search_path=public as $$
 delete from kaban_resource_leases where project_id=p_project_id and resource_key=p_resource_key and execution_id=p_execution_id and owner=p_owner and fence_token=p_fence_token returning true
$$;

create or replace function kaban_finish_execution(p_execution_id uuid,p_fence_token bigint,p_outcome text,p_error jsonb default null)
returns boolean language plpgsql security definer set search_path=public as $$
begin
 update kaban_executions set state='finished',outcome=p_outcome,error=p_error,finished_at=now(),lease_expires_at=null
 where execution_id=p_execution_id and fence_token=p_fence_token;
 return found;
end $$;

create or replace function kaban_commit_content_revision(
 p_project_id text,p_execution_id uuid,p_owner text,p_execution_fence bigint,
 p_resource_key text,p_resource_fence bigint,p_content_set_id uuid,p_expected_version bigint,
 p_proposed_revision_id uuid,p_payload jsonb,p_content_hash text,p_artifacts jsonb default '[]'::jsonb
) returns table(content_set_id uuid,revision_id uuid,version bigint) language plpgsql security definer set search_path=public as $$
declare v_revision_no bigint; v_version bigint;
begin
 if not exists(select 1 from kaban_executions e where e.execution_id=p_execution_id and e.project_id=p_project_id and e.lease_owner=p_owner and e.fence_token=p_execution_fence and e.lease_expires_at>now()) then
   raise exception 'LEASE_CONFLICT';
 end if;
 if p_resource_key is not null and not exists(select 1 from kaban_resource_leases r where r.project_id=p_project_id and r.resource_key=p_resource_key and r.execution_id=p_execution_id and r.owner=p_owner and r.fence_token=p_resource_fence and r.lease_expires_at>now()) then
   raise exception 'LEASE_CONFLICT';
 end if;
 select c.version,coalesce(max(r.revision_no),0)+1 into v_version,v_revision_no
 from kaban_content_sets c left join kaban_content_revisions r on r.project_id=c.project_id and r.content_set_id=c.content_set_id
 where c.project_id=p_project_id and c.content_set_id=p_content_set_id group by c.version for update of c;
 if v_version is distinct from p_expected_version then raise exception 'VERSION_CONFLICT'; end if;
 insert into kaban_content_revisions(revision_id,project_id,content_set_id,revision_no,payload,content_hash)
 values(p_proposed_revision_id,p_project_id,p_content_set_id,v_revision_no,p_payload,p_content_hash);
 update kaban_content_sets set current_revision_id=p_proposed_revision_id,version=version+1,updated_at=now()
 where project_id=p_project_id and kaban_content_sets.content_set_id=p_content_set_id returning kaban_content_sets.version into v_version;
 return query select p_content_set_id,p_proposed_revision_id,v_version;
end $$;

create or replace function kaban_record_approval(p_project_id text,p_content_set_id uuid,p_revision_id uuid,p_content_hash text,p_action text,p_actor text)
returns uuid language plpgsql security definer set search_path=public as $$
declare v_id uuid;
begin
 insert into kaban_approvals(project_id,content_set_id,revision_id,content_hash,action,actor)
 values(p_project_id,p_content_set_id,p_revision_id,p_content_hash,p_action,p_actor) returning approval_id into v_id;
 if p_action='approve' then update kaban_content_sets set approved_revision_id=p_revision_id,approved_content_hash=p_content_hash where project_id=p_project_id and content_set_id=p_content_set_id;
 elsif p_action='return_to_draft' then update kaban_content_sets set approved_revision_id=null,approved_content_hash=null where project_id=p_project_id and content_set_id=p_content_set_id; end if;
 return v_id;
end $$;

create or replace function kaban_create_or_get_publication_run(p_project_id text,p_publication_key text,p_content_set_id uuid,p_revision_id uuid,p_execution_id uuid)
returns uuid language plpgsql security definer set search_path=public as $$
declare v_id uuid;
begin
 select publication_run_id into v_id from kaban_publication_runs where project_id=p_project_id and publication_key=p_publication_key;
 if v_id is not null then return v_id; end if;
 insert into kaban_publication_runs(project_id,publication_key,content_set_id,revision_id,execution_id)
 values(p_project_id,p_publication_key,p_content_set_id,p_revision_id,p_execution_id) returning publication_run_id into v_id;
 return v_id;
exception when unique_violation then
 select publication_run_id into v_id from kaban_publication_runs where project_id=p_project_id and publication_key=p_publication_key; return v_id;
end $$;

create or replace function kaban_transition_publication_step(
 p_project_id text,p_publication_run_id uuid,p_step_key text,p_request_fingerprint text,
 p_from_state text,p_to_state text,p_external_ids jsonb default null,p_error jsonb default null,
 p_execution_id uuid default null,p_owner text default null,p_fence_token bigint default null
) returns table(state text,request_fingerprint text,external_ids jsonb) language plpgsql security definer set search_path=public as $$
begin
 if p_execution_id is not null and not exists(select 1 from kaban_executions e where e.execution_id=p_execution_id and e.project_id=p_project_id and e.lease_owner=p_owner and e.fence_token=p_fence_token and e.lease_expires_at>now()) then raise exception 'LEASE_CONFLICT'; end if;
 insert into kaban_publication_steps(project_id,publication_run_id,step_key,state,request_fingerprint,external_ids,last_error,execution_fence)
 values(p_project_id,p_publication_run_id,p_step_key,p_to_state,p_request_fingerprint,p_external_ids,p_error,p_fence_token)
 on conflict(project_id,publication_run_id,step_key) do update set state=excluded.state,external_ids=excluded.external_ids,last_error=excluded.last_error,execution_fence=excluded.execution_fence,updated_at=now()
 where kaban_publication_steps.state=p_from_state and kaban_publication_steps.request_fingerprint=p_request_fingerprint;
 return query select s.state,s.request_fingerprint,s.external_ids from kaban_publication_steps s where s.project_id=p_project_id and s.publication_run_id=p_publication_run_id and s.step_key=p_step_key;
end $$;

create or replace function kaban_mark_stale_sending_unknown(p_project_id text,p_older_than timestamptz)
returns bigint language plpgsql security definer set search_path=public as $$
declare n bigint;
begin update kaban_publication_steps set state='unknown_delivery',updated_at=now() where project_id=p_project_id and state='sending' and updated_at<p_older_than; get diagnostics n=row_count; return n; end $$;

create or replace function kaban_claim_due_execution(p_now timestamptz,p_worker_owner text,p_lease_seconds int)
returns uuid language plpgsql security definer set search_path=public as $$
declare v_id uuid;
begin
 select execution_id into v_id from kaban_executions where state='queued' and coalesce(scheduled_for,p_now)<=p_now and (retry_deadline_at is null or retry_deadline_at>=p_now) order by scheduled_for nulls first limit 1 for update skip locked;
 if v_id is not null then perform kaban_start_execution(v_id,p_worker_owner,p_lease_seconds); end if; return v_id;
end $$;

create or replace function kaban_schedule_dispatch_retry(p_execution_id uuid,p_retry_at timestamptz,p_wait_condition jsonb default null)
returns boolean language plpgsql security definer set search_path=public as $$
begin update kaban_executions set state='queued',scheduled_for=p_retry_at,wait_condition=p_wait_condition,lease_owner=null,lease_expires_at=null where execution_id=p_execution_id; return found; end $$;

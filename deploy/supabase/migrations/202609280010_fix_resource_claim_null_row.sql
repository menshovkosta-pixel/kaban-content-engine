-- Fix resource lease rejection returning a synthetic NULL row.
-- When ON CONFLICT ... DO UPDATE ... WHERE rejects the claim,
-- the RPC must return zero rows so the client can raise LeaseConflict.

create or replace function kaban_claim_resource(
  p_project_id text,
  p_resource_key text,
  p_execution_id uuid,
  p_owner text,
  p_lease_seconds int
)
returns table(
  owner text,
  fence_token bigint,
  lease_expires_at timestamptz
)
language plpgsql
security definer
set search_path=public
as $$
begin
  insert into kaban_resource_leases(
    project_id,
    resource_key,
    execution_id,
    owner,
    fence_token,
    lease_expires_at
  )
  values(
    p_project_id,
    p_resource_key,
    p_execution_id,
    p_owner,
    1,
    now() + make_interval(secs=>p_lease_seconds)
  )
  on conflict(project_id,resource_key) do update set
    execution_id=excluded.execution_id,
    owner=excluded.owner,
    fence_token=kaban_resource_leases.fence_token+1,
    lease_expires_at=excluded.lease_expires_at
  where
    kaban_resource_leases.lease_expires_at<=now()
    or kaban_resource_leases.execution_id=p_execution_id
  returning
    kaban_resource_leases.owner,
    kaban_resource_leases.fence_token,
    kaban_resource_leases.lease_expires_at
  into owner,fence_token,lease_expires_at;

  if owner is null then
    return;
  end if;

  return next;
end $$;
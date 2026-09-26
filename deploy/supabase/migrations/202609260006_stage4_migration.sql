-- Stage 4 restartable local -> cloud migration. Provider-generic; Project mapping stays in adapters.
create table if not exists kaban_migration_identities (
  project_id text not null references kaban_projects(project_id) on delete cascade,
  source_identity text not null,
  source_path text not null,
  source_sha256 text not null,
  destination_type text not null,
  destination_id text,
  committed_at timestamptz not null default now(),
  primary key(project_id, source_identity)
);
alter table kaban_migration_identities enable row level security;

alter table kaban_publication_runs add column if not exists metadata jsonb not null default '{}'::jsonb;

create or replace function kaban_ensure_migration_project(p_project_id text)
returns void language plpgsql security definer set search_path=public as $$
begin
  insert into kaban_projects(project_id,name) values(p_project_id,p_project_id)
  on conflict(project_id) do nothing;
end $$;

create or replace function kaban_resolve_migration_content_set(
  p_project_id text,p_content_key text,p_proposed_content_set_id uuid)
returns table(content_set_id uuid) language plpgsql security definer set search_path=public as $$
declare v_id uuid;
begin
  select cs.content_set_id into v_id from kaban_content_sets cs
    where cs.project_id=p_project_id and cs.content_key=p_content_key;
  if v_id is null then
    insert into kaban_content_sets(content_set_id,project_id,content_key)
      values(p_proposed_content_set_id,p_project_id,p_content_key)
      on conflict(project_id,content_key) do nothing;
    select cs.content_set_id into v_id from kaban_content_sets cs
      where cs.project_id=p_project_id and cs.content_key=p_content_key;
  end if;
  return query select v_id;
end $$;

create or replace function kaban_import_migration_bundle(
  p_project_id text,
  p_content_key text,
  p_content_set_id uuid,
  p_revision_id uuid,
  p_source_identity text,
  p_source_path text,
  p_source_sha256 text,
  p_content_date date,
  p_locale text,
  p_payload jsonb,
  p_content_hash text,
  p_approved boolean,
  p_status jsonb,
  p_publication jsonb,
  p_artifacts jsonb,
  p_companion_identities jsonb default '[]'::jsonb
) returns table(content_set_id uuid,revision_id uuid)
language plpgsql security definer set search_path=public as $$
declare
  v_existing text;
  v_revision_no bigint;
  v_artifact jsonb;
  v_companion jsonb;
  v_publication_key text;
begin
  select destination_id into v_existing from kaban_migration_identities
    where project_id=p_project_id and source_identity=p_source_identity;
  if v_existing is not null then
    return query select p_content_set_id, v_existing::uuid;
    return;
  end if;

  if not exists(select 1 from kaban_content_sets where project_id=p_project_id and content_set_id=p_content_set_id) then
    raise exception 'migration content_set not resolved';
  end if;

  select coalesce(max(cr.revision_no),0)+1 into v_revision_no
    from kaban_content_revisions cr where cr.project_id=p_project_id and cr.content_set_id=p_content_set_id;

  insert into kaban_content_revisions(revision_id,project_id,content_set_id,revision_no,payload,content_hash)
    values(p_revision_id,p_project_id,p_content_set_id,v_revision_no,p_payload,p_content_hash)
    on conflict(project_id,revision_id) do nothing;

  update kaban_content_sets set
    content_date=coalesce(p_content_date,content_date), locale=coalesce(p_locale,locale),
    current_revision_id=p_revision_id, version=greatest(version,v_revision_no), updated_at=now(),
    approved_revision_id=case when p_approved then p_revision_id else approved_revision_id end,
    approved_content_hash=case when p_approved then p_content_hash else approved_content_hash end
    where project_id=p_project_id and content_set_id=p_content_set_id;

  if p_approved then
    insert into kaban_approvals(project_id,content_set_id,revision_id,content_hash,action,actor)
      select p_project_id,p_content_set_id,p_revision_id,p_content_hash,'approve','migration'
      where not exists(select 1 from kaban_approvals where project_id=p_project_id and revision_id=p_revision_id and action='approve');
  end if;

  for v_artifact in select value from jsonb_array_elements(coalesce(p_artifacts,'[]'::jsonb)) loop
    insert into kaban_artifacts(project_id,content_set_id,revision_id,kind,logical_name,r2_key,sha256,size_bytes,mime_type,metadata)
      values(p_project_id,p_content_set_id,p_revision_id,v_artifact->>'kind',v_artifact->>'logical_name',v_artifact->>'r2_key',v_artifact->>'sha256',
             (v_artifact->>'size_bytes')::bigint,coalesce(v_artifact->>'mime_type','application/octet-stream'),jsonb_build_object('migration',true))
      on conflict(project_id,r2_key) do nothing;
    insert into kaban_migration_identities(project_id,source_identity,source_path,source_sha256,destination_type,destination_id)
      values(p_project_id,v_artifact->>'source_identity',coalesce(v_artifact->>'source_path',v_artifact->>'logical_name'),v_artifact->>'sha256','r2',v_artifact->>'r2_key')
      on conflict(project_id,source_identity) do nothing;
  end loop;

  if p_publication is not null and p_publication <> '{}'::jsonb then
    v_publication_key := 'migration:' || p_content_key || ':' || p_source_identity;
    insert into kaban_publication_runs(project_id,publication_key,content_set_id,revision_id,state,metadata)
      values(p_project_id,v_publication_key,p_content_set_id,p_revision_id,coalesce(nullif(p_publication->>'state',''),'pending'),p_publication)
      on conflict(project_id,publication_key) do nothing;
  end if;

  insert into kaban_migration_identities(project_id,source_identity,source_path,source_sha256,destination_type,destination_id)
    values(p_project_id,p_source_identity,p_source_path,p_source_sha256,'postgresql',p_revision_id::text)
    on conflict(project_id,source_identity) do nothing;

  for v_companion in select value from jsonb_array_elements(coalesce(p_companion_identities,'[]'::jsonb)) loop
    insert into kaban_migration_identities(project_id,source_identity,source_path,source_sha256,destination_type,destination_id)
      values(p_project_id,v_companion->>'source_identity',v_companion->>'source_path',v_companion->>'source_sha256','postgresql',p_content_set_id::text)
      on conflict(project_id,source_identity) do nothing;
  end loop;

  return query select p_content_set_id,p_revision_id;
end $$;

create or replace function kaban_import_migration_setting(
  p_project_id text,p_setting_key text,p_value jsonb,p_source_identity text,p_source_path text,p_source_sha256 text)
returns void language plpgsql security definer set search_path=public as $$
begin
  if exists(select 1 from kaban_migration_identities where project_id=p_project_id and source_identity=p_source_identity) then return; end if;
  insert into kaban_project_settings(project_id,setting_key,value) values(p_project_id,p_setting_key,p_value)
    on conflict(project_id,setting_key) do update set value=excluded.value,version=kaban_project_settings.version+1,updated_at=now();
  insert into kaban_migration_identities(project_id,source_identity,source_path,source_sha256,destination_type,destination_id)
    values(p_project_id,p_source_identity,p_source_path,p_source_sha256,'postgresql',p_setting_key)
    on conflict(project_id,source_identity) do nothing;
end $$;

create or replace function kaban_import_migration_history(
  p_project_id text,p_source_identity text,p_source_path text,p_source_sha256 text,p_payload jsonb)
returns void language plpgsql security definer set search_path=public as $$
declare v_execution_id uuid; v_kind text; v_state text; v_operation text;
begin
  if exists(select 1 from kaban_migration_identities where project_id=p_project_id and source_identity=p_source_identity) then return; end if;
  v_execution_id := gen_random_uuid();
  v_kind := coalesce(p_payload->>'kind','migration_history');
  v_state := coalesce(p_payload#>>'{data,state}','finished');
  v_operation := coalesce(p_payload#>>'{data,operation}',v_kind);
  insert into kaban_executions(execution_id,project_id,operation,trigger,state,outcome,requested_by,command_payload,created_at,finished_at)
    values(v_execution_id,p_project_id,v_operation,'migration','finished',v_state,'migration',p_payload,now(),now());
  insert into kaban_execution_events(project_id,execution_id,event_type,payload)
    values(p_project_id,v_execution_id,'migration_import',p_payload);
  insert into kaban_migration_identities(project_id,source_identity,source_path,source_sha256,destination_type,destination_id)
    values(p_project_id,p_source_identity,p_source_path,p_source_sha256,'postgresql',v_execution_id::text);
end $$;

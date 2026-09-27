-- Stage 4 runtime canonical commit hardening.
--
-- Fixes:
-- 1. first content_set creation happens inside canonical commit-last;
-- 2. exact resource lease identity is fenced;
-- 3. uploaded R2 artifact metadata is registered atomically.

drop function if exists kaban_commit_content_revision(
    text,
    uuid,
    text,
    bigint,
    text,
    bigint,
    uuid,
    bigint,
    uuid,
    jsonb,
    text,
    jsonb
);

create or replace function kaban_commit_content_revision(
    p_project_id text,
    p_execution_id uuid,
    p_owner text,
    p_execution_fence bigint,
    p_resource_key text,
    p_resource_fence bigint,
    p_content_key text,
    p_content_set_id uuid,
    p_expected_version bigint,
    p_proposed_revision_id uuid,
    p_payload jsonb,
    p_content_hash text,
    p_artifacts jsonb default '[]'::jsonb
)
returns table(
    content_set_id uuid,
    revision_id uuid,
    version bigint
)
language plpgsql
security definer
set search_path=public
as $$
declare
    v_revision_no bigint;
    v_version bigint;
    v_existing_content_set_id uuid;
    v_existing_content_key text;
    v_artifact jsonb;
begin
    -- Execution lease/fence.
    if not exists(
        select 1
        from kaban_executions e
        where e.execution_id = p_execution_id
          and e.project_id = p_project_id
          and e.lease_owner = p_owner
          and e.fence_token = p_execution_fence
          and e.lease_expires_at > now()
    ) then
        raise exception 'LEASE_CONFLICT';
    end if;

    -- Resource lease must be checked using exactly the key claimed by runner.
    if p_resource_key is not null and not exists(
        select 1
        from kaban_resource_leases r
        where r.project_id = p_project_id
          and r.resource_key = p_resource_key
          and r.execution_id = p_execution_id
          and r.owner = p_owner
          and r.fence_token = p_resource_fence
          and r.lease_expires_at > now()
    ) then
        raise exception 'LEASE_CONFLICT';
    end if;

    -- Existing canonical content set.
    select
        c.content_set_id,
        c.content_key,
        c.version
    into
        v_existing_content_set_id,
        v_existing_content_key,
        v_version
    from kaban_content_sets c
    where c.project_id = p_project_id
      and c.content_set_id = p_content_set_id
    for update;

    -- First generation: create the canonical content set in this transaction.
    if v_existing_content_set_id is null then
        if p_content_key is null or btrim(p_content_key) = '' then
            raise exception 'VERSION_CONFLICT';
        end if;

        insert into kaban_content_sets(
            content_set_id,
            project_id,
            content_key,
            content_date,
            locale,
            version
        )
        values(
            p_content_set_id,
            p_project_id,
            p_content_key,
            nullif(p_payload->>'iso_date', '')::date,
            nullif(p_payload->>'language', ''),
            0
        )
        on conflict(project_id, content_key) do nothing;

        select
            c.content_set_id,
            c.content_key,
            c.version
        into
            v_existing_content_set_id,
            v_existing_content_key,
            v_version
        from kaban_content_sets c
        where c.project_id = p_project_id
          and c.content_key = p_content_key
        for update;
    end if;

    -- A concurrent creator must never redirect already-uploaded R2 objects
    -- to another canonical content_set UUID.
    if v_existing_content_set_id is distinct from p_content_set_id then
        raise exception 'VERSION_CONFLICT';
    end if;

    if p_content_key is not null
       and v_existing_content_key is distinct from p_content_key then
        raise exception 'VERSION_CONFLICT';
    end if;

    if v_version is distinct from p_expected_version then
        raise exception 'VERSION_CONFLICT';
    end if;

    select coalesce(max(r.revision_no), 0) + 1
    into v_revision_no
    from kaban_content_revisions r
    where r.project_id = p_project_id
      and r.content_set_id = p_content_set_id;

    insert into kaban_content_revisions(
        revision_id,
        project_id,
        content_set_id,
        revision_no,
        payload,
        content_hash
    )
    values(
        p_proposed_revision_id,
        p_project_id,
        p_content_set_id,
        v_revision_no,
        p_payload,
        p_content_hash
    );

    update kaban_content_sets
    set
        current_revision_id = p_proposed_revision_id,
        version = kaban_content_sets.version + 1,
        content_date = coalesce(
            nullif(p_payload->>'iso_date', '')::date,
            content_date
        ),
        locale = coalesce(
            nullif(p_payload->>'language', ''),
            locale
        ),
        updated_at = now()
    where project_id = p_project_id
      and kaban_content_sets.content_set_id = p_content_set_id
    returning kaban_content_sets.version
    into v_version;

    -- R2 objects already exist. Their metadata becomes canonical in the
    -- same transaction as content_set/revision.
    for v_artifact in
        select value
        from jsonb_array_elements(
            coalesce(p_artifacts, '[]'::jsonb)
        )
    loop
        insert into kaban_artifacts(
            project_id,
            content_set_id,
            revision_id,
            kind,
            logical_name,
            r2_key,
            sha256,
            size_bytes,
            mime_type,
            metadata
        )
        values(
            p_project_id,
            p_content_set_id,
            p_proposed_revision_id,
            v_artifact->>'kind',
            v_artifact->>'logical_name',
            v_artifact->>'r2_key',
            v_artifact->>'sha256',
            (v_artifact->>'size_bytes')::bigint,
            coalesce(
                v_artifact->>'mime_type',
                'application/octet-stream'
            ),
            coalesce(
                v_artifact->'metadata',
                '{}'::jsonb
            )
        )
        on conflict(project_id, r2_key) do nothing;
    end loop;

    return query
    select
        p_content_set_id,
        p_proposed_revision_id,
        v_version;
end
$$;

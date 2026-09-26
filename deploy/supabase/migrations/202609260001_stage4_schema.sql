create extension if not exists pgcrypto;

create table if not exists kaban_projects (
  project_id text primary key,
  name text not null,
  created_at timestamptz not null default now()
);

create table if not exists kaban_channels (
  channel_id uuid primary key default gen_random_uuid(),
  project_id text not null references kaban_projects(project_id) on delete cascade,
  kind text not null,
  locale text,
  config jsonb not null default '{}'::jsonb,
  unique(project_id, channel_id)
);

create table if not exists kaban_project_settings (
  setting_id uuid primary key default gen_random_uuid(),
  project_id text not null references kaban_projects(project_id) on delete cascade,
  setting_key text not null,
  value jsonb not null default '{}'::jsonb,
  version bigint not null default 1,
  updated_at timestamptz not null default now(),
  unique(project_id, setting_key),
  unique(project_id, setting_id)
);

create table if not exists kaban_content_sets (
  content_set_id uuid primary key default gen_random_uuid(),
  project_id text not null references kaban_projects(project_id) on delete cascade,
  content_key text not null,
  content_date date,
  locale text,
  version bigint not null default 0,
  current_revision_id uuid,
  approved_revision_id uuid,
  approved_content_hash text,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  unique(project_id, content_key),
  unique(project_id, content_set_id)
);

create table if not exists kaban_content_revisions (
  revision_id uuid primary key default gen_random_uuid(),
  project_id text not null,
  content_set_id uuid not null,
  revision_no bigint not null,
  payload jsonb not null,
  content_hash text not null,
  created_at timestamptz not null default now(),
  unique(project_id, revision_id),
  unique(project_id, content_set_id, revision_no),
  foreign key(project_id, content_set_id) references kaban_content_sets(project_id, content_set_id) on delete cascade
);

alter table kaban_content_sets
  drop constraint if exists kaban_content_sets_current_revision_fk,
  add constraint kaban_content_sets_current_revision_fk foreign key(project_id, current_revision_id)
    references kaban_content_revisions(project_id, revision_id) deferrable initially deferred;
alter table kaban_content_sets
  drop constraint if exists kaban_content_sets_approved_revision_fk,
  add constraint kaban_content_sets_approved_revision_fk foreign key(project_id, approved_revision_id)
    references kaban_content_revisions(project_id, revision_id) deferrable initially deferred;

create table if not exists kaban_approvals (
  approval_id uuid primary key default gen_random_uuid(),
  project_id text not null,
  content_set_id uuid not null,
  revision_id uuid not null,
  content_hash text not null,
  action text not null,
  actor text,
  created_at timestamptz not null default now(),
  unique(project_id, approval_id),
  foreign key(project_id, content_set_id) references kaban_content_sets(project_id, content_set_id) on delete cascade,
  foreign key(project_id, revision_id) references kaban_content_revisions(project_id, revision_id)
);

create table if not exists kaban_artifacts (
  artifact_id uuid primary key default gen_random_uuid(),
  project_id text not null,
  content_set_id uuid,
  revision_id uuid,
  kind text not null,
  logical_name text not null,
  r2_key text not null,
  sha256 text not null,
  size_bytes bigint not null,
  mime_type text not null,
  metadata jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now(),
  unique(project_id, artifact_id),
  unique(project_id, r2_key),
  foreign key(project_id, content_set_id) references kaban_content_sets(project_id, content_set_id) on delete cascade,
  foreign key(project_id, revision_id) references kaban_content_revisions(project_id, revision_id)
);

create table if not exists kaban_scheduler_jobs (
  project_id text not null references kaban_projects(project_id) on delete cascade,
  job_id text not null,
  config_hash text not null,
  handler text not null,
  cron text not null,
  timezone text not null,
  params jsonb not null default '{}'::jsonb,
  misfire_grace_minutes integer not null default 0,
  retry_policy jsonb not null default '{}'::jsonb,
  enabled boolean not null default true,
  updated_at timestamptz not null default now(),
  primary key(project_id, job_id)
);

create table if not exists kaban_executions (
  execution_id uuid primary key default gen_random_uuid(),
  project_id text not null references kaban_projects(project_id) on delete cascade,
  job_id text,
  slot_id text,
  operation text not null,
  trigger text,
  content_key text,
  content_set_id uuid,
  expected_version bigint,
  command_payload jsonb not null default '{}'::jsonb,
  requested_by text,
  idempotency_key text,
  dispatch_nonce uuid,
  state text not null default 'queued',
  lease_owner text,
  fence_token bigint not null default 0,
  lease_expires_at timestamptz,
  outcome text,
  error jsonb,
  wait_condition jsonb,
  scheduled_for timestamptz,
  misfire_deadline_at timestamptz,
  retry_deadline_at timestamptz,
  created_at timestamptz not null default now(),
  started_at timestamptz,
  finished_at timestamptz,
  unique(project_id, execution_id),
  foreign key(project_id, content_set_id) references kaban_content_sets(project_id, content_set_id)
);
create unique index if not exists kaban_execution_idempotency_uq
  on kaban_executions(project_id, idempotency_key) where idempotency_key is not null;
create unique index if not exists kaban_execution_slot_uq
  on kaban_executions(project_id, job_id, slot_id) where job_id is not null and slot_id is not null;

create table if not exists kaban_resource_leases (
  project_id text not null references kaban_projects(project_id) on delete cascade,
  resource_key text not null,
  execution_id uuid not null,
  owner text not null,
  fence_token bigint not null default 0,
  lease_expires_at timestamptz not null,
  primary key(project_id, resource_key),
  foreign key(project_id, execution_id) references kaban_executions(project_id, execution_id) on delete cascade
);

create table if not exists kaban_execution_events (
  event_id bigserial primary key,
  project_id text not null,
  execution_id uuid not null,
  event_type text not null,
  payload jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now(),
  foreign key(project_id, execution_id) references kaban_executions(project_id, execution_id) on delete cascade
);

create table if not exists kaban_publication_runs (
  publication_run_id uuid primary key default gen_random_uuid(),
  project_id text not null references kaban_projects(project_id) on delete cascade,
  publication_key text not null,
  content_set_id uuid,
  revision_id uuid,
  execution_id uuid,
  state text not null default 'pending',
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  unique(project_id, publication_run_id),
  unique(project_id, publication_key),
  foreign key(project_id, content_set_id) references kaban_content_sets(project_id, content_set_id),
  foreign key(project_id, revision_id) references kaban_content_revisions(project_id, revision_id),
  foreign key(project_id, execution_id) references kaban_executions(project_id, execution_id)
);

create table if not exists kaban_publication_steps (
  publication_step_id uuid primary key default gen_random_uuid(),
  project_id text not null,
  publication_run_id uuid not null,
  step_key text not null,
  state text not null default 'pending',
  request_fingerprint text not null,
  external_ids jsonb,
  last_error jsonb,
  execution_fence bigint,
  updated_at timestamptz not null default now(),
  unique(project_id, publication_step_id),
  unique(project_id, publication_run_id, step_key),
  foreign key(project_id, publication_run_id) references kaban_publication_runs(project_id, publication_run_id) on delete cascade
);

create table if not exists kaban_usage_samples (
  sample_id bigserial primary key,
  project_id text references kaban_projects(project_id) on delete cascade,
  metric text not null,
  value numeric not null,
  unit text not null,
  metadata jsonb not null default '{}'::jsonb,
  sampled_at timestamptz not null default now()
);

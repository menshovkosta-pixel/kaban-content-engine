do $$
declare t text;
begin
  foreach t in array array[
    'kaban_projects','kaban_channels','kaban_project_settings','kaban_content_sets',
    'kaban_content_revisions','kaban_approvals','kaban_artifacts','kaban_scheduler_jobs',
    'kaban_executions','kaban_resource_leases','kaban_execution_events','kaban_publication_runs',
    'kaban_publication_steps','kaban_usage_samples'
  ] loop
    execute format('alter table %I enable row level security', t);
    execute format('revoke insert, update, delete on table %I from anon, authenticated', t);
  end loop;
end $$;

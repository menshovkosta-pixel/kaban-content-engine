import type { CommandInput, DueExecution, Env, OrchestrationStore, ReadStore } from "./types.ts";

function query(params: Record<string, string>): string {
  return new URLSearchParams(params).toString();
}

export class SupabaseWorkerStore implements ReadStore, OrchestrationStore {
  private readonly env: Env;
  private readonly fetcher: typeof fetch;

  constructor(env: Env, fetcher: typeof fetch = fetch) {
    this.env = env;
    this.fetcher = (...args) => fetcher(...args);
  }

  private async request(path: string, init: RequestInit = {}): Promise<any> {
    const serviceKey = this.env.SUPABASE_SERVICE_KEY;

    const authHeaders: Record<string, string> = serviceKey.startsWith("sb_secret_")
      ? {}
      : { Authorization: `Bearer ${serviceKey}` };

    const response = await this.fetcher(
      `${this.env.SUPABASE_URL.replace(/\/$/, "")}/rest/v1/${path}`,
      {
        ...init,
        headers: {
          apikey: serviceKey,
          ...authHeaders,
          Accept: "application/json",
          "Content-Type": "application/json",
          Prefer: "return=representation",
          ...(init.headers ?? {}),
        },
      },
    );

    if (!response.ok) {
      throw new Error(
        `Supabase HTTP ${response.status}: ${(await response.text()).slice(0, 300)}`,
      );
    }

    if (response.status === 204) {
      return null;
    }

    const text = await response.text();
    return text ? JSON.parse(text) : null;
  }

  private async rpc(name: string, payload: Record<string, unknown>): Promise<any> {
    return this.request(`rpc/${name}`, {
      method: "POST",
      body: JSON.stringify(payload),
    });
  }

  async listProjects(): Promise<unknown> {
    return this.request(
      `kaban_projects?${query({
        select: "project_id,name,enabled",
        order: "project_id.asc",
      })}`,
    );
  }

  async getContent(projectId: string, contentKey: string): Promise<unknown> {
    const sets = await this.request(
      `kaban_content_sets?${query({
        project_id: `eq.${projectId}`,
        content_key: `eq.${contentKey}`,
        select: "*",
        limit: "1",
      })}`,
    );

    const contentSet = Array.isArray(sets) ? sets[0] ?? null : null;

    if (!contentSet) {
      return {
        content_set: null,
        revision: null,
        artifacts: [],
      };
    }

    let revision = null;

    if (contentSet.current_revision_id) {
      const revisions = await this.request(
        `kaban_content_revisions?${query({
          project_id: `eq.${projectId}`,
          revision_id: `eq.${contentSet.current_revision_id}`,
          select: "*",
          limit: "1",
        })}`,
      );

      revision = Array.isArray(revisions) ? revisions[0] ?? null : null;
    }

    const artifacts = await this.request(
      `kaban_artifacts?${query({
        project_id: `eq.${projectId}`,
        content_set_id: `eq.${contentSet.content_set_id}`,
        select:
          "artifact_id,project_id,revision_id,kind,logical_name,r2_key,sha256,size_bytes,mime_type",
        order: "logical_name.asc",
      })}`,
    );

    const runs = await this.request(
      `kaban_publication_runs?${query({
        project_id: `eq.${projectId}`,
        content_set_id: `eq.${contentSet.content_set_id}`,
        select: "*",
        order: "created_at.desc",
        limit: "1",
      })}`,
    );

    const run = Array.isArray(runs) ? runs[0] ?? null : null;

    let steps: unknown[] = [];

    if (run?.publication_run_id) {
      const rows = await this.request(
        `kaban_publication_steps?${query({
          project_id: `eq.${projectId}`,
          publication_run_id: `eq.${run.publication_run_id}`,
          select: "*",
          order: "step_key.asc",
        })}`,
      );

      steps = Array.isArray(rows) ? rows : [];
    }

    return {
      content_set: contentSet,
      revision,
      artifacts: Array.isArray(artifacts) ? artifacts : [],
      publication: {
        run,
        steps,
      },
    };
  }

  async getExecution(projectId: string, executionId: string): Promise<unknown> {
    return this.request(
      `kaban_executions?${query({
        project_id: `eq.${projectId}`,
        execution_id: `eq.${executionId}`,
        select: "*",
        limit: "1",
      })}`,
    );
  }

  async history(projectId: string): Promise<unknown> {
    return this.request(
      `kaban_execution_events?${query({
        project_id: `eq.${projectId}`,
        select: "*",
        order: "created_at.desc",
        limit: "100",
      })}`,
    );
  }

  async usage(projectId: string): Promise<unknown> {
    const rows = await this.request(
      `kaban_usage_samples?${query({
        project_id: `eq.${projectId}`,
        select: "*",
        order: "sampled_at.desc",
        limit: "100",
      })}`,
    );

    const dbBytesRaw = await this.rpc("kaban_database_size_bytes", {});
    const dbBytes = Number(
      Array.isArray(dbBytesRaw) ? dbBytesRaw[0] : dbBytesRaw,
    );

    const limit = this.env.KABAN_LIMIT_DB_BYTES
      ? Number(this.env.KABAN_LIMIT_DB_BYTES)
      : null;

    const ratio = limit && limit > 0 ? dbBytes / limit : null;

    const quotaLevel =
      ratio == null
        ? null
        : ratio >= 0.95
          ? "critical"
          : ratio >= 0.85
            ? "high"
            : ratio >= 0.70
              ? "warning"
              : "ok";

    return [
      {
        metric: "db_storage_bytes",
        value: dbBytes,
        unit: "bytes",
        quality: "db_exact",
        limit,
        quota_level: quotaLevel,
      },
      ...(Array.isArray(rows) ? rows : []),
    ];
  }

  async health(projectId: string): Promise<unknown> {
    const now = new Date();

    const [
      healthRows,
      dueRows,
      staleLeases,
      unknownSteps,
      horizonRows,
    ] = await Promise.all([
      this.request(
        `kaban_runtime_health?${query({
          project_id: `eq.${projectId}`,
          select: "last_cron_tick",
          limit: "1",
        })}`,
      ),
      this.request(
        `kaban_executions?${query({
          project_id: `eq.${projectId}`,
          state: "in.(queued,pending,dispatched,running)",
          scheduled_for: `lte.${now.toISOString()}`,
          select:
            "execution_id,scheduled_for,misfire_deadline_at,state",
          order: "scheduled_for.asc.nullsfirst",
          limit: "100",
        })}`,
      ),
      this.request(
        `kaban_resource_leases?${query({
          project_id: `eq.${projectId}`,
          lease_expires_at: `lt.${now.toISOString()}`,
          select: "resource_key",
          limit: "100",
        })}`,
      ),
      this.request(
        `kaban_publication_steps?${query({
          project_id: `eq.${projectId}`,
          state: "eq.unknown_delivery",
          select: "publication_step_id",
          limit: "100",
        })}`,
      ),
      this.request(
        `kaban_executions?${query({
          project_id: `eq.${projectId}`,
          scheduled_for: `gt.${now.toISOString()}`,
          select: "scheduled_for",
          order: "scheduled_for.desc",
          limit: "1",
        })}`,
      ),
    ]);

    const due = Array.isArray(dueRows) ? dueRows : [];

    const oldest =
      due
        .map((row: any) => row.scheduled_for)
        .filter(Boolean)
        .sort()[0] ?? null;

    const missed = due.filter(
      (row: any) =>
        row.misfire_deadline_at &&
        Date.parse(row.misfire_deadline_at) < now.getTime(),
    ).length;

    const furthest =
      Array.isArray(horizonRows) && horizonRows[0]?.scheduled_for
        ? Date.parse(horizonRows[0].scheduled_for)
        : null;

    return {
      project_id: projectId,
      last_cron_tick: Array.isArray(healthRows)
        ? healthRows[0]?.last_cron_tick ?? null
        : null,
      oldest_due_execution_at: oldest,
      stale_lease_count: Array.isArray(staleLeases)
        ? staleLeases.length
        : 0,
      missed_slot_count: missed,
      schedule_horizon_days:
        furthest == null
          ? 0
          : Math.max(
              0,
              (furthest - now.getTime()) / 86400000,
            ),
      unknown_delivery_count: Array.isArray(unknownSteps)
        ? unknownSteps.length
        : 0,
    };
  }

  async artifact(projectId: string, artifactId: string): Promise<unknown> {
    return this.request(
      `kaban_artifacts?${query({
        project_id: `eq.${projectId}`,
        artifact_id: `eq.${artifactId}`,
        select:
          "artifact_id,project_id,r2_key,sha256,size_bytes,mime_type,logical_name",
        limit: "1",
      })}`,
    );
  }

  async createCommand(input: CommandInput): Promise<unknown> {
    return this.rpc("kaban_create_or_get_command", {
      p_project_id: input.project_id,
      p_execution_id: input.execution_id,
      p_operation: input.operation,
      p_content_key: input.content_key ?? null,
      p_content_set_id: input.content_set_id ?? null,
      p_expected_version: input.expected_version ?? null,
      p_command_payload: input.payload,
      p_requested_by: input.requested_by,
      p_idempotency_key: input.idempotency_key,
      p_scheduled_for: null,
      p_misfire_deadline_at: null,
      p_retry_deadline_at: null,
    });
  }

  async reconcilePublication(
    projectId: string,
    runId: string,
    actor: string,
    payload: Record<string, unknown>,
  ): Promise<unknown> {
    const stepKey = String(payload.step_key ?? "");
    const state = String(payload.state ?? "");

    if (!stepKey || !["sent", "failed"].includes(state)) {
      throw new Error(
        "reconcile требует step_key и state sent|failed",
      );
    }

    return this.request(
      `kaban_publication_steps?${query({
        project_id: `eq.${projectId}`,
        publication_run_id: `eq.${runId}`,
        step_key: `eq.${stepKey}`,
      })}`,
      {
        method: "PATCH",
        body: JSON.stringify({
          state,
          last_error: {
            reconciled_by: actor,
            note: payload.note ?? null,
          },
          updated_at: new Date().toISOString(),
        }),
      },
    );
  }

  async recordTick(): Promise<void> {
    await this.rpc("kaban_record_cron_tick", {
      p_now: new Date().toISOString(),
    });
  }

  async dueCandidates(limit: number): Promise<DueExecution[]> {
    const rows = await this.request(
      `kaban_executions?${query({
        state: "in.(queued,pending)",
        select:
          "execution_id,project_id,operation,state,wait_condition,misfire_deadline_at,retry_deadline_at,scheduled_for,lease_expires_at",
        order: "scheduled_for.asc.nullsfirst",
        limit: String(limit),
      })}`,
    );

    const now = Date.now();

    return (Array.isArray(rows) ? rows : []).filter(
      (row) =>
        !row.scheduled_for ||
        Date.parse(row.scheduled_for) <= now,
    );
  }

  async waitSatisfied(execution: DueExecution): Promise<boolean> {
    if (!execution.wait_condition) {
      return true;
    }

    if (execution.wait_condition.kind !== "content_approved") {
      return false;
    }

    const contentKey = String(
      execution.wait_condition.data.content_key ?? "",
    );

    if (!contentKey) {
      return false;
    }

    const rows = await this.request(
      `kaban_content_sets?${query({
        project_id: `eq.${execution.project_id}`,
        content_key: `eq.${contentKey}`,
        select: "approved_revision_id",
        limit: "1",
      })}`,
    );

    return (
      Array.isArray(rows) &&
      Boolean(rows[0]?.approved_revision_id)
    );
  }

  async markExpired(
    execution: DueExecution,
    now: Date,
  ): Promise<boolean> {
    const deadlines = [
      execution.misfire_deadline_at,
      execution.retry_deadline_at,
    ].filter(Boolean) as string[];

    if (
      !deadlines.some(
        (value) => Date.parse(value) < now.getTime(),
      )
    ) {
      return false;
    }

    const result = await this.rpc(
      "kaban_expire_execution_if_due",
      {
        p_execution_id: execution.execution_id,
        p_now: now.toISOString(),
      },
    );

    const value = Array.isArray(result)
      ? result[0]?.kaban_expire_execution_if_due ??
        result[0]
      : result;

    return value === true || value === "true";
  }

  async projectsWithShortHorizon(
    minDays: number,
  ): Promise<string[]> {
    const projects = await this.request(
      `kaban_projects?${query({
        enabled: "eq.true",
        select: "project_id",
      })}`,
    );

    const threshold =
      Date.now() + minDays * 86400_000;

    const out: string[] = [];

    for (
      const project of Array.isArray(projects)
        ? projects
        : []
    ) {
      const rows = await this.request(
        `kaban_executions?${query({
          project_id: `eq.${project.project_id}`,
          scheduled_for: `gt.${new Date().toISOString()}`,
          select: "scheduled_for",
          order: "scheduled_for.desc",
          limit: "1",
        })}`,
      );

      const furthest =
        Array.isArray(rows) &&
        rows[0]?.scheduled_for
          ? Date.parse(rows[0].scheduled_for)
          : 0;

      if (furthest < threshold) {
        out.push(String(project.project_id));
      }
    }

    return out;
  }

  async ensureScheduleProjection(
    projectId: string,
  ): Promise<void> {
    const dayKey = new Date()
      .toISOString()
      .slice(0, 10);

    await this.rpc("kaban_create_or_get_command", {
      p_project_id: projectId,
      p_execution_id: crypto.randomUUID(),
      p_operation: "kaban.schedule_projection",
      p_content_key: null,
      p_content_set_id: null,
      p_expected_version: null,
      p_command_payload: {
        horizon_days: 35,
      },
      p_requested_by: "cloudflare-cron",
      p_idempotency_key:
        `schedule-projection:${projectId}:${dayKey}`,
      p_scheduled_for: null,
      p_misfire_deadline_at: null,
      p_retry_deadline_at: null,
    });
  }

  async claimDue(
    execution: DueExecution,
    now: Date,
  ): Promise<import("./types.ts").DispatchClaim | null> {
    const dispatchNonce = crypto.randomUUID();

    const result = await this.rpc(
      "kaban_claim_execution_for_dispatch",
      {
        p_execution_id: execution.execution_id,
        p_now: now.toISOString(),
        p_dispatch_nonce: dispatchNonce,
      },
    );

    const claimedId = Array.isArray(result)
      ? result[0]?.kaban_claim_execution_for_dispatch ??
        result[0]?.execution_id
      : result;

    return claimedId &&
      String(claimedId) === execution.execution_id
      ? {
          ...execution,
          state: "dispatched",
          dispatch_nonce: dispatchNonce,
        }
      : null;
  }

  async scheduleDispatchRetry(
    execution: import("./types.ts").DispatchClaim,
    retryAt: Date,
  ): Promise<void> {
    await this.rpc(
      "kaban_schedule_dispatch_retry_safe",
      {
        p_execution_id: execution.execution_id,
        p_dispatch_nonce: execution.dispatch_nonce,
        p_retry_at: retryAt.toISOString(),
        p_wait_condition: execution.wait_condition,
      },
    );
  }
}

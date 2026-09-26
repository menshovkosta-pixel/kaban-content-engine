export interface Env {
  SUPABASE_URL: string;
  SUPABASE_SERVICE_KEY: string;
  GITHUB_OWNER: string;
  GITHUB_REPO: string;
  GITHUB_WORKFLOW_FILE: string;
  GITHUB_DISPATCH_TOKEN: string;
  CF_ACCESS_TEAM_DOMAIN: string;
  CF_ACCESS_AUD: string;
  KABAN_PUBLIC_ORIGIN: string;
  KABAN_LIMIT_DB_BYTES?: string;
  ASSETS?: { fetch(request: Request): Promise<Response> };
  ARTIFACTS?: { get(key: string): Promise<{ body: BodyInit | ReadableStream | null; httpMetadata?: { contentType?: string }; customMetadata?: Record<string,string> } | null> };
}

export interface AccessIdentity {
  sub: string;
  email: string;
  actor: string;
}

export interface WaitCondition {
  kind: string;
  data: Record<string, unknown>;
}

export interface DueExecution {
  execution_id: string;
  project_id: string;
  operation: string;
  state: string;
  wait_condition: WaitCondition | null;
  misfire_deadline_at: string | null;
  retry_deadline_at: string | null;
}

export interface DispatchClaim extends DueExecution {
  dispatch_nonce: string;
}

export interface CommandInput {
  execution_id: string;
  project_id: string;
  operation: string;
  content_key?: string | null;
  content_set_id?: string | null;
  expected_version?: number | null;
  payload: Record<string, unknown>;
  requested_by: string;
  idempotency_key: string;
}

export interface ReadStore {
  listProjects(): Promise<unknown>;
  getContent(projectId: string, contentKey: string): Promise<unknown>;
  getExecution(projectId: string, executionId: string): Promise<unknown>;
  history(projectId: string): Promise<unknown>;
  usage(projectId: string): Promise<unknown>;
  health(projectId: string): Promise<unknown>;
  artifact(projectId: string, artifactId: string): Promise<unknown>;
  createCommand(input: CommandInput): Promise<unknown>;
  reconcilePublication(projectId: string, runId: string, actor: string, payload: Record<string, unknown>): Promise<unknown>;
}

export interface OrchestrationStore {
  recordTick(): Promise<void>;
  dueCandidates(limit: number): Promise<DueExecution[]>;
  waitSatisfied(execution: DueExecution): Promise<boolean>;
  markExpired(execution: DueExecution, now: Date): Promise<boolean>;
  projectsWithShortHorizon(minDays: number): Promise<string[]>;
  ensureScheduleProjection(projectId: string): Promise<void>;
  claimDue(execution: DueExecution, now: Date): Promise<DispatchClaim | null>;
  scheduleDispatchRetry(execution: DispatchClaim, retryAt: Date): Promise<void>;
}

export interface GithubDispatcher {
  dispatch(executionId: string): Promise<void>;
}

export interface WorkerDependencies {
  store: ReadStore & OrchestrationStore;
  github: GithubDispatcher;
  authenticate?: (request: Request, env: Env) => Promise<AccessIdentity>;
  verifyAccessJwt?: (request: Request, env: Env) => Promise<AccessIdentity>;
}

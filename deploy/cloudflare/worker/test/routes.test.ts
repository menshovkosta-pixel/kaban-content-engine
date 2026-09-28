import test from "node:test";
import assert from "node:assert/strict";
import { handleRequest } from "../src/routes.ts";

function env() {
  return { CF_ACCESS_TEAM_DOMAIN: "team.cloudflareaccess.com", CF_ACCESS_AUD: "aud", KABAN_PUBLIC_ORIGIN: "https://admin.example.com" } as any;
}
class FakeStore {
  calls: any[] = [];
  idem = new Map<string, any>();
  executions = new Map<string, any>();
  claimed = new Set<string>();
  enableDispatchClaims = false;

  async listProjects() { return [{ project_id: "caelus" }]; }

  async createCommand(input: any) {
    this.calls.push(input);
    const key = `${input.project_id}:${input.idempotency_key}`;
    const existing = this.idem.get(key);
    if (existing) return existing;

    const row = {
      execution_id: input.execution_id,
      project_id: input.project_id,
      operation: input.operation,
      requested_by: input.requested_by,
      state: "queued",
      wait_condition: null,
      misfire_deadline_at: null,
      retry_deadline_at: null,
    };

    this.idem.set(key, row);
    this.executions.set(row.execution_id, row);
    return row;
  }

  async getExecution(_projectId: string, executionId: string) {
    const row = this.executions.get(executionId);
    return row ? [row] : [];
  }

  async claimDue(execution: any, _now: Date) {
    if (!this.enableDispatchClaims) return null;
    if (this.claimed.has(execution.execution_id)) return null;

    this.claimed.add(execution.execution_id);
    return {
      ...execution,
      dispatch_nonce: `nonce:${execution.execution_id}`,
    };
  }

  async scheduleDispatchRetry(_execution: any, _retryAt: Date) {}
}
const authenticate = async () => ({ sub: "user-1", email: "user@example.com", actor: "user@example.com" });
const headers = { "Cf-Access-Jwt-Assertion":"ok", Origin:"https://admin.example.com", "Content-Type":"application/json" };

test("healthz is public", async () => {
  const response = await handleRequest(new Request("https://worker.example/healthz"), env(), { store: new FakeStore() as any, github: {} as any, authenticate });
  assert.equal(response.status, 200);
});

test("route project and actor override browser conflicts", async () => {
  const store = new FakeStore();
  const request = new Request("https://worker.example/api/projects/caelus/commands", {
    method: "POST", headers,
    body: JSON.stringify({ execution_id:"11111111-1111-4111-8111-111111111111", operation:"approve", content_key:"2026-09-26:ru", content_set_id:null, expected_version:7, payload:{}, idempotency_key:"idem-1", project_id:"other", requested_by:"attacker" }),
  });
  const response = await handleRequest(request, env(), { store: store as any, github: {} as any, authenticate });
  assert.equal(response.status, 202);
  assert.equal(store.calls[0].project_id, "caelus");
  assert.equal(store.calls[0].requested_by, "user@example.com");
});

test("duplicate idempotency returns one canonical execution", async () => {
  const store = new FakeStore();
  const body = { execution_id:"11111111-1111-4111-8111-111111111111", operation:"approve", content_key:"2026-09-26:ru", expected_version:7, payload:{}, idempotency_key:"same-idem" };
  const make = () => new Request("https://worker.example/api/projects/caelus/commands", { method:"POST", headers, body:JSON.stringify(body) });
  const first = await handleRequest(make(), env(), { store: store as any, github: {} as any, authenticate });
  const second = await handleRequest(make(), env(), { store: store as any, github: {} as any, authenticate });
  assert.equal((await first.json() as any).execution_id, (await second.json() as any).execution_id);
  assert.equal(store.calls.length, 2);
});

test("artifact route proxies private R2 object without exposing credentials", async () => {
  const store:any = new FakeStore();
  store.artifact = async () => [{ artifact_id:"a1", r2_key:"projects/caelus/x.png", sha256:"abc", mime_type:"image/png" }];
  const bucket={ get: async (key:string) => key==="projects/caelus/x.png" ? { body:new Uint8Array([1,2,3]), httpMetadata:{contentType:"image/png"} } : null };
  const request=new Request("https://worker.example/api/projects/caelus/artifacts/a1",{headers:{"Cf-Access-Jwt-Assertion":"ok"}});
  const response=await handleRequest(request,{...env(),ARTIFACTS:bucket} as any,{store,github:{} as any,authenticate});
  assert.equal(response.status,200);
  assert.equal(response.headers.get("Content-Type"),"image/png");
  assert.deepEqual([...new Uint8Array(await response.arrayBuffer())],[1,2,3]);
});

test("project health route returns authenticated project-scoped summary", async () => {
  const store:any = new FakeStore();
  store.health = async (projectId:string) => ({ project_id:projectId, last_cron_tick:"2026-09-26T00:00:00Z", unknown_delivery_count:1 });
  const request=new Request("https://worker.example/api/projects/caelus/health",{headers:{"Cf-Access-Jwt-Assertion":"ok"}});
  const response=await handleRequest(request,env(),{store,github:{} as any,authenticate});
  assert.equal(response.status,200);
  const body:any=await response.json();
  assert.equal(body.project_id,"caelus");
  assert.equal(body.unknown_delivery_count,1);
});


test("command route accepts canonical execution id returned as RPC string", async () => {
  const executionId = "66666666-6666-4666-8666-666666666666";
  const dispatched: string[] = [];

  const store: any = new FakeStore();
  store.enableDispatchClaims = true;

  store.createCommand = async (_input: any) => executionId;

  store.getExecution = async (_projectId: string, id: string) =>
    id === executionId
      ? [{
          execution_id: executionId,
          project_id: "caelus",
          operation: "regenerate_field",
          state: "queued",
          wait_condition: null,
          misfire_deadline_at: null,
          retry_deadline_at: null,
        }]
      : [];

  const github = {
    async dispatch(id: string) {
      dispatched.push(id);
    },
  };

  const request = new Request(
    "https://worker.example/api/projects/caelus/commands",
    {
      method: "POST",
      headers,
      body: JSON.stringify({
        execution_id: executionId,
        operation: "regenerate_field",
        content_key: "2099-01-23:ru",
        payload: { sign: "aries", field: "overview" },
        idempotency_key: "rpc-string-contract",
      }),
    },
  );

  const response = await handleRequest(
    request,
    env(),
    {
      store,
      github: github as any,
      authenticate,
    },
  );

  assert.equal(response.status, 202);
  assert.equal(await response.json(), executionId);
  assert.deepEqual(dispatched, [executionId]);
});

test("new command dispatches its canonical execution exactly once", async () => {
  const store = new FakeStore();
  store.enableDispatchClaims = true;
  const dispatched: string[] = [];

  const github = {
    async dispatch(executionId: string) {
      dispatched.push(executionId);
    },
  };

  const executionId = "22222222-2222-4222-8222-222222222222";

  const request = new Request(
    "https://worker.example/api/projects/caelus/commands",
    {
      method: "POST",
      headers,
      body: JSON.stringify({
        execution_id: executionId,
        operation: "approve",
        content_key: "2026-09-26:ru",
        content_set_id: null,
        expected_version: 7,
        payload: {},
        idempotency_key: "dispatch-new-command",
      }),
    },
  );

  const response = await handleRequest(
    request,
    env(),
    {
      store: store as any,
      github: github as any,
      authenticate,
    },
  );

  assert.equal(response.status, 202);
  assert.deepEqual(dispatched, [executionId]);
});

test("idempotent duplicate dispatches only the canonical execution once", async () => {
  const store = new FakeStore();
  store.enableDispatchClaims = true;
  const dispatched: string[] = [];

  const github = {
    async dispatch(executionId: string) {
      dispatched.push(executionId);
    },
  };

  const firstExecutionId =
    "33333333-3333-4333-8333-333333333333";

  const duplicateExecutionId =
    "44444444-4444-4444-8444-444444444444";

  const make = (executionId: string) =>
    new Request(
      "https://worker.example/api/projects/caelus/commands",
      {
        method: "POST",
        headers,
        body: JSON.stringify({
          execution_id: executionId,
          operation: "approve",
          content_key: "2026-09-26:ru",
          content_set_id: null,
          expected_version: 7,
          payload: {},
          idempotency_key: "dispatch-same-idem",
        }),
      },
    );

  const first = await handleRequest(
    make(firstExecutionId),
    env(),
    {
      store: store as any,
      github: github as any,
      authenticate,
    },
  );

  const second = await handleRequest(
    make(duplicateExecutionId),
    env(),
    {
      store: store as any,
      github: github as any,
      authenticate,
    },
  );

  assert.equal(first.status, 202);
  assert.equal(second.status, 202);

  const firstBody: any = await first.json();
  const secondBody: any = await second.json();

  assert.equal(firstBody.execution_id, firstExecutionId);
  assert.equal(secondBody.execution_id, firstExecutionId);

  assert.deepEqual(dispatched, [firstExecutionId]);
});

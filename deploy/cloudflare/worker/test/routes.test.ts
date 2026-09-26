import test from "node:test";
import assert from "node:assert/strict";
import { handleRequest } from "../src/routes.ts";

function env() {
  return { CF_ACCESS_TEAM_DOMAIN: "team.cloudflareaccess.com", CF_ACCESS_AUD: "aud", KABAN_PUBLIC_ORIGIN: "https://admin.example.com" } as any;
}
class FakeStore {
  calls: any[] = [];
  idem = new Map<string, any>();
  async listProjects() { return [{ project_id: "caelus" }]; }
  async createCommand(input: any) {
    this.calls.push(input);
    const key = `${input.project_id}:${input.idempotency_key}`;
    const existing = this.idem.get(key);
    if (existing) return existing;
    const row = { execution_id: input.execution_id, project_id: input.project_id, requested_by: input.requested_by };
    this.idem.set(key, row);
    return row;
  }
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

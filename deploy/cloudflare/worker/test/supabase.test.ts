import test from "node:test";
import assert from "node:assert/strict";
import { SupabaseWorkerStore } from "../src/supabase.ts";

const env = {
  SUPABASE_URL: "https://db.example",
  SUPABASE_SERVICE_KEY: "service-key",
} as any;

function execution() {
  return { execution_id: "11111111-1111-4111-8111-111111111111", project_id: "caelus", operation: "publish_ru", state: "queued", wait_condition: null, misfire_deadline_at: null, retry_deadline_at: null };
}

test("claimDue uses dispatch claim RPC rather than execution lease RPC", async () => {
  const calls: Array<{ url: string; body: any }> = [];
  const fakeFetch = async (input: any, init: any) => {
    calls.push({ url: String(input), body: JSON.parse(init.body) });
    return new Response(JSON.stringify([{ kaban_claim_execution_for_dispatch: execution().execution_id }]), { status: 200 });
  };
  const store = new SupabaseWorkerStore(env, fakeFetch as any);
  const claim = await store.claimDue(execution() as any, new Date("2026-09-26T00:00:00Z"));
  assert.match(calls[0].url, /rpc\/kaban_claim_execution_for_dispatch$/);
  assert.equal(typeof calls[0].body.p_dispatch_nonce, "string");
  assert.equal(claim?.execution_id, execution().execution_id);
  assert.equal(claim?.dispatch_nonce, calls[0].body.p_dispatch_nonce);
});

test("dispatch retry is guarded by the same dispatch nonce", async () => {
  const calls: Array<{ url: string; body: any }> = [];
  const fakeFetch = async (input: any, init: any) => {
    calls.push({ url: String(input), body: JSON.parse(init.body) });
    return new Response("true", { status: 200 });
  };
  const store = new SupabaseWorkerStore(env, fakeFetch as any);
  await store.scheduleDispatchRetry({ ...execution(), dispatch_nonce: "22222222-2222-4222-8222-222222222222" } as any, new Date("2026-09-26T00:10:00Z"));
  assert.match(calls[0].url, /rpc\/kaban_schedule_dispatch_retry_safe$/);
  assert.equal(calls[0].body.p_dispatch_nonce, "22222222-2222-4222-8222-222222222222");
});

test("createCommand uses canonical p_command_payload and client execution id", async () => {
  const calls: Array<{ url: string; body: any }> = [];
  const fakeFetch = async (input: any, init: any) => {
    calls.push({ url: String(input), body: JSON.parse(init.body) });
    return new Response(JSON.stringify("11111111-1111-4111-8111-111111111111"), { status: 200 });
  };
  const store = new SupabaseWorkerStore(env, fakeFetch as any);
  await store.createCommand({
    execution_id:"11111111-1111-4111-8111-111111111111", project_id:"caelus", operation:"approve",
    content_key:"2026-09-26:ru", content_set_id:null, expected_version:7, payload:{x:1}, requested_by:"user@example.com", idempotency_key:"idem-1",
  });
  assert.equal(calls[0].body.p_execution_id, "11111111-1111-4111-8111-111111111111");
  assert.deepEqual(calls[0].body.p_command_payload, {x:1});
  assert.equal("p_payload" in calls[0].body, false);
});

test("expiry uses atomic RPC", async () => {
  const calls: Array<{ url: string; body: any }> = [];
  const fakeFetch = async (input: any, init: any) => {
    calls.push({ url: String(input), body: JSON.parse(init.body) });
    return new Response("true", { status: 200 });
  };
  const store = new SupabaseWorkerStore(env, fakeFetch as any);
  const expired = await store.markExpired({ ...execution(), misfire_deadline_at:"2026-09-25T23:00:00Z" } as any, new Date("2026-09-26T00:00:00Z"));
  assert.equal(expired, true);
  assert.match(calls[0].url, /rpc\/kaban_expire_execution_if_due$/);
});

test("getContent returns content set with current revision and project-scoped artifacts", async () => {
  const calls: string[] = [];
  const fakeFetch = async (input: any) => {
    const url = String(input); calls.push(url);
    if (url.includes("kaban_content_sets")) return new Response(JSON.stringify([{ project_id:"caelus", content_set_id:"aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa", content_key:"2026-09-26:ru", current_revision_id:"bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb", version:7 }]), {status:200});
    if (url.includes("kaban_content_revisions")) return new Response(JSON.stringify([{ project_id:"caelus", revision_id:"bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb", payload:{ signs:{ aries:{card:"x"} } }, content_hash:"hash" }]), {status:200});
    if (url.includes("kaban_artifacts")) return new Response(JSON.stringify([{ project_id:"caelus", artifact_id:"cccccccc-cccc-4ccc-8ccc-cccccccccccc", logical_name:"caelus_aries.png" }]), {status:200});
    if (url.includes("kaban_publication_runs")) return new Response("[]", {status:200});
    throw new Error(`unexpected URL ${url}`);
  };
  const store = new SupabaseWorkerStore(env, fakeFetch as any);
  const result: any = await store.getContent("caelus", "2026-09-26:ru");
  assert.equal(result.content_set.version, 7);
  assert.equal(result.revision.payload.signs.aries.card, "x");
  assert.equal(result.artifacts.length, 1);
  assert.ok(calls.every((url) => url.includes("project_id=eq.caelus")));
});

test("getContent includes latest publication run and steps for reconciliation", async () => {
  const fakeFetch = async (input: any) => {
    const url = String(input);
    if (url.includes("kaban_content_sets")) return new Response(JSON.stringify([{ project_id:"caelus", content_set_id:"aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa", content_key:"2026-09-26:ru", current_revision_id:null, version:7 }]), {status:200});
    if (url.includes("kaban_artifacts")) return new Response("[]", {status:200});
    if (url.includes("kaban_publication_runs")) return new Response(JSON.stringify([{ project_id:"caelus", publication_run_id:"dddddddd-dddd-4ddd-8ddd-dddddddddddd", content_set_id:"aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa" }]), {status:200});
    if (url.includes("kaban_publication_steps")) return new Response(JSON.stringify([{ step_key:"album:1", state:"unknown_delivery" }]), {status:200});
    throw new Error(`unexpected URL ${url}`);
  };
  const store = new SupabaseWorkerStore(env, fakeFetch as any);
  const result: any = await store.getContent("caelus", "2026-09-26:ru");
  assert.equal(result.publication.run.publication_run_id, "dddddddd-dddd-4ddd-8ddd-dddddddddddd");
  assert.equal(result.publication.steps[0].state, "unknown_delivery");
});

test("recordTick persists cron heartbeat through RPC", async () => {
  const calls:string[]=[];
  const fakeFetch=async (input:any, init:any) => { calls.push(String(input)); return new Response("true",{status:200}); };
  const store=new SupabaseWorkerStore(env,fakeFetch as any);
  await store.recordTick();
  assert.match(calls[0],/rpc\/kaban_record_cron_tick$/);
});

test("health returns project-scoped operational indicators", async () => {
  const urls:string[]=[];
  const fakeFetch=async (input:any) => {
    const url=String(input); urls.push(url);
    if (url.includes("kaban_runtime_health")) return new Response(JSON.stringify([{last_cron_tick:"2026-09-26T00:00:00Z"}]),{status:200});
    if (url.includes("kaban_executions") && url.includes("scheduled_for=gt")) return new Response(JSON.stringify([{scheduled_for:"2026-10-20T00:00:00Z"}]),{status:200});
    if (url.includes("kaban_executions")) return new Response(JSON.stringify([{execution_id:"e1",scheduled_for:"2026-09-25T23:00:00Z",misfire_deadline_at:"2026-09-25T23:30:00Z"}]),{status:200});
    if (url.includes("kaban_resource_leases")) return new Response(JSON.stringify([{resource_key:"content:x"}]),{status:200});
    if (url.includes("kaban_publication_steps")) return new Response(JSON.stringify([{publication_step_id:"p1"}]),{status:200});
    throw new Error(`unexpected ${url}`);
  };
  const store=new SupabaseWorkerStore(env,fakeFetch as any);
  const result:any=await store.health("caelus");
  assert.equal(result.project_id,"caelus");
  assert.equal(result.stale_lease_count,1);
  assert.equal(result.unknown_delivery_count,1);
  assert.ok(urls.every(url => url.includes("rpc/") || url.includes("project_id=eq.caelus")));
});

test("usage labels exact database size without upgrading estimated provider metrics", async () => {
  const fakeFetch=async (input:any) => {
    const url=String(input);
    if (url.includes("rpc/kaban_database_size_bytes")) return new Response("15000",{status:200});
    if (url.includes("kaban_usage_samples")) return new Response(JSON.stringify([{metric:"egress_bytes",value:99,unit:"bytes",quality:"estimated"}]),{status:200});
    throw new Error(`unexpected ${url}`);
  };
  const store=new SupabaseWorkerStore({...env,KABAN_LIMIT_DB_BYTES:"20000"} as any,fakeFetch as any);
  const result:any=await store.usage("caelus");
  const db=result.find((x:any)=>x.metric==="db_storage_bytes");
  const egress=result.find((x:any)=>x.metric==="egress_bytes");
  assert.equal(db.quality,"db_exact");
  assert.equal(db.quota_level,"warning");
  assert.equal(egress.quality,"estimated");
});

import test from "node:test";
import assert from "node:assert/strict";
import { CommandClient } from "../../../../projects/caelus/cloud_ui/app.js";

function response(status: number, body: any) {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

test("stale version 409 is shown and is never auto-retried", async () => {
  let calls = 0;
  const messages: string[] = [];
  const client = new CommandClient({
    projectId: "caelus",
    origin: "https://admin.example.com",
    fetcher: async () => { calls++; return response(409, { error: "Version conflict" }); },
    onMessage: (message: string) => messages.push(message),
    uuid: () => "11111111-1111-4111-8111-111111111111",
  });
  await assert.rejects(() => client.submit("approve", { contentKey: "2026-09-26:ru", version: 7, payload: {} }));
  assert.equal(calls, 1);
  assert.match(messages.at(-1) ?? "", /content changed; reload current revision/i);
});

test("duplicate click reuses one in-flight command and one idempotency key", async () => {
  const bodies: any[] = [];
  let resolveFetch!: (value: Response) => void;
  const fetchPromise = new Promise<Response>((resolve) => { resolveFetch = resolve; });
  const client = new CommandClient({
    projectId: "caelus",
    origin: "https://admin.example.com",
    fetcher: async (_url: string, init: RequestInit) => { bodies.push(JSON.parse(String(init.body))); return fetchPromise; },
    onMessage: () => {},
    uuid: () => "22222222-2222-4222-8222-222222222222",
  });
  const input = { contentKey: "2026-09-26:ru", version: 7, payload: { sign: "aries" } };
  const first = client.submit("regenerate_sign", input);
  const second = client.submit("regenerate_sign", input);
  assert.equal(first, second);
  assert.equal(bodies.length, 1);
  resolveFetch(response(202, { execution_id: "22222222-2222-4222-8222-222222222222" }));
  await first;
  assert.equal(bodies[0].idempotency_key, "22222222-2222-4222-8222-222222222222");
});

test("CAELUS UI derives Auckland date, card artifacts and uniqueness diagnostics without business validation", async () => {
  const { aucklandDate, artifactBySign, uniquenessDiagnostics } = await import("../../../../projects/caelus/cloud_ui/app.js");
  assert.equal(aucklandDate(new Date("2026-09-26T12:30:00Z")), "2026-09-27");
  const artifacts = artifactBySign([
    { artifact_id: "art-1", logical_name: "caelus_aries.png", mime_type: "image/png" },
    { artifact_id: "art-2", logical_name: "telegram_aries.jpg", mime_type: "image/jpeg" },
  ]);
  assert.equal(artifacts.aries.artifact_id, "art-1");
  const diagnostics = uniquenessDiagnostics({
    generation: { uniqueness: { conflicts: [{ sign: "aries", score: 0.84, field: "card" }], threshold: 0.8 } },
  });
  assert.equal(diagnostics.threshold, 0.8);
  assert.equal(diagnostics.conflicts[0].sign, "aries");
});

test("CAELUS UI polls execution until terminal state without creating a second command", async () => {
  const { pollExecution } = await import("../../../../projects/caelus/cloud_ui/app.js");
  let calls = 0;
  const fetcher = async () => {
    calls += 1;
    return new Response(JSON.stringify(calls === 1 ? [{ state: "running" }] : [{ state: "finished", result: { ok: true } }]), { status: 200 });
  };
  const result = await pollExecution({
    projectId: "caelus",
    executionId: "11111111-1111-4111-8111-111111111111",
    fetcher,
    sleep: async () => {},
    maxPolls: 3,
  });
  assert.equal(result.state, "finished");
  assert.equal(calls, 2);
});

test("unknown delivery reconciliation is explicit and uses publication endpoint", async () => {
  const { reconcileUnknownDelivery } = await import("../../../../projects/caelus/cloud_ui/app.js");
  const calls = [];
  const fetcher = async (url, init) => {
    calls.push({ url: String(url), init });
    return new Response(JSON.stringify({ ok: true }), { status: 200 });
  };
  await reconcileUnknownDelivery({
    projectId: "caelus",
    runId: "run-1",
    stepKey: "album:1",
    state: "sent",
    fetcher,
  });
  assert.match(calls[0].url, /\/api\/projects\/caelus\/publications\/run-1\/reconcile$/);
  assert.deepEqual(JSON.parse(calls[0].init.body), { step_key: "album:1", state: "sent" });
});

import test from "node:test";
import assert from "node:assert/strict";
import { orchestrateCron } from "../src/orchestrator.ts";
import { AmbiguousDispatchError, DefinitiveDispatchError } from "../src/github.ts";

class FakeStore {
  due: any[] = [];
  horizons: string[] = [];
  projections: string[] = [];
  claimed: string[] = [];
  retries: Array<[string, string]> = [];
  ticks = 0;
  approved = new Set<string>();
  expired = new Set<string>();
  async recordTick() { this.ticks++; }
  async dueCandidates(limit: number) { return this.due.slice(0, limit); }
  async waitSatisfied(row: any) { return row.wait_condition?.kind !== "content_approved" || this.approved.has(row.execution_id); }
  async markExpired(row: any) { return this.expired.has(row.execution_id); }
  async projectsWithShortHorizon() { return this.horizons; }
  async ensureScheduleProjection(projectId: string) { this.projections.push(projectId); }
  async claimDue(row: any) {
    this.claimed.push(row.execution_id);
    return { ...row, dispatch_nonce: `nonce-${row.execution_id}` };
  }
  async scheduleDispatchRetry(row: any, retryAt: Date) { this.retries.push([row.execution_id, retryAt.toISOString()]); }
}
class FakeGithub {
  dispatched: string[] = [];
  error: Error | null = null;
  async dispatch(id: string) { if (this.error) throw this.error; this.dispatched.push(id); }
}
const row = (id="e1") => ({ execution_id:id, project_id:"caelus", operation:"publish", state:"queued", wait_condition:null, misfire_deadline_at:null, retry_deadline_at:null });

test("waiting approval causes zero GitHub dispatches", async () => {
  const store = new FakeStore(); const github = new FakeGithub();
  store.due = [{ ...row(), wait_condition: { kind:"content_approved", data:{ content_key:"2026-09-26:ru" } } }];
  await orchestrateCron(store as any, github as any, new Date("2026-09-26T00:00:00Z"));
  assert.deepEqual(github.dispatched, []);
  assert.deepEqual(store.claimed, []);
});

test("approved due execution is claimed and dispatched exactly once", async () => {
  const store = new FakeStore(); const github = new FakeGithub();
  store.due = [{ ...row(), wait_condition: { kind:"content_approved", data:{ content_key:"2026-09-26:ru" } } }];
  store.approved.add("e1");
  await orchestrateCron(store as any, github as any, new Date("2026-09-26T00:00:00Z"));
  assert.deepEqual(github.dispatched, ["e1"]);
  assert.deepEqual(store.claimed, ["e1"]);
});

test("horizon below seven days creates one idempotent projection request", async () => {
  const store = new FakeStore(); const github = new FakeGithub();
  store.horizons = ["caelus"];
  await orchestrateCron(store as any, github as any, new Date("2026-09-26T00:00:00Z"));
  assert.deepEqual(store.projections, ["caelus"]);
});

test("definitive GitHub failure schedules safe retry", async () => {
  const store = new FakeStore(); const github = new FakeGithub();
  store.due = [row()]; github.error = new DefinitiveDispatchError("503");
  await orchestrateCron(store as any, github as any, new Date("2026-09-26T00:00:00Z"));
  assert.equal(store.retries.length, 1);
});

test("ambiguous GitHub timeout schedules delayed safe retry using same dispatch claim", async () => {
  const store = new FakeStore(); const github = new FakeGithub();
  store.due = [row()]; github.error = new AmbiguousDispatchError("timeout");
  const now = new Date("2026-09-26T00:00:00Z");
  await orchestrateCron(store as any, github as any, now);
  assert.equal(store.retries.length, 1);
  assert.equal(store.retries[0][0], "e1");
  assert.equal(store.retries[0][1], "2026-09-26T00:10:00.000Z");
});

test("cron handles at most ten due executions per invocation", async () => {
  const store = new FakeStore(); const github = new FakeGithub();
  store.due = Array.from({length: 14}, (_, i) => row(`e${i}`));
  await orchestrateCron(store as any, github as any, new Date("2026-09-26T00:00:00Z"));
  assert.equal(github.dispatched.length, 10);
});

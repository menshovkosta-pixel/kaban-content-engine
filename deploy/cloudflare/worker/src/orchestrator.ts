import type { GithubDispatcher, OrchestrationStore } from "./types.ts";

export interface OrchestratorDependencies {
  store: OrchestrationStore;
  github: GithubDispatcher;
}

export async function runCron({ store, github }: OrchestratorDependencies, now = new Date()): Promise<void> {
  await store.recordTick();

  for (const projectId of await store.projectsWithShortHorizon(7)) {
    await store.ensureScheduleProjection(projectId);
  }

  const candidates = (await store.dueCandidates(10)).slice(0, 10);
  for (const execution of candidates) {
    if (await store.markExpired(execution, now)) continue;
    if (!(await store.waitSatisfied(execution))) continue;
    const claimed = await store.claimDue(execution, now);
    if (!claimed) continue;
    try {
      await github.dispatch(claimed.execution_id);
    } catch (_error) {
      // Retry is delayed and guarded by dispatch_nonce. If an ambiguous dispatch actually
      // started an Action, start_execution moves the row to running before this retry
      // can requeue it; the guarded RPC then becomes a no-op.
      await store.scheduleDispatchRetry(claimed, new Date(now.getTime() + 10 * 60_000));
    }
  }
}

export async function orchestrateCron(store: OrchestrationStore, github: GithubDispatcher, now = new Date()): Promise<void> {
  return runCron({ store, github }, now);
}

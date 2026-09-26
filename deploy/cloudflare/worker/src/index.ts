import { GithubDispatchClient } from "./github.ts";
import { runCron } from "./orchestrator.ts";
import { handleRequest } from "./routes.ts";
import { SupabaseWorkerStore } from "./supabase.ts";
import type { Env } from "./types.ts";

function dependencies(env: Env) {
  const store = new SupabaseWorkerStore(env);
  return { store, github: new GithubDispatchClient(env) };
}

export default {
  async fetch(request: Request, env: Env): Promise<Response> {
    const path = new URL(request.url).pathname;
    if (path.startsWith("/projects/") && env.ASSETS) return env.ASSETS.fetch(request);
    return handleRequest(request, env, dependencies(env));
  },
  async scheduled(_controller: ScheduledController, env: Env, ctx: ExecutionContext): Promise<void> {
    ctx.waitUntil(runCron(dependencies(env)));
  },
};

import { assertMutationRequest, HttpError, verifyAccess } from "./auth.ts";
import type { AccessIdentity, CommandInput, DueExecution, Env, WorkerDependencies } from "./types.ts";

function json(value: unknown, status = 200): Response {
  return new Response(JSON.stringify(value), { status, headers: { "Content-Type": "application/json", "Cache-Control": "no-store" } });
}

function first<T>(value: T): T extends unknown[] ? unknown : T {
  return (Array.isArray(value) ? value[0] ?? null : value) as never;
}

function pathParts(url: URL): string[] {
  return url.pathname.split("/").filter(Boolean);
}

export async function handleRequest(request: Request, env: Env, deps: WorkerDependencies): Promise<Response> {
  if (new URL(request.url).pathname === "/healthz") return json({ ok: true });
  try {
    const authenticate = deps.authenticate ?? deps.verifyAccessJwt ?? verifyAccess;
    const identity: AccessIdentity = await authenticate(request, env);
    const actor = identity.actor || identity.email || identity.sub;
    assertMutationRequest(request, env);
    const url = new URL(request.url);
    const parts = pathParts(url);

    if (request.method === "GET" && url.pathname === "/api/projects") return json(await deps.store.listProjects());
    if (parts[0] !== "api" || parts[1] !== "projects" || !parts[2]) throw new HttpError(404, "Route not found");
    const projectId = decodeURIComponent(parts[2]);

    if (request.method === "GET" && parts[3] === "content" && parts[4]) return json(first(await deps.store.getContent(projectId, decodeURIComponent(parts[4]))));
    if (request.method === "GET" && parts[3] === "executions" && parts[4]) return json(first(await deps.store.getExecution(projectId, parts[4])));
    if (request.method === "GET" && parts[3] === "history") return json(await deps.store.history(projectId));
    if (request.method === "GET" && parts[3] === "usage") return json(await deps.store.usage(projectId));
    if (request.method === "GET" && parts[3] === "health") return json(await deps.store.health(projectId));
    if (request.method === "GET" && parts[3] === "artifacts" && parts[4]) {
      const metadata = first(await deps.store.artifact(projectId, parts[4])) as Record<string, unknown> | null;
      if (!metadata) throw new HttpError(404, "Artifact not found");
      const key = typeof metadata.r2_key === "string" ? metadata.r2_key : "";
      if (env.ARTIFACTS && key) {
        const object = await env.ARTIFACTS.get(key);
        if (!object) throw new HttpError(404, "Artifact object not found");
        return new Response(object.body, {
          status: 200,
          headers: {
            "Content-Type": object.httpMetadata?.contentType || String(metadata.mime_type || "application/octet-stream"),
            "Cache-Control": "private, max-age=300",
            "ETag": `"${String(metadata.sha256 || "")}"`,
          },
        });
      }
      return json(metadata);
    }

    if (request.method === "POST" && parts[3] === "commands" && parts.length === 4) {
      const body = await request.json() as Record<string, unknown>;
      const input: CommandInput = {
        execution_id: String(body.execution_id ?? ""),
        project_id: projectId,
        operation: String(body.operation ?? ""),
        content_key: body.content_key == null ? null : String(body.content_key),
        content_set_id: body.content_set_id == null ? null : String(body.content_set_id),
        expected_version: body.expected_version == null ? null : Number(body.expected_version),
        payload: typeof body.payload === "object" && body.payload !== null ? body.payload as Record<string, unknown> : {},
        requested_by: actor,
        idempotency_key: String(body.idempotency_key ?? ""),
      };
      if (!input.execution_id || !input.operation || !input.idempotency_key) throw new HttpError(400, "execution_id, operation и idempotency_key обязательны");
      const created = first(await deps.store.createCommand(input)) as Record<string, unknown> | null;
      if (!created) throw new HttpError(500, "Command creation returned no execution");

      const canonicalExecutionId = String(created.execution_id ?? "");
      if (!canonicalExecutionId) throw new HttpError(500, "Command creation returned no execution_id");

      const execution = first(
        await deps.store.getExecution(projectId, canonicalExecutionId),
      ) as DueExecution | null;

      if (execution) {
        const now = new Date();
        const claimed = await deps.store.claimDue(execution, now);

        if (claimed) {
          try {
            await deps.github.dispatch(claimed.execution_id);
          } catch (_error) {
            await deps.store.scheduleDispatchRetry(
              claimed,
              new Date(now.getTime() + 10 * 60_000),
            );
          }
        }
      }

      return json(created, 202);
    }

    if (request.method === "POST" && parts[3] === "publications" && parts[4] && parts[5] === "reconcile") {
      const body = await request.json() as Record<string, unknown>;
      return json(await deps.store.reconcilePublication(projectId, parts[4], actor, body));
    }
    throw new HttpError(404, "Route not found");
  } catch (error) {
    if (error instanceof HttpError) return json({ error: error.message }, error.status);
    return json({ error: "Internal error" }, 500);
  }
}

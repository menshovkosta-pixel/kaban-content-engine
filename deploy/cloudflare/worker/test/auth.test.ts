import test from "node:test";
import assert from "node:assert/strict";
import { requireAccess, requireSameOriginJson } from "../src/auth.ts";

const env = {
  CF_ACCESS_TEAM_DOMAIN: "team.cloudflareaccess.com",
  CF_ACCESS_AUD: "aud-123",
  KABAN_PUBLIC_ORIGIN: "https://admin.example.com",
} as any;

test("rejects missing Access assertion", async () => {
  const request = new Request("https://worker.example/api/projects");
  await assert.rejects(
    () => requireAccess(request, env, async () => ({ sub: "never", email: "never@example.com", actor: "never@example.com" })),
    (error: any) => error?.status === 401,
  );
});

test("rejects invalid Access assertion", async () => {
  const request = new Request("https://worker.example/api/projects", { headers: { "Cf-Access-Jwt-Assertion": "bad" } });
  await assert.rejects(
    () => requireAccess(request, env, async () => { throw new Error("invalid"); }),
    (error: any) => error?.status === 401,
  );
});

test("accepts validated identity and exposes stable actor", async () => {
  const request = new Request("https://worker.example/api/projects", { headers: { "Cf-Access-Jwt-Assertion": "good" } });
  const identity = await requireAccess(request, env, async () => ({ sub: "user-1", email: "user@example.com", actor: "user@example.com" }));
  assert.equal(identity.actor, "user@example.com");
});

test("rejects foreign origin", () => {
  const request = new Request("https://worker.example/api/projects/caelus/commands", {
    method: "POST",
    headers: { Origin: "https://evil.example", "Content-Type": "application/json" },
    body: "{}",
  });
  assert.throws(() => requireSameOriginJson(request, env), /Origin/);
});

test("rejects non-json mutation", () => {
  const request = new Request("https://worker.example/api/projects/caelus/commands", {
    method: "POST",
    headers: { Origin: env.KABAN_PUBLIC_ORIGIN, "Content-Type": "text/plain" },
    body: "{}",
  });
  assert.throws(() => requireSameOriginJson(request, env), /application\/json/);
});

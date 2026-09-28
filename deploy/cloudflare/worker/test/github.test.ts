import test from "node:test";
import assert from "node:assert/strict";
import { GithubDispatchClient } from "../src/github.ts";

test("GitHub dispatcher invokes injected fetch without rebinding this", async () => {
  const env = {
    GITHUB_OWNER: "owner",
    GITHUB_REPO: "repo",
    GITHUB_WORKFLOW_FILE: "workflow.yml",
    GITHUB_DISPATCH_TOKEN: "secret",
  } as any;

  const fetcher = function (
    this: unknown,
    _input: RequestInfo | URL,
    _init?: RequestInit,
  ): Promise<Response> {
    assert.equal(
      this,
      undefined,
      "fetch must not be invoked as an object method",
    );

    return Promise.resolve(
      new Response(null, { status: 204 }),
    );
  } as typeof fetch;

  const client = new GithubDispatchClient(env, fetcher);

  await client.dispatch(
    "11111111-1111-4111-8111-111111111111",
  );
});
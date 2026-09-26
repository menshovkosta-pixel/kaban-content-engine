import type { Env, GithubDispatcher } from "./types.ts";

export class DefinitiveDispatchError extends Error {
  readonly ambiguous = false;
}

export class AmbiguousDispatchError extends Error {
  readonly ambiguous = true;
}

export class GithubDispatchClient implements GithubDispatcher {
  private readonly env: Env;
  private readonly fetcher: typeof fetch;

  constructor(env: Env, fetcher: typeof fetch = fetch) {
    this.env = env;
    this.fetcher = fetcher;
  }

  async dispatch(executionId: string): Promise<void> {
    const url = `https://api.github.com/repos/${encodeURIComponent(this.env.GITHUB_OWNER)}/${encodeURIComponent(this.env.GITHUB_REPO)}/actions/workflows/${encodeURIComponent(this.env.GITHUB_WORKFLOW_FILE)}/dispatches`;
    let response: Response;
    try {
      response = await this.fetcher(url, {
        method: "POST",
        headers: {
          Accept: "application/vnd.github+json",
          Authorization: `Bearer ${this.env.GITHUB_DISPATCH_TOKEN}`,
          "Content-Type": "application/json",
          "User-Agent": "KABAN-Control-Plane",
          "X-GitHub-Api-Version": "2022-11-28",
        },
        body: JSON.stringify({ ref: "main", inputs: { execution_id: executionId } }),
      });
    } catch (error) {
      throw new AmbiguousDispatchError(`GitHub dispatch transport ambiguity: ${String(error)}`);
    }
    if (!response.ok) {
      const body = await response.text();
      throw new DefinitiveDispatchError(`GitHub dispatch HTTP ${response.status}: ${body.slice(0, 200)}`);
    }
  }
}

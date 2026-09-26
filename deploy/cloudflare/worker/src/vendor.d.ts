declare module "jose" {
  export function createRemoteJWKSet(url: URL): unknown;
  export function jwtVerify(token: string, key: unknown, options: { issuer: string; audience: string }): Promise<{ payload: Record<string, unknown> }>;
}
interface ScheduledController {}
interface ExecutionContext {
  waitUntil(promise: Promise<unknown>): void;
}

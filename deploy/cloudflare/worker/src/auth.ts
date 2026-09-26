import type { AccessIdentity, Env } from "./types.ts";

export class HttpError extends Error {
  readonly status: number;
  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

type Verifier = (token: string, env: Env) => Promise<Partial<AccessIdentity>>;

type JwtHeader = { alg?: string; kid?: string };
type JwtPayload = { iss?: string; aud?: string | string[]; exp?: number; nbf?: number; sub?: string; email?: string };
type Jwk = JsonWebKey & { kid?: string; alg?: string };

function decodeBase64Url(value: string): Uint8Array {
  const normalized = value.replace(/-/g, "+").replace(/_/g, "/").padEnd(Math.ceil(value.length / 4) * 4, "=");
  const binary = atob(normalized);
  return Uint8Array.from(binary, (char) => char.charCodeAt(0));
}

function decodeJson<T>(value: string): T {
  return JSON.parse(new TextDecoder().decode(decodeBase64Url(value))) as T;
}

function accessIssuer(env: Env): string {
  const host = env.CF_ACCESS_TEAM_DOMAIN.replace(/^https?:\/\//, "").replace(/\/$/, "");
  return `https://${host}`;
}

function audienceMatches(actual: string | string[] | undefined, expected: string): boolean {
  return typeof actual === "string" ? actual === expected : Array.isArray(actual) && actual.includes(expected);
}

export async function verifyCloudflareAccessJwt(token: string, env: Env): Promise<AccessIdentity> {
  const parts = token.split(".");
  if (parts.length !== 3) throw new Error("Access JWT имеет неверный формат");
  const [encodedHeader, encodedPayload, encodedSignature] = parts;
  const header = decodeJson<JwtHeader>(encodedHeader);
  const payload = decodeJson<JwtPayload>(encodedPayload);
  if (header.alg !== "RS256" || !header.kid) throw new Error("Access JWT использует неподдерживаемый ключ");

  const issuer = accessIssuer(env);
  if (payload.iss !== issuer || !audienceMatches(payload.aud, env.CF_ACCESS_AUD)) throw new Error("Access JWT issuer/audience недействителен");
  const now = Math.floor(Date.now() / 1000);
  if (payload.exp !== undefined && payload.exp <= now) throw new Error("Access JWT истёк");
  if (payload.nbf !== undefined && payload.nbf > now) throw new Error("Access JWT ещё не действует");

  const response = await fetch(`${issuer}/cdn-cgi/access/certs`, { headers: { Accept: "application/json" } });
  if (!response.ok) throw new Error(`Access JWKS HTTP ${response.status}`);
  const body = await response.json() as { keys?: Jwk[] };
  const jwk = body.keys?.find((candidate) => candidate.kid === header.kid);
  if (!jwk) throw new Error("Access JWT signing key не найден");
  const key = await crypto.subtle.importKey(
    "jwk",
    jwk,
    { name: "RSASSA-PKCS1-v1_5", hash: "SHA-256" },
    false,
    ["verify"],
  );
  const signed = new TextEncoder().encode(`${encodedHeader}.${encodedPayload}`);
  const verified = await crypto.subtle.verify("RSASSA-PKCS1-v1_5", key, decodeBase64Url(encodedSignature), signed);
  if (!verified) throw new Error("Access JWT signature недействительна");

  const email = typeof payload.email === "string" ? payload.email : "";
  const sub = typeof payload.sub === "string" ? payload.sub : "";
  if (!email || !sub) throw new Error("Access JWT не содержит sub/email");
  return { sub, email, actor: email };
}

export async function requireAccess(request: Request, env: Env, verifier: Verifier = verifyCloudflareAccessJwt): Promise<AccessIdentity> {
  const token = request.headers.get("Cf-Access-Jwt-Assertion");
  if (!token) throw new HttpError(401, "Cloudflare Access assertion отсутствует");
  try {
    const identity = await verifier(token, env);
    const sub = typeof identity.sub === "string" ? identity.sub : "";
    const email = typeof identity.email === "string" ? identity.email : "";
    if (!sub) throw new Error("Access identity не содержит sub");
    return { sub, email, actor: email || sub };
  } catch {
    throw new HttpError(401, "Cloudflare Access assertion недействителен");
  }
}

export const verifyAccess = requireAccess;

export function requireSameOriginJson(request: Request, env: Env): void {
  if (request.method !== "POST") return;
  if (request.headers.get("Origin") !== env.KABAN_PUBLIC_ORIGIN) throw new HttpError(403, "Origin запрещён");
  const contentType = request.headers.get("Content-Type") ?? "";
  if (!contentType.toLowerCase().startsWith("application/json")) throw new HttpError(415, "Mutation требует application/json");
}

export const assertMutationRequest = requireSameOriginJson;

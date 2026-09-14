// API client + shared types for the AndroRemote console.

export const TOKEN_KEY = "artoken";

export function getToken(): string {
  return localStorage.getItem(TOKEN_KEY) || "";
}
export function setToken(t: string) {
  localStorage.setItem(TOKEN_KEY, t);
}

export class ApiError extends Error {
  status: number;
  /** parsed JSON body of the failed response, when the server sent one */
  payload?: unknown;
  constructor(status: number, message: string, payload?: unknown) {
    super(message);
    this.status = status;
    this.payload = payload;
  }
}

export async function api<T = unknown>(path: string, opts: RequestInit = {}): Promise<T> {
  const headers: Record<string, string> = { "Content-Type": "application/json" };
  const tok = getToken();
  if (tok) headers["Authorization"] = `Bearer ${tok}`;
  const res = await fetch(path, { ...opts, headers });
  if (res.status === 401) throw new ApiError(401, "unauthorized");
  const contentType = (res.headers.get("content-type") ?? "").split(";")[0];
  if (!contentType.includes("application/json")) {
    throw new ApiError(
      res.status,
      `${path} answered ${res.status} ${res.statusText} with ${contentType || "no content type"}, not JSON. The API backend is not reachable at this origin - start the C2 server with --web, or point the dev proxy at it with VITE_API_TARGET.`,
    );
  }
  let body: unknown = {};
  try { body = await res.json(); } catch { /* empty body */ }
  if (!res.ok) {
    const detail = body && typeof body === "object" && "error" in body && typeof body.error === "string" ? body.error : res.statusText;
    throw new ApiError(res.status, detail, body);
  }
  return body as T; // shape is the caller's declared contract; content-type is validated above
}

export interface SessionInfo {
  cid: string; tag: string; model: string;
  status: "online" | "idle" | "offline";
  last_seen: number; last_seen_age: number;
  pending: number; enc: boolean; seq: number;
  has_result: boolean; last_cmd: string | null;
}
export interface PluginInfo { name: string; version: string; description: string; enabled: boolean }
export interface ServerInfo {
  uptime: number; port: number; tls: boolean; enc: boolean; key_fp: string | null;
  tunnel_url: string | null; tunnel_mode: string; tunnel_host: string | null;
  tunnel_running: boolean; tunnel_alive: boolean;
  plugins: PluginInfo[]; web_port: number | null;
}
export interface Snapshot { server: ServerInfo; active: string | null; sessions: SessionInfo[] }
export interface LogEvent { type: "log"; ts: number; sym: string; msg: string }
export interface ResultEvent {
  type: "result"; ts: number; cid: string; tag: string; cmd: string;
  result: string; truncated: boolean; ok: boolean;
}
export interface SessionEvent { type: "session"; event: string; cid: string; tag?: string; model?: string }
export type BusEvent = LogEvent | ResultEvent | SessionEvent;

export const postOp = <T = unknown>(op: string, args: Record<string, unknown> = {}, extra: Record<string, unknown> = {}) =>
  api<T>("/api/op", { method: "POST", body: JSON.stringify({ op, args, ...extra }) });

export const runCmd = (cmd: string, cid: string | null) =>
  api<{ cmd: string; ok: boolean | null; result: string }>("/api/cmd", { method: "POST", body: JSON.stringify({ cmd, cid }) });

export const sessionAction = (action: string, cid: string, alias?: string) =>
  api("/api/session", { method: "POST", body: JSON.stringify({ action, cid, alias }) });

/** Fetch a binary artifact with the bearer token and save it via the browser.
 * A plain <a href> cannot carry the header, so token-protected servers 401 it. */
export async function downloadFile(url: string, filename: string): Promise<void> {
  const headers: Record<string, string> = {};
  const tok = getToken();
  if (tok) headers["Authorization"] = `Bearer ${tok}`;
  const res = await fetch(url, { headers });
  if (!res.ok) throw new ApiError(res.status, `download failed (${res.status})`);
  const blob = await res.blob();
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = filename;
  a.click();
  URL.revokeObjectURL(a.href);
}

// ── builds ────────────────────────────────────────────────────────────────

export interface BuildRecord {
  file: string;
  latest: boolean;
  /** UTC, format YYYYMMDDTHHMMSSZ */
  built_at: string;
  /** baked C2 base URL; empty means adb-direct (no C2 endpoint) */
  c2_url: string;
  enc: boolean;
  pin_set: boolean;
  /** short fingerprint of the payload key, never the key */
  psk_fp: string | null;
  signer_sha256: string | null;
  sha256: string;
  size: number;
  keystore: string;
  download_url: string;
}

export interface BuildConfig {
  c2_url: string;
  enc: boolean;
  psk_fp: string | null;
  pin_available: boolean;
  signer_sha256: string | null;
}

export interface BuildEnv {
  cloudflared: boolean;
  build_tools: boolean;
  keystore: boolean;
  signer_sha256: string | null;
  missing: string[];
}

export interface BuildsResponse {
  builds: BuildRecord[];
  next: BuildConfig;
  env: BuildEnv;
  building: boolean;
}

export interface BuildResult {
  ok: boolean;
  code: number;
  log: string[];
  c2_url: string;
  builds?: BuildRecord[];
  next?: BuildConfig;
}

export const listBuilds = () => api<BuildsResponse>("/api/builds");

/** Empty url builds for whatever the server currently exposes (tunnel, then named tunnel, then adb-direct). */
export const runBuild = (url = "") =>
  api<BuildResult>("/api/build", { method: "POST", body: JSON.stringify({ url }) });

export const setTunnelMode = (mode: "off" | "quick" | "named") =>
  api<{ ok: boolean; mode: string }>("/api/tunnel/mode", { method: "POST", body: JSON.stringify({ mode }) });

export const setupTunnel = (hostname: string) =>
  api<{ ok: boolean; tunnel_url: string }>("/api/tunnel/setup", { method: "POST", body: JSON.stringify({ hostname }) });

/** Shape of the 409 body from /api/tunnel/setup when cloudflared is not logged in.
 * Surfaces on the caught ApiError as `payload`. */
export interface TunnelLoginRequired {
  needs_login: true;
  command: string;
  detail: string;
}

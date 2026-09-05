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
  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

export async function api<T = any>(path: string, opts: RequestInit = {}): Promise<T> {
  const headers: Record<string, string> = { "Content-Type": "application/json" };
  const tok = getToken();
  if (tok) headers["Authorization"] = `Bearer ${tok}`;
  const res = await fetch(path, { ...opts, headers });
  if (res.status === 401) throw new ApiError(401, "unauthorized");
  let body: any = {};
  try { body = await res.json(); } catch { /* empty body */ }
  if (!res.ok) throw new ApiError(res.status, body.error || res.statusText);
  return body as T;
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

export const postOp = <T = any>(op: string, args: Record<string, unknown> = {}, extra: Record<string, unknown> = {}) =>
  api<T>("/api/op", { method: "POST", body: JSON.stringify({ op, args, ...extra }) });

export const runCmd = (cmd: string, cid: string | null) =>
  api<{ cmd: string; ok: boolean | null; result: string }>("/api/cmd", { method: "POST", body: JSON.stringify({ cmd, cid }) });

export const sessionAction = (action: string, cid: string, alias?: string) =>
  api("/api/session", { method: "POST", body: JSON.stringify({ action, cid, alias }) });

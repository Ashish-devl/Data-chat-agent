/** Talks to the DataChat Agent API with the signed-in user's session token. */

const TOKEN_KEY = "datachat-console-token";

/** The API's address: VITE_API_URL, or the server this console is served from (at /console). */
export function apiBase(): string {
  const fromEnv = import.meta.env.VITE_API_URL as string | undefined;
  if (fromEnv) return fromEnv.replace(/\/+$/, "");
  const prefix = location.pathname.replace(/\/console(\/.*)?$/, "");
  return `${location.origin}${prefix === "/" ? "" : prefix}`;
}

export function getToken(): string | null {
  try {
    return localStorage.getItem(TOKEN_KEY);
  } catch {
    return null;
  }
}

export function setToken(token: string | null) {
  try {
    if (token) localStorage.setItem(TOKEN_KEY, token);
    else localStorage.removeItem(TOKEN_KEY);
  } catch {
    /* private mode: stay signed in for this tab only */
  }
}

export class ApiError extends Error {
  constructor(readonly status: number, message: string) {
    super(message);
  }
}

function describe(detail: unknown, status: number): string {
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail) && detail[0]?.msg) return String(detail[0].msg);
  return `Request failed (${status})`;
}

export async function api<T = unknown>(path: string, init: RequestInit = {}): Promise<T> {
  const headers: Record<string, string> = {};
  const token = getToken();
  if (token) headers.Authorization = `Bearer ${token}`;
  if (typeof init.body === "string") headers["Content-Type"] = "application/json";
  const res = await fetch(`${apiBase()}/v1${path}`, { ...init, headers });
  if (res.status === 401 && token) {
    setToken(null);
    window.dispatchEvent(new Event("datachat-signed-out"));
  }
  if (!res.ok) {
    let detail: unknown = res.statusText;
    try {
      detail = (await res.json()).detail;
    } catch {
      /* not JSON */
    }
    throw new ApiError(res.status, describe(detail, res.status));
  }
  if (res.status === 204) return undefined as T;
  const type = res.headers.get("content-type") ?? "";
  return (type.includes("json") ? res.json() : res.text()) as Promise<T>;
}

export const post = <T,>(path: string, body?: unknown) =>
  api<T>(path, { method: "POST", body: body === undefined ? undefined : JSON.stringify(body) });
export const patch = <T,>(path: string, body: unknown) =>
  api<T>(path, { method: "PATCH", body: JSON.stringify(body) });
export const del = (path: string) => api<void>(path, { method: "DELETE" });

export interface Me {
  id: string;
  email: string;
  name: string | null;
  role: "owner" | "member";
  tenant_id: string;
  tenant_name: string;
}

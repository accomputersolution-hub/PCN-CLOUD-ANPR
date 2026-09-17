import type { LoginResponse } from "./types";

const API_BASE = import.meta.env.VITE_API_BASE_URL || "/api/v1";

let accessToken: string | null = localStorage.getItem("pcn_access");
let refreshToken: string | null = localStorage.getItem("pcn_refresh");

export function setTokens(access: string | null, refresh: string | null) {
  accessToken = access;
  refreshToken = refresh;
  if (access) localStorage.setItem("pcn_access", access);
  else localStorage.removeItem("pcn_access");
  if (refresh) localStorage.setItem("pcn_refresh", refresh);
  else localStorage.removeItem("pcn_refresh");
}

export function getAccessToken() {
  return accessToken;
}

async function refreshAccess(): Promise<boolean> {
  if (!refreshToken) return false;
  const res = await fetch(`${API_BASE}/auth/refresh`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ refresh_token: refreshToken }),
  });
  if (!res.ok) {
    setTokens(null, null);
    return false;
  }
  const body = (await res.json()) as { access_token: string; refresh_token: string };
  setTokens(body.access_token, body.refresh_token);
  return true;
}

export class ApiError extends Error {
  status: number;
  code: string;
  path: string;
  constructor(status: number, code: string, message: string, path = "") {
    super(message);
    this.status = status;
    this.code = code;
    this.path = path;
  }
}

function formatApiFailure(path: string, status: number, message: string): string {
  const endpoint = `${API_BASE}${path}`;
  if (import.meta.env.DEV) {
    return `${message} (${status} ${endpoint})`;
  }
  return message;
}

export async function api<T>(path: string, init: RequestInit = {}, retry = true): Promise<T> {
  const headers = new Headers(init.headers);
  if (!(init.body instanceof FormData) && !headers.has("Content-Type") && init.body) {
    headers.set("Content-Type", "application/json");
  }
  if (accessToken) headers.set("Authorization", `Bearer ${accessToken}`);
  let res: Response;
  try {
    res = await fetch(`${API_BASE}${path}`, { ...init, headers });
  } catch (err) {
    const reason = err instanceof Error ? err.message : "Network request failed";
    throw new ApiError(0, "network_error", formatApiFailure(path, 0, reason), path);
  }
  if (res.status === 401 && retry && refreshToken) {
    const ok = await refreshAccess();
    if (ok) return api<T>(path, init, false);
  }
  if (!res.ok) {
    let code = "error";
    let message = res.statusText || `HTTP ${res.status}`;
    try {
      const body = await res.json();
      const detail = body.detail ?? body;
      code = detail.code ?? code;
      message = detail.message ?? (typeof detail === "string" ? detail : JSON.stringify(detail));
    } catch {
      /* ignore */
    }
    throw new ApiError(res.status, code, formatApiFailure(path, res.status, message), path);
  }
  if (res.status === 204) return undefined as T;
  const text = await res.text();
  if (!text) return undefined as T;
  return JSON.parse(text) as T;
}

export async function loginRequest(email: string, password: string) {
  const body = await api<LoginResponse>("/auth/login", {
    method: "POST",
    body: JSON.stringify({ email, password }),
  });
  setTokens(body.access_token, body.refresh_token);
  return body;
}

export async function logoutRequest() {
  if (refreshToken && accessToken) {
    try {
      await api("/auth/logout", {
        method: "POST",
        body: JSON.stringify({ refresh_token: refreshToken }),
      });
    } catch {
      /* ignore */
    }
  }
  setTokens(null, null);
}

export function wsUrl() {
  const configured = import.meta.env.VITE_WS_BASE_URL;
  if (configured) return `${configured}/ws?token=${accessToken ?? ""}`;
  const proto = window.location.protocol === "https:" ? "wss:" : "ws:";
  return `${proto}//${window.location.host}/api/v1/ws?token=${accessToken ?? ""}`;
}

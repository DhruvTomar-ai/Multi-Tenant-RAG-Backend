import { ApiError } from "./errors";
import type { Tokens } from "./types";

const API = "/api/v1";
const REFRESH_KEY = "rag.refresh";
const DEFAULT_TIMEOUT_MS = 30_000;
/** Long enough for retrieval plus the backend's Groq timeout and retries. */
export const CHAT_TIMEOUT_MS = 120_000;
export const UPLOAD_TIMEOUT_MS = 180_000;
/** The first search after a restart loads the embedding model. */
export const SEARCH_TIMEOUT_MS = 60_000;

// The access token stays in memory, out of reach of anything that reads browser storage. The refresh
// token is in sessionStorage so a reload keeps the session, and it is gone when the tab closes. The
// backend returns tokens only as JSON, so httpOnly cookies are not available without changing it.
let accessToken: string | null = null;
let refreshing: Promise<boolean> | null = null;
let onExpired: () => void = () => {};

export const session = {
  hasRefreshToken: () => sessionStorage.getItem(REFRESH_KEY) !== null,
  store(tokens: Tokens) {
    accessToken = tokens.access_token;
    sessionStorage.setItem(REFRESH_KEY, tokens.refresh_token);
  },
  clear() {
    accessToken = null;
    sessionStorage.removeItem(REFRESH_KEY);
  },
  onExpired(callback: () => void) {
    onExpired = callback;
  },
};

interface RequestOptions {
  method?: string;
  json?: unknown;
  form?: FormData;
  query?: Record<string, string | number | undefined>;
  /** False for login, register and refresh: a 401 there is an answer, not an expired session. */
  authenticated?: boolean;
  timeoutMs?: number;
}

function buildUrl(path: string, query: RequestOptions["query"]) {
  const params = new URLSearchParams();
  for (const [key, value] of Object.entries(query ?? {})) {
    if (value !== undefined && value !== "") params.set(key, String(value));
  }
  const suffix = params.toString();
  return `${API}${path}${suffix ? `?${suffix}` : ""}`;
}

async function send(path: string, options: RequestOptions): Promise<Response> {
  const headers: Record<string, string> = {};
  if (options.json !== undefined) headers["Content-Type"] = "application/json";
  if (options.authenticated !== false && accessToken) headers.Authorization = `Bearer ${accessToken}`;
  try {
    return await fetch(buildUrl(path, options.query), {
      method: options.method ?? "GET",
      headers,
      body: options.form ?? (options.json !== undefined ? JSON.stringify(options.json) : undefined),
      signal: AbortSignal.timeout(options.timeoutMs ?? DEFAULT_TIMEOUT_MS),
    });
  } catch (error) {
    if (error instanceof DOMException && error.name === "TimeoutError") throw new ApiError("timeout", 0);
    throw new ApiError("network", 0);
  }
}

export function refreshSession(): Promise<boolean> {
  // Parallel 401s share one refresh, so a burst of requests cannot race each other.
  refreshing ??= (async () => {
    const refresh_token = sessionStorage.getItem(REFRESH_KEY);
    if (!refresh_token) return false;
    const response = await send("/auth/refresh", { method: "POST", json: { refresh_token }, authenticated: false });
    if (!response.ok) {
      if (response.status === 401) return false;
      throw await toError(response);
    }
    session.store((await response.json()) as Tokens);
    return true;
  })().finally(() => {
    refreshing = null;
  });
  return refreshing;
}

async function toError(response: Response): Promise<ApiError> {
  let detail: unknown;
  let code: string | undefined;
  try {
    const body = (await response.json()) as { detail?: unknown; code?: string };
    detail = body.detail;
    code = body.code;
  } catch {
    // Not JSON, such as a proxy error page. The status alone decides the message.
  }
  return new ApiError("http", response.status, detail, code, response.headers.get("Retry-After"));
}

async function authorized(path: string, options: RequestOptions): Promise<Response> {
  let response = await send(path, options);
  if (response.status === 401 && options.authenticated !== false) {
    if (await refreshSession()) response = await send(path, options);
    if (response.status === 401) {
      session.clear();
      onExpired();
    }
  }
  if (!response.ok) throw await toError(response);
  return response;
}

export async function api<T>(path: string, options: RequestOptions = {}): Promise<T> {
  const response = await authorized(path, options);
  return (response.status === 204 ? undefined : await response.json()) as T;
}

export async function apiBlob(path: string): Promise<Blob> {
  return (await authorized(path, { timeoutMs: UPLOAD_TIMEOUT_MS })).blob();
}

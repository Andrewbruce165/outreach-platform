import { supabase } from "./supabase";
import { errorMessageFromEnvelope } from "./error-codes";

const BACKEND_URL =
  (import.meta.env.VITE_BACKEND_URL as string | undefined)?.replace(/\/$/, "") ??
  "";

export class ApiError extends Error {
  status: number;
  code: string;
  detail: unknown;
  constructor(status: number, code: string, message: string, detail?: unknown) {
    super(message);
    this.status = status;
    this.code = code;
    this.detail = detail;
  }
}

type Json = Record<string, unknown> | unknown[] | string | number | boolean | null;

interface ApiOptions {
  method?: "GET" | "POST" | "PATCH" | "PUT" | "DELETE";
  body?: Json | FormData;
  query?: Record<string, string | number | boolean | undefined | null>;
  signal?: AbortSignal;
  /** Skip Authorization header (used for unauthenticated endpoints). */
  anonymous?: boolean;
}

async function getAccessToken(): Promise<string | null> {
  if (typeof window === "undefined") return null;
  try {
    const { data } = await supabase.auth.getSession();
    return data.session?.access_token ?? null;
  } catch {
    return null;
  }
}

async function waitForAccessToken(): Promise<string | null> {
  for (let i = 0; i < 20; i++) {
    const token = await getAccessToken();
    if (token) return token;
    await new Promise((resolve) => setTimeout(resolve, 100));
  }
  return null;
}

function buildUrl(path: string, query?: ApiOptions["query"]) {
  const base = BACKEND_URL || "";
  const url = new URL(path.startsWith("http") ? path : base + path, base || window.location.origin);
  if (query) {
    for (const [k, v] of Object.entries(query)) {
      if (v === undefined || v === null) continue;
      url.searchParams.set(k, String(v));
    }
  }
  return url.toString();
}

export async function api<T = unknown>(path: string, opts: ApiOptions = {}): Promise<T> {
  const headers: Record<string, string> = {};
  let body: BodyInit | undefined;

  if (opts.body !== undefined) {
    if (opts.body instanceof FormData) {
      body = opts.body;
    } else {
      headers["Content-Type"] = "application/json";
      body = JSON.stringify(opts.body);
    }
  }

  let token: string | null = null;
  if (!opts.anonymous) {
    token = await waitForAccessToken();
    if (token) headers["Authorization"] = `Bearer ${token}`;
  }

  const res = await fetch(buildUrl(path, opts.query), {
    method: opts.method ?? "GET",
    headers,
    body,
    signal: opts.signal,
  });

  if (res.status === 204) return undefined as T;

  const contentType = res.headers.get("content-type") ?? "";
  const payload = contentType.includes("application/json") ? await res.json() : await res.text();

  if (!res.ok) {
    // Backend envelope: { detail: { code, message } } or { detail: "..." }
    const detail = (payload as { detail?: unknown })?.detail;
    let code = "UNKNOWN";
    let message = `Request failed (${res.status})`;
    if (detail && typeof detail === "object" && "code" in detail) {
      code = String((detail as { code: unknown }).code);
      message = errorMessageFromEnvelope(code, detail as Record<string, unknown>);
    } else if (typeof detail === "string") {
      message = detail;
    } else if (res.status >= 500) {
      code = "SERVER_ERROR";
      message = errorMessageFromEnvelope(code, {});
    }

    if (res.status === 401 && token && (code === "TOKEN_EXPIRED" || code === "TOKEN_INVALID")) {
      // Hook for sign-out / redirect handled in the route layer.
      if (typeof window !== "undefined") {
        window.dispatchEvent(new CustomEvent("aimly:auth-expired"));
      }
    }

    throw new ApiError(res.status, code, message, detail);
  }

  return payload as T;
}

export const apiBaseUrl = BACKEND_URL;

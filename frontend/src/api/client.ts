/**
 * Minimal fetch client.
 *
 * S1 only needs ``/healthz``/``/readyz`` and a single authenticated catalog
 * call. Auth is the dev skeleton (X-Ugrile-Identity / X-Ugrile-Tenant).
 */

export interface HealthReport {
  status: "ok" | "degraded" | "down";
  database: boolean;
  schema_version: string;
  app_version: string;
}

export interface ApiError extends Error {
  code?: string;
  details?: Record<string, unknown>;
  status: number;
}

export interface ApiClient {
  healthz(): Promise<HealthReport>;
  readyz(): Promise<HealthReport>;
  get<T>(path: string, init?: RequestInit): Promise<T>;
  post<T>(path: string, body: unknown, init?: RequestInit): Promise<T>;
}

export interface ClientConfig {
  baseUrl: string;
  identity?: string;
  tenant?: string;
}

export function createApiClient(config: ClientConfig): ApiClient {
  const baseUrl = config.baseUrl.replace(/\/$/, "");

  const headers = (): HeadersInit => {
    const h: Record<string, string> = { "Content-Type": "application/json" };
    if (config.identity) h["X-Ugrile-Identity"] = config.identity;
    if (config.tenant) h["X-Ugrile-Tenant"] = config.tenant;
    return h;
  };

  const request = async <T>(
    method: string,
    path: string,
    body?: unknown,
    init?: RequestInit,
  ): Promise<T> => {
    const r = await fetch(`${baseUrl}${path}`, {
      method,
      headers: { ...headers(), ...(init?.headers ?? {}) },
      body: body === undefined ? undefined : JSON.stringify(body),
      ...init,
    });
    if (!r.ok) {
      let detail: unknown = undefined;
      try {
        detail = await r.json();
      } catch {
        detail = await r.text();
      }
      const err: ApiError = Object.assign(
        new Error(`API ${method} ${path} -> ${r.status}`),
        {
          status: r.status,
          code:
            typeof detail === "object" && detail !== null && "code" in detail
              ? String((detail as { code: unknown }).code)
              : undefined,
          details:
            typeof detail === "object" && detail !== null && "details" in detail
              ? ((detail as { details: unknown }).details as Record<string, unknown>)
              : undefined,
        },
      );
      throw err;
    }
    if (r.status === 204) {
      return undefined as T;
    }
    return (await r.json()) as T;
  };

  return {
    healthz: () => request<HealthReport>("GET", "/healthz"),
    readyz: () => request<HealthReport>("GET", "/readyz"),
    get: <T>(path: string, init?: RequestInit) => request<T>("GET", path, undefined, init),
    post: <T>(path: string, body: unknown, init?: RequestInit) =>
      request<T>("POST", path, body, init),
  };
}
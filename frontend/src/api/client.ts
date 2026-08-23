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
  post<T>(path: string, body?: unknown, init?: RequestInit): Promise<T>;
}

export interface ClientConfig {
  baseUrl: string;
  identity?: string;
  tenant?: string;
}

// ---------------------------------------------------------------------------
// Domain types for the S4 manager UI.
// ---------------------------------------------------------------------------

export interface OverviewKpi {
  stores_total: number;
  stores_covered: number;
  days_uncovered: number;
  conflicts: number;
  extra_home_days: number;
  extra_other_days: number;
  sales_unattributed: number;
  epay_invalid: number;
  epay_fresh: boolean;
  sheet_sync_total: number;
  sheet_sync_stale: number;
  sheet_sync_error: number;
}

export interface OverviewManagerRow {
  user_id: string;
  display_name: string;
  stores_covered: number;
  stores_total: number;
  days_uncovered: number;
  last_sync: string | null;
}

export interface OverviewNeedsAttention {
  code: string;
  severity: number;
  title: string;
  detail: string;
  store_id: string | null;
  person_id: string | null;
  business_date: string | null;
}

export interface OverviewReport {
  month_id: string;
  year: number;
  month: number;
  state: string;
  revision: number;
  rule_pack_version: string | null;
  kpis: OverviewKpi;
  managers: OverviewManagerRow[];
  needs_attention: OverviewNeedsAttention[];
}

export interface ProgramCell {
  business_date: string;
  person_id: string | null;
  store_id: string | null;
  status: string;
  working_kind: string | null;
  display_name: string | null;
  home_store_id: string | null;
  badge: string | null;
  locked: boolean;
}

export interface ProgramRow {
  row_id: string;
  label: string;
  home_store_id: string | null;
  cells: ProgramCell[];
}

export interface ProgramChoice {
  person_id: string;
  display_name: string;
  home_store_id: string;
  allowed_store_ids: string[];
  working_kinds: string[];
}

export interface ProgramChoices {
  month_id: string;
  business_date: string;
  store_id: string;
  choices: ProgramChoice[];
}

export interface ProgramGrid {
  month_id: string;
  year: number;
  month: number;
  revision: number;
  dates: string[];
  rows: ProgramRow[];
  legend: string[];
}

export interface ExceptionEntry {
  code: string;
  severity: number;
  title: string;
  detail: string;
  blocking_close: boolean;
  store_id: string | null;
  person_id: string | null;
  business_date: string | null;
  action_hint: string;
}

export interface ChecklistItem {
  code: string;
  severity: number;
  title: string;
  detail: string;
  blocking: boolean;
}

export interface CloseChecklist {
  month_id: string;
  revision: number;
  state: string;
  blockers: ChecklistItem[];
  generated_at: string | null;
  export_summary: Record<string, unknown>[];
  job_summary: Record<string, unknown>[];
  expected_revision: number;
}

export interface PontajTotalsResponse {
  month_id: string;
  revision: number;
  totals: Record<
    string,
    { working_days: number; leave_days: number; off_days: number; hours: number }
  >;
}

export interface MonthSummary {
  id: string;
  tenant_id: string;
  year: number;
  month: number;
  state: string;
  revision: number;
  closed_at: string | null;
}

export interface StoreSummary {
  id: string;
  tenant_id: string;
  company_code: string;
  internal_code: string;
  external_code: string | null;
  name: string;
  is_active: boolean;
}

export interface PersonSummary {
  id: string;
  tenant_id: string;
  internal_code: string;
  external_code: string | null;
  display_name: string;
  home_store_id: string;
  is_active: boolean;
}

export interface AttributionRow {
  person_id: string;
  store_id: string;
  business_date: string;
  amount: number | string;
  currency: string;
  generation: string;
  working_kind: string;
  revision: number;
}

export interface AttributionResponse {
  month_id: string;
  revision: number;
  total_rows: number;
  company_total: number | string;
  rows: AttributionRow[];
  anomalies: Record<string, unknown>[];
}

export interface GridCalculation {
  id: number;
  tenant_id: string;
  month_id: string;
  store_id: string;
  person_id: string;
  rule_pack_version: string;
  revision: number;
  inputs_hash: string;
  outputs_hash: string;
  payload: string;
}

export interface EpayFreshness {
  store_id: string;
  is_fresh: boolean;
  fresh_count: number;
  expected_count: number;
  threshold: string;
}

export interface EpayReadbackItem {
  person_id: string;
  category: string;
  value: number | null;
  raw_value: string | null;
  is_valid: boolean;
}

export interface EpayReadback {
  store_id: string;
  month_id: string;
  observed_at: string;
  valid_count: number;
  invalid_count: number;
  items: EpayReadbackItem[];
}

export interface SheetProjection {
  store_id: string;
  generation: string;
  last_success_generation: string | null;
  last_run_at: string | null;
  last_error: string | null;
  failures: number;
  payload: { grila: Record<string, unknown>; pontaj: Record<string, unknown> } | null;
}

export interface SheetReconciliation {
  store_id: string;
  month_id: string;
  available: boolean;
  generation: string | null;
  format_version: string | null;
  revision: number | null;
  rule_pack_version: string | null;
  projected_at: string | null;
  verification_mode: string | null;
  verified: boolean;
  grila_rows: number | null;
  pontaj_rows: number | null;
  grila_checksum_sha256: string | null;
  pontaj_checksum_sha256: string | null;
  projection_checksum_sha256: string | null;
}

export interface CloseOutcome {
  month_id: string;
  revision: number;
  new_state: string;
  audit_event_id: number;
  blockers: Array<Record<string, unknown>>;
}

// ---------------------------------------------------------------------------
// Client
// ---------------------------------------------------------------------------

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
    post: <T>(path: string, body?: unknown, init?: RequestInit) =>
      request<T>("POST", path, body, init),
  };
}

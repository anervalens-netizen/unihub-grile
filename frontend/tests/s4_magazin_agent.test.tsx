import { describe, expect, it, vi } from "vitest";
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import "@testing-library/jest-dom/vitest";
import { Magazin } from "../src/pages/Magazin";
import { Agent } from "../src/pages/Agent";
import type { ApiClient, MonthSummary } from "../src/api/client";
import type { Capability } from "../src/capabilities";

const MONTH: MonthSummary = { id: "month_tenantacme_2026-08", tenant_id: "tenant_acme", year: 2026, month: 8, state: "OPEN", revision: 2, closed_at: null };
const store = { id: "store_x", tenant_id: "tenant_acme", company_code: "ACME", internal_code: "SX", external_code: null, name: "Demo Store", is_active: true };
const person = { id: "person_a", tenant_id: "tenant_acme", internal_code: "PA", external_code: null, display_name: "Alice", home_store_id: "store_x", is_active: true };
const ADMIN_CAPABILITIES = new Set<Capability>(["schedule.read", "schedule.write", "grid.read", "epay.read", "sheet.read", "sheet.sync", "export.create", "jobs.read"]);
const MANAGER_CAPABILITIES = new Set<Capability>(["schedule.read", "schedule.write", "grid.read", "epay.read", "sheet.read", "jobs.read"]);
const JOB_DIAGNOSTICS_PATH = "/worker/jobs/diagnostics?terminal_limit=50";
const PROJECTION_CHECKSUM = "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef";

function makeGrid(revision = 2) {
  return { month_id: MONTH.id, year: 2026, month: 8, revision, dates: ["2026-08-01"], legend: ["NORMAL"], rows: [{ row_id: "person_a", label: "Alice", home_store_id: "store_x", cells: [{ business_date: "2026-08-01", person_id: "person_a", store_id: "store_x", status: "WORKING", working_kind: "NORMAL", display_name: "Alice", home_store_id: "store_x", badge: "NORMAL", locked: false }] }] };
}

const choices = {
  month_id: MONTH.id,
  business_date: "2026-08-01",
  store_id: "store_x",
  choices: [
    { person_id: "person_a", display_name: "Alice", home_store_id: "store_x", allowed_store_ids: ["store_x"], working_kinds: ["NORMAL", "EXTRA_HOME", "EXTRA_OTHER"] },
  ],
};

const gridPayload = JSON.stringify({
  inputs: { epay: { under_50: 3, at_or_over_50: 2 } },
  components: {
    total_salary: "1250.00",
    main_commission: "275.00",
    epay_commission: "45.00",
  },
  anomalies: [{ code: "TARGET_ZERO", store_id: "store_x", person_id: "person_a" }],
});

const diagnostics = {
  jobs: [
    { id: 71, kind: "EXPORT_XLSX_STORE", state: "DONE", attempts: 1, max_attempts: 3, last_error: null, month_id: MONTH.id, store_ids: ["store_x"] },
    { id: 72, kind: "GOOGLE_PROJECTION_STORE", state: "RETRY", attempts: 2, max_attempts: 4, last_error: "provider timeout", month_id: MONTH.id, store_ids: ["store_x"] },
    { id: 73, kind: "EXPORT_XLSX_STORE", state: "FAILED", attempts: 3, max_attempts: 3, last_error: "other store", month_id: MONTH.id, store_ids: ["store_y"] },
  ],
};

interface ApiOptions {
  programRevisions?: number[];
  postFailures?: Array<{ status: number; code?: string }>;
  jobsFailure?: boolean;
  failPaths?: string[];
}

function apiForPage({ programRevisions = [2], postFailures = [], jobsFailure = false, failPaths = [] }: ApiOptions = {}) {
  const calls: string[] = [];
  let programRead = 0;
  let postAttempt = 0;
  const api = { healthz: vi.fn(), readyz: vi.fn(), post: vi.fn(async (path: string) => {
    const failure = postFailures[postAttempt];
    postAttempt += 1;
    if (failure) throw failure;
    return { job_id: "job-1", path };
  }), get: vi.fn(async (path: string) => {
    calls.push(path);
    if (path === JOB_DIAGNOSTICS_PATH) {
      if (jobsFailure) throw new Error("jobs unavailable");
      return diagnostics;
    }
    const failedToken = failPaths.find((token) => path.includes(token));
    if (failedToken) throw new Error(`${failedToken} unavailable`);
    if (path === "/catalog/stores") return [store];
    if (path === "/catalog/people?store_id=store_x") return [person];
    if (path.includes("/program/choices")) return choices;
    if (path.includes("/program?perspective=")) {
      const revision = programRevisions[Math.min(programRead, programRevisions.length - 1)] ?? 2;
      programRead += 1;
      return makeGrid(revision);
    }
    if (path.includes("/pontaj-totals")) return { month_id: MONTH.id, revision: 2, totals: { person_a: { working_days: 1, leave_days: 0, off_days: 0, hours: 11 } } };
    if (path.includes("/attribution")) return { month_id: MONTH.id, revision: 2, total_rows: 1, company_total: "125.50", rows: [{ person_id: "person_a", store_id: "store_x", business_date: "2026-08-01", amount: "125.50", currency: "RON", generation: "g1", working_kind: "NORMAL", revision: 2 }], anomalies: [{ code: "ATTRIBUTION_WARNING", store_id: "store_x" }] };
    if (path.includes("/grid")) return [{ id: 1, tenant_id: "tenant_acme", month_id: MONTH.id, store_id: "store_x", person_id: "person_a", rule_pack_version: "v1", revision: 2, inputs_hash: "in", outputs_hash: "out", payload: gridPayload }];
    if (path.includes("/epay/freshness")) return { store_id: "store_x", is_fresh: true, fresh_count: 2, expected_count: 2, threshold: "2026-08-01" };
    if (path.includes("/sheet-reconciliation")) return { store_id: "store_x", month_id: MONTH.id, available: true, generation: "g1", format_version: "v2", revision: 2, rule_pack_version: "v1", projected_at: "2026-08-23T12:00:00+00:00", verification_mode: "live_readback", verified: true, grila_rows: 1, pontaj_rows: 1, grila_checksum_sha256: "a".repeat(64), pontaj_checksum_sha256: "b".repeat(64), projection_checksum_sha256: PROJECTION_CHECKSUM };
    if (path.includes("/sheet-projection")) return { store_id: "store_x", generation: "g1", last_success_generation: "g1", last_run_at: null, last_error: null, failures: 0, payload: null };
    throw new Error(`unexpected GET ${path}`);
  }) } as unknown as ApiClient;
  return { api, calls };
}

describe("Magazin and Agent contract routes", () => {
  it("uses scoped routes and exposes complete admin store command state", async () => {
    const { api, calls } = apiForPage();
    render(<Magazin api={api} storeId="store_x" months={[MONTH]} monthsError={null} capabilities={ADMIN_CAPABILITIES} />);
    expect(await screen.findByRole("heading", { name: /Demo Store/ })).toBeInTheDocument();
    expect(screen.getAllByText(/126/).length).toBeGreaterThan(0);
    expect(screen.getByText("g1")).toBeInTheDocument();
    expect(screen.getByText("Deschisă · rev. 2")).toBeInTheDocument();
    expect(screen.getByText("1 grilă · 1 atribuire")).toBeInTheDocument();
    expect(screen.getByText("Verificat")).toHaveClass("text-ok");
    expect(screen.getByText("rev. 2 · v1")).toBeInTheDocument();
    expect(screen.getByText("v2 · 0123456789ab")).toBeInTheDocument();
    expect(screen.getByText("2026-08-23T12:00:00+00:00")).toBeInTheDocument();
    expect(await screen.findByText(/Export XLSX #71/)).toBeInTheDocument();
    expect(screen.getByText(/Sheet #72/)).toBeInTheDocument();
    expect(screen.getByText(/Finalizat · 1\/3/)).toHaveClass("text-ok");
    expect(screen.getByText(/Reîncercare · 2\/4/)).toHaveClass("text-warn");
    expect(screen.getAllByText("Actualizat").length).toBeGreaterThan(0);

    fireEvent.click(screen.getByRole("tab", { name: "Grilă & Pontaj" }));
    expect(screen.getByText("1.250 RON")).toBeInTheDocument();
    expect(screen.getByText("275 RON")).toBeInTheDocument();
    expect(screen.getByText("45 RON")).toBeInTheDocument();
    expect(screen.getByText("3 / 2")).toBeInTheDocument();
    expect(screen.getByText("TARGET_ZERO")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("tab", { name: "Control" }));
    const initialDiagnosticsReads = calls.filter((path) => path === JOB_DIAGNOSTICS_PATH).length;
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: /Sincronizează Sheet/i }));
      await new Promise((resolve) => setTimeout(resolve, 0));
    });
    await waitFor(() => expect(calls.filter((path) => path === JOB_DIAGNOSTICS_PATH)).toHaveLength(initialDiagnosticsReads + 1));
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: /Exportă XLSX/i }));
      await new Promise((resolve) => setTimeout(resolve, 0));
    });
    await waitFor(() => expect(calls.filter((path) => path === JOB_DIAGNOSTICS_PATH)).toHaveLength(initialDiagnosticsReads + 2));
    expect(calls).toContain("/catalog/people?store_id=store_x");
    expect(calls).toContain(`/months/${MONTH.id}/sheet-reconciliation?store_id=store_x`);
    expect(calls.some((path) => path.includes("store_id=store_x"))).toBe(true);
    expect(calls.some((path) => path.includes("/store/"))).toBe(false);
    expect((api.post as ReturnType<typeof vi.fn>).mock.calls[0]).toEqual([`/months/${MONTH.id}/sheet-projection/enqueue`, { store_id: "store_x" }]);
    expect((api.post as ReturnType<typeof vi.fn>).mock.calls[1]).toEqual([`/months/${MONTH.id}/export/store`, { store_id: "store_x" }]);
  });

  it("keeps job diagnostics failure isolated from the core store detail", async () => {
    const { api } = apiForPage({ jobsFailure: true });
    render(<Magazin api={api} storeId="store_x" months={[MONTH]} monthsError={null} capabilities={MANAGER_CAPABILITIES} />);
    expect(await screen.findByRole("heading", { name: /Demo Store/ })).toBeInTheDocument();
    expect(screen.getByText(/Starea joburilor este indisponibilă: jobs unavailable/)).toBeInTheDocument();
    expect(screen.getByText("2/2")).toBeInTheDocument();
  });

  it("keeps store calendar and other data when an independent subsystem read fails", async () => {
    const { api } = apiForPage({ failPaths: ["/epay/freshness"] });
    render(<Magazin api={api} storeId="store_x" months={[MONTH]} monthsError={null} capabilities={MANAGER_CAPABILITIES} />);
    expect(await screen.findByRole("heading", { name: /Demo Store/ })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Editează calendarul/i })).toBeInTheDocument();
    expect(screen.getAllByText(/126/).length).toBeGreaterThan(0);
    expect(screen.getByRole("alert")).toHaveTextContent(/E-pay: \/epay\/freshness unavailable/);
    fireEvent.click(screen.getByRole("tab", { name: "Calendar" }));
    expect(screen.getByRole("button", { name: /Alice pe 2026-08-01/i })).toBeInTheDocument();
  });

  it("keeps manager calendar editing but removes admin-only sync/export actions", async () => {
    const { api } = apiForPage();
    render(<Magazin api={api} storeId="store_x" months={[MONTH]} monthsError={null} capabilities={MANAGER_CAPABILITIES} />);
    expect(await screen.findByRole("heading", { name: /Demo Store/ })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Editează calendarul/i })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Sincronizează Sheet/i })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Exportă XLSX/i })).not.toBeInTheDocument();
    expect(api.post).not.toHaveBeenCalled();
  });

  it("uses scoped choices and retries store calendar edits on the refreshed revision", async () => {
    const { api, calls } = apiForPage({ programRevisions: [2, 3, 4], postFailures: [{ status: 409, code: "STALE_REVISION" }] });
    render(<Magazin api={api} storeId="store_x" months={[MONTH]} monthsError={null} capabilities={MANAGER_CAPABILITIES} />);
    expect(await screen.findByRole("heading", { name: /Demo Store/ })).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /Editează calendarul/i }));
    fireEvent.click(screen.getByRole("button", { name: /Alice pe 2026-08-01/i }));
    expect(await screen.findByRole("button", { name: "Salvează" })).toBeInTheDocument();
    expect(calls).toContain(`/months/${MONTH.id}/program/choices?business_date=2026-08-01&store_id=store_x`);

    fireEvent.click(screen.getByRole("button", { name: "Salvează" }));
    expect(await screen.findByRole("alert")).toHaveTextContent(/revizia 3/i);
    expect((api.post as ReturnType<typeof vi.fn>).mock.calls[0]?.[0]).toBe(`/months/${MONTH.id}/program/cell?expected_revision=2`);

    fireEvent.click(screen.getByRole("button", { name: "Salvează" }));
    await waitFor(() => expect((api.post as ReturnType<typeof vi.fn>).mock.calls).toHaveLength(2));
    expect((api.post as ReturnType<typeof vi.fn>).mock.calls[1]?.[0]).toBe(`/months/${MONTH.id}/program/cell?expected_revision=3`);
  });

  it("keeps the existing person-filtered Agent routes", async () => {
    const { api, calls } = apiForPage();
    render(<Agent api={api} personId="person_a" months={[MONTH]} monthsError={null} />);
    expect(await screen.findByText("Alice")).toBeInTheDocument();
    expect(screen.getByText("store_x")).toBeInTheDocument();
    expect(screen.getByText(/125.50 RON/)).toBeInTheDocument();
    expect(screen.getByText(/Actualizat \(2\/2\)/)).toBeInTheDocument();
    expect(calls).toContain(`/months/${MONTH.id}/epay/freshness?store_id=store_x`);
    expect(calls).toContain(`/months/${MONTH.id}/sheet-projection?store_id=store_x`);
    expect(calls.some((path) => path.includes("/agents/") || path.includes("/people/"))).toBe(false);
  });

  it("keeps agent schedule and sales when Sheet read fails", async () => {
    const { api } = apiForPage({ failPaths: ["/sheet-projection"] });
    render(<Agent api={api} personId="person_a" months={[MONTH]} monthsError={null} />);
    expect(await screen.findByText("Alice")).toBeInTheDocument();
    expect(screen.getByText(/125.50 RON/)).toBeInTheDocument();
    expect(screen.getByRole("alert")).toHaveTextContent(/Sheet: \/sheet-projection unavailable/);
    expect(screen.getByText("fără proiecție")).toBeInTheDocument();
  });
});

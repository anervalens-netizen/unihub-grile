import { describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";
import "@testing-library/jest-dom/vitest";
import { Magazin } from "../src/pages/Magazin";
import { Agent } from "../src/pages/Agent";
import type { ApiClient, MonthSummary } from "../src/api/client";

const MONTH: MonthSummary = { id: "month_tenantacme_2026-08", tenant_id: "tenant_acme", year: 2026, month: 8, state: "OPEN", revision: 2, closed_at: null };
const grid = { month_id: MONTH.id, year: 2026, month: 8, revision: 2, dates: ["2026-08-01"], legend: ["NORMAL"], rows: [{ row_id: "person_a", label: "Alice", home_store_id: "store_x", cells: [{ business_date: "2026-08-01", person_id: "person_a", store_id: "store_x", status: "WORKING", working_kind: "NORMAL", display_name: "Alice", home_store_id: "store_x", badge: "NORMAL", locked: false }] }] };
const store = { id: "store_x", tenant_id: "tenant_acme", company_code: "ACME", internal_code: "SX", external_code: null, name: "Demo Store", is_active: true };
const person = { id: "person_a", tenant_id: "tenant_acme", internal_code: "PA", external_code: null, display_name: "Alice", home_store_id: "store_x", is_active: true };

function apiForPage() {
  const calls: string[] = [];
  const api = { healthz: vi.fn(), readyz: vi.fn(), post: vi.fn(async (path: string) => ({ job_id: "job-1", path })), get: vi.fn(async (path: string) => {
    calls.push(path);
    if (path === "/catalog/stores") return [store];
    if (path === "/catalog/people?store_id=store_x") return [person];
    if (path.includes("/program")) return grid;
    if (path.includes("/pontaj-totals")) return { month_id: MONTH.id, revision: 2, totals: { person_a: { working_days: 1, leave_days: 0, off_days: 0, hours: 11 } } };
    if (path.includes("/attribution")) return { month_id: MONTH.id, revision: 2, total_rows: 1, company_total: "125.50", rows: [{ person_id: "person_a", store_id: "store_x", business_date: "2026-08-01", amount: "125.50", currency: "RON", generation: "g1", working_kind: "NORMAL", revision: 2 }], anomalies: [] };
    if (path.includes("/grid")) return [{ id: 1, tenant_id: "tenant_acme", month_id: MONTH.id, store_id: "store_x", person_id: "person_a", rule_pack_version: "v1", revision: 2, inputs_hash: "in", outputs_hash: "out", payload: "{}" }];
    if (path.includes("/epay/freshness")) return { store_id: "store_x", is_fresh: true, fresh_count: 2, expected_count: 2, threshold: "2026-08-01" };
    if (path.includes("/sheet-projection")) return { store_id: "store_x", generation: "g1", last_success_generation: "g1", last_run_at: null, last_error: null, failures: 0, payload: null };
    throw new Error(`unexpected GET ${path}`);
  }) } as unknown as ApiClient;
  return { api, calls };
}

describe("Magazin and Agent contract routes", () => {
  it("uses actual store/person scoped routes and renders response fields/actions", async () => {
    const { api, calls } = apiForPage();
    render(<Magazin api={api} storeId="store_x" months={[MONTH]} monthsError={null} />);
    expect(await screen.findByRole("heading", { name: /Demo Store/ })).toBeInTheDocument();
    expect(screen.getAllByText(/125.50 RON/).length).toBeGreaterThan(0);
    expect(screen.getByText(/g1/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Sync Sheet" }));
    fireEvent.click(screen.getByRole("button", { name: "Export XLSX" }));
    expect(calls).toContain("/catalog/people?store_id=store_x");
    expect(calls.some((path) => path.includes("store_id=store_x"))).toBe(true);
    expect(calls.some((path) => path.includes("/store/"))).toBe(false);
    expect((api.post as ReturnType<typeof vi.fn>).mock.calls[0]).toEqual([`/months/${MONTH.id}/sheet-projection/enqueue`, { store_id: "store_x" }]);
    expect((api.post as ReturnType<typeof vi.fn>).mock.calls[1]).toEqual([`/months/${MONTH.id}/export/store`, { store_id: "store_x" }]);
  });

  it("uses actual person-filtered response fields and store-scoped routes", async () => {
    const { api, calls } = apiForPage();
    render(<Agent api={api} personId="person_a" months={[MONTH]} monthsError={null} />);
    expect(await screen.findByText("Alice")).toBeInTheDocument();
    expect(screen.getByText("store_x")).toBeInTheDocument();
    expect(screen.getByText(/125.50 RON/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Sync Sheet" })).not.toBeInTheDocument();
    expect(calls).toContain(`/months/${MONTH.id}/epay/freshness?store_id=store_x`);
    expect(calls).toContain(`/months/${MONTH.id}/sheet-projection?store_id=store_x`);
    expect(calls.some((path) => path.includes("/agents/") || path.includes("/people/"))).toBe(false);
  });
});

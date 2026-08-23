/** Command Center coverage for the manager overview. */

import { describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import "@testing-library/jest-dom/vitest";
import { Overview } from "../src/pages/Overview";
import type { ApiClient, MonthSummary, OverviewReport, ProgramGrid } from "../src/api/client";

const MONTH: MonthSummary = {
  id: "month_tenantacme_2026-08",
  tenant_id: "tenant_acme",
  year: 2026,
  month: 8,
  state: "OPEN",
  revision: 1,
  closed_at: null,
};

const REPORT: OverviewReport = {
  month_id: MONTH.id,
  year: 2026,
  month: 8,
  state: "OPEN",
  revision: 1,
  rule_pack_version: "mobiup-v1-compat",
  kpis: {
    stores_total: 4,
    stores_covered: 2,
    days_uncovered: 60,
    conflicts: 1,
    extra_home_days: 5,
    extra_other_days: 2,
    sales_unattributed: 0,
    epay_invalid: 0,
    epay_fresh: true,
    sheet_sync_total: 3,
    sheet_sync_stale: 1,
    sheet_sync_error: 0,
  },
  managers: [{
    user_id: "alice",
    display_name: "Alice",
    stores_covered: 2,
    stores_total: 2,
    days_uncovered: 0,
    last_sync: null,
  }],
  needs_attention: [
    { code: "STORE_DAY_UNCOVERED", severity: 1, title: "Magazin fără agent", detail: "store_x neacoperit pe 2026-08-05", store_id: "store_x", person_id: null, business_date: "2026-08-05" },
    { code: "INVALID_WORKING_KIND", severity: 2, title: "Clasificare invalidă", detail: "kind=EXTRA_OTHER pentru home_store", store_id: "store_y", person_id: "person_a", business_date: "2026-08-06" },
    { code: "TARGET_ZERO_FOR_WORKED_STORE", severity: 2, title: "Target lipsă/zero", detail: "target lipsă pentru store_x", store_id: "store_x", person_id: null, business_date: "2026-08-07" },
  ],
};

const STORES = [
  { id: "store_x", tenant_id: "tenant_acme", company_code: "ACME", internal_code: "SX", external_code: null, name: "Demo Store X", is_active: true },
  { id: "store_y", tenant_id: "tenant_acme", company_code: "ACME", internal_code: "SY", external_code: null, name: "Demo Store Y", is_active: true },
];

const PEOPLE_PROGRAM: ProgramGrid = {
  month_id: MONTH.id,
  year: 2026,
  month: 8,
  revision: 1,
  dates: [],
  rows: Array.from({ length: 6 }, (_, index) => ({
    row_id: `person_${index}`,
    label: `Agent ${index}`,
    home_store_id: "store_x",
    cells: [],
  })),
  legend: [],
};

function makeApi({ failPeopleProgram = false }: { failPeopleProgram?: boolean } = {}): ApiClient {
  return {
    healthz: vi.fn(),
    readyz: vi.fn(),
    get: vi.fn(async (path: string) => {
      if (path.startsWith(`/months/${MONTH.id}/overview`)) return REPORT;
      if (path === "/catalog/stores") return STORES;
      if (path === `/months/${MONTH.id}/program?perspective=people`) {
        if (failPeopleProgram) throw new Error("people program unavailable");
        return PEOPLE_PROGRAM;
      }
      throw new Error(`unexpected GET ${path}`);
    }),
    post: vi.fn(),
  } as unknown as ApiClient;
}

describe("Overview command center", () => {
  it("renders network KPIs, operational statuses, stores and attention priorities", async () => {
    const api = makeApi();
    render(<Overview api={api} months={[MONTH]} monthsError={null} />);
    expect(await screen.findByText("2/4")).toBeInTheDocument();
    expect(screen.getByText("Persoane")).toBeInTheDocument();
    expect(screen.getByText("6")).toBeInTheDocument();
    expect(screen.getByText("Calendar")).toBeInTheDocument();
    expect(screen.getByText("52%")).toBeInTheDocument();
    expect(screen.getByText("Targeturi")).toBeInTheDocument();
    expect(screen.getByText("E-pay")).toBeInTheDocument();
    expect(screen.getByText("OK")).toBeInTheDocument();
    expect(screen.getByText("Sync / export")).toBeInTheDocument();
    expect(screen.getByText("1 în lucru")).toBeInTheDocument();
    expect(screen.getByText("60")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Demo Store X/i })).toBeInTheDocument();
    expect(screen.getByText("Magazin fără agent")).toBeInTheDocument();
    expect(screen.getByText("Clasificare invalidă")).toBeInTheDocument();
    expect(screen.getByText("Target lipsă\/zero")).toBeInTheDocument();
    expect(screen.getByText("Alice")).toBeInTheDocument();
  });

  it("keeps the hub usable when the auxiliary people-scope read fails", async () => {
    const api = makeApi({ failPeopleProgram: true });
    render(<Overview api={api} months={[MONTH]} monthsError={null} />);
    expect(await screen.findByText("2/4")).toBeInTheDocument();
    expect(screen.getByText("scope indisponibil")).toBeInTheDocument();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("surfaces API errors via the accessible alert role", async () => {
    const api = {
      healthz: vi.fn(),
      readyz: vi.fn(),
      get: vi.fn(async () => { throw new Error("boom"); }),
      post: vi.fn(),
    } as unknown as ApiClient;
    render(<Overview api={api} months={[MONTH]} monthsError={null} />);
    expect(await screen.findByRole("alert")).toHaveTextContent(/boom/);
  });

  it("renders gracefully when no months are available", () => {
    render(<Overview api={makeApi()} months={[]} monthsError={null} />);
    expect(screen.getByText(/Nicio lună disponibilă/)).toBeInTheDocument();
  });
});

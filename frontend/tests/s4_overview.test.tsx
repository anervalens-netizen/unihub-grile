/**
 * S4 Overview page tests.
 *
 * Verifies the KPI cards render from a stubbed ``/overview`` payload and
 * the "Necesită atenție" list stays sorted by severity (the API already
 * sorts server-side; the test confirms the rendering preserves order).
 */

import { describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import "@testing-library/jest-dom/vitest";
import { Overview } from "../src/pages/Overview";
import type {
  ApiClient,
  MonthSummary,
  OverviewReport,
} from "../src/api/client";

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
  managers: [
    {
      user_id: "alice",
      display_name: "Alice",
      stores_covered: 2,
      stores_total: 2,
      days_uncovered: 0,
      last_sync: null,
    },
  ],
  needs_attention: [
    {
      code: "STORE_DAY_UNCOVERED",
      severity: 1,
      title: "Magazin fără agent",
      detail: "store_x neacoperit pe 2026-08-05",
      store_id: "store_x",
      person_id: null,
      business_date: "2026-08-05",
    },
    {
      code: "INVALID_WORKING_KIND",
      severity: 2,
      title: "Clasificare invalidă",
      detail: "kind=EXTRA_OTHER pentru home_store",
      store_id: "store_y",
      person_id: "person_a",
      business_date: "2026-08-06",
    },
  ],
};

function makeApi(): ApiClient {
  return {
    healthz: vi.fn(),
    readyz: vi.fn(),
    get: vi.fn(async (path: string) => {
      if (path.startsWith(`/months/${MONTH.id}/overview`)) {
        return REPORT;
      }
      throw new Error(`unexpected GET ${path}`);
    }),
    post: vi.fn(),
  } as unknown as ApiClient;
}

describe("Overview page", () => {
  it("renders KPI cards and the sorted needs-attention list", async () => {
    const api = makeApi();
    render(
      <Overview api={api} months={[MONTH]} monthsError={null} />,
    );
    // The KPI title and the managers table column header both render
    // "Magazine acoperite"; ``findAllByText`` confirms at least one match.
    expect((await screen.findAllByText("Magazine acoperite")).length).toBeGreaterThan(0);
    expect(screen.getByText("2 / 4")).toBeTruthy();
    expect(screen.getAllByText("Zile neacoperite").length).toBeGreaterThan(0);
    expect(screen.getByText("60")).toBeTruthy();
    // The two needs-attention rows render in API order (severity asc).
    const titles = screen.getAllByRole("listitem").map((node) => node.textContent ?? "");
    const firstTitle = titles.find((text) => text.includes("Magazin fără agent"));
    const secondTitle = titles.find((text) => text.includes("Clasificare invalidă"));
    expect(firstTitle).toBeTruthy();
    expect(secondTitle).toBeTruthy();
    expect(titles.indexOf(firstTitle!)).toBeLessThan(titles.indexOf(secondTitle!));
  });

  it("surfaces API errors via the accessible alert role", async () => {
    const api = {
      healthz: vi.fn(),
      readyz: vi.fn(),
      get: vi.fn(async () => {
        throw new Error("boom");
      }),
      post: vi.fn(),
    } as unknown as ApiClient;
    render(<Overview api={api} months={[MONTH]} monthsError={null} />);
    expect(await screen.findByRole("alert")).toHaveTextContent(/boom/);
  });

  it("renders gracefully when no months are available", () => {
    const api = makeApi();
    render(<Overview api={api} months={[]} monthsError={null} />);
    expect(screen.getByText(/Nicio lună disponibilă/)).toBeTruthy();
  });
});

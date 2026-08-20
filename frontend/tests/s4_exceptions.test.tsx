/**
 * S4 Exceptions page test.
 *
 * Verifies that the typed exception list renders in API order (sorted by
 * severity). The Exceptions endpoint is responsible for sorting; the page
 * preserves the order without re-sorting.
 */

import { describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import "@testing-library/jest-dom/vitest";
import { Exceptions } from "../src/pages/Exceptions";
import type {
  ApiClient,
  ExceptionEntry,
  MonthSummary,
} from "../src/api/client";

const MONTH: MonthSummary = {
  id: "month_tenantacme_2026-08",
  tenant_id: "tenant_acme",
  year: 2026,
  month: 8,
  state: "OPEN",
  revision: 0,
  closed_at: null,
};

const ENTRIES: ExceptionEntry[] = [
  {
    code: "STORE_DAY_UNCOVERED",
    severity: 1,
    title: "Magazin fără agent",
    detail: "store_x neacoperit pe 2026-08-05",
    blocking_close: true,
    store_id: "store_x",
    person_id: null,
    business_date: "2026-08-05",
    action_hint: "Deschide Magazin → Program → alege agent",
  },
  {
    code: "INVALID_WORKING_KIND",
    severity: 2,
    title: "Clasificare invalidă",
    detail: "kind=EXTRA_OTHER pentru home_store",
    blocking_close: true,
    store_id: "store_y",
    person_id: "person_a",
    business_date: "2026-08-06",
    action_hint: "Verifică home/other",
  },
  {
    code: "SHEET_CANARY_REQUIRED",
    severity: 3,
    title: "Canary Sheet nesincronizat",
    detail: "Sheets rămase stale",
    blocking_close: false,
    store_id: null,
    person_id: null,
    business_date: null,
    action_hint: "Așteaptă sincronizarea Sheet",
  },
];

function makeApi(): ApiClient {
  return {
    healthz: vi.fn(),
    readyz: vi.fn(),
    get: vi.fn(async (path: string) => {
      if (path.endsWith("/exceptions")) return ENTRIES;
      throw new Error(`unexpected GET ${path}`);
    }),
    post: vi.fn(),
  } as unknown as ApiClient;
}

describe("Exceptions page severity sort", () => {
  it("renders the typed list and preserves API-side severity order", async () => {
    const api = makeApi();
    render(<Exceptions api={api} months={[MONTH]} monthsError={null} />);
    const titles = await screen.findAllByRole("listitem");
    expect(titles).toHaveLength(3);
    const texts = titles.map((node) => node.textContent ?? "");
    // Severity 1 first, 2 second, 3 last — preserved from API sort.
    expect(texts[0]).toMatch(/Magazin fără agent/);
    expect(texts[1]).toMatch(/Clasificare invalidă/);
    expect(texts[2]).toMatch(/Canary Sheet nesincronizat/);
  });

  it("renders an action hint for every blocker", async () => {
    const api = makeApi();
    render(<Exceptions api={api} months={[MONTH]} monthsError={null} />);
    expect(await screen.findByText(/Deschide Magazin/)).toBeTruthy();
    expect(screen.getByText(/Verifică home\/other/)).toBeTruthy();
  });
});

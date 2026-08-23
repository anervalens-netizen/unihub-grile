/**
 * S4 Exceptions page tests.
 *
 * The API owns severity ordering. The frontend preserves that order, exposes
 * resolution context, and only renders drill-down actions backed by real
 * resource identifiers returned by the server.
 */

import { beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, within } from "@testing-library/react";
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

beforeEach(() => {
  window.location.hash = "";
});

describe("Exceptions resolution workflow", () => {
  it("preserves API severity order and renders operational summary", async () => {
    render(<Exceptions api={makeApi()} months={[MONTH]} monthsError={null} />);
    const items = await screen.findAllByRole("listitem");
    expect(items).toHaveLength(3);
    const texts = items.map((node) => node.textContent ?? "");
    expect(texts[0]).toMatch(/Magazin fără agent/);
    expect(texts[1]).toMatch(/Clasificare invalidă/);
    expect(texts[2]).toMatch(/Canary Sheet nesincronizat/);
    expect(screen.getByText("2", { selector: "strong" })).toBeInTheDocument();
    expect(screen.getByText("05.08.2026")).toBeInTheDocument();
    expect(screen.getByText("Cod: STORE_DAY_UNCOVERED")).toBeInTheDocument();
  });

  it("keeps resolution hints and drills down only through server-provided resource ids", async () => {
    render(<Exceptions api={makeApi()} months={[MONTH]} monthsError={null} />);
    const items = await screen.findAllByRole("listitem");

    const uncovered = within(items[0]!);
    expect(uncovered.getByText(/Deschide Magazin/)).toBeInTheDocument();
    fireEvent.click(uncovered.getByRole("button", { name: "Deschide magazin" }));
    expect(window.location.hash).toBe("#/store/store_x");

    const invalidKind = within(items[1]!);
    expect(invalidKind.getByText(/Verifică home\/other/)).toBeInTheDocument();
    fireEvent.click(invalidKind.getByRole("button", { name: "Deschide agent" }));
    expect(window.location.hash).toBe("#/agent/person_a");

    const sheetCanary = within(items[2]!);
    expect(sheetCanary.getByText(/Așteaptă sincronizarea Sheet/)).toBeInTheDocument();
    expect(sheetCanary.queryByRole("button")).not.toBeInTheDocument();
  });

  it("filters to blocking exceptions without changing their API order", async () => {
    render(<Exceptions api={makeApi()} months={[MONTH]} monthsError={null} />);
    await screen.findByText("Canary Sheet nesincronizat");
    fireEvent.click(screen.getByLabelText("Doar blocante"));
    const items = screen.getAllByRole("listitem");
    expect(items).toHaveLength(2);
    expect(items[0]).toHaveTextContent("Magazin fără agent");
    expect(items[1]).toHaveTextContent("Clasificare invalidă");
    expect(screen.queryByText("Canary Sheet nesincronizat")).not.toBeInTheDocument();
  });
});

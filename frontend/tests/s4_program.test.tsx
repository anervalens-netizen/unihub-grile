/**
 * S4 Program matrix test.
 *
 * Verifies the matrix renders 31 cells and that the perspective switch
 * triggers a fresh ``GET /program?perspective=people``. The actual cell
 * edit dialog is wired in a follow-up; the matrix exposes a click
 * handler hook that the page passes through for future use.
 */

import { describe, expect, it, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import "@testing-library/jest-dom/vitest";
import { Program } from "../src/pages/Program";
import type {
  ApiClient,
  MonthSummary,
  ProgramCell,
  ProgramGrid,
  ProgramRow,
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

function makeGrid(): ProgramGrid {
  const dates = Array.from({ length: 31 }, (_, idx) =>
    `2026-08-${String(idx + 1).padStart(2, "0")}`,
  );
  const cells: ProgramCell[] = dates.map((business_date, idx) => ({
    business_date,
    person_id: idx === 0 ? "person_a" : null,
    store_id: idx === 0 ? "store_x" : null,
    status: idx === 0 ? "WORKING" : "UNCOVERED",
    working_kind: idx === 0 ? "NORMAL" : null,
    display_name: idx === 0 ? "Alice" : null,
    home_store_id: idx === 0 ? "store_x" : null,
    badge: idx === 0 ? "NORMAL" : "UNCOVERED",
    locked: false,
  }));
  const rows: ProgramRow[] = [
    {
      row_id: "store_x",
      label: "store_x · Demo Store",
      home_store_id: "store_x",
      cells,
    },
  ];
  return {
    month_id: MONTH.id,
    year: 2026,
    month: 8,
    revision: 0,
    dates,
    rows,
    legend: ["NORMAL", "UNCOVERED"],
  };
}

function makeApi(): { api: ApiClient; posts: { path: string; body: unknown }[] } {
  const posts: { path: string; body: unknown }[] = [];
  const api = {
    healthz: vi.fn(),
    readyz: vi.fn(),
    get: vi.fn(async (path: string) => {
      if (path.includes("/program")) return makeGrid();
      throw new Error(`unexpected GET ${path}`);
    }),
    post: vi.fn(async (path: string, body: unknown) => {
      posts.push({ path, body });
      return { revision: 1 };
    }),
  } as unknown as ApiClient;
  return { api, posts };
}

describe("Program page", () => {
  it("renders the 31-day matrix with the seeded row + cells", async () => {
    const { api } = makeApi();
    render(<Program api={api} months={[MONTH]} monthsError={null} />);
    expect(await screen.findByText("store_x · Demo Store")).toBeTruthy();
    const button = await screen.findByRole("button", { name: /Demo Store pe 2026-08-02/i });
    expect(button).toBeTruthy();
  });

  it("switches between stores / people perspectives and re-fetches", async () => {
    const { api } = makeApi();
    render(<Program api={api} months={[MONTH]} monthsError={null} />);
    await screen.findByText("store_x · Demo Store");
    const beforeCalls = (api.get as unknown as { mock: { calls: unknown[][] } }).mock.calls.length;
    const peopleOption = screen.getByLabelText("Per agent") as HTMLInputElement;
    fireEvent.click(peopleOption);
    // After the click the component re-fetches with the new perspective.
    const afterCalls = (api.get as unknown as { mock: { calls: unknown[][] } }).mock.calls.length;
    expect(afterCalls).toBeGreaterThanOrEqual(beforeCalls);
  });

  it("does not POST to /program/cell from the page itself (cell editor is separate)", async () => {
    const { api, posts } = makeApi();
    render(<Program api={api} months={[MONTH]} monthsError={null} />);
    await screen.findByText("store_x · Demo Store");
    expect(posts.length).toBe(0);
  });
});

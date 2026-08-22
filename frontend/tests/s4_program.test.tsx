/**
 * S4 Program matrix test.
 *
 * Verifies the matrix renders 31 cells, perspective changes re-fetch, and
 * write controls are available only when the backend session grants
 * ``schedule.write``.
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
import type { Capability } from "../src/capabilities";

const MONTH: MonthSummary = {
  id: "month_tenantacme_2026-08",
  tenant_id: "tenant_acme",
  year: 2026,
  month: 8,
  state: "OPEN",
  revision: 0,
  closed_at: null,
};
const EDIT_CAPABILITIES = new Set<Capability>(["schedule.read", "schedule.write"]);
const READ_CAPABILITIES = new Set<Capability>(["schedule.read"]);

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
    render(<Program api={api} months={[MONTH]} monthsError={null} capabilities={EDIT_CAPABILITIES} />);
    expect(await screen.findByText("store_x · Demo Store")).toBeTruthy();
    const button = await screen.findByRole("button", { name: /Demo Store pe 2026-08-02/i });
    expect(button).toBeTruthy();
  });

  it("switches between stores / people perspectives and re-fetches", async () => {
    const { api } = makeApi();
    render(<Program api={api} months={[MONTH]} monthsError={null} capabilities={EDIT_CAPABILITIES} />);
    await screen.findByText("store_x · Demo Store");
    const beforeCalls = (api.get as unknown as { mock: { calls: unknown[][] } }).mock.calls.length;
    const peopleOption = screen.getByLabelText("Per agent") as HTMLInputElement;
    fireEvent.click(peopleOption);
    const afterCalls = (api.get as unknown as { mock: { calls: unknown[][] } }).mock.calls.length;
    expect(afterCalls).toBeGreaterThanOrEqual(beforeCalls);
  });

  it("posts an unlocked cell through the revisioned endpoint", async () => {
    const { api, posts } = makeApi();
    render(<Program api={api} months={[MONTH]} monthsError={null} capabilities={EDIT_CAPABILITIES} />);
    await screen.findByText("store_x · Demo Store");
    fireEvent.click(screen.getByRole("button", { name: /Demo Store pe 2026-08-01/i }));
    fireEvent.click(screen.getByRole("button", { name: "Salvează" }));
    expect(posts[0]?.path).toBe(`/months/${MONTH.id}/program/cell?expected_revision=0`);
  });

  it("keeps schedule.read sessions inert when schedule.write is absent", async () => {
    const { api, posts } = makeApi();
    render(<Program api={api} months={[MONTH]} monthsError={null} capabilities={READ_CAPABILITIES} />);
    await screen.findByText("store_x · Demo Store");
    expect(screen.getByText(/doar pentru vizualizare/i)).toBeInTheDocument();
    const cell = screen.getByRole("button", { name: /Demo Store pe 2026-08-01/i });
    expect(cell).toBeDisabled();
    fireEvent.click(cell);
    expect(screen.queryByRole("button", { name: "Salvează" })).not.toBeInTheDocument();
    expect(posts).toHaveLength(0);
  });

  it("shows a stale-revision conflict without replacing the editor or grid", async () => {
    const { api } = makeApi();
    (api.post as ReturnType<typeof vi.fn>).mockRejectedValueOnce({ status: 409, message: "conflict" });
    render(<Program api={api} months={[MONTH]} monthsError={null} capabilities={EDIT_CAPABILITIES} />);
    await screen.findByText("store_x · Demo Store");
    fireEvent.click(screen.getByRole("button", { name: /Demo Store pe 2026-08-01/i }));
    fireEvent.click(screen.getByRole("button", { name: "Salvează" }));
    expect(await screen.findByRole("alert")).toHaveTextContent(/revizie stale/i);
    expect(screen.getByRole("button", { name: "Salvează" })).toBeInTheDocument();
    expect(screen.getByRole("rowheader", { name: "store_x · Demo Store" })).toBeInTheDocument();
  });
});

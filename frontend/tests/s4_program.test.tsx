import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { Program } from "../src/pages/Program";
import type { ApiClient, MonthSummary, ProgramGrid } from "../src/api/client";

const MONTH: MonthSummary = {
  id: "month_tenantacme_2026-08",
  tenant_id: "tenant_acme",
  year: 2026,
  month: 8,
  state: "OPEN",
  revision: 0,
};

function makeGrid(perspective: "stores" | "people" = "stores"): ProgramGrid {
  const dates = Array.from({ length: 31 }, (_, index) => `2026-08-${String(index + 1).padStart(2, "0")}`);
  return {
    month_id: MONTH.id,
    perspective,
    revision: 0,
    dates,
    legend: ["NORMAL", "UNCOVERED"],
    rows: [
      {
        row_id: perspective === "stores" ? "store_x" : "person_a",
        label: perspective === "stores" ? "store_x · Demo Store" : "Alice",
        home_store_id: perspective === "people" ? "store_x" : null,
        cells: dates.map((businessDate, index) => ({
          business_date: businessDate,
          person_id: perspective === "stores" ? "person_a" : "person_a",
          store_id: "store_x",
          display_name: "Alice",
          status: index === 1 ? "OFF" : "WORKING",
          working_kind: index === 1 ? null : "NORMAL",
          badge: index === 1 ? "OFF" : "NORMAL",
          locked: false,
        })),
      },
    ],
  };
}

function makeApi() {
  const posts: Array<{ path: string; body: unknown }> = [];
  const api: ApiClient = {
    healthz: vi.fn(),
    get: vi.fn(async (path: string) => {
      if (path.includes("perspective=people")) return makeGrid("people") as never;
      if (path.startsWith("/catalog/people")) {
        return [{ id: "person_a", display_name: "Alice", home_store_id: "store_x", is_active: true }] as never;
      }
      if (path === "/catalog/stores") {
        return [{ id: "store_x", name: "store_x", internal_code: "store_x", company_code: null, is_active: true }] as never;
      }
      return makeGrid("stores") as never;
    }),
    post: vi.fn(async (path: string, body?: unknown) => {
      posts.push({ path, body });
      return {} as never;
    }),
    fetchBlob: vi.fn(),
  };
  return { api, posts };
}

describe("Program page", () => {
  it("renders the 31-day matrix with the seeded row + cells", async () => {
    const { api } = makeApi();
    render(<Program api={api} months={[MONTH]} monthsError={null} />);
    expect(await screen.findByText("store_x · Demo Store")).toBeInTheDocument();
    expect(screen.getByRole("grid")).toHaveAttribute("aria-colcount", "32");
    expect(screen.getByRole("button", { name: /Demo Store pe 2026-08-01/i })).toBeInTheDocument();
  });

  it("switches between stores / people perspectives and re-fetches", async () => {
    const { api } = makeApi();
    render(<Program api={api} months={[MONTH]} monthsError={null} />);
    await screen.findByText("store_x · Demo Store");
    const beforeCalls = (api.get as unknown as { mock: { calls: unknown[][] } }).mock.calls.length;
    fireEvent.click(screen.getByLabelText("Per agent"));
    await waitFor(() => expect(screen.getByText("Alice")).toBeInTheDocument());
    const afterCalls = (api.get as unknown as { mock: { calls: unknown[][] } }).mock.calls.length;
    expect(afterCalls).toBeGreaterThanOrEqual(beforeCalls);
  });

  it("posts an unlocked cell through the revisioned endpoint", async () => {
    const { api, posts } = makeApi();
    render(<Program api={api} months={[MONTH]} monthsError={null} />);
    await screen.findByText("store_x · Demo Store");
    fireEvent.click(screen.getByRole("button", { name: /Demo Store pe 2026-08-01/i }));
    fireEvent.click(screen.getByRole("button", { name: "Salvează" }));
    expect(posts[0]?.path).toBe(`/months/${MONTH.id}/program/cell?expected_revision=0`);
  });

  it("shows a stale-revision conflict without replacing the editor or grid", async () => {
    const { api } = makeApi();
    (api.post as ReturnType<typeof vi.fn>).mockRejectedValueOnce({ status: 409, message: "conflict" });
    render(<Program api={api} months={[MONTH]} monthsError={null} />);
    await screen.findByText("store_x · Demo Store");
    fireEvent.click(screen.getByRole("button", { name: /Demo Store pe 2026-08-01/i }));
    fireEvent.click(screen.getByRole("button", { name: "Salvează" }));
    expect(await screen.findByRole("alert")).toHaveTextContent(/revizie stale/i);
    expect(screen.getByRole("button", { name: "Salvează" })).toBeInTheDocument();
    expect(screen.getByRole("rowheader", { name: "store_x · Demo Store" })).toBeInTheDocument();
  });
});
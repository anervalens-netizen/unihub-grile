/**
 * S4 Program matrix test.
 *
 * Verifies the matrix renders 31 cells, perspective changes re-fetch, write
 * controls honor ``schedule.write``, scoped choices drive the editor and a
 * stale CAS conflict recovers onto the fresh revision before retry.
 */

import { describe, expect, it, vi } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import "@testing-library/jest-dom/vitest";
import { Program } from "../src/pages/Program";
import type {
  ApiClient,
  MonthSummary,
  ProgramCell,
  ProgramChoices,
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

function makeGrid(revision = 0, firstCellLocked = false): ProgramGrid {
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
    home_store_id: "store_x",
    badge: idx === 0 ? "NORMAL" : "UNCOVERED",
    locked: idx === 0 ? firstCellLocked : false,
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
    revision,
    dates,
    rows,
    legend: ["NORMAL", "UNCOVERED"],
  };
}

const CHOICES: ProgramChoices = {
  month_id: MONTH.id,
  business_date: "2026-08-01",
  store_id: "store_x",
  choices: [
    {
      person_id: "person_a",
      display_name: "Alice",
      home_store_id: "store_x",
      allowed_store_ids: ["store_x", "store_y"],
      working_kinds: ["NORMAL", "EXTRA_HOME", "EXTRA_OTHER"],
    },
    {
      person_id: "person_b",
      display_name: "Bob",
      home_store_id: "store_y",
      allowed_store_ids: ["store_x", "store_y"],
      working_kinds: ["NORMAL", "EXTRA_HOME", "EXTRA_OTHER"],
    },
  ],
};

interface ApiOptions {
  programRevisions?: number[];
  postFailures?: Array<{ status: number; code?: string }>;
}

function makeApi(options: ApiOptions = {}): { api: ApiClient; posts: { path: string; body: unknown }[]; gets: string[] } {
  const posts: { path: string; body: unknown }[] = [];
  const gets: string[] = [];
  let programRead = 0;
  let postAttempt = 0;
  const revisions = options.programRevisions ?? [0];
  const api = {
    healthz: vi.fn(),
    readyz: vi.fn(),
    get: vi.fn(async (path: string) => {
      gets.push(path);
      if (path.includes("/program/choices")) return CHOICES;
      if (path.includes("/program?perspective=")) {
        const revision = revisions[Math.min(programRead, revisions.length - 1)] ?? 0;
        programRead += 1;
        return makeGrid(revision);
      }
      throw new Error(`unexpected GET ${path}`);
    }),
    post: vi.fn(async (path: string, body: unknown) => {
      posts.push({ path, body });
      const failure = options.postFailures?.[postAttempt];
      postAttempt += 1;
      if (failure) throw failure;
      return { revision: 1 };
    }),
  } as unknown as ApiClient;
  return { api, posts, gets };
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
    await waitFor(() => {
      const afterCalls = (api.get as unknown as { mock: { calls: unknown[][] } }).mock.calls.length;
      expect(afterCalls).toBeGreaterThan(beforeCalls);
    });
  });

  it("loads scoped choices and posts an unlocked cell through the revisioned endpoint", async () => {
    const { api, posts, gets } = makeApi();
    render(<Program api={api} months={[MONTH]} monthsError={null} capabilities={EDIT_CAPABILITIES} />);
    await screen.findByText("store_x · Demo Store");
    fireEvent.click(screen.getByRole("button", { name: /Demo Store pe 2026-08-01/i }));
    expect(await screen.findByRole("option", { name: "Bob" })).toBeInTheDocument();
    expect(gets).toContain(`/months/${MONTH.id}/program/choices?business_date=2026-08-01&store_id=store_x`);
    fireEvent.click(screen.getByRole("button", { name: "Salvează" }));
    await waitFor(() => expect(posts).toHaveLength(1));
    expect(posts[0]?.path).toBe(`/months/${MONTH.id}/program/cell?expected_revision=0`);
  });

  it("normalizes OFF/LEAVE payloads so store and working kind are not persisted", async () => {
    const { api, posts } = makeApi();
    render(<Program api={api} months={[MONTH]} monthsError={null} capabilities={EDIT_CAPABILITIES} />);
    await screen.findByText("store_x · Demo Store");
    fireEvent.click(screen.getByRole("button", { name: /Demo Store pe 2026-08-01/i }));
    await screen.findByRole("button", { name: "Salvează" });
    fireEvent.change(screen.getByLabelText("Status"), { target: { value: "OFF" } });
    fireEvent.click(screen.getByRole("button", { name: "Salvează" }));
    await waitFor(() => expect(posts).toHaveLength(1));
    expect(posts[0]?.body).toMatchObject({ status: "OFF", store_id: null, working_kind: null });
  });

  it("keeps schedule.read sessions inert when schedule.write is absent", async () => {
    const { api, posts, gets } = makeApi();
    render(<Program api={api} months={[MONTH]} monthsError={null} capabilities={READ_CAPABILITIES} />);
    await screen.findByText("store_x · Demo Store");
    expect(screen.getByText(/doar pentru vizualizare/i)).toBeInTheDocument();
    const cell = screen.getByRole("button", { name: /Demo Store pe 2026-08-01/i });
    expect(cell).toBeDisabled();
    fireEvent.click(cell);
    expect(screen.queryByRole("button", { name: "Salvează" })).not.toBeInTheDocument();
    expect(posts).toHaveLength(0);
    expect(gets.some((path) => path.includes("/program/choices"))).toBe(false);
  });

  it("recovers a typed STALE_REVISION onto the fresh revision and retries with CAS", async () => {
    const { api, posts } = makeApi({
      programRevisions: [0, 1, 2],
      postFailures: [{ status: 409, code: "STALE_REVISION" }],
    });
    render(<Program api={api} months={[MONTH]} monthsError={null} capabilities={EDIT_CAPABILITIES} />);
    await screen.findByText("store_x · Demo Store");
    fireEvent.click(screen.getByRole("button", { name: /Demo Store pe 2026-08-01/i }));
    await screen.findByRole("button", { name: "Salvează" });
    fireEvent.change(screen.getByLabelText("Agent"), { target: { value: "person_b" } });
    fireEvent.click(screen.getByRole("button", { name: "Salvează" }));

    expect(await screen.findByRole("alert")).toHaveTextContent(/revizia 1/i);
    expect(screen.getByRole("button", { name: "Salvează" })).toBeInTheDocument();
    expect((screen.getByLabelText("Agent") as HTMLSelectElement).value).toBe("person_b");
    expect(posts[0]?.path).toBe(`/months/${MONTH.id}/program/cell?expected_revision=0`);

    fireEvent.click(screen.getByRole("button", { name: "Salvează" }));
    await waitFor(() => expect(posts).toHaveLength(2));
    expect(posts[1]?.path).toBe(`/months/${MONTH.id}/program/cell?expected_revision=1`);
  });

  it("does not misclassify MONTH_CLOSED as a stale revision", async () => {
    const { api } = makeApi({ postFailures: [{ status: 409, code: "MONTH_CLOSED" }] });
    render(<Program api={api} months={[MONTH]} monthsError={null} capabilities={EDIT_CAPABILITIES} />);
    await screen.findByText("store_x · Demo Store");
    fireEvent.click(screen.getByRole("button", { name: /Demo Store pe 2026-08-01/i }));
    await screen.findByRole("button", { name: "Salvează" });
    fireEvent.click(screen.getByRole("button", { name: "Salvează" }));
    expect(await screen.findByRole("alert")).toHaveTextContent(/luna a fost închisă/i);
    expect(screen.queryByRole("button", { name: "Salvează" })).not.toBeInTheDocument();
    expect(screen.queryByText(/revizia stale/i)).not.toBeInTheDocument();
  });
});

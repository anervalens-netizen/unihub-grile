import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import "@testing-library/jest-dom/vitest";
import { describe, expect, it, vi } from "vitest";
import type { ApiClient, MonthSummary, ProgramGrid } from "../src/api/client";
import type { Capability } from "../src/capabilities";
import { requestErrorMessage } from "../src/api/requestError";
import { Exceptions } from "../src/pages/Exceptions";
import { Jobs } from "../src/pages/Jobs";
import { Program } from "../src/pages/Program";

const MONTH: MonthSummary = {
  id: "month_tenantacme_2026-08",
  tenant_id: "tenant_acme",
  year: 2026,
  month: 8,
  state: "OPEN",
  revision: 2,
  closed_at: null,
};

const READ_CAPABILITIES = new Set<Capability>(["schedule.read"]);

const EMPTY_PROGRAM: ProgramGrid = {
  month_id: MONTH.id,
  year: 2026,
  month: 8,
  revision: 2,
  dates: [],
  rows: [],
  legend: [],
};

const JOB_DIAGNOSTICS = {
  counts: { queued: 0, retrying: 1, running: 0, failed: 0, done: 0 },
  terminal_history_limit: 50,
  jobs: [{
    id: 41,
    kind: "GOOGLE_PROJECTION_STORE",
    state: "RETRY",
    attempts: 2,
    max_attempts: 5,
    run_after: "2026-08-23T10:30:00Z",
    locked_at: null,
    created_at: "2026-08-23T10:00:00Z",
    updated_at: "2026-08-23T10:15:00Z",
    last_error: "provider timeout",
    month_id: MONTH.id,
    store_ids: ["store_x"],
  }],
};

function apiWithGet(get: ApiClient["get"]): ApiClient {
  return {
    healthz: vi.fn(),
    readyz: vi.fn(),
    get,
    post: vi.fn(),
  } as unknown as ApiClient;
}

describe("FE-010 request state contract", () => {
  it("normalizes 403 and typed 409 errors without collapsing their meaning", () => {
    expect(requestErrorMessage({ status: 403, code: "FORBIDDEN", message: "raw" }))
      .toBe("Acces refuzat pentru această operațiune.");
    expect(requestErrorMessage({ status: 409, code: "STALE_REVISION" }))
      .toMatch(/s-au schimbat între timp/i);
    expect(requestErrorMessage({ status: 409, code: "MONTH_CLOSED" }))
      .toMatch(/luna este închisă/i);
    expect(requestErrorMessage(new Error("boom"))).toBe("boom");
  });

  it("shows an explicit Program loading state while the primary read is pending", async () => {
    const pending = new Promise<ProgramGrid>(() => undefined);
    const api = apiWithGet(vi.fn(async () => pending) as unknown as ApiClient["get"]);
    render(<Program api={api} months={[MONTH]} monthsError={null} capabilities={READ_CAPABILITIES} />);
    expect(await screen.findByRole("status")).toHaveTextContent("Încarc programul");
    expect(screen.queryByText("Programul este gol.")).not.toBeInTheDocument();
  });

  it("does not present a failed Program read as empty and retries into the successful empty state", async () => {
    let attempts = 0;
    const api = apiWithGet(vi.fn(async () => {
      attempts += 1;
      if (attempts === 1) throw { status: 403, code: "FORBIDDEN" };
      return EMPTY_PROGRAM;
    }) as unknown as ApiClient["get"]);

    render(<Program api={api} months={[MONTH]} monthsError={null} capabilities={READ_CAPABILITIES} />);
    expect(await screen.findByRole("alert")).toHaveTextContent("Acces refuzat");
    expect(screen.queryByText("Programul este gol.")).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Reîncearcă" }));
    expect(await screen.findByText("Programul este gol.")).toBeInTheDocument();
    expect(attempts).toBe(2);
  });

  it("keeps Exceptions error distinct from empty and supports explicit retry", async () => {
    let attempts = 0;
    const api = apiWithGet(vi.fn(async () => {
      attempts += 1;
      if (attempts === 1) throw new Error("exceptions unavailable");
      return [];
    }) as unknown as ApiClient["get"]);

    render(<Exceptions api={api} months={[MONTH]} monthsError={null} />);
    expect(await screen.findByRole("alert")).toHaveTextContent("exceptions unavailable");
    expect(screen.queryByText("Nicio excepție deschisă.")).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Reîncearcă" }));
    expect(await screen.findByText("Nicio excepție deschisă.")).toBeInTheDocument();
    expect(attempts).toBe(2);
  });

  it("keeps the last-good Jobs diagnostics visible when a refresh fails", async () => {
    let attempts = 0;
    const api = apiWithGet(vi.fn(async () => {
      attempts += 1;
      if (attempts === 1) return JOB_DIAGNOSTICS;
      throw new Error("refresh unavailable");
    }) as unknown as ApiClient["get"]);

    render(<Jobs api={api} />);
    expect(await screen.findByText("#41")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Actualizează" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("refresh unavailable");
    expect(screen.getByText("#41")).toBeInTheDocument();
    expect(screen.getByText(/ultima stare încărcată cu succes/i)).toBeInTheDocument();
    await waitFor(() => expect(attempts).toBe(2));
  });
});

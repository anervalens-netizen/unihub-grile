import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import "@testing-library/jest-dom/vitest";
import { describe, expect, it, vi } from "vitest";
import type { ApiClient } from "../src/api/client";
import { Jobs } from "../src/pages/Jobs";

const DIAGNOSTICS = {
  counts: { queued: 1, retrying: 1, running: 1, failed: 1, done: 2 },
  terminal_history_limit: 50,
  jobs: [
    {
      id: 41,
      kind: "GOOGLE_PROJECTION_STORE",
      state: "RETRY",
      attempts: 2,
      max_attempts: 5,
      run_after: "2026-08-22T12:30:00Z",
      locked_at: null,
      created_at: "2026-08-22T12:00:00Z",
      updated_at: "2026-08-22T12:15:00Z",
      last_error: "RETRYABLE attempt=2/5: provider timeout",
      month_id: "month_acme_2026_08",
      store_ids: ["store_acme_s1"],
    },
    {
      id: 40,
      kind: "EXPORT_XLSX_STORE",
      state: "DONE",
      attempts: 1,
      max_attempts: 3,
      run_after: "2026-08-22T12:00:00Z",
      locked_at: null,
      created_at: "2026-08-22T12:00:00Z",
      updated_at: "2026-08-22T12:02:00Z",
      last_error: null,
      month_id: "month_acme_2026_08",
      store_ids: ["store_acme_s1"],
    },
  ],
} as const;

function makeApi(): ApiClient {
  return {
    healthz: vi.fn(),
    readyz: vi.fn(),
    get: vi.fn(async (path: string) => {
      if (path === "/worker/jobs/diagnostics?terminal_limit=50") return DIAGNOSTICS;
      throw new Error(`unexpected GET ${path}`);
    }),
    post: vi.fn(),
  } as unknown as ApiClient;
}

describe("Jobs operator workspace", () => {
  it("renders queue, retry, failure and recent job diagnostics", async () => {
    const api = makeApi();
    render(<Jobs api={api} />);

    expect(await screen.findByText("#41")).toBeInTheDocument();
    expect(screen.getByText("#40")).toBeInTheDocument();
    expect(screen.getByText("provider timeout", { exact: false })).toBeInTheDocument();
    expect(screen.getByText("2/5")).toBeInTheDocument();
    expect(screen.getAllByText("store_acme_s1")).toHaveLength(2);
    expect(screen.getByText("Eșuate")).toBeInTheDocument();
    expect(screen.getByText("Finalizate")).toBeInTheDocument();
  });

  it("refreshes diagnostics without coupling to other pages", async () => {
    const api = makeApi();
    render(<Jobs api={api} />);
    await screen.findByText("#41");

    fireEvent.click(screen.getByRole("button", { name: "Actualizează" }));
    await waitFor(() => expect(api.get).toHaveBeenCalledTimes(2));
  });

  it("surfaces diagnostics API failure as an accessible alert", async () => {
    const api = {
      healthz: vi.fn(),
      readyz: vi.fn(),
      get: vi.fn(async () => { throw new Error("jobs unavailable"); }),
      post: vi.fn(),
    } as unknown as ApiClient;

    render(<Jobs api={api} />);
    expect(await screen.findByRole("alert")).toHaveTextContent("jobs unavailable");
  });
});

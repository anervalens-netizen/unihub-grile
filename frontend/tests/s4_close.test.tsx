/**
 * S4 Close page tests.
 *
 * Reopen reason validation: the Reopen button is disabled until the user
 * has typed at least 4 non-whitespace characters; the inline help text
 * updates accordingly.
 */

import { describe, expect, it, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import "@testing-library/jest-dom/vitest";
import { Close } from "../src/pages/Close";
import type {
  ApiClient,
  CloseChecklist,
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

const CHECKLIST: CloseChecklist = {
  month_id: MONTH.id,
  revision: 0,
  state: "OPEN",
  blockers: [
    {
      code: "STORE_DAY_UNCOVERED",
      severity: 1,
      title: "Magazin fără agent",
      detail: "store_x neacoperit pe 2026-08-05",
      blocking: true,
    },
  ],
  generated_at: null,
  export_summary: [],
  job_summary: [],
  expected_revision: 0,
};

function makeApi(): ApiClient {
  return {
    healthz: vi.fn(),
    readyz: vi.fn(),
    get: vi.fn(async (path: string) => {
      if (path.endsWith("/close-checklist")) return CHECKLIST;
      if (path.endsWith("/close-events")) return [];
      throw new Error(`unexpected GET ${path}`);
    }),
    post: vi.fn(async () => CHECKLIST),
  } as unknown as ApiClient;
}

describe("Close page reopen reason validation", () => {
  it("disables the Reopen button until the reason has at least 4 chars", async () => {
    const api = makeApi();
    render(<Close api={api} months={[MONTH]} monthsError={null} />);
    const button = await screen.findByRole("button", { name: /Reopen/i });
    expect(button).toBeDisabled();
    const textarea = screen.getByRole("textbox") as HTMLTextAreaElement;
    fireEvent.change(textarea, { target: { value: "ab" } });
    expect(button).toBeDisabled();
    fireEvent.change(textarea, { target: { value: "abcd" } });
    expect(button).toBeEnabled();
  });

  it("rejects whitespace-only reasons", async () => {
    const api = makeApi();
    render(<Close api={api} months={[MONTH]} monthsError={null} />);
    const button = await screen.findByRole("button", { name: /Reopen/i });
    const textarea = screen.getByRole("textbox") as HTMLTextAreaElement;
    fireEvent.change(textarea, { target: { value: "    " } });
    expect(button).toBeDisabled();
  });

  it("renders the audit timeline even when empty", async () => {
    const api = makeApi();
    render(<Close api={api} months={[MONTH]} monthsError={null} />);
    expect(await screen.findByText(/Audit timeline/)).toBeTruthy();
    expect(screen.getByText(/Niciun eveniment/)).toBeTruthy();
  });
});

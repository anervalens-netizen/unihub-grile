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

const CLEAR_CHECKLIST: CloseChecklist = {
  ...{
    month_id: MONTH.id,
    revision: 0,
    state: "OPEN",
    blockers: [],
    generated_at: null,
    export_summary: [],
    job_summary: [],
    expected_revision: 0,
  },
};

const CLOSED_CHECKLIST: CloseChecklist = {
  ...CLEAR_CHECKLIST,
  revision: 1,
  expected_revision: 1,
  state: "CLOSED",
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

function makeApi(checklist: CloseChecklist = CHECKLIST): ApiClient {
  return {
    healthz: vi.fn(),
    readyz: vi.fn(),
    get: vi.fn(async (path: string) => {
      if (path.endsWith("/close-checklist")) return checklist;
      if (path.endsWith("/close-events")) return [];
      throw new Error(`unexpected GET ${path}`);
    }),
    post: vi.fn(async () => checklist),
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

  it("blocks preparation while checklist blockers remain", async () => {
    const api = makeApi();
    render(<Close api={api} months={[MONTH]} monthsError={null} />);
    expect(await screen.findByRole("button", { name: /Pregătește închiderea/i })).toBeDisabled();
  });

  it("prepares, confirms, posts expected_revision, and refreshes checklist/events", async () => {
    let checklistCalls = 0;
    const events = [{
      id: 1, month_id: MONTH.id, action: "CLOSE", previous_state: "OPEN", new_state: "CLOSED",
      revision_before: 0, revision_after: 1, actor_id: "admin", reason: null, blockers: "[]",
      previous_event_digest: null, event_digest: "digest-close",
    }];
    const api = {
      healthz: vi.fn(), readyz: vi.fn(),
      get: vi.fn(async (path: string) => {
        if (path.endsWith("/close-checklist")) return checklistCalls++ === 0 ? CLEAR_CHECKLIST : CLOSED_CHECKLIST;
        if (path.endsWith("/close-events")) return checklistCalls > 1 ? events : [];
        throw new Error(`unexpected GET ${path}`);
      }),
      post: vi.fn(async (path: string, body: unknown) => {
        expect(path).toBe(`/months/${MONTH.id}/close`);
        expect(body).toEqual({ expected_revision: 0 });
        return { month_id: MONTH.id, revision: 1, new_state: "CLOSED", audit_event_id: 1, blockers: [] };
      }),
    } as unknown as ApiClient;
    render(<Close api={api} months={[MONTH]} monthsError={null} />);
    fireEvent.click(await screen.findByRole("button", { name: /Pregătește închiderea/i }));
    expect(screen.getByRole("alertdialog")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /Confirmă închiderea/i }));
    expect(await screen.findByText("CLOSE", { selector: "strong" })).toBeInTheDocument();
    expect(screen.getAllByText(/CLOSED/).length).toBeGreaterThan(0);
  });
});

/**
 * S4 Close page tests.
 *
 * Reopen reason validation: the Reopen button is disabled until the user
 * has typed at least 4 non-whitespace characters; the inline help text
 * updates accordingly.
 */

import { describe, expect, it, vi } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
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
  month_id: MONTH.id,
  revision: 0,
  state: "OPEN",
  blockers: [],
  generated_at: null,
  export_summary: [],
  job_summary: [],
  expected_revision: 0,
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
    {
      code: "SHEET_CANARY_REQUIRED",
      severity: 3,
      title: "Canary Sheet nesincronizat",
      detail: "Sheet pending",
      blocking: false,
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
  it("disables the Reopen button until a CLOSED month has a reason with at least 4 chars", async () => {
    const api = makeApi(CLOSED_CHECKLIST);
    render(<Close api={api} months={[MONTH]} monthsError={null} />);
    const button = await screen.findByRole("button", { name: /Reopen/i });
    expect(button).toBeDisabled();
    const textarea = screen.getByRole("textbox") as HTMLTextAreaElement;
    expect(textarea).toBeEnabled();
    fireEvent.change(textarea, { target: { value: "ab" } });
    expect(button).toBeDisabled();
    fireEvent.change(textarea, { target: { value: "abcd" } });
    expect(button).toBeEnabled();
  });

  it("keeps reopen inert while the month is not CLOSED", async () => {
    const api = makeApi(CLEAR_CHECKLIST);
    render(<Close api={api} months={[MONTH]} monthsError={null} />);
    const button = await screen.findByRole("button", { name: /Reopen/i });
    const textarea = screen.getByRole("textbox") as HTMLTextAreaElement;
    expect(textarea).toBeDisabled();
    expect(button).toBeDisabled();
    expect(screen.getByText(/Reopen devine disponibil când luna este CLOSED/i)).toBeInTheDocument();
  });

  it("rejects whitespace-only reasons", async () => {
    const api = makeApi(CLOSED_CHECKLIST);
    render(<Close api={api} months={[MONTH]} monthsError={null} />);
    const button = await screen.findByRole("button", { name: /Reopen/i });
    const textarea = screen.getByRole("textbox") as HTMLTextAreaElement;
    fireEvent.change(textarea, { target: { value: "    " } });
    expect(button).toBeDisabled();
  });

  it("renders blocker/advisory counts and audit validation snapshots with the digest chain", async () => {
    const events = [{
      id: 7,
      month_id: MONTH.id,
      action: "CLOSE",
      previous_state: "OPEN",
      new_state: "CLOSED",
      revision_before: 2,
      revision_after: 3,
      actor_id: "user_admin",
      reason: null,
      blockers: JSON.stringify([{
        code: "SHEET_CANARY_REQUIRED",
        store_id: "store_x",
        person_id: null,
        business_date: "2026-08-05",
        message: "canary pending at close time",
      }]),
      previous_event_digest: "digest-prev",
      event_digest: "digest-current",
    }];
    const api = {
      healthz: vi.fn(), readyz: vi.fn(), post: vi.fn(),
      get: vi.fn(async (path: string) => {
        if (path.endsWith("/close-checklist")) return CHECKLIST;
        if (path.endsWith("/close-events")) return events;
        throw new Error(`unexpected GET ${path}`);
      }),
    } as unknown as ApiClient;

    render(<Close api={api} months={[MONTH]} monthsError={null} />);
    expect(await screen.findByText(/Condiții blocante:/)).toHaveTextContent("Condiții blocante: 1 · avertismente: 1");
    expect(screen.getByText("blocant")).toBeInTheDocument();
    expect(screen.getByText("avertisment")).toBeInTheDocument();
    expect(screen.getByText(/#7/)).toBeInTheDocument();
    fireEvent.click(screen.getByText(/Snapshot validare \(1\)/));
    expect(screen.getByText(/canary pending at close time/)).toBeInTheDocument();
    expect(screen.getByText(/2026-08-05 · store_x · —/)).toBeInTheDocument();
    expect(screen.getByText(/Lanț audit:/)).toHaveTextContent(/digest-prev.*digest-current/);
  });

  it("keeps checklist and close controls usable when audit history fails", async () => {
    const api = {
      healthz: vi.fn(), readyz: vi.fn(), post: vi.fn(),
      get: vi.fn(async (path: string) => {
        if (path.endsWith("/close-checklist")) return CLEAR_CHECKLIST;
        if (path.endsWith("/close-events")) throw new Error("audit unavailable");
        throw new Error(`unexpected GET ${path}`);
      }),
    } as unknown as ApiClient;
    render(<Close api={api} months={[MONTH]} monthsError={null} />);
    expect(await screen.findByRole("button", { name: /Pregătește închiderea/i })).toBeEnabled();
    expect(screen.getByRole("alert")).toHaveTextContent(/Istoricul audit este indisponibil: audit unavailable/);
    expect(screen.getByText(/Nicio condiție blocantă detectată/)).toBeInTheDocument();
  });

  it("blocks preparation while checklist blockers remain", async () => {
    const api = makeApi();
    render(<Close api={api} months={[MONTH]} monthsError={null} />);
    expect(await screen.findByRole("button", { name: /Pregătește închiderea/i })).toBeDisabled();
  });

  it("reloads checklist and exits confirmation when close hits STALE_REVISION", async () => {
    let checklistCalls = 0;
    const refreshedChecklist: CloseChecklist = {
      ...CLEAR_CHECKLIST,
      revision: 1,
      expected_revision: 1,
    };
    const api = {
      healthz: vi.fn(), readyz: vi.fn(),
      get: vi.fn(async (path: string) => {
        if (path.endsWith("/close-checklist")) return checklistCalls++ === 0 ? CLEAR_CHECKLIST : refreshedChecklist;
        if (path.endsWith("/close-events")) return [];
        throw new Error(`unexpected GET ${path}`);
      }),
      post: vi.fn(async () => {
        throw { status: 409, code: "STALE_REVISION" };
      }),
    } as unknown as ApiClient;

    render(<Close api={api} months={[MONTH]} monthsError={null} />);
    fireEvent.click(await screen.findByRole("button", { name: /Pregătește închiderea/i }));
    fireEvent.click(screen.getByRole("button", { name: /Confirmă închiderea/i }));
    expect(await screen.findByRole("alert")).toHaveTextContent(/reîncărcat la revizia 1/i);
    expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Pregătește închiderea/i })).toBeEnabled();
    expect((api.post as ReturnType<typeof vi.fn>).mock.calls[0]).toEqual([
      `/months/${MONTH.id}/close`,
      { expected_revision: 0 },
    ]);
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
    await waitFor(() => expect(screen.getByRole("textbox")).toBeEnabled());
  });
});

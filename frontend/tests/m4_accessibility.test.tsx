import { readFileSync } from "node:fs";
import { join } from "node:path";
import { useState } from "react";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import "@testing-library/jest-dom/vitest";
import { describe, expect, it, vi } from "vitest";
import type { ApiClient, ProgramCell, ProgramGrid } from "../src/api/client";
import { Layout } from "../src/components/Layout";
import { ProgramMatrix } from "../src/components/ProgramMatrix";
import { Jobs } from "../src/pages/Jobs";

function TabFixture() {
  const [active, setActive] = useState<"one" | "two" | "three">("one");
  return (
    <Layout sidebar={<span>nav</span>} header={<span>header</span>}>
      <div role="tablist" aria-label="Secțiuni test">
        <button type="button" role="tab" aria-selected={active === "one"} onClick={() => setActive("one")}>Unu</button>
        <button type="button" role="tab" aria-selected={active === "two"} onClick={() => setActive("two")}>Doi</button>
        <button type="button" role="tab" aria-selected={active === "three"} onClick={() => setActive("three")}>Trei</button>
      </div>
      <section>
        <button type="button" onClick={() => setActive("two")}>Schimbă panelul</button>
        <span>Panel {active}</span>
      </section>
    </Layout>
  );
}

const GRID: ProgramGrid = {
  month_id: "month_1",
  year: 2026,
  month: 8,
  revision: 1,
  dates: ["2026-08-01"],
  legend: ["NORMAL"],
  rows: [{
    row_id: "person_a",
    label: "Alice",
    home_store_id: "store_x",
    cells: [{
      business_date: "2026-08-01",
      person_id: "person_a",
      store_id: "store_x",
      status: "WORKING",
      working_kind: "NORMAL",
      display_name: "Alice",
      home_store_id: "store_x",
      badge: "NORMAL",
      locked: false,
    }],
  }],
};

function MatrixFixture() {
  const [editing, setEditing] = useState<{ rowId: string; businessDate: string } | null>(null);
  const [editValue, setEditValue] = useState({
    personId: "person_a",
    storeId: "store_x",
    status: "WORKING",
    workingKind: "NORMAL",
  });
  return (
    <ProgramMatrix
      grid={GRID}
      onCellClick={(rowId: string, cell: ProgramCell) => setEditing({ rowId, businessDate: cell.business_date })}
      editing={editing}
      editValue={editValue}
      people={[{ id: "person_a", label: "Alice", homeStoreId: "store_x" }]}
      stores={[{ id: "store_x", label: "Magazin X" }]}
      onEditChange={setEditValue}
      onCancelEdit={() => setEditing(null)}
    />
  );
}

const JOB_DIAGNOSTICS = {
  counts: { queued: 0, retrying: 0, running: 0, failed: 0, done: 1 },
  terminal_history_limit: 50,
  jobs: [{
    id: 1,
    kind: "EXPORT_XLSX_STORE",
    state: "DONE",
    attempts: 1,
    max_attempts: 3,
    run_after: "2026-08-23T10:00:00Z",
    locked_at: null,
    created_at: "2026-08-23T10:00:00Z",
    updated_at: "2026-08-23T10:01:00Z",
    last_error: null,
    month_id: "month_1",
    store_ids: ["store_x"],
  }],
} as const;

describe("FE-014 keyboard and accessibility pass", () => {
  it("declares Romanian as the document language", () => {
    const html = readFileSync(join(process.cwd(), "index.html"), "utf8");
    expect(html).toContain('<html lang="ro">');
  });

  it("provides roving tab stops, arrow/Home/End navigation and tabpanel linkage", async () => {
    render(<TabFixture />);
    const one = screen.getByRole("tab", { name: "Unu" });
    const two = screen.getByRole("tab", { name: "Doi" });
    const three = screen.getByRole("tab", { name: "Trei" });

    await waitFor(() => expect(one).toHaveAttribute("tabindex", "0"));
    expect(two).toHaveAttribute("tabindex", "-1");
    one.focus();
    fireEvent.keyDown(one, { key: "ArrowRight" });
    await waitFor(() => expect(two).toHaveAttribute("aria-selected", "true"));
    expect(two).toHaveFocus();

    fireEvent.keyDown(two, { key: "End" });
    await waitFor(() => expect(three).toHaveAttribute("aria-selected", "true"));
    expect(three).toHaveFocus();

    fireEvent.keyDown(three, { key: "Home" });
    await waitFor(() => expect(one).toHaveAttribute("aria-selected", "true"));
    expect(one).toHaveFocus();

    const panel = await screen.findByRole("tabpanel");
    expect(one).toHaveAttribute("aria-controls", panel.id);
    expect(panel).toHaveAttribute("aria-labelledby", one.id);
  });

  it("recovers focus to the selected tab when the focused panel control is removed", async () => {
    render(<TabFixture />);
    const switcher = screen.getByRole("button", { name: "Schimbă panelul" });
    switcher.focus();
    fireEvent.click(switcher);

    const two = screen.getByRole("tab", { name: "Doi" });
    await waitFor(() => expect(two).toHaveAttribute("aria-selected", "true"));
    await waitFor(() => expect(two).toHaveFocus());
  });

  it("moves focus into the calendar editor and Escape returns it to the edited cell", async () => {
    render(<MatrixFixture />);
    const cell = screen.getByRole("button", { name: /Alice pe 2026-08-01/i });
    cell.focus();
    fireEvent.click(cell);

    const agent = await screen.findByRole("combobox", { name: "Agent" });
    await waitFor(() => expect(agent).toHaveFocus());
    fireEvent.keyDown(agent, { key: "Escape" });
    await waitFor(() => expect(cell).toHaveFocus());
  });

  it("exposes the jobs grid with table, row and column-header semantics", async () => {
    const api = {
      healthz: vi.fn(),
      readyz: vi.fn(),
      get: vi.fn(async () => JOB_DIAGNOSTICS),
      post: vi.fn(),
    } as unknown as ApiClient;
    render(<Jobs api={api} />);

    const table = await screen.findByRole("table", { name: "Istoric joburi asincrone" });
    expect(table).toBeInTheDocument();
    expect(screen.getAllByRole("columnheader")).toHaveLength(6);
    expect(screen.getAllByRole("row").length).toBeGreaterThanOrEqual(2);
  });
});

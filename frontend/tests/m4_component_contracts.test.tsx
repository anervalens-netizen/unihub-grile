import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import "@testing-library/jest-dom/vitest";
import { describe, expect, it, vi } from "vitest";
import type { MonthSummary, ProgramGrid, ProgramRow } from "../src/api/client";
import type { Capability } from "../src/capabilities";
import { MonthSelector } from "../src/components/MonthSelector";
import { Nav } from "../src/components/Nav";
import { ProgramMatrix } from "../src/components/ProgramMatrix";
import { LoadingState, RequestError } from "../src/components/RequestState";

const OPEN_MONTH: MonthSummary = {
  id: "month_open",
  tenant_id: "tenant_acme",
  year: 2026,
  month: 8,
  state: "OPEN",
  revision: 4,
  closed_at: null,
};

const CLOSED_MONTH: MonthSummary = {
  ...OPEN_MONTH,
  id: "month_closed",
  month: 7,
  state: "CLOSED",
  revision: 9,
};

function largeGrid(rowCount = 50): ProgramGrid {
  const rows: ProgramRow[] = Array.from({ length: rowCount }, (_, index) => ({
    row_id: `row_${index}`,
    label: `Rând ${index}`,
    home_store_id: "store_x",
    cells: [{
      business_date: "2026-08-01",
      person_id: `person_${index}`,
      store_id: "store_x",
      status: "WORKING",
      working_kind: "NORMAL",
      display_name: `Agent ${index}`,
      home_store_id: "store_x",
      badge: "NORMAL",
      locked: false,
    }],
  }));
  return {
    month_id: OPEN_MONTH.id,
    year: 2026,
    month: 8,
    revision: 4,
    dates: ["2026-08-01"],
    rows,
    legend: ["NORMAL"],
  };
}

describe("FE-015 shared component contracts", () => {
  it("renders translated month states and sends the selected month id", () => {
    const onChange = vi.fn();
    render(
      <MonthSelector
        months={[OPEN_MONTH, CLOSED_MONTH]}
        value={OPEN_MONTH.id}
        onChange={onChange}
        error={null}
      />,
    );

    const selector = screen.getByRole("combobox", { name: "Alege luna" });
    expect(screen.getByRole("option", { name: /2026-08 · Deschisă · rev\. 4/i })).toBeInTheDocument();
    expect(screen.getByRole("option", { name: /2026-07 · Închisă · rev\. 9/i })).toBeInTheDocument();
    fireEvent.change(selector, { target: { value: CLOSED_MONTH.id } });
    expect(onChange).toHaveBeenCalledWith(CLOSED_MONTH.id);
  });

  it("keeps MonthSelector error and empty states distinct from an interactive selector", () => {
    const { rerender } = render(
      <MonthSelector months={[OPEN_MONTH]} value={OPEN_MONTH.id} onChange={vi.fn()} error="months unavailable" />,
    );
    expect(screen.getByRole("alert")).toHaveTextContent("months unavailable");
    expect(screen.queryByRole("combobox")).not.toBeInTheDocument();

    rerender(<MonthSelector months={[]} value={null} onChange={vi.fn()} error={null} />);
    expect(screen.getByText(/Nicio lună disponibilă pentru organizație/i)).toBeInTheDocument();
    expect(screen.queryByText(/ingest\/fixture/i)).not.toBeInTheDocument();
    expect(screen.queryByRole("combobox")).not.toBeInTheDocument();
  });

  it("exposes RequestError as an alert and wires explicit retry", () => {
    const retry = vi.fn();
    render(<RequestError message="citirea a eșuat" onRetry={retry} />);
    expect(screen.getByRole("alert")).toHaveTextContent("citirea a eșuat");
    fireEvent.click(screen.getByRole("button", { name: "Reîncearcă" }));
    expect(retry).toHaveBeenCalledTimes(1);
  });

  it("exposes LoadingState as a live status surface", () => {
    render(<LoadingState>Încarc datele…</LoadingState>);
    expect(screen.getByRole("status")).toHaveTextContent("Încarc datele…");
  });

  it("maps Store and Agent detail routes back to Hub as the active navigation item", () => {
    const capabilities = new Set<Capability>(["schedule.read"]);
    const { rerender } = render(
      <Nav route={{ name: "store", segments: ["store_x"] }} months={[OPEN_MONTH]} capabilities={capabilities} role="MANAGER" />,
    );
    expect(screen.getByRole("button", { name: /^Hub$/i })).toHaveAttribute("aria-current", "page");
    expect(screen.getByText("Luni disponibile").parentElement).toHaveTextContent("1");

    rerender(
      <Nav route={{ name: "agent", segments: ["person_a"] }} months={[OPEN_MONTH]} capabilities={capabilities} role="MANAGER" />,
    );
    expect(screen.getByRole("button", { name: /^Hub$/i })).toHaveAttribute("aria-current", "page");
  });

  it("keeps a read-only ProgramMatrix inert while retaining an accessible grid label", async () => {
    render(<ProgramMatrix grid={largeGrid(1)} viewportHeight={72} rowHeight={36} />);
    const grid = screen.getByRole("grid", { name: "Calendar program lunar" });
    expect(grid).toHaveAttribute("aria-rowcount", "1");
    const cell = await screen.findByRole("button", { name: /Rând 0 pe 2026-08-01/i });
    expect(cell).toBeDisabled();
    expect(cell).toHaveAccessibleName(/doar citire/i);
  });

  it("virtualizes large ProgramMatrix row sets and advances the rendered window on scroll", async () => {
    render(<ProgramMatrix grid={largeGrid()} viewportHeight={72} rowHeight={36} />);
    const grid = screen.getByRole("grid", { name: "Calendar program lunar" });

    expect(await screen.findByText("Rând 0")).toBeInTheDocument();
    expect(screen.queryByText("Rând 20")).not.toBeInTheDocument();

    Object.defineProperty(grid, "scrollTop", { value: 720, writable: true, configurable: true });
    fireEvent.scroll(grid);

    await waitFor(() => expect(screen.getByText("Rând 20")).toBeInTheDocument());
    expect(screen.queryByText("Rând 0")).not.toBeInTheDocument();
  });
});

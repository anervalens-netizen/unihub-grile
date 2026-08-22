import { render, screen } from "@testing-library/react";
import "@testing-library/jest-dom/vitest";
import { describe, expect, it } from "vitest";
import {
  canAccessRoute,
  type Capability,
} from "../src/capabilities";
import { Nav } from "../src/components/Nav";

const managerCapabilities = new Set<Capability>([
  "catalog.read",
  "schedule.read",
  "schedule.write",
  "grid.read",
  "holiday.read",
  "epay.read",
  "sheet.read",
  "export.read",
  "month.read",
  "jobs.read",
]);

const readonlyCapabilities = new Set<Capability>([
  "catalog.read",
  "holiday.read",
  "month.read",
]);

const adminCapabilities = new Set<Capability>([
  ...managerCapabilities,
  "grid.compute",
  "payroll.master.read",
  "payroll.master.write",
  "holiday.write",
  "epay.write",
  "sheet.sync",
  "export.create",
  "month.close.read",
  "month.close",
  "month.reopen",
  "admin.fixture",
]);

describe("capability-aware shell", () => {
  it("shows a manager only modules the backend policy permits", () => {
    render(
      <Nav
        route={{ name: "overview", segments: [] }}
        months={[]}
        capabilities={managerCapabilities}
        role="MANAGER"
      />,
    );

    expect(screen.getByRole("button", { name: /^Hub$/i })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Program/i })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Excepții/i })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Joburi/i })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Management/i })).not.toBeInTheDocument();
  });

  it("does not invent operational modules for readonly", () => {
    render(
      <Nav
        route={{ name: "overview", segments: [] }}
        months={[]}
        capabilities={readonlyCapabilities}
        role="READONLY"
      />,
    );

    expect(screen.getByText(/Nu există module operaționale/i)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Program/i })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Management/i })).not.toBeInTheDocument();
  });

  it("blocks direct routes unless all backend-required capabilities are present", () => {
    expect(
      canAccessRoute(managerCapabilities, { name: "close", segments: [] }),
    ).toBe(false);
    expect(
      canAccessRoute(adminCapabilities, { name: "close", segments: [] }),
    ).toBe(true);
    expect(
      canAccessRoute(readonlyCapabilities, { name: "overview", segments: [] }),
    ).toBe(false);
    expect(
      canAccessRoute(managerCapabilities, { name: "store", segments: ["store_acme_s1"] }),
    ).toBe(true);
  });
});

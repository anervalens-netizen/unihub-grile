import type { Route } from "./router";

export type Capability =
  | "catalog.read"
  | "schedule.read"
  | "schedule.write"
  | "grid.read"
  | "grid.compute"
  | "payroll.master.read"
  | "payroll.master.write"
  | "holiday.read"
  | "holiday.write"
  | "epay.read"
  | "epay.write"
  | "sheet.read"
  | "sheet.sync"
  | "export.read"
  | "export.create"
  | "month.read"
  | "month.close.read"
  | "month.close"
  | "month.reopen"
  | "admin.fixture"
  | "jobs.read";

export interface SessionInfo {
  user_id: string;
  tenant_id: string;
  role: "ADMIN" | "MANAGER" | "READONLY";
  email: string;
  capabilities: Capability[];
}

export function hasCapability(
  capabilities: ReadonlySet<Capability>,
  capability: Capability,
): boolean {
  return capabilities.has(capability);
}

export function hasAllCapabilities(
  capabilities: ReadonlySet<Capability>,
  required: readonly Capability[],
): boolean {
  return required.every((capability) => capabilities.has(capability));
}

export function requiredCapabilitiesForRoute(route: Route): readonly Capability[] {
  switch (route.name) {
    case "overview":
    case "program":
    case "exceptions":
      return ["schedule.read"];
    case "jobs":
      return ["jobs.read"];
    case "close":
      return ["month.close.read"];
    case "store":
      return ["catalog.read", "schedule.read", "grid.read", "epay.read", "sheet.read"];
    case "agent":
      return ["schedule.read", "grid.read", "epay.read", "sheet.read"];
    default:
      return [];
  }
}

export function canAccessRoute(
  capabilities: ReadonlySet<Capability>,
  route: Route,
): boolean {
  return hasAllCapabilities(capabilities, requiredCapabilitiesForRoute(route));
}

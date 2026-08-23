import type { Capability } from "./capabilities";

export const GRILE_SHELL_CONTRACT_VERSION = "grile-shell.v1" as const;

export type HostRouteName =
  | "overview"
  | "program"
  | "exceptions"
  | "close"
  | "jobs"
  | "store"
  | "agent";

export interface HostRouteSpec {
  route: HostRouteName;
  pathTemplate: string;
  entityParam: "store_id" | "person_id" | null;
  requiredCapabilities: readonly Capability[];
}

/**
 * Stable host-facing deep-link inventory for a future Retail shell mount.
 *
 * The host may choose its own outer mount path. These are Grile-owned child
 * routes and capability requirements; Retail must not duplicate Grile route
 * authorization logic.
 */
export const HOST_ROUTE_CONTRACT: readonly HostRouteSpec[] = [
  {
    route: "overview",
    pathTemplate: "#/overview",
    entityParam: null,
    requiredCapabilities: ["schedule.read"],
  },
  {
    route: "program",
    pathTemplate: "#/program",
    entityParam: null,
    requiredCapabilities: ["schedule.read"],
  },
  {
    route: "exceptions",
    pathTemplate: "#/exceptions",
    entityParam: null,
    requiredCapabilities: ["schedule.read"],
  },
  {
    route: "close",
    pathTemplate: "#/close",
    entityParam: null,
    requiredCapabilities: ["month.close.read"],
  },
  {
    route: "jobs",
    pathTemplate: "#/jobs",
    entityParam: null,
    requiredCapabilities: ["jobs.read"],
  },
  {
    route: "store",
    pathTemplate: "#/store/:store_id",
    entityParam: "store_id",
    requiredCapabilities: [
      "catalog.read",
      "schedule.read",
      "grid.read",
      "epay.read",
      "sheet.read",
    ],
  },
  {
    route: "agent",
    pathTemplate: "#/agent/:person_id",
    entityParam: "person_id",
    requiredCapabilities: ["schedule.read", "grid.read", "epay.read", "sheet.read"],
  },
] as const;

export function standaloneDeepLink(route: HostRouteName, entityId?: string): string {
  const spec = HOST_ROUTE_CONTRACT.find((candidate) => candidate.route === route);
  if (!spec) {
    throw new Error(`Unknown Grile host route: ${route}`);
  }
  if (spec.entityParam === null) {
    return spec.pathTemplate;
  }
  if (!entityId) {
    throw new Error(`${route} deep link requires ${spec.entityParam}`);
  }
  return spec.pathTemplate.replace(`:${spec.entityParam}`, encodeURIComponent(entityId));
}

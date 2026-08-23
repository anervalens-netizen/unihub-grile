import { describe, expect, it } from "vitest";

import { requiredCapabilitiesForRoute } from "../src/capabilities";
import {
  GRILE_SHELL_CONTRACT_VERSION,
  HOST_ROUTE_CONTRACT,
  standaloneDeepLink,
} from "../src/integrationContract";


describe("M6 host integration contract", () => {
  it("pins one versioned route inventory to the real frontend capability map", () => {
    expect(GRILE_SHELL_CONTRACT_VERSION).toBe("grile-shell.v1");
    expect(HOST_ROUTE_CONTRACT.map((spec) => spec.route)).toEqual([
      "overview",
      "program",
      "exceptions",
      "close",
      "jobs",
      "store",
      "agent",
    ]);

    for (const spec of HOST_ROUTE_CONTRACT) {
      expect(spec.requiredCapabilities).toEqual(
        requiredCapabilitiesForRoute({ name: spec.route, segments: [] }),
      );
    }
  });

  it("builds stable standalone child deep links without owning the host mount path", () => {
    expect(standaloneDeepLink("overview")).toBe("#/overview");
    expect(standaloneDeepLink("program")).toBe("#/program");
    expect(standaloneDeepLink("store", "store_acme_s1")).toBe("#/store/store_acme_s1");
    expect(standaloneDeepLink("agent", "person_acme_a")).toBe("#/agent/person_acme_a");
  });

  it("fails closed when an entity deep link omits its stable identifier", () => {
    expect(() => standaloneDeepLink("store")).toThrow("requires store_id");
    expect(() => standaloneDeepLink("agent")).toThrow("requires person_id");
  });
});

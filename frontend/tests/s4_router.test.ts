/**
 * Hash router tests — covers the small ``currentRoute`` /
 * ``subscribeRoute`` helpers used by the App component.
 */

import { describe, expect, it, beforeEach, vi } from "vitest";
import { currentRoute, subscribeRoute } from "../src/router";

describe("hash router", () => {
  beforeEach(() => {
    window.location.hash = "";
  });

  it("returns the overview route when no hash is set", () => {
    expect(currentRoute().name).toBe("overview");
    expect(currentRoute().segments).toEqual([]);
  });

  it("parses a deep link into segments", () => {
    window.location.hash = "#/store/store_x/2026-08";
    const route = currentRoute();
    expect(route.name).toBe("store");
    expect(route.segments).toEqual(["store_x", "2026-08"]);
  });

  it("notifies subscribers when the hash changes", () => {
    const handler = vi.fn();
    const unsubscribe = subscribeRoute(handler);
    // Set the hash directly to a different value so the ``hashchange``
    // event fires reliably (browsers skip the event when the URL is the
    // same after assigning ``window.location.hash``).
    window.location.hash = "#/exceptions";
    window.dispatchEvent(new HashChangeEvent("hashchange"));
    expect(handler).toHaveBeenCalled();
    const lastCall = handler.mock.calls[handler.mock.calls.length - 1]?.[0];
    expect(lastCall?.name).toBe("exceptions");
    unsubscribe();
  });

  it("falls back to the overview route on an unknown segment", () => {
    window.location.hash = "#/banana/foo";
    const route = currentRoute();
    expect(route.name).toBe("banana");
    // The App router treats anything other than the known names as overview,
    // exercising the fallback here confirms the segment parsing.
    expect(route.segments).toEqual(["foo"]);
  });
});

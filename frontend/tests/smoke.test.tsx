/**
 * Smoke test for the S1 frontend.
 *
 * Renders the health page and confirms the static copy shows up. The
 * network calls are stubbed so the test runs without a backend.
 */

import { describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import "@testing-library/jest-dom/vitest";
import { Health } from "../src/pages/Health";
import type { HealthReport } from "../src/api/client";

describe("Health page", () => {
  it("renders the static structure with a stubbed client", () => {
    const fakeApi = {
      healthz: vi.fn(),
      readyz: vi.fn(),
      get: vi.fn(),
      post: vi.fn(),
    };

    const health: HealthReport = {
      status: "ok",
      database: true,
      schema_version: "0001_initial_schema",
      app_version: "0.1.0",
    };

    render(<Health health={health} error={null} api={fakeApi} />);

    expect(screen.getByRole("heading", { level: 2 })).toHaveTextContent(/Backend health/i);
    expect(screen.getByText("ok")).toBeTruthy();
    expect(screen.getByText("0001_initial_schema")).toBeTruthy();
  });

  it("surfaces connection errors without crashing", () => {
    const fakeApi = {
      healthz: vi.fn(),
      readyz: vi.fn(),
      get: vi.fn(),
      post: vi.fn(),
    };

    render(<Health health={null} error="network down" api={fakeApi} />);

    expect(screen.getByRole("alert")).toHaveTextContent(/network down/i);
  });
});
import { useEffect, useState } from "react";
import { Health } from "./pages/Health";
import { Overview } from "./pages/Overview";
import { Layout } from "./components/Layout";
import { createApiClient, type HealthReport } from "./api/client";

const apiBaseUrl = (import.meta.env.VITE_API_BASE_URL ?? "/api") as string;
const identity = (import.meta.env.VITE_DEV_IDENTITY ?? "user_admin") as string;
const tenant = (import.meta.env.VITE_DEV_TENANT ?? "tenant_fixture") as string;

export function App() {
  const [health, setHealth] = useState<HealthReport | null>(null);
  const [error, setError] = useState<string | null>(null);
  const api = createApiClient({ baseUrl: apiBaseUrl, identity, tenant });

  useEffect(() => {
    let cancelled = false;
    api
      .healthz()
      .then((report) => {
        if (!cancelled) setHealth(report);
      })
      .catch((e: unknown) => {
        if (!cancelled) setError(String(e));
      });
    return () => {
      cancelled = true;
    };
  }, []);

  return (
    <Layout
      header={
        <header className="app-header">
          <h1>UniHub Grile — Foundation (S1)</h1>
          <p className="muted">
            Standalone stack, schema, AC-02 invariants, fixture connector,
            one worker. No Retail/Google I/O at this stage.
          </p>
        </header>
      }
    >
      <Health health={health} error={error} api={api} />
      <Overview api={api} />
    </Layout>
  );
}
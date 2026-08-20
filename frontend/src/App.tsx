import { useEffect, useMemo, useState } from "react";
import { Health } from "./pages/Health";
import { Overview } from "./pages/Overview";
import { Program } from "./pages/Program";
import { Exceptions } from "./pages/Exceptions";
import { Close } from "./pages/Close";
import { Magazin } from "./pages/Magazin";
import { Agent } from "./pages/Agent";
import { Layout } from "./components/Layout";
import { Nav } from "./components/Nav";
import {
  createApiClient,
  type ApiClient,
  type HealthReport,
  type MonthSummary,
} from "./api/client";
import { currentRoute, subscribeRoute, type Route } from "./router";

const apiBaseUrl = (import.meta.env.VITE_API_BASE_URL ?? "/api") as string;
const identity = (import.meta.env.VITE_DEV_IDENTITY ?? "user_admin") as string;
const tenant = (import.meta.env.VITE_DEV_TENANT ?? "tenant_acme") as string;

export interface AppState {
  api: ApiClient;
  health: HealthReport | null;
  healthError: string | null;
  months: MonthSummary[];
  monthsError: string | null;
}

export function App() {
  const [health, setHealth] = useState<HealthReport | null>(null);
  const [healthError, setHealthError] = useState<string | null>(null);
  const [months, setMonths] = useState<MonthSummary[]>([]);
  const [monthsError, setMonthsError] = useState<string | null>(null);
  const [route, setRoute] = useState<Route>(() => currentRoute());

  const api = useMemo<ApiClient>(
    () => createApiClient({ baseUrl: apiBaseUrl, identity, tenant }),
    [],
  );

  useEffect(() => subscribeRoute(setRoute), []);

  useEffect(() => {
    let cancelled = false;
    api
      .healthz()
      .then((report) => {
        if (!cancelled) setHealth(report);
      })
      .catch((e: unknown) => {
        if (!cancelled) setHealthError(String(e));
      });
    api
      .get<MonthSummary[]>("/months")
      .then((list) => {
        if (!cancelled) setMonths(list);
      })
      .catch((e: unknown) => {
        if (!cancelled) setMonthsError(String(e));
      });
    return () => {
      cancelled = true;
    };
  }, [api]);

  return (
    <Layout
      header={
        <header className="app-header">
          <h1>UniHub Grile — Manager UI (S4)</h1>
          <p className="muted">
            Overview, Program, Magazin, Agent, Excepții, Close — toate pe
            aceeași lună și același tenant. Calendarul este autoritatea; Pontajul,
            vânzările și grila sunt derivate.
          </p>
          <Nav route={route} months={months} />
        </header>
      }
    >
      <Health health={health} error={healthError} api={api} />
      <main className="app-main">
        <PageRouter
          api={api}
          route={route}
          months={months}
          monthsError={monthsError}
        />
      </main>
    </Layout>
  );
}

interface PageRouterProps {
  api: ApiClient;
  route: Route;
  months: MonthSummary[];
  monthsError: string | null;
}

function PageRouter({ api, route, months, monthsError }: PageRouterProps) {
  switch (route.name) {
    case "program":
      return <Program api={api} months={months} monthsError={monthsError} />;
    case "exceptions":
      return <Exceptions api={api} months={months} monthsError={monthsError} />;
    case "close":
      return <Close api={api} months={months} monthsError={monthsError} />;
    case "store":
      return (
        <Magazin
          api={api}
          storeId={route.segments[1] ?? null}
          months={months}
          monthsError={monthsError}
        />
      );
    case "agent":
      return (
        <Agent
          api={api}
          personId={route.segments[1] ?? null}
          months={months}
          monthsError={monthsError}
        />
      );
    case "overview":
    default:
      return (
        <Overview
          api={api}
          months={months}
          monthsError={monthsError}
        />
      );
  }
}

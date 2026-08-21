import { useEffect, useMemo, useState } from "react";
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
    api.healthz()
      .then((report) => {
        if (!cancelled) {
          setHealth(report);
          setHealthError(null);
        }
      })
      .catch((e: unknown) => {
        if (!cancelled) setHealthError(String(e));
      });
    api.get<MonthSummary[]>("/months")
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

  const pageMeta = getPageMeta(route);
  const systemState = healthError ? "offline" : health?.status === "ok" ? "online" : "checking";

  return (
    <Layout
      sidebar={<Nav route={route} months={months} />}
      header={
        <div className="topbar-inner">
          <div className="topbar-copy">
            <span className="eyebrow">MANAGER WORKSPACE</span>
            <h1>{pageMeta.title}</h1>
            <p>{pageMeta.subtitle}</p>
          </div>
          <div className="topbar-status" aria-label="Stare sistem">
            <span className={`status-dot status-${systemState}`} aria-hidden="true" />
            <span>{systemState === "online" ? "Sistem operațional" : systemState === "offline" ? "API indisponibil" : "Verific sistemul"}</span>
            {health?.app_version && <small>v{health.app_version}</small>}
          </div>
        </div>
      }
    >
      <PageRouter api={api} route={route} months={months} monthsError={monthsError} />
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
          storeId={route.segments[0] ?? null}
          months={months}
          monthsError={monthsError}
        />
      );
    case "agent":
      return (
        <Agent
          api={api}
          personId={route.segments[0] ?? null}
          months={months}
          monthsError={monthsError}
        />
      );
    case "overview":
    default:
      return <Overview api={api} months={months} monthsError={monthsError} />;
  }
}

function getPageMeta(route: Route): { title: string; subtitle: string } {
  switch (route.name) {
    case "program":
      return { title: "Calendar operațional", subtitle: "Planificare, suplimentare și acoperire într-o singură matrice." };
    case "exceptions":
      return { title: "Excepții și control", subtitle: "Tot ce necesită intervenție înainte să devină problemă." };
    case "close":
      return { title: "Închidere lună", subtitle: "Validare, audit și blocarea perioadei cu trasabilitate." };
    case "store":
      return { title: "Control magazin", subtitle: "Calendar, agenți, vânzări, pontaj și grilă într-un singur spațiu." };
    case "agent":
      return { title: "Control agent", subtitle: "Program și rezultate individuale, fără schimbarea contextului." };
    default:
      return { title: "Command Center", subtitle: "Imagine de ansamblu asupra rețelei și acces direct la fiecare magazin." };
  }
}

import { useEffect, useMemo, useState } from "react";
import { Overview } from "./pages/Overview";
import { Program } from "./pages/Program";
import { Exceptions } from "./pages/Exceptions";
import { Close } from "./pages/Close";
import { Magazin } from "./pages/Magazin";
import { Agent } from "./pages/Agent";
import { Jobs } from "./pages/Jobs";
import { Layout } from "./components/Layout";
import { Nav } from "./components/Nav";
import {
  createApiClient,
  type ApiClient,
  type HealthReport,
  type MonthSummary,
} from "./api/client";
import {
  canAccessRoute,
  type Capability,
  type SessionInfo,
} from "./capabilities";
import { currentRoute, subscribeRoute, type Route } from "./router";

const apiBaseUrl = (import.meta.env.VITE_API_BASE_URL ?? "/api") as string;
// Standalone development keeps convenient fixture defaults. Production builds
// never synthesize an admin/tenant identity; the future Retail host provides
// identity through the configured provider boundary.
const identity = (import.meta.env.VITE_DEV_IDENTITY ??
  (import.meta.env.DEV ? "user_admin" : undefined)) as string | undefined;
const tenant = (import.meta.env.VITE_DEV_TENANT ??
  (import.meta.env.DEV ? "tenant_acme" : undefined)) as string | undefined;

export function App() {
  const [health, setHealth] = useState<HealthReport | null>(null);
  const [healthError, setHealthError] = useState<string | null>(null);
  const [session, setSession] = useState<SessionInfo | null>(null);
  const [sessionError, setSessionError] = useState<string | null>(null);
  const [months, setMonths] = useState<MonthSummary[]>([]);
  const [monthsError, setMonthsError] = useState<string | null>(null);
  const [route, setRoute] = useState<Route>(() => currentRoute());

  const api = useMemo<ApiClient>(
    () => createApiClient({ baseUrl: apiBaseUrl, identity, tenant }),
    [],
  );
  const capabilities = useMemo<ReadonlySet<Capability>>(
    () => new Set(session?.capabilities ?? []),
    [session],
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
    api.get<SessionInfo>("/session")
      .then((principal) => {
        if (!cancelled) {
          setSession(principal);
          setSessionError(null);
        }
      })
      .catch((e: unknown) => {
        if (!cancelled) setSessionError(String(e));
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
      sidebar={
        <Nav
          route={route}
          months={months}
          capabilities={capabilities}
          role={session?.role ?? null}
        />
      }
      header={
        <div className="topbar-inner">
          <div className="topbar-copy">
            <span className="eyebrow">UNIHUB GRILE</span>
            <h1>{pageMeta.title}</h1>
            <p>{pageMeta.subtitle}</p>
          </div>
          <div className="topbar-status" aria-label="Stare sistem">
            <span className={`status-dot status-${systemState}`} aria-hidden="true" />
            <span>{systemState === "online" ? "Online" : systemState === "offline" ? "API offline" : "Verificare"}</span>
            {health?.app_version && <small>v{health.app_version}</small>}
          </div>
        </div>
      }
    >
      <PageRouter
        api={api}
        route={route}
        months={months}
        monthsError={monthsError}
        session={session}
        sessionError={sessionError}
        capabilities={capabilities}
      />
    </Layout>
  );
}

interface PageRouterProps {
  api: ApiClient;
  route: Route;
  months: MonthSummary[];
  monthsError: string | null;
  session: SessionInfo | null;
  sessionError: string | null;
  capabilities: ReadonlySet<Capability>;
}

function PageRouter({
  api,
  route,
  months,
  monthsError,
  session,
  sessionError,
  capabilities,
}: PageRouterProps) {
  if (sessionError) {
    return (
      <section className="card" aria-label="Drepturi indisponibile">
        <h2>Nu pot încărca drepturile de acces</h2>
        <p className="error" role="alert">{sessionError}</p>
      </section>
    );
  }
  if (!session) {
    return <section className="card"><p className="muted">Verific drepturile de acces…</p></section>;
  }
  if (!canAccessRoute(capabilities, route)) {
    return (
      <section className="card" aria-label="Acces indisponibil">
        <h2>Acces indisponibil</h2>
        <p className="muted">Rolul {session.role} nu are capability-urile necesare pentru această secțiune.</p>
      </section>
    );
  }

  switch (route.name) {
    case "program":
      return <Program api={api} months={months} monthsError={monthsError} capabilities={capabilities} />;
    case "exceptions":
      return <Exceptions api={api} months={months} monthsError={monthsError} />;
    case "close":
      return <Close api={api} months={months} monthsError={monthsError} capabilities={capabilities} />;
    case "jobs":
      return <Jobs api={api} />;
    case "store":
      return <Magazin api={api} storeId={route.segments[0] ?? null} months={months} monthsError={monthsError} capabilities={capabilities} />;
    case "agent":
      return <Agent api={api} personId={route.segments[0] ?? null} months={months} monthsError={monthsError} />;
    case "overview":
    default:
      return <Overview api={api} months={months} monthsError={monthsError} />;
  }
}

function getPageMeta(route: Route): { title: string; subtitle: string } {
  switch (route.name) {
    case "program":
      return { title: "Program", subtitle: "Planificare și acoperire operațională." };
    case "exceptions":
      return { title: "Excepții", subtitle: "Diferențe și situații care necesită intervenție." };
    case "close":
      return { title: "Management", subtitle: "Închidere, validare și audit lunar." };
    case "jobs":
      return { title: "Joburi", subtitle: "Coadă, retry-uri și rezultate asincrone." };
    case "store":
      return { title: "Magazin", subtitle: "Program, agenți, pontaj și grilă." };
    case "agent":
      return { title: "Agent", subtitle: "Program și rezultate individuale." };
    default:
      return { title: "Grile & Program", subtitle: "Control centralizat pentru rețea." };
  }
}

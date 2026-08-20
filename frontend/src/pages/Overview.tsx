import { useEffect, useState } from "react";
import type { ApiClient } from "../api/client";

export interface OverviewProps {
  api: ApiClient;
}

interface CatalogTenant {
  id: string;
  name: string;
  timezone: string;
  is_active: boolean;
}

interface CatalogStore {
  id: string;
  tenant_id: string;
  internal_code: string;
  company_code: string;
  name: string;
}

export function Overview({ api }: OverviewProps) {
  const [stores, setStores] = useState<CatalogStore[]>([]);
  const [tenants, setTenants] = useState<CatalogTenant[]>([]);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    Promise.all([
      api.get<CatalogStore[]>("/catalog/stores").catch((e: unknown) => {
        // Non-admins get a 403; treat as empty so the page still renders.
        if (e instanceof Error && /403/.test(e.message)) return [];
        throw e;
      }),
      api.get<CatalogTenant[]>("/catalog/tenants").catch(() => []),
    ])
      .then(([s, t]) => {
        if (cancelled) return;
        setStores(s);
        setTenants(t);
      })
      .catch((e: unknown) => {
        if (cancelled) return;
        setError(String(e));
      });
    return () => {
      cancelled = true;
    };
  }, [api]);

  return (
    <section className="card" aria-label="Catalog overview">
      <h2>Catalog</h2>
      {error && (
        <p className="error" role="alert">
          {error}
        </p>
      )}
      <div className="grid">
        <article>
          <h3>Tenants</h3>
          <p className="muted">
            Loaded: <strong>{tenants.length}</strong>
          </p>
          <ul>
            {tenants.map((t) => (
              <li key={t.id}>
                {t.name} <span className="muted">({t.id})</span>
              </li>
            ))}
          </ul>
        </article>
        <article>
          <h3>Stores</h3>
          <p className="muted">
            Loaded: <strong>{stores.length}</strong>
          </p>
          <ul>
            {stores.map((s) => (
              <li key={s.id}>
                {s.name} <span className="muted">({s.internal_code})</span>
              </li>
            ))}
          </ul>
        </article>
      </div>
    </section>
  );
}
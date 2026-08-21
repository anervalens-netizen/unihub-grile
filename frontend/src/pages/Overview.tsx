import { useEffect, useMemo, useState } from "react";
import {
  type ApiClient,
  type MonthSummary,
  type OverviewReport,
  type StoreSummary,
} from "../api/client";
import { MonthSelector } from "../components/MonthSelector";
import { navigate } from "../router";

export interface OverviewProps {
  api: ApiClient;
  months: MonthSummary[];
  monthsError: string | null;
}

export function Overview({ api, months, monthsError }: OverviewProps) {
  const [monthId, setMonthId] = useState<string | null>(months[0]?.id ?? null);
  const [report, setReport] = useState<OverviewReport | null>(null);
  const [stores, setStores] = useState<StoreSummary[]>([]);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    setMonthId((current) => current ?? months[0]?.id ?? null);
  }, [months]);

  useEffect(() => {
    if (!monthId) {
      setReport(null);
      return;
    }
    let cancelled = false;
    setError(null);
    Promise.all([
      api.get<OverviewReport>(`/months/${monthId}/overview`),
      api.get<StoreSummary[]>("/catalog/stores"),
    ])
      .then(([overview, storeList]) => {
        if (cancelled) return;
        setReport(overview);
        setStores(storeList.filter((store) => store.is_active));
      })
      .catch((e: unknown) => {
        if (!cancelled) setError(e instanceof Error ? e.message : String(e));
      });
    return () => {
      cancelled = true;
    };
  }, [api, monthId]);

  const issuesByStore = useMemo(() => {
    const map = new Map<string, { count: number; severity: number }>();
    for (const item of report?.needs_attention ?? []) {
      if (!item.store_id) continue;
      const current = map.get(item.store_id) ?? { count: 0, severity: 0 };
      map.set(item.store_id, {
        count: current.count + 1,
        severity: Math.max(current.severity, item.severity),
      });
    }
    return map;
  }, [report]);

  return (
    <div className="command-page">
      <section className="command-hero">
        <div>
          <span className="eyebrow">NETWORK CONTROL</span>
          <h2>Rețeaua, într-o singură privire</h2>
          <p>Prioritizează excepțiile, intră direct într-un magazin și modifică programul fără să pierzi contextul lunii.</p>
        </div>
        <div className="command-hero-actions">
          <MonthSelector months={months} value={monthId} onChange={setMonthId} error={monthsError} />
          <button type="button" className="button-secondary" onClick={() => navigate("program")}>Deschide calendarul</button>
          <button type="button" className="button-primary" onClick={() => navigate("exceptions")}>Vezi excepțiile</button>
        </div>
      </section>

      {error && <p className="error" role="alert">{error}</p>}
      {!report && !error && <div className="loading-panel">Încarc situația operațională…</div>}

      {report && (
        <>
          <section className="kpi-strip" aria-label="Indicatori principali">
            <Metric label="Magazine acoperite" value={`${report.kpis.stores_covered}/${report.kpis.stores_total}`} detail="acoperire program" tone={report.kpis.stores_covered === report.kpis.stores_total ? "ok" : "warn"} />
            <Metric label="Zile neacoperite" value={String(report.kpis.days_uncovered)} detail="necesită programare" tone={report.kpis.days_uncovered === 0 ? "ok" : "err"} />
            <Metric label="Conflicte" value={String(report.kpis.conflicts)} detail="agent / zi" tone={report.kpis.conflicts === 0 ? "ok" : "err"} />
            <Metric label="Suplimentare" value={String(report.kpis.extra_home_days + report.kpis.extra_other_days)} detail={`${report.kpis.extra_home_days} aici · ${report.kpis.extra_other_days} extern`} tone="neutral" />
            <Metric label="Vânzări neatribuite" value={String(report.kpis.sales_unattributed)} detail="de verificat" tone={report.kpis.sales_unattributed === 0 ? "ok" : "warn"} />
          </section>

          <div className="command-grid">
            <section className="panel network-panel">
              <div className="panel-heading">
                <div>
                  <span className="eyebrow">MAGAZINE</span>
                  <h3>Control rețea</h3>
                </div>
                <span className="context-pill">{report.state} · rev {report.revision}</span>
              </div>
              <div className="store-grid">
                {stores.map((store) => {
                  const issue = issuesByStore.get(store.id);
                  const status = !issue ? "ok" : issue.severity >= 2 ? "err" : "warn";
                  return (
                    <button key={store.id} type="button" className="store-command-card" onClick={() => navigate("store", store.id)}>
                      <div className="store-card-topline">
                        <span className={`status-dot status-${status === "ok" ? "online" : status === "warn" ? "checking" : "offline"}`} />
                        <span>{status === "ok" ? "Operațional" : status === "warn" ? "Necesită atenție" : "Intervenție"}</span>
                        <span className="store-code">{store.internal_code}</span>
                      </div>
                      <strong>{store.name}</strong>
                      <small>{store.company_code || "Fără firmă"}</small>
                      <div className="store-card-footer">
                        <span>{issue ? `${issue.count} excepție${issue.count === 1 ? "" : "i"}` : "Fără excepții"}</span>
                        <span className="store-open">Control →</span>
                      </div>
                    </button>
                  );
                })}
              </div>
            </section>

            <section className="panel attention-panel">
              <div className="panel-heading">
                <div>
                  <span className="eyebrow">PRIORITĂȚI</span>
                  <h3>Necesită atenție</h3>
                </div>
                <span className="count-badge">{report.needs_attention.length}</span>
              </div>
              {report.needs_attention.length === 0 ? (
                <div className="empty-state"><strong>Totul este în regulă.</strong><span>Nicio excepție deschisă pentru luna selectată.</span></div>
              ) : (
                <div className="attention-list">
                  {report.needs_attention.slice(0, 8).map((item, index) => (
                    <button
                      type="button"
                      className="attention-item"
                      key={`${item.code}-${item.business_date ?? "none"}-${index}`}
                      onClick={() => item.store_id ? navigate("store", item.store_id) : navigate("exceptions")}
                    >
                      <span className={`severity-rail severity-${item.severity}`} />
                      <span className="attention-copy">
                        <strong>{item.title}</strong>
                        <small>{item.detail}</small>
                      </span>
                      <span className="attention-date">{item.business_date?.slice(8, 10) ?? "—"}</span>
                    </button>
                  ))}
                </div>
              )}
            </section>
          </div>

          <section className="panel managers-panel">
            <div className="panel-heading">
              <div>
                <span className="eyebrow">RESPONSABILITATE</span>
                <h3>Manageri</h3>
              </div>
            </div>
            <div className="manager-cards">
              {report.managers.filter((row) => row.stores_total > 0).map((row) => {
                const coverage = row.stores_total === 0 ? 0 : Math.round((row.stores_covered / row.stores_total) * 100);
                return (
                  <article className="manager-card" key={row.user_id}>
                    <div className="manager-avatar" aria-hidden="true">{initials(row.display_name)}</div>
                    <div className="manager-copy">
                      <strong>{row.display_name}</strong>
                      <span>{row.stores_covered}/{row.stores_total} magazine · {row.days_uncovered} zile neacoperite</span>
                    </div>
                    <div className="coverage-meter" aria-label={`Acoperire ${coverage}%`}><span style={{ width: `${coverage}%` }} /></div>
                    <strong className="coverage-value">{coverage}%</strong>
                  </article>
                );
              })}
            </div>
          </section>
        </>
      )}
    </div>
  );
}

interface MetricProps {
  label: string;
  value: string;
  detail: string;
  tone: "ok" | "warn" | "err" | "neutral";
}

function Metric({ label, value, detail, tone }: MetricProps) {
  return (
    <article className={`metric-card metric-${tone}`}>
      <span>{label}</span>
      <strong>{value}</strong>
      <small>{detail}</small>
    </article>
  );
}

function initials(name: string): string {
  return name.split(/\s+/).filter(Boolean).slice(0, 2).map((part) => part[0]?.toUpperCase() ?? "").join("");
}

import { useEffect, useMemo, useState } from "react";
import {
  type ApiClient,
  type MonthSummary,
  type OverviewReport,
  type ProgramGrid,
  type StoreSummary,
} from "../api/client";
import { isolatedRead } from "../api/isolatedRead";
import { MonthSelector } from "../components/MonthSelector";
import { LoadingState, RequestError, requestErrorMessage } from "../components/RequestState";
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
  const [storesError, setStoresError] = useState<string | null>(null);
  const [peopleTotal, setPeopleTotal] = useState<number | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [reloadToken, setReloadToken] = useState(0);

  useEffect(() => {
    setMonthId((current) => current ?? months[0]?.id ?? null);
  }, [months]);

  useEffect(() => {
    if (!monthId) {
      setReport(null);
      setStores([]);
      setStoresError(null);
      setPeopleTotal(null);
      setLoading(false);
      return;
    }
    let cancelled = false;
    setReport(null);
    setStores([]);
    setError(null);
    setStoresError(null);
    setPeopleTotal(null);
    setLoading(true);
    Promise.all([
      api.get<OverviewReport>(`/months/${monthId}/overview`),
      isolatedRead(api.get<StoreSummary[]>("/catalog/stores")),
      isolatedRead(api.get<ProgramGrid>(`/months/${monthId}/program?perspective=people`)),
    ])
      .then(([overview, storeRead, peopleRead]) => {
        if (cancelled) return;
        setReport(overview);
        setStores(storeRead.value?.filter((store) => store.is_active) ?? []);
        setStoresError(storeRead.error);
        setPeopleTotal(peopleRead.value?.rows.length ?? null);
      })
      .catch((e: unknown) => {
        if (!cancelled) setError(requestErrorMessage(e));
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [api, monthId, reloadToken]);

  const retry = () => setReloadToken((value) => value + 1);

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

  const operational = useMemo(() => {
    if (!report) return null;
    const daysInMonth = new Date(Date.UTC(report.year, report.month, 0)).getUTCDate();
    const totalStoreDays = report.kpis.stores_total * daysInMonth;
    const completedStoreDays = Math.max(0, totalStoreDays - report.kpis.days_uncovered);
    const calendarCompletion = totalStoreDays === 0
      ? 0
      : Math.round((completedStoreDays / totalStoreDays) * 100);
    const targetAnomalies = report.needs_attention.filter(
      (item) => item.code === "TARGET_ZERO_FOR_WORKED_STORE",
    ).length;
    return { calendarCompletion, targetAnomalies };
  }, [report]);

  return (
    <div className="command-page">
      <section className="command-hero">
        <div>
          <span className="eyebrow">GRILE / LUNA ÎN CURS</span>
          <h2>Overview — program și grile</h2>
          <p>Situația lunii într-o prezentare densă, apropiată de UniHub Retail, cu acces direct pe magazin.</p>
        </div>
        <div className="command-hero-actions">
          <MonthSelector months={months} value={monthId} onChange={setMonthId} error={monthsError} />
          <button type="button" className="button-secondary" onClick={() => navigate("program")}>Program</button>
          <button type="button" className="button-primary" onClick={() => navigate("exceptions")}>Excepții</button>
        </div>
      </section>

      {error && <RequestError message={error} onRetry={retry} />}
      {loading && <LoadingState>Încarc situația operațională…</LoadingState>}

      {!loading && !error && report && operational && (
        <>
          <section className="kpi-strip" aria-label="Indicatori principali">
            <Metric label="Magazine" value={`${report.kpis.stores_covered}/${report.kpis.stores_total}`} detail="cu program acoperit" tone={report.kpis.stores_covered === report.kpis.stores_total ? "ok" : "warn"} />
            <Metric label="Persoane" value={peopleTotal === null ? "—" : String(peopleTotal)} detail={peopleTotal === null ? "scope indisponibil" : "în scope-ul lunii"} tone="neutral" />
            <Metric label="Calendar" value={`${operational.calendarCompletion}%`} detail={`${report.kpis.days_uncovered} zile neacoperite`} tone={operational.calendarCompletion === 100 ? "ok" : "warn"} />
            <Metric label="Conflicte" value={String(report.kpis.conflicts)} detail="agent / zi" tone={report.kpis.conflicts === 0 ? "ok" : "err"} />
            <Metric label="Targeturi" value={String(operational.targetAnomalies)} detail="lipsă / zero pe zile lucrate" tone={operational.targetAnomalies === 0 ? "ok" : "warn"} />
            <Metric label="E-pay" value={report.kpis.epay_fresh && report.kpis.epay_invalid === 0 ? "OK" : "Atenție"} detail={`${report.kpis.epay_invalid} valori invalide`} tone={report.kpis.epay_fresh && report.kpis.epay_invalid === 0 ? "ok" : "err"} />
            <Metric label="Sync / export" value={report.kpis.sheet_sync_stale > 0 ? `${report.kpis.sheet_sync_stale} în lucru` : "La zi"} detail={`${report.kpis.sheet_sync_error} eșuate · ${report.kpis.sheet_sync_total} total`} tone={report.kpis.sheet_sync_error > 0 ? "err" : report.kpis.sheet_sync_stale > 0 ? "warn" : "ok"} />
            <Metric label="Zile neacoperite" value={String(report.kpis.days_uncovered)} detail="necesită completare" tone={report.kpis.days_uncovered === 0 ? "ok" : "err"} />
            <Metric label="Suplimentare" value={String(report.kpis.extra_home_days + report.kpis.extra_other_days)} detail={`${report.kpis.extra_home_days} aici · ${report.kpis.extra_other_days} extern`} tone="neutral" />
            <Metric label="Vânzări neatribuite" value={String(report.kpis.sales_unattributed)} detail="reconciliere" tone={report.kpis.sales_unattributed === 0 ? "ok" : "warn"} />
          </section>

          <div className="command-grid">
            <section className="panel network-panel">
              <div className="panel-heading">
                <div><span className="eyebrow">MAGAZINE / STRUCTURĂ</span><h3>Control rețea</h3></div>
                <span className="context-pill">{report.state} · rev {report.revision}</span>
              </div>

              {storesError && <RequestError message={`Structura magazinelor este indisponibilă: ${storesError}`} onRetry={retry} />}
              {!storesError && stores.length === 0 && (
                <div className="empty-state"><strong>Niciun magazin activ.</strong><span>Catalogul nu conține magazine active în scope-ul curent.</span></div>
              )}
              {!storesError && stores.length > 0 && (
                <div className="retail-overview-table">
                  <div className="retail-overview-row head">
                    <span>Magazin</span><span>Cod</span><span>Firmă</span><span>Status</span><span>Excepții</span><span></span>
                  </div>
                  {stores.map((store) => {
                    const issue = issuesByStore.get(store.id);
                    const status = !issue ? "ok" : issue.severity >= 2 ? "err" : "warn";
                    return (
                      <div className="retail-overview-row" key={store.id}>
                        <button type="button" className="retail-store-link" onClick={() => navigate("store", store.id)}>{store.name}</button>
                        <span>{store.internal_code}</span>
                        <span>{store.company_code || "—"}</span>
                        <span className="retail-status">
                          <span className={`status-dot status-${status === "ok" ? "online" : status === "warn" ? "checking" : "offline"}`} />
                          {status === "ok" ? "OK" : status === "warn" ? "Atenție" : "Intervenție"}
                        </span>
                        <span>{issue?.count ?? 0}</span>
                        <button type="button" className="retail-store-link retail-open" onClick={() => navigate("store", store.id)}>Deschide</button>
                      </div>
                    );
                  })}
                </div>
              )}
            </section>

            <section className="panel attention-panel">
              <div className="panel-heading">
                <div><span className="eyebrow">CONTROL CALITATE</span><h3>Necesită atenție</h3></div>
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
                      <span className="attention-copy"><strong>{item.title}</strong><small>{item.detail}</small></span>
                      <span className="attention-date">{item.business_date?.slice(8, 10) ?? "—"}</span>
                    </button>
                  ))}
                </div>
              )}
            </section>
          </div>

          <section className="panel managers-panel">
            <div className="panel-heading"><div><span className="eyebrow">MANAGERI REGIONALI</span><h3>Completare pe structură</h3></div></div>
            <div className="manager-cards">
              {report.managers.filter((row) => row.stores_total > 0).map((row) => {
                const coverage = Math.round((row.stores_covered / row.stores_total) * 100);
                return (
                  <article className="manager-card" key={row.user_id}>
                    <div className="manager-avatar" aria-hidden="true">{initials(row.display_name)}</div>
                    <div className="manager-copy"><strong>{row.display_name}</strong><span>{row.stores_total} magazine · {row.days_uncovered} zile neacoperite</span></div>
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

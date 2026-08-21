import { useEffect, useState } from "react";
import {
  type ApiClient,
  type MonthSummary,
  type OverviewReport,
} from "../api/client";
import { MonthSelector } from "../components/MonthSelector";

export interface OverviewProps {
  api: ApiClient;
  months: MonthSummary[];
  monthsError: string | null;
}

export function Overview({ api, months, monthsError }: OverviewProps) {
  const [monthId, setMonthId] = useState<string | null>(months[0]?.id ?? null);
  const [report, setReport] = useState<OverviewReport | null>(null);
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
    api
      .get<OverviewReport>(`/months/${monthId}/overview`)
      .then((response) => {
        if (!cancelled) setReport(response);
      })
      .catch((e: unknown) => {
        if (!cancelled) setError(String(e));
      });
    return () => {
      cancelled = true;
    };
  }, [api, monthId]);

  return (
    <section className="card" aria-label="Overview">
      <header className="card-header">
        <h2>Overview</h2>
        <MonthSelector
          months={months}
          value={monthId}
          onChange={setMonthId}
          error={monthsError}
        />
      </header>
      {error && (
        <p className="error" role="alert">
          {error}
        </p>
      )}
      {report && <OverviewBody report={report} />}
    </section>
  );
}

interface OverviewBodyProps {
  report: OverviewReport;
}

function OverviewBody({ report }: OverviewBodyProps) {
  const { kpis, managers, needs_attention } = report;
  const scopedManagers = managers.filter((row) => row.stores_total > 0);
  return (
    <div className="overview-grid">
      <KpiCard
        title="Magazine acoperite"
        value={`${kpis.stores_covered} / ${kpis.stores_total}`}
        tone={kpis.stores_covered === kpis.stores_total ? "ok" : "warn"}
      />
      <KpiCard
        title="Zile neacoperite"
        value={String(kpis.days_uncovered)}
        tone={kpis.days_uncovered === 0 ? "ok" : "err"}
      />
      <KpiCard
        title="Conflicte agent/zi"
        value={String(kpis.conflicts)}
        tone={kpis.conflicts === 0 ? "ok" : "err"}
      />
      <KpiCard
        title="Zile suplimentare"
        value={`${kpis.extra_home_days} acasă · ${kpis.extra_other_days} alt mag.`}
        tone="muted"
      />
      <KpiCard
        title="Vânzări neatribuite"
        value={String(kpis.sales_unattributed)}
        tone={kpis.sales_unattributed === 0 ? "ok" : "warn"}
      />
      <KpiCard
        title="E-pay"
        value={kpis.epay_fresh ? "Proaspăt" : `${kpis.epay_invalid} invalide`}
        tone={kpis.epay_fresh ? "ok" : "warn"}
      />
      <KpiCard
        title="Google / Export"
        value={`${kpis.sheet_sync_total} joburi · ${kpis.sheet_sync_stale} pending · ${kpis.sheet_sync_error} failed`}
        tone={kpis.sheet_sync_error > 0 ? "err" : kpis.sheet_sync_stale > 0 ? "warn" : "ok"}
      />
      <KpiCard
        title="Revizie"
        value={`${report.state} · rev ${report.revision}`}
        tone="muted"
      />

      <section className="overview-card overview-needs" aria-label="Necesită atenție">
        <h3>Necesită atenție</h3>
        {needs_attention.length === 0 ? (
          <p className="muted">Nicio excepție deschisă.</p>
        ) : (
          <ul className="needs-list">
            {needs_attention.slice(0, 10).map((item) => (
              <li key={`${item.code}-${item.business_date}-${item.store_id ?? ""}`}>
                <span className={`severity-chip severity-${item.severity}`}>
                  S{item.severity}
                </span>
                <strong>{item.title}</strong>
                <span className="muted">{item.detail}</span>
              </li>
            ))}
          </ul>
        )}
      </section>

      <section className="overview-card overview-managers" aria-label="Manageri">
        <h3>Manageri</h3>
        <table className="managers-table">
          <thead>
            <tr>
              <th scope="col">Manager</th>
              <th scope="col">Magazine acoperite</th>
              <th scope="col">Zile neacoperite</th>
            </tr>
          </thead>
          <tbody>
            {scopedManagers.map((row) => (
              <tr key={row.user_id}>
                <td>{row.display_name}</td>
                <td>
                  {row.stores_covered} / {row.stores_total}
                </td>
                <td>{row.days_uncovered}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </section>
    </div>
  );
}

interface KpiCardProps {
  title: string;
  value: string;
  tone: "ok" | "warn" | "err" | "muted";
}

function KpiCard({ title, value, tone }: KpiCardProps) {
  return (
    <article className={`kpi-card tone-${tone}`} aria-label={title}>
      <span className="kpi-title muted">{title}</span>
      <span className="kpi-value">{value}</span>
    </article>
  );
}

import { useEffect, useMemo, useState } from "react";
import {
  type ApiClient,
  type ExceptionEntry,
  type MonthSummary,
} from "../api/client";
import { MonthSelector } from "../components/MonthSelector";
import { navigate } from "../router";

export interface ExceptionsProps {
  api: ApiClient;
  months: MonthSummary[];
  monthsError: string | null;
}

type Filter = "all" | "blocking";

export function Exceptions({ api, months, monthsError }: ExceptionsProps) {
  const [monthId, setMonthId] = useState<string | null>(months[0]?.id ?? null);
  const [entries, setEntries] = useState<ExceptionEntry[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [filter, setFilter] = useState<Filter>("all");

  useEffect(() => {
    setMonthId((current) => current ?? months[0]?.id ?? null);
  }, [months]);

  useEffect(() => {
    if (!monthId) {
      setEntries([]);
      return;
    }
    let cancelled = false;
    setError(null);
    api
      .get<ExceptionEntry[]>(`/months/${monthId}/exceptions`)
      .then((response) => {
        if (!cancelled) setEntries(response);
      })
      .catch((e: unknown) => {
        if (!cancelled) setError(e instanceof Error ? e.message : String(e));
      });
    return () => {
      cancelled = true;
    };
  }, [api, monthId]);

  const visible = useMemo(() => {
    return filter === "all"
      ? entries
      : entries.filter((entry) => entry.blocking_close);
  }, [entries, filter]);

  const summary = useMemo(() => ({
    total: entries.length,
    blocking: entries.filter((entry) => entry.blocking_close).length,
    stores: new Set(entries.flatMap((entry) => entry.store_id ? [entry.store_id] : [])).size,
    people: new Set(entries.flatMap((entry) => entry.person_id ? [entry.person_id] : [])).size,
  }), [entries]);

  return (
    <section className="card" aria-label="Excepții">
      <header className="card-header">
        <div>
          <h2>Excepții</h2>
          <p className="muted">Situații detectate de server, cu resursa afectată și următorul pas recomandat.</p>
        </div>
        <div className="program-toolbar">
          <MonthSelector
            months={months}
            value={monthId}
            onChange={setMonthId}
            error={monthsError}
          />
          <fieldset className="perspective-switch">
            <legend className="muted">Filtru</legend>
            <label>
              <input
                type="radio"
                name="filter"
                checked={filter === "all"}
                onChange={() => setFilter("all")}
              />
              Toate
            </label>
            <label>
              <input
                type="radio"
                name="filter"
                checked={filter === "blocking"}
                onChange={() => setFilter("blocking")}
              />
              Doar blocante
            </label>
          </fieldset>
        </div>
      </header>

      <section className="kpi-strip" aria-label="Sumar excepții">
        <Metric label="Total" value={String(summary.total)} detail="în luna selectată" />
        <Metric label="Blocante" value={String(summary.blocking)} detail="opresc închiderea" />
        <Metric label="Magazine" value={String(summary.stores)} detail="cu excepții contextualizate" />
        <Metric label="Agenți" value={String(summary.people)} detail="cu excepții contextualizate" />
      </section>

      {error && (
        <p className="error" role="alert">
          {error}
        </p>
      )}
      {!error && entries.length === 0 ? (
        <div className="empty-state">
          <strong>Nicio excepție deschisă.</strong>
          <span>Serverul nu raportează situații care necesită intervenție pentru luna selectată.</span>
        </div>
      ) : visible.length === 0 ? (
        <div className="empty-state">
          <strong>Nicio excepție pentru filtrul curent.</strong>
          <span>Există excepții în lună, dar niciuna nu corespunde filtrului selectat.</span>
        </div>
      ) : (
        <ul className="exception-list">
          {visible.map((entry, index) => (
            <li key={`${entry.code}-${entry.business_date}-${index}`}>
              <div className="exception-head">
                <span
                  className={`severity-chip severity-${entry.severity}`}
                  aria-label={`Severitate ${entry.severity}`}
                >
                  S{entry.severity}
                </span>
                <strong>{entry.title}</strong>
                {entry.blocking_close && (
                  <span className="badge badge-BLOCANT">blocant close</span>
                )}
              </div>

              <p>{entry.detail}</p>
              <div className="exception-context" aria-label="Context excepție">
                <span className="context-pill">{entry.business_date ? formatDate(entry.business_date) : "fără dată"}</span>
                {entry.store_id && <span className="context-pill">Magazin: {entry.store_id}</span>}
                {entry.person_id && <span className="context-pill">Agent: {entry.person_id}</span>}
                <span className="context-pill">Cod: {entry.code}</span>
              </div>

              <div className="exception-resolution">
                <span className="eyebrow">REZOLVARE RECOMANDATĂ</span>
                <p>{entry.action_hint}</p>
                {(entry.store_id || entry.person_id) && (
                  <div className="editor-actions" aria-label="Drill-down excepție">
                    {entry.store_id && (
                      <button
                        type="button"
                        className="button-primary"
                        onClick={() => navigate("store", entry.store_id!)}
                      >
                        Deschide magazin
                      </button>
                    )}
                    {entry.person_id && (
                      <button
                        type="button"
                        className="button-secondary"
                        onClick={() => navigate("agent", entry.person_id!)}
                      >
                        Deschide agent
                      </button>
                    )}
                  </div>
                )}
              </div>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

function Metric({ label, value, detail }: { label: string; value: string; detail: string }) {
  return (
    <article className="metric-card metric-neutral">
      <span>{label}</span>
      <strong>{value}</strong>
      <small>{detail}</small>
    </article>
  );
}

function formatDate(value: string): string {
  const [year, month, day] = value.split("-");
  return year && month && day ? `${day}.${month}.${year}` : value;
}

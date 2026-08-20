import { useEffect, useMemo, useState } from "react";
import {
  type ApiClient,
  type ExceptionEntry,
  type MonthSummary,
} from "../api/client";
import { MonthSelector } from "../components/MonthSelector";

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
        if (!cancelled) setError(String(e));
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

  return (
    <section className="card" aria-label="Excepții">
      <header className="card-header">
        <h2>Excepții</h2>
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
      {error && (
        <p className="error" role="alert">
          {error}
        </p>
      )}
      {visible.length === 0 ? (
        <p className="muted">Nicio excepție pentru filtrul curent.</p>
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
              <p className="muted">{entry.detail}</p>
              <p className="muted">
                {entry.business_date ?? "—"} · {entry.store_id ?? "—"} ·{" "}
                {entry.person_id ?? "—"}
              </p>
              <p>
                <em>Acțiune:</em> {entry.action_hint}
              </p>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

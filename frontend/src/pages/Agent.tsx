import { useEffect, useState } from "react";
import {
  type ApiClient,
  type MonthSummary,
  type ProgramGrid,
} from "../api/client";
import { MonthSelector } from "../components/MonthSelector";
import { ProgramMatrix } from "../components/ProgramMatrix";

export interface AgentProps {
  api: ApiClient;
  personId: string | null;
  months: MonthSummary[];
  monthsError: string | null;
}

export function Agent({ api, personId, months, monthsError }: AgentProps) {
  const [monthId, setMonthId] = useState<string | null>(months[0]?.id ?? null);
  const [grid, setGrid] = useState<ProgramGrid | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    setMonthId((current) => current ?? months[0]?.id ?? null);
  }, [months]);

  useEffect(() => {
    if (!monthId || !personId) {
      setGrid(null);
      return;
    }
    let cancelled = false;
    setError(null);
    api
      .get<ProgramGrid>(`/months/${monthId}/program?perspective=people`)
      .then((response) => {
        if (cancelled) return;
        const filteredRows = response.rows.filter((row) => row.row_id === personId);
        setGrid({ ...response, rows: filteredRows });
      })
      .catch((e: unknown) => {
        if (!cancelled) setError(String(e));
      });
    return () => {
      cancelled = true;
    };
  }, [api, monthId, personId]);

  return (
    <section className="card" aria-label="Agent">
      <header className="card-header">
        <h2>Agent {personId ?? ""}</h2>
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
      {grid && grid.rows.length === 0 && (
        <p className="muted">Persoana nu are rânduri în calendarul acestei luni.</p>
      )}
      {grid && grid.rows.length > 0 && (
        <ProgramMatrix grid={grid} viewportHeight={360} />
      )}
    </section>
  );
}

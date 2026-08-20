import { useEffect, useState } from "react";
import {
  type ApiClient,
  type MonthSummary,
  type ProgramGrid,
  type PontajTotalsResponse,
} from "../api/client";
import { MonthSelector } from "../components/MonthSelector";
import { ProgramMatrix } from "../components/ProgramMatrix";

export interface MagazinProps {
  api: ApiClient;
  storeId: string | null;
  months: MonthSummary[];
  monthsError: string | null;
}

export function Magazin({ api, storeId, months, monthsError }: MagazinProps) {
  const [monthId, setMonthId] = useState<string | null>(months[0]?.id ?? null);
  const [grid, setGrid] = useState<ProgramGrid | null>(null);
  const [totals, setTotals] = useState<PontajTotalsResponse | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    setMonthId((current) => current ?? months[0]?.id ?? null);
  }, [months]);

  useEffect(() => {
    if (!monthId) {
      setGrid(null);
      setTotals(null);
      return;
    }
    let cancelled = false;
    setError(null);
    Promise.all([
      api.get<ProgramGrid>(`/months/${monthId}/program?perspective=people`),
      api.get<PontajTotalsResponse>(`/months/${monthId}/pontaj-totals`),
    ])
      .then(([gridResponse, totalsResponse]) => {
        if (cancelled) return;
        const filteredRows = storeId
          ? gridResponse.rows.filter((row) =>
              row.cells.some(
                (cell) =>
                  cell.home_store_id === storeId || cell.store_id === storeId,
              ),
            )
          : gridResponse.rows;
        setGrid({
          ...gridResponse,
          rows: filteredRows,
        });
        setTotals(totalsResponse);
      })
      .catch((e: unknown) => {
        if (!cancelled) setError(String(e));
      });
    return () => {
      cancelled = true;
    };
  }, [api, monthId, storeId]);

  return (
    <section className="card" aria-label="Magazin">
      <header className="card-header">
        <h2>Magazin {storeId ?? ""}</h2>
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
      {grid && totals && (
        <div className="magazin-grid">
          <section>
            <h3>Calendar magazin</h3>
            <ProgramMatrix grid={grid} viewportHeight={420} />
          </section>
          <section>
            <h3>Pontaj per persoană</h3>
            <table className="pontaj-table">
              <thead>
                <tr>
                  <th scope="col">Persoană</th>
                  <th scope="col">Zile lucrate</th>
                  <th scope="col">Concedii</th>
                  <th scope="col">Libere</th>
                  <th scope="col">Ore nete</th>
                </tr>
              </thead>
              <tbody>
                {Object.entries(totals.totals)
                  .filter(([personId]) =>
                    storeId
                      ? grid.rows.some((row) => row.row_id === personId)
                      : true,
                  )
                  .map(([personId, bucket]) => (
                    <tr key={personId}>
                      <td>{personId}</td>
                      <td>{bucket.working_days}</td>
                      <td>{bucket.leave_days}</td>
                      <td>{bucket.off_days}</td>
                      <td>{bucket.hours.toFixed(2)}</td>
                    </tr>
                  ))}
              </tbody>
            </table>
          </section>
        </div>
      )}
    </section>
  );
}

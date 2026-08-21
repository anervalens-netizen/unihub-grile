import { useEffect, useState } from "react";
import {
  type ApiClient,
  type AttributionResponse,
  type EpayFreshness,
  type GridCalculation,
  type MonthSummary,
  type ProgramGrid,
  type PersonSummary,
  type SheetProjection,
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
  const [person, setPerson] = useState<PersonSummary | null>(null);
  const [attribution, setAttribution] = useState<AttributionResponse | null>(null);
  const [gridRows, setGridRows] = useState<GridCalculation[]>([]);
  const [epay, setEpay] = useState<EpayFreshness | null>(null);
  const [projection, setProjection] = useState<SheetProjection | null>(null);
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
      .then(async (response) => {
        const row = response.rows.find((candidate) => candidate.row_id === personId);
        const storeId = row?.cells.find((cell) => cell.home_store_id)?.home_store_id;
        if (!storeId) {
          throw new Error("Magazinul de bază al agentului nu este disponibil în program.");
        }
        const [attributionResponse, gridResponse, freshnessResponse, projectionResponse] =
          await Promise.all([
            api.get<AttributionResponse>(`/months/${monthId}/attribution`),
            api.get<GridCalculation[]>(`/months/${monthId}/grid`),
            api.get<EpayFreshness>(
              `/months/${monthId}/epay/freshness?store_id=${encodeURIComponent(storeId)}`,
            ),
            api.get<SheetProjection>(
              `/months/${monthId}/sheet-projection?store_id=${encodeURIComponent(storeId)}`,
            ),
          ]);
        if (cancelled) return;
        setGrid({ ...response, rows: response.rows.filter((candidate) => candidate.row_id === personId) });
        setPerson({
          id: personId,
          tenant_id: "",
          internal_code: personId,
          external_code: null,
          display_name: row?.label ?? personId,
          home_store_id: storeId,
          is_active: true,
        });
        setAttribution({
          ...attributionResponse,
          rows: attributionResponse.rows.filter((item) => item.person_id === personId),
        });
        setGridRows(gridResponse.filter((item) => item.person_id === personId));
        setEpay(freshnessResponse);
        setProjection(projectionResponse);
      })
      .catch((e: unknown) => {
        if (!cancelled) setError(e instanceof Error ? e.message : String(e));
      });
    return () => {
      cancelled = true;
    };
  }, [api, monthId, personId]);

  const salesTotal = attribution?.rows.reduce((sum, row) => sum + Number(row.amount), 0);
  const projectionState = projection?.last_error
    ? `Eroare proiecție: ${projection.last_error}`
    : projection?.last_success_generation ?? "fără proiecție";

  return (
    <section className="card" aria-label="Agent">
      <header className="card-header">
        <h2>Agent {personId ?? ""}</h2>
        <MonthSelector months={months} value={monthId} onChange={setMonthId} error={monthsError} />
      </header>
      {error && <p className="error" role="alert">{error}</p>}
      {grid && grid.rows.length === 0 && (
        <p className="muted">Persoana nu are rânduri în calendarul acestei luni.</p>
      )}
      <dl className="agent-contract">
        <dt>Magazin de bază</dt><dd>{person?.home_store_id ?? "—"}</dd>
        <dt>Credit vânzări lunar</dt><dd>{salesTotal === undefined ? "—" : `${salesTotal.toFixed(2)} RON`}</dd>
        <dt>Componente grilă</dt><dd>{gridRows.length}</dd>
        <dt>E-pay</dt><dd>{epay ? `${epay.is_fresh ? "Proaspăt" : "Stale"} (${epay.fresh_count}/${epay.expected_count})` : "—"}</dd>
        <dt>Proiecție Sheet</dt><dd>{projectionState}</dd>
      </dl>
      {grid && grid.rows.length > 0 && <ProgramMatrix grid={grid} viewportHeight={360} />}
    </section>
  );
}

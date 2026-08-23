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
import { isolatedRead } from "../api/isolatedRead";
import { MonthSelector } from "../components/MonthSelector";
import { ProgramMatrix } from "../components/ProgramMatrix";
import { LoadingState, RequestError, requestErrorMessage } from "../components/RequestState";

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
  const [subsystemErrors, setSubsystemErrors] = useState<string[]>([]);
  const [loading, setLoading] = useState(false);
  const [reloadToken, setReloadToken] = useState(0);

  useEffect(() => {
    setMonthId((current) => current ?? months[0]?.id ?? null);
  }, [months]);

  useEffect(() => {
    if (!monthId || !personId) {
      setGrid(null);
      setPerson(null);
      setAttribution(null);
      setGridRows([]);
      setEpay(null);
      setProjection(null);
      setSubsystemErrors([]);
      setLoading(false);
      return;
    }
    let cancelled = false;
    setGrid(null);
    setPerson(null);
    setAttribution(null);
    setGridRows([]);
    setEpay(null);
    setProjection(null);
    setError(null);
    setSubsystemErrors([]);
    setLoading(true);
    api
      .get<ProgramGrid>(`/months/${monthId}/program?perspective=people`)
      .then(async (response) => {
        const row = response.rows.find((candidate) => candidate.row_id === personId);
        const storeId = row?.cells.find((cell) => cell.home_store_id)?.home_store_id;
        if (!storeId) {
          throw new Error("Magazinul de bază al agentului nu este disponibil în program.");
        }
        const [attributionRead, gridRead, freshnessRead, projectionRead] = await Promise.all([
          isolatedRead(api.get<AttributionResponse>(`/months/${monthId}/attribution`)),
          isolatedRead(api.get<GridCalculation[]>(`/months/${monthId}/grid`)),
          isolatedRead(api.get<EpayFreshness>(
            `/months/${monthId}/epay/freshness?store_id=${encodeURIComponent(storeId)}`,
          )),
          isolatedRead(api.get<SheetProjection>(
            `/months/${monthId}/sheet-projection?store_id=${encodeURIComponent(storeId)}`,
          )),
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
        setAttribution(attributionRead.value ? {
          ...attributionRead.value,
          rows: attributionRead.value.rows.filter((item) => item.person_id === personId),
        } : null);
        setGridRows(gridRead.value?.filter((item) => item.person_id === personId) ?? []);
        setEpay(freshnessRead.value);
        setProjection(projectionRead.value);
        setSubsystemErrors([
          attributionRead.error ? `Vânzări/atribuire: ${attributionRead.error}` : null,
          gridRead.error ? `Grilă: ${gridRead.error}` : null,
          freshnessRead.error ? `E-pay: ${freshnessRead.error}` : null,
          projectionRead.error ? `Sheet: ${projectionRead.error}` : null,
        ].filter((message): message is string => message !== null));
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
  }, [api, monthId, personId, reloadToken]);

  const retry = () => setReloadToken((value) => value + 1);
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
      {error && <RequestError message={error} onRetry={retry} />}
      {loading && <LoadingState>Încarc detaliile agentului…</LoadingState>}
      {!loading && !error && subsystemErrors.length > 0 && (
        <RequestError message={`Date parțial indisponibile — ${subsystemErrors.join(" · ")}`} onRetry={retry} />
      )}
      {!loading && !error && grid && grid.rows.length === 0 && (
        <div className="empty-state"><strong>Fără program în această lună.</strong><span>Persoana nu are rânduri în calendarul lunii selectate.</span></div>
      )}
      {!loading && !error && grid && (
        <>
          <dl className="agent-contract">
            <dt>Magazin de bază</dt><dd>{person?.home_store_id ?? "—"}</dd>
            <dt>Credit vânzări lunar</dt><dd>{salesTotal === undefined ? "—" : `${salesTotal.toFixed(2)} RON`}</dd>
            <dt>Componente grilă</dt><dd>{gridRows.length}</dd>
            <dt>E-pay</dt><dd>{epay ? `${epay.is_fresh ? "Proaspăt" : "Stale"} (${epay.fresh_count}/${epay.expected_count})` : "—"}</dd>
            <dt>Proiecție Sheet</dt><dd>{projectionState}</dd>
          </dl>
          {grid.rows.length > 0 && <ProgramMatrix grid={grid} viewportHeight={360} />}
        </>
      )}
    </section>
  );
}

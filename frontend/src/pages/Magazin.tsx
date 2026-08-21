import { useEffect, useState } from "react";
import {
  type ApiClient,
  type AttributionResponse,
  type EpayFreshness,
  type GridCalculation,
  type MonthSummary,
  type PersonSummary,
  type PontajTotalsResponse,
  type ProgramGrid,
  type SheetProjection,
  type StoreSummary,
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
  const [store, setStore] = useState<StoreSummary | null>(null);
  const [people, setPeople] = useState<PersonSummary[]>([]);
  const [attribution, setAttribution] = useState<AttributionResponse | null>(null);
  const [gridRows, setGridRows] = useState<GridCalculation[]>([]);
  const [freshness, setFreshness] = useState<EpayFreshness | null>(null);
  const [projection, setProjection] = useState<SheetProjection | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [actionMessage, setActionMessage] = useState<string | null>(null);

  useEffect(() => {
    setMonthId((current) => current ?? months[0]?.id ?? null);
  }, [months]);

  useEffect(() => {
    if (!monthId || !storeId) {
      setGrid(null);
      return;
    }
    let cancelled = false;
    setError(null);
    const query = `?store_id=${encodeURIComponent(storeId)}`;
    Promise.all([
      api.get<ProgramGrid>(`/months/${monthId}/program?perspective=people`),
      api.get<PontajTotalsResponse>(`/months/${monthId}/pontaj-totals`),
      api.get<StoreSummary[]>(`/catalog/stores`),
      api.get<PersonSummary[]>(`/catalog/people?store_id=${encodeURIComponent(storeId)}`),
      api.get<AttributionResponse>(`/months/${monthId}/attribution`),
      api.get<GridCalculation[]>(`/months/${monthId}/grid`),
      api.get<EpayFreshness>(`/months/${monthId}/epay/freshness${query}`),
      api.get<SheetProjection>(`/months/${monthId}/sheet-projection${query}`),
    ]).then(([gridResponse, totalsResponse, storesResponse, peopleResponse, attributionResponse, gridResponseRows, freshnessResponse, projectionResponse]) => {
      if (cancelled) return;
      setStore(storesResponse.find((item) => item.id === storeId) ?? storesResponse[0] ?? null);
      setPeople(peopleResponse);
      setGrid({
        ...gridResponse,
        rows: gridResponse.rows.filter((row) => row.cells.some((cell) => cell.store_id === storeId || cell.home_store_id === storeId)),
      });
      setTotals(totalsResponse);
      setAttribution({ ...attributionResponse, rows: attributionResponse.rows.filter((row) => row.store_id === storeId) });
      setGridRows(gridResponseRows.filter((row) => row.store_id === storeId));
      setFreshness(freshnessResponse);
      setProjection(projectionResponse);
    }).catch((e: unknown) => {
      if (!cancelled) setError(e instanceof Error ? e.message : String(e));
    });
    return () => { cancelled = true; };
  }, [api, monthId, storeId]);

  function enqueue(path: string, body: Record<string, string>) {
    if (!monthId) return;
    setActionMessage(null);
    api.post<Record<string, unknown>>(`/months/${monthId}${path}`, body)
      .then((result) => setActionMessage(`Job ${String(result.job_id ?? result.status ?? "trimis")} în coadă.`))
      .catch((e: unknown) => setActionMessage(e instanceof Error ? e.message : String(e)));
  }

  const salesByPerson = new Map<string, number>();
  attribution?.rows.forEach((row) => salesByPerson.set(row.person_id, (salesByPerson.get(row.person_id) ?? 0) + Number(row.amount)));

  return (
    <section className="card" aria-label="Magazin">
      <header className="card-header">
        <div>
          <h2 aria-label={`Magazin ${store?.name ?? storeId ?? ""}`}>Magazin <span>{store?.name ?? storeId ?? ""}</span></h2>
          <p className="muted">{store?.internal_code} · firmă {store?.company_code ?? "—"}</p>
        </div>
        <MonthSelector months={months} value={monthId} onChange={setMonthId} error={monthsError} />
      </header>
      {error && <p className="error" role="alert">{error}</p>}
      {actionMessage && <p className="muted" role="status">{actionMessage}</p>}
      {grid && <section><h3>Calendar și atribuiri</h3><ProgramMatrix grid={grid} viewportHeight={420} /></section>}
      <div className="magazin-grid">
        <section>
          <h3>Atribuire vânzări</h3>
          {people.map((person) => <div key={person.id}>{person.display_name} — {salesByPerson.get(person.id)?.toFixed(2) ?? "0.00"} RON</div>)}
          <p>Total magazin: {attribution ? `${Number(attribution.company_total).toFixed(2)} RON` : "—"}</p>
        </section>
        <section>
          <h3>E-pay</h3>
          <p>{freshness?.is_fresh ? "Citire proaspătă" : "Citire stale/indisponibilă"} · {freshness?.fresh_count ?? 0}/{freshness?.expected_count ?? 0} valori proaspete</p>
        </section>
        <section>
          <h3>Proiecție Sheet</h3>
          <p>Generație: {projection?.last_success_generation ?? "fără proiecție bună"}</p>
          {projection?.last_error && <p className="error-text">{projection.last_error}</p>}
        </section>
        <section>
          <h3>Acțiuni</h3>
          <button type="button" onClick={() => enqueue("/sheet-projection/enqueue", { store_id: storeId ?? "" })}>Sync Sheet</button>{" "}
          <button type="button" onClick={() => enqueue("/export/store", { store_id: storeId ?? "" })}>Export XLSX</button>
        </section>
        <section>
          <h3>Pontaj</h3>
          {totals && Object.entries(totals.totals).filter(([personId]) => people.some((person) => person.id === personId)).map(([personId, bucket]) => (
            <div key={personId}>{personId}: {bucket.working_days} zile · {bucket.hours.toFixed(2)} ore</div>
          ))}
        </section>
        <p className="muted">Grila: {gridRows.length} calcul(e) server-side.</p>
      </div>
    </section>
  );
}

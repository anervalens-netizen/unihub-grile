import { useEffect, useMemo, useState } from "react";
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
import { navigate } from "../router";

export interface MagazinProps {
  api: ApiClient;
  storeId: string | null;
  months: MonthSummary[];
  monthsError: string | null;
}

type StoreTab = "control" | "calendar" | "payroll";

function isApiError(error: unknown): error is { status: number } {
  return typeof error === "object" && error !== null && "status" in error && typeof (error as { status?: unknown }).status === "number";
}

export function Magazin({ api, storeId, months, monthsError }: MagazinProps) {
  const [monthId, setMonthId] = useState<string | null>(months[0]?.id ?? null);
  const [grid, setGrid] = useState<ProgramGrid | null>(null);
  const [totals, setTotals] = useState<PontajTotalsResponse | null>(null);
  const [store, setStore] = useState<StoreSummary | null>(null);
  const [allStores, setAllStores] = useState<StoreSummary[]>([]);
  const [people, setPeople] = useState<PersonSummary[]>([]);
  const [attribution, setAttribution] = useState<AttributionResponse | null>(null);
  const [gridRows, setGridRows] = useState<GridCalculation[]>([]);
  const [freshness, setFreshness] = useState<EpayFreshness | null>(null);
  const [projection, setProjection] = useState<SheetProjection | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [actionMessage, setActionMessage] = useState<string | null>(null);
  const [activeTab, setActiveTab] = useState<StoreTab>("control");
  const [editing, setEditing] = useState<{ rowId: string; businessDate: string } | null>(null);
  const [editValue, setEditValue] = useState({ personId: "", storeId: "", status: "WORKING", workingKind: "NORMAL" });
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);

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
      api.get<StoreSummary[]>("/catalog/stores"),
      api.get<PersonSummary[]>(`/catalog/people?store_id=${encodeURIComponent(storeId)}`),
      api.get<AttributionResponse>(`/months/${monthId}/attribution`),
      api.get<GridCalculation[]>(`/months/${monthId}/grid`),
      api.get<EpayFreshness>(`/months/${monthId}/epay/freshness${query}`),
      api.get<SheetProjection>(`/months/${monthId}/sheet-projection${query}`),
    ])
      .then(([gridResponse, totalsResponse, storesResponse, peopleResponse, attributionResponse, gridResponseRows, freshnessResponse, projectionResponse]) => {
        if (cancelled) return;
        const filteredGrid = filterGridForStore(gridResponse, storeId);
        setAllStores(storesResponse.filter((item) => item.is_active));
        setStore(storesResponse.find((item) => item.id === storeId) ?? null);
        setPeople(peopleResponse.filter((item) => item.is_active));
        setGrid(filteredGrid);
        setTotals(totalsResponse);
        setAttribution({ ...attributionResponse, rows: attributionResponse.rows.filter((row) => row.store_id === storeId) });
        setGridRows(gridResponseRows.filter((row) => row.store_id === storeId && row.revision === gridResponse.revision));
        setFreshness(freshnessResponse);
        setProjection(projectionResponse);
      })
      .catch((e: unknown) => {
        if (!cancelled) setError(e instanceof Error ? e.message : String(e));
      });
    return () => {
      cancelled = true;
    };
  }, [api, monthId, storeId]);

  const salesByPerson = useMemo(() => {
    const map = new Map<string, number>();
    attribution?.rows.forEach((row) => map.set(row.person_id, (map.get(row.person_id) ?? 0) + Number(row.amount)));
    return map;
  }, [attribution]);

  const storeSalesTotal = Array.from(salesByPerson.values()).reduce((total, amount) => total + amount, 0);
  const peopleIds = new Set(people.map((person) => person.id));
  const storeHours = totals ? Object.entries(totals.totals).filter(([personId]) => peopleIds.has(personId)).reduce((sum, [, bucket]) => sum + bucket.hours, 0) : 0;
  const storeWorkingDays = totals ? Object.entries(totals.totals).filter(([personId]) => peopleIds.has(personId)).reduce((sum, [, bucket]) => sum + bucket.working_days, 0) : 0;
  const matrixPeople = useMemo(() => buildMatrixPeople(grid, people), [grid, people]);

  function enqueue(path: string, body: Record<string, string>) {
    if (!monthId) return;
    setActionMessage(null);
    api.post<Record<string, unknown>>(`/months/${monthId}${path}`, body)
      .then((result) => setActionMessage(`Job ${String(result.job_id ?? result.status ?? "trimis")} a fost pus în coadă.`))
      .catch((e: unknown) => setActionMessage(e instanceof Error ? e.message : String(e)));
  }

  function beginEdit(rowId: string, cell: ProgramGrid["rows"][number]["cells"][number]) {
    if (!storeId) return;
    setSaveError(null);
    setEditing({ rowId, businessDate: cell.business_date });
    setEditValue({
      personId: cell.person_id ?? matrixPeople[0]?.id ?? "",
      storeId: cell.store_id ?? storeId,
      status: cell.status || "WORKING",
      workingKind: cell.working_kind || "NORMAL",
    });
  }

  function saveCell() {
    if (!monthId || !storeId || !grid || !editing || !editValue.personId) return;
    setSaving(true);
    setSaveError(null);
    api.post(`/months/${monthId}/program/cell?expected_revision=${grid.revision}`, {
      person_id: editValue.personId,
      business_date: editing.businessDate,
      status: editValue.status,
      store_id: editValue.storeId || null,
      working_kind: editValue.workingKind,
    })
      .then(() => api.get<ProgramGrid>(`/months/${monthId}/program?perspective=people`))
      .then((response) => {
        setGrid(filterGridForStore(response, storeId));
        setEditing(null);
        setActionMessage("Programul magazinului a fost actualizat.");
      })
      .catch((e: unknown) => {
        if (isApiError(e) && e.status === 409) {
          setSaveError("Programul s-a modificat între timp. Reîncarcă luna înainte de o nouă salvare.");
        } else {
          setSaveError(e instanceof Error ? e.message : String(e));
        }
      })
      .finally(() => setSaving(false));
  }

  if (!storeId) {
    return <section className="panel"><p className="error">Magazinul nu a fost selectat.</p></section>;
  }

  return (
    <div className="store-page">
      <section className="store-hero">
        <div className="store-hero-main">
          <button type="button" className="back-button" onClick={() => navigate("overview")} aria-label="Înapoi la Command Center">←</button>
          <div>
            <span className="eyebrow">STORE COMMAND</span>
            <h2>{store?.name ?? "Magazin"}</h2>
            <p>{store?.internal_code ?? storeId} · {store?.company_code ?? "fără firmă"}</p>
          </div>
        </div>
        <div className="store-hero-actions">
          <MonthSelector months={months} value={monthId} onChange={setMonthId} error={monthsError} />
          <span className={`operational-chip ${freshness?.is_fresh ? "ok" : "warn"}`}>{freshness?.is_fresh ? "Date actualizate" : "Verifică E-pay"}</span>
        </div>
      </section>

      {error && <p className="error" role="alert">{error}</p>}
      {saveError && <p className="error" role="alert">{saveError}</p>}
      {actionMessage && <p className="action-message" role="status">{actionMessage}</p>}

      <section className="kpi-strip store-kpis">
        <Metric label="Agenți" value={String(people.length)} detail="alocați magazinului" />
        <Metric label="Vânzări atribuite" value={`${formatMoney(storeSalesTotal)} RON`} detail={`${attribution?.rows.length ?? 0} înregistrări`} />
        <Metric label="Pontaj" value={`${storeHours.toFixed(1)} h`} detail={`${storeWorkingDays} zile lucrate`} />
        <Metric label="Grile calculate" value={String(gridRows.length)} detail={`revizia ${grid?.revision ?? "—"}`} />
        <Metric label="E-pay" value={`${freshness?.fresh_count ?? 0}/${freshness?.expected_count ?? 0}`} detail={freshness?.is_fresh ? "proaspăt" : "necesită sync"} />
      </section>

      <div className="segmented-tabs" role="tablist" aria-label="Secțiuni magazin">
        <TabButton active={activeTab === "control"} onClick={() => setActiveTab("control")}>Control</TabButton>
        <TabButton active={activeTab === "calendar"} onClick={() => setActiveTab("calendar")}>Calendar</TabButton>
        <TabButton active={activeTab === "payroll"} onClick={() => setActiveTab("payroll")}>Grilă & Pontaj</TabButton>
      </div>

      {activeTab === "control" && (
        <div className="store-control-grid">
          <section className="panel store-team-panel">
            <div className="panel-heading">
              <div><span className="eyebrow">ECHIPĂ</span><h3>Agenți și vânzări</h3></div>
              <span className="context-pill">{formatMoney(storeSalesTotal)} RON</span>
            </div>
            <div className="agent-performance-list">
              {people.map((person) => {
                const sales = salesByPerson.get(person.id) ?? 0;
                const bucket = totals?.totals[person.id];
                return (
                  <button type="button" className="agent-performance-row" key={person.id} onClick={() => navigate("agent", person.id)}>
                    <span className="manager-avatar">{initials(person.display_name)}</span>
                    <span className="agent-row-copy"><strong>{person.display_name}</strong><small>{bucket?.working_days ?? 0} zile · {bucket?.hours.toFixed(1) ?? "0.0"} h</small></span>
                    <span className="agent-sales"><strong>{formatMoney(sales)}</strong><small>RON</small></span>
                    <span className="store-open">→</span>
                  </button>
                );
              })}
              {people.length === 0 && <div className="empty-state"><strong>Niciun agent activ.</strong><span>Adaugă sau realocă agenți din sursa de catalog.</span></div>}
            </div>
          </section>

          <section className="panel store-actions-panel">
            <div className="panel-heading"><div><span className="eyebrow">ACȚIUNI</span><h3>Operațiuni magazin</h3></div></div>
            <button type="button" className="operation-card" onClick={() => setActiveTab("calendar")}>
              <span className="operation-icon">▦</span><span><strong>Editează calendarul</strong><small>Program, suplimentare, mutări</small></span><span>→</span>
            </button>
            <button type="button" className="operation-card" onClick={() => enqueue("/sheet-projection/enqueue", { store_id: storeId })}>
              <span className="operation-icon">↻</span><span><strong>Sincronizează Sheet</strong><small>{projection?.last_success_generation ? `Ultima generație ${projection.last_success_generation}` : "Fără sincronizare reușită"}</small></span><span>→</span>
            </button>
            <button type="button" className="operation-card" onClick={() => enqueue("/export/store", { store_id: storeId })}>
              <span className="operation-icon">⇩</span><span><strong>Exportă XLSX</strong><small>Grilă și pontaj magazin</small></span><span>→</span>
            </button>
          </section>

          <section className="panel sync-panel">
            <div className="panel-heading"><div><span className="eyebrow">INTEGRITATE DATE</span><h3>Sync & stare</h3></div></div>
            <div className="sync-status-row"><span>E-pay</span><strong className={freshness?.is_fresh ? "text-ok" : "text-warn"}>{freshness?.is_fresh ? "Proaspăt" : "Stale / indisponibil"}</strong></div>
            <div className="sync-status-row"><span>Sheet projection</span><strong>{projection?.last_success_generation ?? "—"}</strong></div>
            <div className="sync-status-row"><span>Erori sync</span><strong className={projection?.last_error ? "text-err" : "text-ok"}>{projection?.last_error ? "Există" : "0"}</strong></div>
            {projection?.last_error && <p className="error-text compact-error">{projection.last_error}</p>}
          </section>
        </div>
      )}

      {activeTab === "calendar" && (
        <section className="panel calendar-panel">
          <div className="panel-heading">
            <div><span className="eyebrow">PROGRAM LUNAR</span><h3>Calendar magazin</h3></div>
            <span className="context-pill">click pe o zi pentru editare</span>
          </div>
          {grid ? (
            <ProgramMatrix
              grid={grid}
              viewportHeight={520}
              onCellClick={beginEdit}
              editing={editing}
              editValue={editValue}
              people={matrixPeople}
              stores={allStores.map((item) => ({ id: item.id, label: item.name || item.internal_code }))}
              onEditChange={setEditValue}
              onSave={saveCell}
              onCancelEdit={() => setEditing(null)}
              saving={saving}
            />
          ) : <div className="loading-panel">Încarc calendarul…</div>}
        </section>
      )}

      {activeTab === "payroll" && (
        <div className="payroll-grid">
          <section className="panel">
            <div className="panel-heading"><div><span className="eyebrow">PONTAJ</span><h3>Ore și zile</h3></div></div>
            <div className="data-table-wrap">
              <table className="data-table">
                <thead><tr><th>Agent</th><th>Zile</th><th>Concediu</th><th>Ore</th></tr></thead>
                <tbody>
                  {people.map((person) => {
                    const bucket = totals?.totals[person.id];
                    return <tr key={person.id}><td><strong>{person.display_name}</strong></td><td>{bucket?.working_days ?? 0}</td><td>{bucket?.leave_days ?? 0}</td><td>{bucket?.hours.toFixed(2) ?? "0.00"}</td></tr>;
                  })}
                </tbody>
              </table>
            </div>
          </section>

          <section className="panel">
            <div className="panel-heading"><div><span className="eyebrow">GRILĂ</span><h3>Calcul server-side</h3></div><span className="count-badge">{gridRows.length}</span></div>
            <div className="grid-calculation-list">
              {gridRows.map((row) => (
                <article className="grid-calculation-card" key={row.id}>
                  <div><span>Agent</span><strong>{people.find((person) => person.id === row.person_id)?.display_name ?? row.person_id}</strong></div>
                  <div><span>Rule pack</span><strong>{row.rule_pack_version}</strong></div>
                  <div><span>Revizie</span><strong>{row.revision}</strong></div>
                  <small>Output {row.outputs_hash.slice(0, 10)}…</small>
                </article>
              ))}
              {gridRows.length === 0 && <div className="empty-state"><strong>Fără calcul disponibil.</strong><span>Grila server-side nu are încă rezultate pentru revizia curentă.</span></div>}
            </div>
          </section>
        </div>
      )}
    </div>
  );
}

function filterGridForStore(grid: ProgramGrid, storeId: string): ProgramGrid {
  return {
    ...grid,
    rows: grid.rows.filter((row) => row.cells.some((cell) => cell.store_id === storeId || cell.home_store_id === storeId)),
  };
}

function buildMatrixPeople(grid: ProgramGrid | null, catalogPeople: PersonSummary[]): Array<{ id: string; label: string; homeStoreId: string }> {
  const map = new Map<string, { id: string; label: string; homeStoreId: string }>();
  for (const person of catalogPeople) map.set(person.id, { id: person.id, label: person.display_name, homeStoreId: person.home_store_id });
  for (const row of grid?.rows ?? []) {
    for (const cell of row.cells) {
      if (cell.person_id && cell.display_name) map.set(cell.person_id, { id: cell.person_id, label: cell.display_name, homeStoreId: cell.home_store_id ?? "" });
    }
  }
  return Array.from(map.values());
}

function formatMoney(value: number): string {
  return new Intl.NumberFormat("ro-RO", { maximumFractionDigits: 0 }).format(value);
}

function initials(name: string): string {
  return name.split(/\s+/).filter(Boolean).slice(0, 2).map((part) => part[0]?.toUpperCase() ?? "").join("");
}

function Metric({ label, value, detail }: { label: string; value: string; detail: string }) {
  return <article className="metric-card metric-neutral"><span>{label}</span><strong>{value}</strong><small>{detail}</small></article>;
}

function TabButton({ active, onClick, children }: { active: boolean; onClick: () => void; children: string }) {
  return <button type="button" role="tab" aria-selected={active} className={active ? "active" : ""} onClick={onClick}>{children}</button>;
}

import { useEffect, useMemo, useRef, useState } from "react";
import {
  type ApiClient,
  type AttributionResponse,
  type EpayFreshness,
  type GridCalculation,
  type MonthSummary,
  type PersonSummary,
  type PontajTotalsResponse,
  type ProgramCell,
  type ProgramChoices,
  type ProgramGrid,
  type SheetProjection,
  type StoreSummary,
} from "../api/client";
import { isolatedRead } from "../api/isolatedRead";
import { hasCapability, type Capability } from "../capabilities";
import { MonthSelector } from "../components/MonthSelector";
import { ProgramMatrix } from "../components/ProgramMatrix";
import { navigate } from "../router";

export interface MagazinProps {
  api: ApiClient;
  storeId: string | null;
  months: MonthSummary[];
  monthsError: string | null;
  capabilities: ReadonlySet<Capability>;
}

type StoreTab = "control" | "calendar" | "payroll";
type EditValue = { personId: string; storeId: string; status: string; workingKind: string };
type Editing = { rowId: string; businessDate: string };
type QueueState = "QUEUED" | "RETRY" | "RUNNING" | "FAILED" | "DONE";

interface JobDiagnostic {
  id: number;
  kind: string;
  state: QueueState;
  attempts: number;
  max_attempts: number;
  last_error: string | null;
  month_id: string | null;
  store_ids: string[];
}

interface JobDiagnostics {
  jobs: JobDiagnostic[];
}

interface GridDetail {
  components: Record<string, unknown>;
  anomalies: Array<Record<string, unknown>>;
  inputs: Record<string, unknown>;
}

function isApiError(error: unknown): error is { status: number; code?: string } {
  return typeof error === "object" && error !== null && "status" in error && typeof (error as { status?: unknown }).status === "number";
}

export function Magazin({ api, storeId, months, monthsError, capabilities }: MagazinProps) {
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
  const [jobDiagnostics, setJobDiagnostics] = useState<JobDiagnostics | null>(null);
  const [jobsError, setJobsError] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [actionMessage, setActionMessage] = useState<string | null>(null);
  const [activeTab, setActiveTab] = useState<StoreTab>("control");
  const [editing, setEditing] = useState<Editing | null>(null);
  const [editValue, setEditValue] = useState<EditValue>({ personId: "", storeId: "", status: "WORKING", workingKind: "NORMAL" });
  const [editPeople, setEditPeople] = useState<Array<{ id: string; label: string; homeStoreId: string }>>([]);
  const [editStores, setEditStores] = useState<Array<{ id: string; label: string }>>([]);
  const [saving, setSaving] = useState(false);
  const [choiceLoading, setChoiceLoading] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);
  const choiceRequestId = useRef(0);
  const canEditSchedule = hasCapability(capabilities, "schedule.write");
  const canSyncSheet = hasCapability(capabilities, "sheet.sync");
  const canCreateExport = hasCapability(capabilities, "export.create");
  const canReadJobs = hasCapability(capabilities, "jobs.read");
  const hasStoreAction = canEditSchedule || canSyncSheet || canCreateExport;

  useEffect(() => {
    setMonthId((current) => current ?? months[0]?.id ?? null);
  }, [months]);

  useEffect(() => {
    choiceRequestId.current += 1;
    setEditing(null);
    setEditPeople([]);
    setEditStores([]);
    setSaveError(null);
    if (!monthId || !storeId) {
      setGrid(null);
      setTotals(null);
      setStore(null);
      setAllStores([]);
      setPeople([]);
      setAttribution(null);
      setGridRows([]);
      setFreshness(null);
      setProjection(null);
      setError(null);
      return;
    }
    let cancelled = false;
    setError(null);
    const query = `?store_id=${encodeURIComponent(storeId)}`;
    Promise.all([
      isolatedRead(api.get<ProgramGrid>(`/months/${monthId}/program?perspective=people`)),
      isolatedRead(api.get<PontajTotalsResponse>(`/months/${monthId}/pontaj-totals`)),
      isolatedRead(api.get<StoreSummary[]>("/catalog/stores")),
      isolatedRead(api.get<PersonSummary[]>(`/catalog/people?store_id=${encodeURIComponent(storeId)}`)),
      isolatedRead(api.get<AttributionResponse>(`/months/${monthId}/attribution`)),
      isolatedRead(api.get<GridCalculation[]>(`/months/${monthId}/grid`)),
      isolatedRead(api.get<EpayFreshness>(`/months/${monthId}/epay/freshness${query}`)),
      isolatedRead(api.get<SheetProjection>(`/months/${monthId}/sheet-projection${query}`)),
    ])
      .then(([programRead, totalsRead, storesRead, peopleRead, attributionRead, gridRead, freshnessRead, projectionRead]) => {
        if (cancelled) return;
        const program = programRead.value;
        const storesResponse = storesRead.value ?? [];
        setGrid(program ? filterGridForStore(program, storeId) : null);
        setTotals(totalsRead.value);
        setAllStores(storesResponse.filter((item) => item.is_active));
        setStore(storesResponse.find((item) => item.id === storeId) ?? null);
        setPeople(peopleRead.value?.filter((item) => item.is_active) ?? []);
        setAttribution(attributionRead.value ? {
          ...attributionRead.value,
          rows: attributionRead.value.rows.filter((row) => row.store_id === storeId),
        } : null);
        setGridRows(
          gridRead.value && program
            ? gridRead.value.filter((row) => row.store_id === storeId && row.revision === program.revision)
            : [],
        );
        setFreshness(freshnessRead.value);
        setProjection(projectionRead.value);
        const failures = [
          programRead.error ? `Calendar: ${programRead.error}` : null,
          totalsRead.error ? `Pontaj: ${totalsRead.error}` : null,
          storesRead.error ? `Catalog magazine: ${storesRead.error}` : null,
          peopleRead.error ? `Echipă: ${peopleRead.error}` : null,
          attributionRead.error ? `Vânzări/atribuire: ${attributionRead.error}` : null,
          gridRead.error ? `Grilă: ${gridRead.error}` : null,
          freshnessRead.error ? `E-pay: ${freshnessRead.error}` : null,
          projectionRead.error ? `Sheet: ${projectionRead.error}` : null,
        ].filter((message): message is string => message !== null);
        setError(failures.length > 0 ? `Date parțial indisponibile — ${failures.join(" · ")}` : null);
      })
      .catch((e: unknown) => {
        if (!cancelled) setError(e instanceof Error ? e.message : String(e));
      });
    return () => {
      cancelled = true;
    };
  }, [api, monthId, storeId]);

  useEffect(() => {
    if (!monthId || !storeId || !canReadJobs) {
      setJobDiagnostics(null);
      setJobsError(null);
      return;
    }
    let cancelled = false;
    setJobsError(null);
    api.get<JobDiagnostics>("/worker/jobs/diagnostics?terminal_limit=50")
      .then((response) => {
        if (!cancelled) setJobDiagnostics(response);
      })
      .catch((e: unknown) => {
        if (!cancelled) setJobsError(e instanceof Error ? e.message : String(e));
      });
    return () => {
      cancelled = true;
    };
  }, [api, monthId, storeId, canReadJobs]);

  const salesByPerson = useMemo(() => {
    const map = new Map<string, number>();
    attribution?.rows.forEach((row) => map.set(row.person_id, (map.get(row.person_id) ?? 0) + Number(row.amount)));
    return map;
  }, [attribution]);

  const gridDetails = useMemo(() => {
    const map = new Map<number, GridDetail | null>();
    for (const row of gridRows) map.set(row.id, parseGridDetail(row.payload));
    return map;
  }, [gridRows]);

  const gridAnomalyCount = useMemo(
    () => Array.from(gridDetails.values()).reduce((sum, detail) => sum + (detail?.anomalies.length ?? 0), 0),
    [gridDetails],
  );

  const storeAttributionAnomalies = useMemo(
    () => (attribution?.anomalies ?? []).filter((item) => anomalyBelongsToStore(item, storeId)),
    [attribution, storeId],
  );

  const storeJobs = useMemo(() => {
    if (!jobDiagnostics || !storeId || !monthId) return [];
    return jobDiagnostics.jobs.filter((job) =>
      job.month_id === monthId
      && job.store_ids.includes(storeId)
      && (job.kind === "GOOGLE_PROJECTION_STORE" || job.kind === "EXPORT_XLSX_STORE"),
    ).slice(0, 6);
  }, [jobDiagnostics, monthId, storeId]);

  const storeSalesTotal = Array.from(salesByPerson.values()).reduce((total, amount) => total + amount, 0);
  const peopleIds = new Set(people.map((person) => person.id));
  const storeHours = totals ? Object.entries(totals.totals).filter(([personId]) => peopleIds.has(personId)).reduce((sum, [, bucket]) => sum + bucket.hours, 0) : 0;
  const storeWorkingDays = totals ? Object.entries(totals.totals).filter(([personId]) => peopleIds.has(personId)).reduce((sum, [, bucket]) => sum + bucket.working_days, 0) : 0;
  const matrixPeople = useMemo(() => buildMatrixPeople(grid, people), [grid, people]);
  const currentMonth = months.find((month) => month.id === monthId) ?? null;

  function enqueue(requiredCapability: Capability, path: string, body: Record<string, string>) {
    if (!monthId || !hasCapability(capabilities, requiredCapability)) return;
    setActionMessage(null);
    api.post<Record<string, unknown>>(`/months/${monthId}${path}`, body)
      .then(async (result) => {
        setActionMessage(`Job ${String(result.job_id ?? result.status ?? "trimis")} a fost pus în coadă.`);
        if (canReadJobs) {
          try {
            setJobDiagnostics(await api.get<JobDiagnostics>("/worker/jobs/diagnostics?terminal_limit=50"));
            setJobsError(null);
          } catch (e: unknown) {
            setJobsError(e instanceof Error ? e.message : String(e));
          }
        }
      })
      .catch((e: unknown) => setActionMessage(e instanceof Error ? e.message : String(e)));
  }

  async function configureEditor(rowId: string, cell: ProgramCell, preferred?: EditValue): Promise<void> {
    if (!monthId || !storeId) throw new Error("Magazinul sau luna nu sunt disponibile.");
    const choices = await api.get<ProgramChoices>(
      `/months/${monthId}/program/choices?business_date=${encodeURIComponent(cell.business_date)}&store_id=${encodeURIComponent(storeId)}`,
    );
    if (choices.choices.length === 0) {
      throw new Error("Nu există agenți eligibili pentru această zi și acest scope.");
    }
    const personChoice = choices.choices.find((choice) => choice.person_id === preferred?.personId)
      ?? choices.choices.find((choice) => choice.person_id === cell.person_id)
      ?? choices.choices[0];
    const allowedStoreIds = Array.from(new Set(choices.choices.flatMap((choice) => choice.allowed_store_ids))).sort();
    const candidateStoreId = preferred?.storeId || cell.store_id || storeId || personChoice.home_store_id;
    const selectedStoreId = allowedStoreIds.includes(candidateStoreId) ? candidateStoreId : (allowedStoreIds[0] ?? "");
    const candidateKind = preferred?.workingKind || cell.working_kind || "NORMAL";
    const selectedKind = personChoice.working_kinds.includes(candidateKind) ? candidateKind : (personChoice.working_kinds[0] ?? "NORMAL");
    const storeLabels = new Map(allStores.map((item) => [item.id, item.name || item.internal_code]));

    setEditPeople(choices.choices.map((choice) => ({ id: choice.person_id, label: choice.display_name, homeStoreId: choice.home_store_id })));
    setEditStores(allowedStoreIds.map((id) => ({ id, label: storeLabels.get(id) ?? id })));
    setEditValue({
      personId: personChoice.person_id,
      storeId: selectedStoreId,
      status: preferred?.status ?? cell.status ?? "WORKING",
      workingKind: selectedKind,
    });
    setEditing({ rowId, businessDate: cell.business_date });
  }

  async function beginEdit(rowId: string, cell: ProgramCell) {
    if (!storeId || !canEditSchedule || cell.locked) return;
    const requestId = ++choiceRequestId.current;
    setChoiceLoading(true);
    setSaveError(null);
    try {
      await configureEditor(rowId, cell);
    } catch (e: unknown) {
      if (requestId === choiceRequestId.current) {
        setEditing(null);
        setSaveError(e instanceof Error ? e.message : String(e));
      }
    } finally {
      if (requestId === choiceRequestId.current) setChoiceLoading(false);
    }
  }

  async function recoverStaleEdit(activeEditing: Editing, draft: EditValue) {
    if (!monthId || !storeId) return;
    setChoiceLoading(true);
    try {
      const response = await api.get<ProgramGrid>(`/months/${monthId}/program?perspective=people`);
      const freshGrid = filterGridForStore(response, storeId);
      setGrid(freshGrid);
      const row = freshGrid.rows.find((candidate) => candidate.row_id === activeEditing.rowId);
      const cell = row?.cells.find((candidate) => candidate.business_date === activeEditing.businessDate);
      if (!cell || cell.locked) {
        setEditing(null);
        setSaveError(`Programul s-a schimbat între timp. Revizia ${freshGrid.revision} a fost încărcată, dar ziua editată nu mai este disponibilă pentru modificare.`);
        return;
      }
      await configureEditor(activeEditing.rowId, cell, draft);
      setSaveError(`Programul s-a schimbat între timp. Am încărcat revizia ${freshGrid.revision}; verifică valorile păstrate și salvează din nou.`);
    } catch (e: unknown) {
      setSaveError(`Programul s-a schimbat între timp, iar reîncărcarea reviziei curente a eșuat: ${e instanceof Error ? e.message : String(e)}`);
    } finally {
      setChoiceLoading(false);
    }
  }

  async function saveCell() {
    if (!canEditSchedule || !monthId || !storeId || !grid || !editing || !editValue.personId) return;
    const activeEditing = editing;
    const draft = { ...editValue };
    setSaving(true);
    setSaveError(null);
    try {
      await api.post(`/months/${monthId}/program/cell?expected_revision=${grid.revision}`, {
        person_id: editValue.personId,
        business_date: editing.businessDate,
        status: editValue.status,
        store_id: editValue.status === "WORKING" ? (editValue.storeId || null) : null,
        working_kind: editValue.status === "WORKING" ? editValue.workingKind : null,
      });
      const response = await api.get<ProgramGrid>(`/months/${monthId}/program?perspective=people`);
      setGrid(filterGridForStore(response, storeId));
      setEditing(null);
      setActionMessage("Programul magazinului a fost actualizat.");
    } catch (e: unknown) {
      if (isApiError(e) && e.status === 409 && e.code === "STALE_REVISION") {
        await recoverStaleEdit(activeEditing, draft);
      } else if (isApiError(e) && e.status === 409 && e.code === "MONTH_CLOSED") {
        setEditing(null);
        setSaveError("Luna a fost închisă între timp. Editarea calendarului a fost oprită; redeschiderea lunii este necesară înainte de alte modificări.");
        try {
          const response = await api.get<ProgramGrid>(`/months/${monthId}/program?perspective=people`);
          setGrid(filterGridForStore(response, storeId));
        } catch {
          // Keep the typed close error even if the refresh fails.
        }
      } else {
        setSaveError(e instanceof Error ? e.message : String(e));
      }
    } finally {
      setSaving(false);
    }
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
          {currentMonth && <span className="context-pill">{currentMonth.state} · rev {grid?.revision ?? currentMonth.revision}</span>}
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
        <Metric label="Anomalii" value={String(gridAnomalyCount + storeAttributionAnomalies.length)} detail={`${gridAnomalyCount} grilă · ${storeAttributionAnomalies.length} atribuire`} />
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
            {canEditSchedule && (
              <button type="button" className="operation-card" onClick={() => setActiveTab("calendar")}>
                <span className="operation-icon">▦</span><span><strong>Editează calendarul</strong><small>Program, suplimentare, mutări</small></span><span>→</span>
              </button>
            )}
            {canSyncSheet && (
              <button type="button" className="operation-card" onClick={() => enqueue("sheet.sync", "/sheet-projection/enqueue", { store_id: storeId })}>
                <span className="operation-icon">↻</span><span><strong>Sincronizează Sheet</strong><small>{projection?.last_success_generation ? `Ultima generație ${projection.last_success_generation}` : "Fără sincronizare reușită"}</small></span><span>→</span>
              </button>
            )}
            {canCreateExport && (
              <button type="button" className="operation-card" onClick={() => enqueue("export.create", "/export/store", { store_id: storeId })}>
                <span className="operation-icon">⇩</span><span><strong>Exportă XLSX</strong><small>Grilă și pontaj magazin</small></span><span>→</span>
              </button>
            )}
            {!hasStoreAction && <p className="muted">Nu există operațiuni de modificare disponibile pentru sesiunea curentă.</p>}
          </section>

          <section className="panel sync-panel">
            <div className="panel-heading"><div><span className="eyebrow">INTEGRITATE DATE</span><h3>Sync & export</h3></div></div>
            <div className="sync-status-row"><span>E-pay</span><strong className={freshness?.is_fresh ? "text-ok" : "text-warn"}>{freshness?.is_fresh ? "Proaspăt" : "Stale / indisponibil"}</strong></div>
            <div className="sync-status-row"><span>Sheet projection</span><strong>{projection?.last_success_generation ?? "—"}</strong></div>
            <div className="sync-status-row"><span>Erori Sheet</span><strong className={projection?.last_error ? "text-err" : "text-ok"}>{projection?.last_error ? "Există" : "0"}</strong></div>
            {projection?.last_error && <p className="error-text compact-error">{projection.last_error}</p>}
            {jobsError && <p className="error-text compact-error">Statusul joburilor este indisponibil: {jobsError}</p>}
            {canReadJobs && !jobsError && storeJobs.length === 0 && <div className="sync-status-row"><span>Joburi sync/export</span><strong>Fără activitate recentă</strong></div>}
            {storeJobs.map((job) => (
              <div className="sync-status-row" key={job.id} title={job.last_error ?? ""}>
                <span>{jobKindLabel(job.kind)} #{job.id}</span>
                <strong className={job.state === "FAILED" ? "text-err" : job.state === "DONE" ? "text-ok" : "text-warn"}>
                  {jobStateLabel(job.state)} · {job.attempts}/{job.max_attempts}
                </strong>
              </div>
            ))}
          </section>
        </div>
      )}

      {activeTab === "calendar" && (
        <section className="panel calendar-panel">
          <div className="panel-heading">
            <div><span className="eyebrow">PROGRAM LUNAR</span><h3>Calendar magazin</h3></div>
            <span className="context-pill">{canEditSchedule ? "click pe o zi pentru editare" : "doar citire"}</span>
          </div>
          {choiceLoading && <p className="muted" role="status">Actualizez opțiunile de editare…</p>}
          {grid ? (
            <ProgramMatrix
              grid={grid}
              viewportHeight={520}
              onCellClick={canEditSchedule ? beginEdit : undefined}
              editing={editing}
              editValue={editValue}
              people={editPeople.length > 0 ? editPeople : matrixPeople}
              stores={editStores.length > 0 ? editStores : allStores.map((item) => ({ id: item.id, label: item.name || item.internal_code }))}
              onEditChange={canEditSchedule ? setEditValue : undefined}
              onSave={canEditSchedule ? saveCell : undefined}
              onCancelEdit={canEditSchedule ? () => {
                choiceRequestId.current += 1;
                setEditing(null);
                setSaveError(null);
              } : undefined}
              saving={saving || choiceLoading}
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
            <div className="panel-heading"><div><span className="eyebrow">GRILĂ</span><h3>Componente și anomalii</h3></div><span className="count-badge">{gridRows.length}</span></div>
            <div className="grid-calculation-list">
              {gridRows.map((row) => {
                const detail = gridDetails.get(row.id);
                const epay = asRecord(detail?.inputs.epay);
                return (
                  <article className="grid-calculation-card" key={row.id}>
                    <div><span>Agent</span><strong>{people.find((person) => person.id === row.person_id)?.display_name ?? row.person_id}</strong></div>
                    <div><span>Total grilă</span><strong>{moneyComponent(detail, "total_salary")}</strong></div>
                    <div><span>Comision principal</span><strong>{moneyComponent(detail, "main_commission")}</strong></div>
                    <div><span>Comision E-pay</span><strong>{moneyComponent(detail, "epay_commission")}</strong></div>
                    <div><span>E-pay input</span><strong>{epay ? `${numericValue(epay.under_50)} / ${numericValue(epay.at_or_over_50)}` : "—"}</strong></div>
                    <div><span>Anomalii</span><strong className={(detail?.anomalies.length ?? 0) > 0 ? "text-warn" : "text-ok"}>{detail ? detail.anomalies.length : "—"}</strong></div>
                    <small>{detail?.anomalies.length ? detail.anomalies.map((item) => String(item.code ?? "ANOMALY")).join(" · ") : `rev ${row.revision} · ${row.rule_pack_version}`}</small>
                  </article>
                );
              })}
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

function parseGridDetail(payload: string): GridDetail | null {
  try {
    const parsed = JSON.parse(payload) as unknown;
    if (!parsed || typeof parsed !== "object") return null;
    const record = parsed as Record<string, unknown>;
    const components = asRecord(record.components);
    const inputs = asRecord(record.inputs);
    const anomalies = Array.isArray(record.anomalies)
      ? record.anomalies.filter((item): item is Record<string, unknown> => Boolean(item) && typeof item === "object")
      : [];
    if (!components || !inputs) return null;
    return { components, inputs, anomalies };
  } catch {
    return null;
  }
}

function asRecord(value: unknown): Record<string, unknown> | null {
  return value && typeof value === "object" && !Array.isArray(value) ? value as Record<string, unknown> : null;
}

function moneyComponent(detail: GridDetail | null | undefined, key: string): string {
  if (!detail) return "—";
  const value = Number(detail.components[key]);
  return Number.isFinite(value) ? `${formatMoney(value)} RON` : "—";
}

function numericValue(value: unknown): string {
  const number = Number(value);
  return Number.isFinite(number) ? String(number) : "—";
}

function anomalyBelongsToStore(anomaly: Record<string, unknown>, storeId: string | null): boolean {
  if (!storeId) return false;
  const anomalyStoreId = anomaly.store_id;
  return anomalyStoreId === undefined || anomalyStoreId === null || anomalyStoreId === storeId;
}

function jobKindLabel(kind: string): string {
  if (kind === "GOOGLE_PROJECTION_STORE") return "Sheet";
  if (kind === "EXPORT_XLSX_STORE") return "Export XLSX";
  return kind.replaceAll("_", " ");
}

function jobStateLabel(state: QueueState): string {
  const labels: Record<QueueState, string> = {
    QUEUED: "În așteptare",
    RETRY: "Retry",
    RUNNING: "Rulează",
    FAILED: "Eșuat",
    DONE: "Finalizat",
  };
  return labels[state];
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

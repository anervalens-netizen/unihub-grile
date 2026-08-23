import { useEffect, useRef, useState } from "react";
import {
  type ApiClient,
  type MonthSummary,
  type ProgramCell,
  type ProgramChoices,
  type ProgramGrid,
} from "../api/client";
import { hasCapability, type Capability } from "../capabilities";
import { MonthSelector } from "../components/MonthSelector";
import { ProgramMatrix } from "../components/ProgramMatrix";
import { LoadingState, RequestError, requestErrorMessage } from "../components/RequestState";

export interface ProgramProps {
  api: ApiClient;
  months: MonthSummary[];
  monthsError: string | null;
  capabilities: ReadonlySet<Capability>;
}

type Perspective = "stores" | "people";
type EditValue = { personId: string; storeId: string; status: string; workingKind: string };
type Editing = { rowId: string; businessDate: string };

function isApiError(error: unknown): error is { status: number; code?: string } {
  return typeof error === "object" && error !== null && "status" in error && typeof (error as { status?: unknown }).status === "number";
}

export function Program({ api, months, monthsError, capabilities }: ProgramProps) {
  const [monthId, setMonthId] = useState<string | null>(months[0]?.id ?? null);
  const [perspective, setPerspective] = useState<Perspective>("stores");
  const [grid, setGrid] = useState<ProgramGrid | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [reloadToken, setReloadToken] = useState(0);
  const [viewportHeight, setViewportHeight] = useState(480);
  const [people, setPeople] = useState<Array<{ id: string; label: string; homeStoreId: string }>>([]);
  const [stores, setStores] = useState<Array<{ id: string; label: string }>>([]);
  const [editing, setEditing] = useState<Editing | null>(null);
  const [editValue, setEditValue] = useState<EditValue>({ personId: "", storeId: "", status: "WORKING", workingKind: "NORMAL" });
  const [saving, setSaving] = useState(false);
  const [choiceLoading, setChoiceLoading] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);
  const choiceRequestId = useRef(0);
  const canEditSchedule = hasCapability(capabilities, "schedule.write");

  useEffect(() => {
    setMonthId((current) => current ?? months[0]?.id ?? null);
  }, [months]);

  useEffect(() => {
    function onResize() {
      if (window.innerWidth < 720) {
        setViewportHeight(320);
      } else if (window.innerWidth < 1100) {
        setViewportHeight(400);
      } else {
        setViewportHeight(560);
      }
    }
    onResize();
    window.addEventListener("resize", onResize);
    return () => window.removeEventListener("resize", onResize);
  }, []);

  useEffect(() => {
    choiceRequestId.current += 1;
    setEditing(null);
    setPeople([]);
    setStores([]);
    setSaveError(null);
    if (!monthId) {
      setGrid(null);
      setLoading(false);
      return;
    }
    let cancelled = false;
    setGrid(null);
    setError(null);
    setLoading(true);
    api
      .get<ProgramGrid>(`/months/${monthId}/program?perspective=${perspective}`)
      .then((response) => {
        if (!cancelled) setGrid(response);
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
  }, [api, monthId, perspective, reloadToken]);

  function targetStoreId(sourceGrid: ProgramGrid, rowId: string, cell: ProgramCell): string | null {
    const row = sourceGrid.rows.find((candidate) => candidate.row_id === rowId);
    if (!row) return null;
    if (perspective === "stores") return cell.store_id ?? cell.home_store_id ?? row.row_id;
    return cell.store_id ?? cell.home_store_id ?? row.home_store_id;
  }

  async function configureEditor(
    sourceGrid: ProgramGrid,
    rowId: string,
    cell: ProgramCell,
    preferred?: EditValue,
  ): Promise<void> {
    if (!monthId) throw new Error("Luna nu este selectată.");
    const storeId = targetStoreId(sourceGrid, rowId, cell);
    if (!storeId) throw new Error("Nu pot determina magazinul pentru această zi.");
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
    const candidateStoreId = preferred?.storeId || cell.store_id || choices.store_id || personChoice.home_store_id;
    const selectedStoreId = allowedStoreIds.includes(candidateStoreId) ? candidateStoreId : (allowedStoreIds[0] ?? "");
    const candidateKind = preferred?.workingKind || cell.working_kind || "NORMAL";
    const selectedKind = personChoice.working_kinds.includes(candidateKind) ? candidateKind : (personChoice.working_kinds[0] ?? "NORMAL");

    setPeople(choices.choices.map((choice) => ({ id: choice.person_id, label: choice.display_name, homeStoreId: choice.home_store_id })));
    setStores(allowedStoreIds.map((id) => ({ id, label: id })));
    setEditValue({
      personId: personChoice.person_id,
      storeId: selectedStoreId,
      status: preferred?.status ?? cell.status ?? "WORKING",
      workingKind: selectedKind,
    });
    setEditing({ rowId, businessDate: cell.business_date });
  }

  async function beginEdit(rowId: string, cell: ProgramCell) {
    if (!canEditSchedule || !grid || cell.locked) return;
    const requestId = ++choiceRequestId.current;
    setChoiceLoading(true);
    setSaveError(null);
    try {
      await configureEditor(grid, rowId, cell);
    } catch (e: unknown) {
      if (requestId === choiceRequestId.current) {
        setEditing(null);
        setSaveError(requestErrorMessage(e));
      }
    } finally {
      if (requestId === choiceRequestId.current) setChoiceLoading(false);
    }
  }

  async function recoverStaleEdit(activeEditing: Editing, draft: EditValue) {
    if (!monthId) return;
    setChoiceLoading(true);
    try {
      const freshGrid = await api.get<ProgramGrid>(`/months/${monthId}/program?perspective=${perspective}`);
      setGrid(freshGrid);
      const row = freshGrid.rows.find((candidate) => candidate.row_id === activeEditing.rowId);
      const cell = row?.cells.find((candidate) => candidate.business_date === activeEditing.businessDate);
      if (!cell || cell.locked) {
        setEditing(null);
        setSaveError(`Programul s-a schimbat între timp. Revizia ${freshGrid.revision} a fost încărcată, dar ziua editată nu mai este disponibilă pentru modificare.`);
        return;
      }
      await configureEditor(freshGrid, activeEditing.rowId, cell, draft);
      setSaveError(`Programul s-a schimbat între timp. Am încărcat revizia ${freshGrid.revision}; verifică valorile păstrate și salvează din nou.`);
    } catch (e: unknown) {
      setSaveError(`Programul s-a schimbat între timp, iar reîncărcarea reviziei curente a eșuat: ${requestErrorMessage(e)}`);
    } finally {
      setChoiceLoading(false);
    }
  }

  async function saveCell() {
    if (!canEditSchedule || !monthId || !grid || !editing || !editValue.personId) return;
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
      const freshGrid = await api.get<ProgramGrid>(`/months/${monthId}/program?perspective=${perspective}`);
      setGrid(freshGrid);
      setEditing(null);
    } catch (e: unknown) {
      if (isApiError(e) && e.status === 409 && e.code === "STALE_REVISION") {
        await recoverStaleEdit(activeEditing, draft);
      } else if (isApiError(e) && e.status === 409 && e.code === "MONTH_CLOSED") {
        setEditing(null);
        setSaveError("Luna a fost închisă între timp. Editarea a fost oprită; redeschiderea lunii este necesară înainte de alte modificări.");
        try {
          setGrid(await api.get<ProgramGrid>(`/months/${monthId}/program?perspective=${perspective}`));
        } catch {
          // The typed close error remains authoritative even if the refresh fails.
        }
      } else {
        setSaveError(requestErrorMessage(e));
      }
    } finally {
      setSaving(false);
    }
  }

  return (
    <section className="card" aria-label="Program">
      <header className="card-header">
        <h2>Program</h2>
        <div className="program-toolbar">
          <MonthSelector
            months={months}
            value={monthId}
            onChange={setMonthId}
            error={monthsError}
          />
          <fieldset className="perspective-switch" aria-label="Perspectivă">
            <legend className="muted">Perspectivă</legend>
            <label>
              <input
                type="radio"
                name="perspective"
                value="stores"
                checked={perspective === "stores"}
                onChange={() => setPerspective("stores")}
              />
              Per magazin
            </label>
            <label>
              <input
                type="radio"
                name="perspective"
                value="people"
                checked={perspective === "people"}
                onChange={() => setPerspective("people")}
              />
              Per agent
            </label>
          </fieldset>
        </div>
      </header>
      {!canEditSchedule && <p className="muted">Programul este disponibil doar pentru vizualizare în sesiunea curentă.</p>}
      {error && <RequestError message={error} onRetry={() => setReloadToken((value) => value + 1)} />}
      {saveError && <p className="error" role="alert">Salvarea nu a reușit: {saveError}</p>}
      {choiceLoading && <p className="muted" role="status">Actualizez opțiunile de editare…</p>}
      {loading && <LoadingState>Încarc programul…</LoadingState>}
      {!loading && !error && grid && grid.rows.length === 0 && (
        <div className="empty-state"><strong>Programul este gol.</strong><span>Nu există rânduri pentru perspectiva și luna selectate.</span></div>
      )}
      {!loading && !error && grid && grid.rows.length > 0 && (
        <ProgramMatrix
          grid={grid}
          viewportHeight={viewportHeight}
          onCellClick={canEditSchedule ? beginEdit : undefined}
          editing={editing}
          editValue={editValue}
          people={people}
          stores={stores}
          onEditChange={canEditSchedule ? setEditValue : undefined}
          onSave={canEditSchedule ? saveCell : undefined}
          onCancelEdit={canEditSchedule ? () => {
            choiceRequestId.current += 1;
            setEditing(null);
            setSaveError(null);
          } : undefined}
          saving={saving || choiceLoading}
        />
      )}
    </section>
  );
}

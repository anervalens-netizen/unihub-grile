import { useEffect, useState } from "react";
import {
  type ApiClient,
  type MonthSummary,
  type PersonSummary,
  type ProgramGrid,
  type StoreSummary,
} from "../api/client";
import { MonthSelector } from "../components/MonthSelector";
import { ProgramMatrix } from "../components/ProgramMatrix";

export interface ProgramProps {
  api: ApiClient;
  months: MonthSummary[];
  monthsError: string | null;
}

type Perspective = "stores" | "people";

function isApiError(error: unknown): error is { status: number } {
  return typeof error === "object" && error !== null && "status" in error && typeof (error as { status?: unknown }).status === "number";
}

export function Program({ api, months, monthsError }: ProgramProps) {
  const [monthId, setMonthId] = useState<string | null>(months[0]?.id ?? null);
  const [perspective, setPerspective] = useState<Perspective>("stores");
  const [grid, setGrid] = useState<ProgramGrid | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [viewportHeight, setViewportHeight] = useState(480);
  const [people, setPeople] = useState<PersonSummary[]>([]);
  const [stores, setStores] = useState<StoreSummary[]>([]);
  const [editing, setEditing] = useState<{ rowId: string; businessDate: string } | null>(null);
  const [editValue, setEditValue] = useState({ personId: "", storeId: "", status: "WORKING", workingKind: "NORMAL" });
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);

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
    if (!monthId) {
      setGrid(null);
      return;
    }
    let cancelled = false;
    setError(null);
    api
      .get<ProgramGrid>(`/months/${monthId}/program?perspective=${perspective}`)
      .then((response) => {
        if (!cancelled) setGrid(response);
      })
      .catch((e: unknown) => {
        if (!cancelled) setError(String(e));
      });
    return () => {
      cancelled = true;
    };
  }, [api, monthId, perspective]);

  useEffect(() => {
    if (!monthId || !grid) return;
    const peopleById = new Map<string, PersonSummary>();
    const storesById = new Map<string, StoreSummary>();
    for (const row of grid.rows) {
      for (const cell of row.cells) {
        if (cell.person_id && cell.display_name && cell.home_store_id) {
          peopleById.set(cell.person_id, {
            id: cell.person_id,
            tenant_id: "",
            internal_code: cell.person_id,
            external_code: null,
            display_name: cell.display_name,
            home_store_id: cell.home_store_id,
            is_active: true,
          });
        }
        if (cell.store_id) {
          storesById.set(cell.store_id, {
            id: cell.store_id,
            tenant_id: "",
            company_code: "",
            internal_code: cell.store_id,
            external_code: null,
            name: cell.store_id,
            is_active: true,
          });
        }
      }
    }
    setPeople([...peopleById.values()]);
    setStores([...storesById.values()]);
  }, [monthId, grid]);

  function beginEdit(rowId: string, cell: ProgramGrid["rows"][number]["cells"][number]) {
    setSaveError(null);
    setEditing({ rowId, businessDate: cell.business_date });
    setEditValue({
      personId: cell.person_id ?? people[0]?.id ?? rowId,
      storeId: cell.store_id ?? stores[0]?.id ?? rowId,
      status: cell.status || "WORKING",
      workingKind: cell.working_kind || "NORMAL",
    });
  }

  function saveCell() {
    if (!monthId || !grid || !editing) return;
    setSaving(true);
    setSaveError(null);
    api.post(`/months/${monthId}/program/cell?expected_revision=${grid.revision}`, {
      person_id: editValue.personId,
      business_date: editing.businessDate,
      status: editValue.status,
      store_id: editValue.storeId || null,
      working_kind: editValue.workingKind,
    }).then(() => {
      setEditing(null);
      return api.get<ProgramGrid>(`/months/${monthId}/program?perspective=${perspective}`);
    }).then(setGrid).catch((e: unknown) => {
      if (isApiError(e) && e.status === 409) {
        setSaveError("Programul s-a schimbat între timp (revizie stale). Editorul și grila curentă au fost păstrate; reîncarcă luna înainte de a salva din nou.");
      } else {
        setSaveError(e instanceof Error ? e.message : String(e));
      }
    }).finally(() => setSaving(false));
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
      {error && <p className="error" role="alert">{error}</p>}
      {saveError && <p className="error" role="alert">Salvarea nu a reușit: {saveError}</p>}
      {grid && (
        <ProgramMatrix
          grid={grid}
          viewportHeight={viewportHeight}
          onCellClick={beginEdit}
          editing={editing}
          editValue={editValue}
          people={people.map((person) => ({ id: person.id, label: person.display_name, homeStoreId: person.home_store_id }))}
          stores={stores.map((store) => ({ id: store.id, label: store.name || store.internal_code }))}
          onEditChange={setEditValue}
          onSave={saveCell}
          onCancelEdit={() => setEditing(null)}
          saving={saving}
        />
      )}
    </section>
  );
}

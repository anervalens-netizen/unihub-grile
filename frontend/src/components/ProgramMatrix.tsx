import { useEffect, useRef, useState } from "react";
import type { ProgramGrid, ProgramCell, ProgramRow } from "../api/client";

export interface ProgramMatrixProps {
  grid: ProgramGrid;
  /** Pixel height of the virtualized viewport. Default 480. */
  viewportHeight?: number;
  /** Row height in pixels; defaults to 36 (matches the calendar cell height). */
  rowHeight?: number;
  /** Called when the user clicks a cell (skipped when the cell is locked). */
  onCellClick?: (rowId: string, cell: ProgramCell) => void;
  /** Optional controlled editor for an unlocked cell. */
  editing?: { rowId: string; businessDate: string } | null;
  editValue?: { personId: string; storeId: string; status: string; workingKind: string };
  people?: Array<{ id: string; label: string; homeStoreId: string }>;
  stores?: Array<{ id: string; label: string }>;
  onEditChange?: (value: { personId: string; storeId: string; status: string; workingKind: string }) => void;
  onSave?: () => void;
  onCancelEdit?: () => void;
  saving?: boolean;
}

interface RowRange {
  start: number;
  end: number;
}

/**
 * Virtualized 31-day matrix.
 *
 * The grid can be wide (75+ rows) but only ~12-18 fit on screen at once.
 * The component renders only the visible rows + a small overscan window so
 * DOM nodes stay bounded regardless of store count.
 */
export function ProgramMatrix({
  grid,
  viewportHeight = 480,
  rowHeight = 36,
  onCellClick,
  editing,
  editValue,
  people = [],
  stores = [],
  onEditChange,
  onSave,
  onCancelEdit,
  saving = false,
}: ProgramMatrixProps) {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const [scrollTop, setScrollTop] = useState(0);
  const [range, setRange] = useState<RowRange>({ start: 0, end: 0 });

  useEffect(() => {
    const total = grid.rows.length;
    const visible = Math.ceil(viewportHeight / rowHeight) + 4;
    const start = Math.max(0, Math.floor(scrollTop / rowHeight) - 2);
    const end = Math.min(total, start + visible);
    setRange({ start, end });
  }, [scrollTop, viewportHeight, rowHeight, grid.rows.length]);

  const total = grid.rows.length;
  const totalHeight = total * rowHeight;
  const offsetTop = range.start * rowHeight;
  const slice = grid.rows.slice(range.start, range.end);

  return (
    <div className="program-matrix">
      <div className="program-matrix-legend" role="list" aria-label="Legendă">
        {grid.legend.map((badge) => (
          <span key={badge} className={`legend-chip badge-${badge}`} role="listitem">
            {badge}
          </span>
        ))}
      </div>
      <div
        ref={containerRef}
        className="program-matrix-scroll"
        style={{ height: viewportHeight }}
        onScroll={(event) =>
          setScrollTop((event.target as HTMLDivElement).scrollTop)
        }
        role="grid"
        aria-rowcount={total}
        aria-colcount={grid.dates.length + 1}
      >
        <div className="program-matrix-header" role="row">
          <span className="program-matrix-cell program-matrix-cell-header" role="columnheader">
            Rând
          </span>
          {grid.dates.map((date) => (
            <span
              key={date}
              className="program-matrix-cell program-matrix-cell-header"
              role="columnheader"
            >
              {Number(date.slice(-2))}
            </span>
          ))}
        </div>
        <div className="program-matrix-body" style={{ height: totalHeight }}>
          <div style={{ height: offsetTop }} aria-hidden="true" />
          {slice.map((row) => (
            <ProgramMatrixRow
              key={row.row_id}
              row={row}
              rowHeight={rowHeight}
              onCellClick={onCellClick}
              editing={editing}
              editValue={editValue}
              people={people}
              stores={stores}
              onEditChange={onEditChange}
              onSave={onSave}
              onCancelEdit={onCancelEdit}
              saving={saving}
            />
          ))}
          <div
            style={{ height: totalHeight - offsetTop - slice.length * rowHeight }}
            aria-hidden="true"
          />
        </div>
      </div>
      <p className="muted">
        Randări virtualizate: {slice.length} / {total} rânduri vizibile.
      </p>
    </div>
  );
}

interface ProgramMatrixRowProps {
  row: ProgramRow;
  rowHeight: number;
  onCellClick?: (rowId: string, cell: ProgramCell) => void;
  editing?: { rowId: string; businessDate: string } | null;
  editValue?: { personId: string; storeId: string; status: string; workingKind: string };
  people: Array<{ id: string; label: string; homeStoreId: string }>;
  stores: Array<{ id: string; label: string }>;
  onEditChange?: (value: { personId: string; storeId: string; status: string; workingKind: string }) => void;
  onSave?: () => void;
  onCancelEdit?: () => void;
  saving: boolean;
}

function ProgramMatrixRow({ row, rowHeight, onCellClick, editing, editValue, people, stores, onEditChange, onSave, onCancelEdit, saving }: ProgramMatrixRowProps) {
  return (
    <div className="program-matrix-row" style={{ height: rowHeight }} role="row">
      <span
        className="program-matrix-cell program-matrix-cell-row-label"
        role="rowheader"
      >
        {row.label}
      </span>
      {row.cells.map((cell) => {
        const isEditing = editing?.rowId === row.row_id && editing.businessDate === cell.business_date;
        if (isEditing && !cell.locked && editValue && onEditChange) {
          return (
            <div key={cell.business_date} className="program-matrix-cell program-cell-editor">
              <select aria-label="Agent pentru celulă" value={editValue.personId} onChange={(event) => onEditChange({ ...editValue, personId: event.target.value })}>
                {people.map((person) => <option key={person.id} value={person.id}>{person.label}</option>)}
              </select>
              <select aria-label="Magazin pentru celulă" value={editValue.storeId} onChange={(event) => onEditChange({ ...editValue, storeId: event.target.value })}>
                {stores.map((store) => <option key={store.id} value={store.id}>{store.label}</option>)}
              </select>
              <select aria-label="Clasificare" value={editValue.workingKind} onChange={(event) => onEditChange({ ...editValue, workingKind: event.target.value })}>
                <option value="NORMAL">Normal</option><option value="EXTRA_HOME">Extra aici</option><option value="EXTRA_OTHER">Extra alt magazin</option>
              </select>
              <button type="button" className="primary" onClick={onSave} disabled={saving}>{saving ? "Salvez…" : "Salvează"}</button>
              <button type="button" onClick={onCancelEdit} disabled={saving}>Anulează</button>
            </div>
          );
        }
        return (
          <button
            key={cell.business_date}
            type="button"
            className={`program-matrix-cell badge-${cell.badge ?? "UNCOVERED"} ${cell.locked ? "locked" : ""}`}
            disabled={cell.locked}
            aria-label={`${row.label} pe ${cell.business_date}: ${cell.badge ?? "fără acoperire"}`}
            title={`${row.label} pe ${cell.business_date}: ${cell.display_name ?? "fără agent"} (${cell.badge ?? "UNCOVERED"})${cell.locked ? " · BLOCAT" : ""}`}
            onClick={() => onCellClick?.(row.row_id, cell)}
          >
            {cell.badge === "NORMAL" || cell.badge === "EXTRA_HOME" || cell.badge === "EXTRA_OTHER" ? shortName(cell.display_name) : cell.badge ?? ""}
          </button>
        );
      })}
    </div>
  );
}

function shortName(displayName: string | null): string {
  if (!displayName) return "?";
  const tokens = displayName.split(/\s+/);
  return tokens.slice(0, 2).map((token) => token[0]).join("").toUpperCase();
}

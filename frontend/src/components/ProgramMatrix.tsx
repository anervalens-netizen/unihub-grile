import { useEffect, useMemo, useRef, useState } from "react";
import type { ProgramGrid, ProgramCell, ProgramRow } from "../api/client";

export interface ProgramMatrixProps {
  grid: ProgramGrid;
  viewportHeight?: number;
  rowHeight?: number;
  onCellClick?: (rowId: string, cell: ProgramCell) => void;
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

interface EditingCell {
  rowId: string;
  businessDate: string;
}

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
  const editorFirstControlRef = useRef<HTMLSelectElement | null>(null);
  const previousEditingRef = useRef<EditingCell | null>(null);
  const [scrollTop, setScrollTop] = useState(0);
  const [range, setRange] = useState<RowRange>({ start: 0, end: 0 });
  const interactive = Boolean(onCellClick);

  useEffect(() => {
    const total = grid.rows.length;
    const visible = Math.ceil(viewportHeight / rowHeight) + 4;
    const start = Math.max(0, Math.floor(scrollTop / rowHeight) - 2);
    const end = Math.min(total, start + visible);
    setRange({ start, end });
  }, [scrollTop, viewportHeight, rowHeight, grid.rows.length]);

  useEffect(() => {
    const previous = previousEditingRef.current;
    if (editing) {
      if (!previous || previous.rowId !== editing.rowId || previous.businessDate !== editing.businessDate) {
        editorFirstControlRef.current?.focus();
      }
    } else if (previous) {
      const cells = containerRef.current?.querySelectorAll<HTMLButtonElement>("button[data-program-cell]") ?? [];
      const previousCell = Array.from(cells).find((cell) =>
        cell.dataset.rowId === previous.rowId && cell.dataset.businessDate === previous.businessDate,
      );
      previousCell?.focus();
    }
    previousEditingRef.current = editing ? { ...editing } : null;
  }, [editing]);

  const total = grid.rows.length;
  const totalHeight = total * rowHeight;
  const offsetTop = range.start * rowHeight;
  const slice = grid.rows.slice(range.start, range.end);
  const gridColumns = `150px repeat(${grid.dates.length}, minmax(42px, 1fr))`;
  const editingContext = useMemo(() => {
    if (!editing) return null;
    const row = grid.rows.find((candidate) => candidate.row_id === editing.rowId);
    const cell = row?.cells.find((candidate) => candidate.business_date === editing.businessDate);
    return row && cell ? { row, cell } : null;
  }, [editing, grid.rows]);

  return (
    <div className="program-matrix">
      <div className="program-matrix-topline">
        <div className="program-matrix-legend" role="list" aria-label="Legendă">
          {grid.legend.map((badge) => (
            <span key={badge} className={`legend-chip badge-${badge}`} role="listitem">
              {labelBadge(badge)}
            </span>
          ))}
        </div>
        <span className="matrix-revision">Revizie {grid.revision}</span>
      </div>

      {editingContext && editValue && onEditChange && (
        <section
          className="program-cell-editor-panel"
          aria-label="Editor program"
          onKeyDown={(event) => {
            if (event.key === "Escape" && onCancelEdit) {
              event.preventDefault();
              onCancelEdit();
            }
          }}
        >
          <div className="editor-context">
            <span className="eyebrow">EDITARE PROGRAM</span>
            <strong>{editingContext.row.label}</strong>
            <span>{formatDate(editingContext.cell.business_date)}</span>
          </div>
          <label>
            <span>Agent</span>
            <select
              ref={editorFirstControlRef}
              value={editValue.personId}
              onChange={(event) => onEditChange({ ...editValue, personId: event.target.value })}
            >
              {people.map((person) => <option key={person.id} value={person.id}>{person.label}</option>)}
            </select>
          </label>
          <label>
            <span>Status</span>
            <select value={editValue.status} onChange={(event) => onEditChange({ ...editValue, status: event.target.value })}>
              <option value="WORKING">Lucrează</option>
              <option value="OFF">Liber</option>
              <option value="LEAVE">Concediu</option>
            </select>
          </label>
          <label>
            <span>Magazin</span>
            <select value={editValue.storeId} disabled={editValue.status !== "WORKING"} onChange={(event) => onEditChange({ ...editValue, storeId: event.target.value })}>
              {stores.map((store) => <option key={store.id} value={store.id}>{store.label}</option>)}
            </select>
          </label>
          <label>
            <span>Tip zi</span>
            <select value={editValue.workingKind} disabled={editValue.status !== "WORKING"} onChange={(event) => onEditChange({ ...editValue, workingKind: event.target.value })}>
              <option value="NORMAL">Normal</option>
              <option value="EXTRA_HOME">Suplimentar aici</option>
              <option value="EXTRA_OTHER">Suplimentar alt magazin</option>
            </select>
          </label>
          <div className="editor-actions">
            <button type="button" className="button-secondary" onClick={onCancelEdit} disabled={saving}>Anulează</button>
            <button type="button" className="button-primary" onClick={onSave} disabled={saving}>{saving ? "Salvez…" : "Salvează"}</button>
          </div>
        </section>
      )}

      <div
        ref={containerRef}
        className="program-matrix-scroll"
        style={{ height: viewportHeight }}
        onScroll={(event) => setScrollTop((event.target as HTMLDivElement).scrollTop)}
        role="grid"
        aria-label="Calendar program lunar"
        aria-rowcount={total}
        aria-colcount={grid.dates.length + 1}
      >
        <div className="program-matrix-header" style={{ gridTemplateColumns: gridColumns }} role="row">
          <span className="program-matrix-cell program-matrix-cell-header program-matrix-corner" role="columnheader">Magazin / Agent</span>
          {grid.dates.map((date) => (
            <span key={date} className="program-matrix-cell program-matrix-cell-header" role="columnheader">
              <small>{weekday(date)}</small>
              <strong>{Number(date.slice(-2))}</strong>
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
              gridColumns={gridColumns}
              onCellClick={onCellClick}
              editing={editing}
              interactive={interactive}
            />
          ))}
          <div style={{ height: totalHeight - offsetTop - slice.length * rowHeight }} aria-hidden="true" />
        </div>
      </div>
      <p className="matrix-footnote">{total} rânduri · {grid.dates.length} zile · {interactive ? "editorul se deschide deasupra matricei pentru a păstra calendarul lizibil." : "vizualizare fără drept de editare."}</p>
    </div>
  );
}

interface ProgramMatrixRowProps {
  row: ProgramRow;
  rowHeight: number;
  gridColumns: string;
  onCellClick?: (rowId: string, cell: ProgramCell) => void;
  editing?: { rowId: string; businessDate: string } | null;
  interactive: boolean;
}

function ProgramMatrixRow({ row, rowHeight, gridColumns, onCellClick, editing, interactive }: ProgramMatrixRowProps) {
  return (
    <div className="program-matrix-row" style={{ height: rowHeight, gridTemplateColumns: gridColumns }} role="row">
      <span className="program-matrix-cell program-matrix-cell-row-label" role="rowheader">{row.label}</span>
      {row.cells.map((cell) => {
        const selected = editing?.rowId === row.row_id && editing.businessDate === cell.business_date;
        return (
          <button
            key={cell.business_date}
            type="button"
            data-program-cell="true"
            data-row-id={row.row_id}
            data-business-date={cell.business_date}
            className={`program-matrix-cell matrix-day badge-${cell.badge ?? "UNCOVERED"} ${cell.locked ? "locked" : ""} ${selected ? "selected" : ""}`}
            disabled={cell.locked || !interactive}
            aria-label={`${row.label} pe ${cell.business_date}: ${cell.badge ?? "fără acoperire"}${cell.locked ? ", blocat" : interactive ? ", activează pentru editare" : ", doar citire"}`}
            title={`${row.label} pe ${cell.business_date}: ${cell.display_name ?? "fără agent"} (${cell.badge ?? "UNCOVERED"})${cell.locked ? " · BLOCAT" : !interactive ? " · DOAR CITIRE" : ""}`}
            onClick={() => onCellClick?.(row.row_id, cell)}
          >
            {cell.badge === "NORMAL" || cell.badge === "EXTRA_HOME" || cell.badge === "EXTRA_OTHER" ? shortName(cell.display_name) : shortBadge(cell.badge)}
          </button>
        );
      })}
    </div>
  );
}

function shortName(displayName: string | null): string {
  if (!displayName) return "?";
  return displayName.split(/\s+/).filter(Boolean).slice(0, 2).map((token) => token[0]).join("").toUpperCase();
}

function shortBadge(badge: string | null): string {
  if (!badge) return "—";
  if (badge === "CONCEDIU") return "C";
  if (badge === "LIBER" || badge === "OFF") return "L";
  if (badge === "UNCOVERED") return "!";
  if (badge === "BLOCAT") return "×";
  return badge.slice(0, 2);
}

function labelBadge(badge: string): string {
  const labels: Record<string, string> = {
    NORMAL: "Normal",
    EXTRA_HOME: "Extra aici",
    EXTRA_OTHER: "Extra alt magazin",
    LIBER: "Liber",
    OFF: "Liber",
    CONCEDIU: "Concediu",
    UNCOVERED: "Neacoperit",
    BLOCAT: "Blocat",
  };
  return labels[badge] ?? badge;
}

function weekday(date: string): string {
  const [year, month, day] = date.split("-").map(Number);
  const labels = ["D", "L", "M", "M", "J", "V", "S"];
  return labels[new Date(Date.UTC(year, month - 1, day)).getUTCDay()] ?? "";
}

function formatDate(date: string): string {
  const [year, month, day] = date.split("-");
  return `${day}.${month}.${year}`;
}

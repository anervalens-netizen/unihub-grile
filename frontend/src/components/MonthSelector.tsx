import type { MonthSummary } from "../api/client";

export interface MonthSelectorProps {
  months: MonthSummary[];
  value: string | null;
  onChange: (monthId: string) => void;
  error: string | null;
}

export function MonthSelector({ months, value, onChange, error }: MonthSelectorProps) {
  if (error) {
    return (
      <p className="error" role="alert">
        {error}
      </p>
    );
  }
  if (months.length === 0) {
    return (
      <p className="muted">
        Nicio lună disponibilă pentru tenant. Folosește POST /ingest/fixture
        pentru a popula luna curentă.
      </p>
    );
  }
  return (
    <label className="month-selector">
      <span className="muted">Luna</span>
      <select
        value={value ?? ""}
        onChange={(event) => onChange(event.target.value)}
        aria-label="Alege luna"
      >
        {months.map((month) => (
          <option key={month.id} value={month.id}>
            {month.year}-{String(month.month).padStart(2, "0")} · {month.state} · rev {month.revision}
          </option>
        ))}
      </select>
    </label>
  );
}

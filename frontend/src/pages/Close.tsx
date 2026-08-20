import { useEffect, useState } from "react";
import {
  type ApiClient,
  type ChecklistItem,
  type CloseChecklist,
  type MonthSummary,
} from "../api/client";
import { MonthSelector } from "../components/MonthSelector";

export interface CloseProps {
  api: ApiClient;
  months: MonthSummary[];
  monthsError: string | null;
}

interface CloseEventOut {
  id: number;
  month_id: string;
  action: string;
  previous_state: string;
  new_state: string;
  revision_before: number;
  revision_after: number;
  actor_id: string;
  reason: string | null;
  blockers: string;
  previous_event_digest: string | null;
  event_digest: string;
}

export function Close({ api, months, monthsError }: CloseProps) {
  const [monthId, setMonthId] = useState<string | null>(months[0]?.id ?? null);
  const [checklist, setChecklist] = useState<CloseChecklist | null>(null);
  const [timeline, setTimeline] = useState<CloseEventOut[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [reason, setReason] = useState("");
  const [reopenError, setReopenError] = useState<string | null>(null);

  useEffect(() => {
    setMonthId((current) => current ?? months[0]?.id ?? null);
  }, [months]);

  useEffect(() => {
    if (!monthId) {
      setChecklist(null);
      setTimeline([]);
      return;
    }
    let cancelled = false;
    setError(null);
    Promise.all([
      api.get<CloseChecklist>(`/months/${monthId}/close-checklist`),
      api.get<CloseEventOut[]>(`/months/${monthId}/close-events`),
    ])
      .then(([checklistResponse, events]) => {
        if (cancelled) return;
        setChecklist(checklistResponse);
        setTimeline(events);
      })
      .catch((e: unknown) => {
        if (!cancelled) setError(String(e));
      });
    return () => {
      cancelled = true;
    };
  }, [api, monthId]);

  const trimmedReason = reason.trim();
  const reasonValid = trimmedReason.length >= 4;

  function handleReopen() {
    if (!monthId) return;
    setReopenError(null);
    api
      .post<CloseChecklist>(`/months/${monthId}/reopen-admin`, {
        reason: trimmedReason,
      })
      .then((response) => {
        setChecklist(response);
        setReason("");
        return api.get<CloseEventOut[]>(`/months/${monthId}/close-events`);
      })
      .then((events) => setTimeline(events))
      .catch((e: unknown) => {
        const message = e instanceof Error ? e.message : String(e);
        setReopenError(message);
      });
  }

  return (
    <section className="card" aria-label="Close">
      <header className="card-header">
        <h2>Close</h2>
        <MonthSelector
          months={months}
          value={monthId}
          onChange={setMonthId}
          error={monthsError}
        />
      </header>
      {error && (
        <p className="error" role="alert">
          {error}
        </p>
      )}
      {checklist && (
        <div className="close-grid">
          <section className="close-checklist" aria-label="Checklist">
            <h3>Checklist</h3>
            <p className="muted">
              Stare: <strong>{checklist.state}</strong> · rev{" "}
              <strong>{checklist.revision}</strong> · expected_revision{" "}
              <strong>{checklist.expected_revision}</strong>
            </p>
            {checklist.blockers.length === 0 ? (
              <p className="muted">Nicio condiție blocantă detectată.</p>
            ) : (
              <ul>
                {checklist.blockers.map((item, index) => (
                  <ChecklistRow key={`${item.code}-${index}`} item={item} />
                ))}
              </ul>
            )}
          </section>
          <section className="close-reopen" aria-label="Reopen admin">
            <h3>Reopen (admin-only)</h3>
            <p className="muted">
              Reopen este admin-only și necesită un motiv de minim 4 caractere.
              Tranziția este jurnalizată în audit-ul imuabil.
            </p>
            <label>
              <span className="muted">Motiv</span>
              <textarea
                value={reason}
                onChange={(event) => setReason(event.target.value)}
                rows={3}
                aria-invalid={!reasonValid && reason.length > 0}
                aria-describedby="reopen-reason-help"
              />
            </label>
            <small
              id="reopen-reason-help"
              className={reasonValid || reason.length === 0 ? "muted" : "error-text"}
            >
              {reason.length === 0
                ? "Scrie motivul."
                : reasonValid
                  ? "Motiv valid."
                  : "Minim 4 caractere."}
            </small>
            <button
              type="button"
              className="primary"
              disabled={!reasonValid}
              onClick={handleReopen}
            >
              Reopen
            </button>
            {reopenError && (
              <p className="error" role="alert">
                {reopenError}
              </p>
            )}
          </section>
          <section className="close-timeline" aria-label="Audit">
            <h3>Audit timeline</h3>
            {timeline.length === 0 ? (
              <p className="muted">Niciun eveniment de close/reopen încă.</p>
            ) : (
              <ol className="audit-timeline">
                {timeline.map((event) => (
                  <li key={event.id}>
                    <strong>{event.action}</strong>{" "}
                    {event.previous_state} → {event.new_state} · rev{" "}
                    {event.revision_before} → {event.revision_after} ·{" "}
                    {event.actor_id}
                    {event.reason && <> · motiv: {event.reason}</>}
                    <br />
                    <code className="muted">{event.event_digest}</code>
                  </li>
                ))}
              </ol>
            )}
          </section>
        </div>
      )}
    </section>
  );
}

interface ChecklistRowProps {
  item: ChecklistItem;
}

function ChecklistRow({ item }: ChecklistRowProps) {
  return (
    <li>
      <span className={`severity-chip severity-${item.severity}`}>S{item.severity}</span>
      <strong>{item.title}</strong>
      <p className="muted">{item.detail}</p>
      <p className="muted">{item.code}</p>
    </li>
  );
}

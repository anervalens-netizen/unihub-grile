import { useEffect, useState } from "react";
import {
  type ApiClient,
  type ChecklistItem,
  type CloseChecklist,
  type CloseOutcome,
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

interface AuditBlocker {
  code: string;
  store_id: string | null;
  person_id: string | null;
  business_date: string | null;
  message: string;
}

interface CloseState {
  checklist: CloseChecklist;
  timeline: CloseEventOut[];
}

function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}

function isStaleRevision(error: unknown): boolean {
  if (typeof error !== "object" || error === null) return false;
  const candidate = error as { status?: unknown; code?: unknown };
  return candidate.status === 409 && candidate.code === "STALE_REVISION";
}

function loadCloseState(api: ApiClient, monthId: string): Promise<CloseState> {
  return Promise.all([
    api.get<CloseChecklist>(`/months/${monthId}/close-checklist`),
    api.get<CloseEventOut[]>(`/months/${monthId}/close-events`),
  ]).then(([checklist, timeline]) => ({ checklist, timeline }));
}

function parseAuditBlockers(raw: string): AuditBlocker[] {
  try {
    const parsed: unknown = JSON.parse(raw);
    if (!Array.isArray(parsed)) return [];
    return parsed
      .filter((item): item is Record<string, unknown> => typeof item === "object" && item !== null)
      .map((item) => ({
        code: typeof item.code === "string" ? item.code : "UNKNOWN",
        store_id: typeof item.store_id === "string" ? item.store_id : null,
        person_id: typeof item.person_id === "string" ? item.person_id : null,
        business_date: typeof item.business_date === "string" ? item.business_date : null,
        message: typeof item.message === "string" ? item.message : "Fără detalii stocate",
      }));
  } catch {
    return [];
  }
}

export function Close({ api, months, monthsError }: CloseProps) {
  const [monthId, setMonthId] = useState<string | null>(months[0]?.id ?? null);
  const [checklist, setChecklist] = useState<CloseChecklist | null>(null);
  const [timeline, setTimeline] = useState<CloseEventOut[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [reason, setReason] = useState("");
  const [reopenError, setReopenError] = useState<string | null>(null);
  const [closeError, setCloseError] = useState<string | null>(null);
  const [confirmClose, setConfirmClose] = useState(false);
  const [closing, setClosing] = useState(false);

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
    setCloseError(null);
    setReopenError(null);
    setConfirmClose(false);
    loadCloseState(api, monthId)
      .then((state) => {
        if (cancelled) return;
        setChecklist(state.checklist);
        setTimeline(state.timeline);
      })
      .catch((e: unknown) => {
        if (!cancelled) setError(errorMessage(e));
      });
    return () => {
      cancelled = true;
    };
  }, [api, monthId]);

  const trimmedReason = reason.trim();
  const reasonValid = trimmedReason.length >= 4;
  const blockingCount = checklist?.blockers.filter((item) => item.blocking).length ?? 0;
  const advisoryCount = checklist?.blockers.length ? checklist.blockers.length - blockingCount : 0;
  const canReopen = checklist?.state === "CLOSED";

  async function handleClose() {
    if (!monthId || !checklist || checklist.blockers.some((item) => item.blocking)) return;
    setClosing(true);
    setCloseError(null);
    try {
      await api.post<CloseOutcome>(`/months/${monthId}/close`, {
        expected_revision: checklist.expected_revision,
      });
      const state = await loadCloseState(api, monthId);
      setChecklist(state.checklist);
      setTimeline(state.timeline);
      setConfirmClose(false);
    } catch (e: unknown) {
      if (isStaleRevision(e)) {
        try {
          const state = await loadCloseState(api, monthId);
          setChecklist(state.checklist);
          setTimeline(state.timeline);
          setConfirmClose(false);
          setCloseError(
            `Revizia s-a schimbat. Checklist-ul a fost reîncărcat la revizia ${state.checklist.expected_revision}; verifică din nou înainte de închidere.`,
          );
        } catch (refreshError: unknown) {
          setCloseError(
            `Revizia s-a schimbat, iar reîncărcarea checklist-ului a eșuat: ${errorMessage(refreshError)}`,
          );
        }
      } else {
        setCloseError(errorMessage(e));
      }
    } finally {
      setClosing(false);
    }
  }

  async function handleReopen() {
    if (!monthId || !canReopen || !reasonValid) return;
    setReopenError(null);
    try {
      const response = await api.post<CloseChecklist>(`/months/${monthId}/reopen-admin`, {
        reason: trimmedReason,
      });
      setChecklist(response);
      setReason("");
      const events = await api.get<CloseEventOut[]>(`/months/${monthId}/close-events`);
      setTimeline(events);
    } catch (e: unknown) {
      setReopenError(errorMessage(e));
    }
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
            <p className="muted">
              Condiții blocante: <strong>{blockingCount}</strong> · avertismente: <strong>{advisoryCount}</strong>
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
          <section className="close-action" aria-label="Închidere lună">
            <h3>Închidere lună</h3>
            <p className="muted">Confirmă explicit după verificarea checklist-ului. Revizia trimisă este {checklist.expected_revision}.</p>
            {checklist.blockers.some((item) => item.blocking) && <p className="error-text">Închiderea este blocată până la rezolvarea tuturor condițiilor blocante.</p>}
            {!confirmClose ? (
              <button type="button" className="primary" disabled={checklist.blockers.some((item) => item.blocking) || checklist.state === "CLOSED"} onClick={() => setConfirmClose(true)}>Pregătește închiderea</button>
            ) : (
              <div role="alertdialog" aria-label="Confirmare închidere">
                <p>Închizi luna la revizia {checklist.expected_revision}? Această acțiune este auditabilă și îngheață luna.</p>
                <button type="button" className="primary" onClick={handleClose} disabled={closing}>{closing ? "Închid…" : "Confirmă închiderea"}</button>
                <button type="button" onClick={() => setConfirmClose(false)} disabled={closing}>Renunță</button>
              </div>
            )}
            {closeError && <p className="error" role="alert">{closeError}</p>}
          </section>
          <section className="close-reopen" aria-label="Reopen admin">
            <h3>Reopen (admin-only)</h3>
            <p className="muted">
              Reopen este admin-only, disponibil numai pentru o lună CLOSED și necesită un motiv de minim 4 caractere.
              Tranziția este jurnalizată în audit-ul imuabil.
            </p>
            <label>
              <span className="muted">Motiv</span>
              <textarea
                value={reason}
                onChange={(event) => setReason(event.target.value)}
                rows={3}
                disabled={!canReopen}
                aria-invalid={canReopen && !reasonValid && reason.length > 0}
                aria-describedby="reopen-reason-help"
              />
            </label>
            <small
              id="reopen-reason-help"
              className={reasonValid || reason.length === 0 || !canReopen ? "muted" : "error-text"}
            >
              {!canReopen
                ? "Reopen devine disponibil când luna este CLOSED."
                : reason.length === 0
                  ? "Scrie motivul."
                  : reasonValid
                    ? "Motiv valid."
                    : "Minim 4 caractere."}
            </small>
            <button
              type="button"
              className="primary"
              disabled={!canReopen || !reasonValid}
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
                {timeline.map((event) => {
                  const auditBlockers = parseAuditBlockers(event.blockers);
                  return (
                    <li key={event.id}>
                      <strong>{event.action}</strong>{" "}
                      <span className="muted">#{event.id}</span> · {event.previous_state} → {event.new_state} · rev{" "}
                      {event.revision_before} → {event.revision_after} · {event.actor_id}
                      {event.reason && <> · motiv: {event.reason}</>}
                      {auditBlockers.length > 0 ? (
                        <details>
                          <summary>Snapshot validare ({auditBlockers.length})</summary>
                          <ul>
                            {auditBlockers.map((blocker, index) => (
                              <li key={`${event.id}-${blocker.code}-${index}`}>
                                <strong>{blocker.code}</strong> — {blocker.message}
                                <div className="muted">
                                  {blocker.business_date ?? "—"} · {blocker.store_id ?? "—"} · {blocker.person_id ?? "—"}
                                </div>
                              </li>
                            ))}
                          </ul>
                        </details>
                      ) : (
                        <p className="muted">Snapshot validare: fără blockers.</p>
                      )}
                      <p className="muted">
                        Lanț audit: precedent <code>{event.previous_event_digest ?? "GENESIS"}</code> → curent <code>{event.event_digest}</code>
                      </p>
                    </li>
                  );
                })}
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
      <strong>{item.title}</strong>{" "}
      <span className={`badge ${item.blocking ? "badge-BLOCANT" : ""}`}>
        {item.blocking ? "blocant" : "avertisment"}
      </span>
      <p className="muted">{item.detail}</p>
      <p className="muted">{item.code}</p>
    </li>
  );
}

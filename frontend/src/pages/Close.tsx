import { useEffect, useState } from "react";
import {
  type ApiClient,
  type ChecklistItem,
  type CloseChecklist,
  type CloseOutcome,
  type MonthSummary,
} from "../api/client";
import { isolatedRead } from "../api/isolatedRead";
import { hasCapability, type Capability } from "../capabilities";
import { MonthSelector } from "../components/MonthSelector";
import { LoadingState, RequestError, requestErrorMessage } from "../components/RequestState";
import { auditActionLabel, monthStateLabel } from "../operationalStatus";

export interface CloseProps {
  api: ApiClient;
  months: MonthSummary[];
  monthsError: string | null;
  capabilities: ReadonlySet<Capability>;
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
  timelineError: string | null;
}

function isStaleRevision(error: unknown): boolean {
  if (typeof error !== "object" || error === null) return false;
  const candidate = error as { status?: unknown; code?: unknown };
  return candidate.status === 409 && candidate.code === "STALE_REVISION";
}

async function loadCloseState(api: ApiClient, monthId: string): Promise<CloseState> {
  const [checklist, timelineRead] = await Promise.all([
    api.get<CloseChecklist>(`/months/${monthId}/close-checklist`),
    isolatedRead(api.get<CloseEventOut[]>(`/months/${monthId}/close-events`)),
  ]);
  return {
    checklist,
    timeline: timelineRead.value ?? [],
    timelineError: timelineRead.error,
  };
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

export function Close({ api, months, monthsError, capabilities }: CloseProps) {
  const [monthId, setMonthId] = useState<string | null>(months[0]?.id ?? null);
  const [checklist, setChecklist] = useState<CloseChecklist | null>(null);
  const [timeline, setTimeline] = useState<CloseEventOut[]>([]);
  const [timelineError, setTimelineError] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [reloadToken, setReloadToken] = useState(0);
  const [reason, setReason] = useState("");
  const [reopenError, setReopenError] = useState<string | null>(null);
  const [closeError, setCloseError] = useState<string | null>(null);
  const [confirmClose, setConfirmClose] = useState(false);
  const [closing, setClosing] = useState(false);
  const canCloseAction = hasCapability(capabilities, "month.close");
  const canReopenAction = hasCapability(capabilities, "month.reopen");

  useEffect(() => {
    setMonthId((current) => current ?? months[0]?.id ?? null);
  }, [months]);

  useEffect(() => {
    if (!monthId) {
      setChecklist(null);
      setTimeline([]);
      setTimelineError(null);
      setLoading(false);
      return;
    }
    let cancelled = false;
    setChecklist(null);
    setTimeline([]);
    setError(null);
    setTimelineError(null);
    setCloseError(null);
    setReopenError(null);
    setConfirmClose(false);
    setLoading(true);
    loadCloseState(api, monthId)
      .then((state) => {
        if (cancelled) return;
        setChecklist(state.checklist);
        setTimeline(state.timeline);
        setTimelineError(state.timelineError);
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
  }, [api, monthId, reloadToken]);

  const retry = () => setReloadToken((value) => value + 1);
  const trimmedReason = reason.trim();
  const reasonValid = trimmedReason.length >= 4;
  const blockingCount = checklist?.blockers.filter((item) => item.blocking).length ?? 0;
  const advisoryCount = checklist?.blockers.length ? checklist.blockers.length - blockingCount : 0;
  const canReopenCurrentMonth = canReopenAction && checklist?.state === "CLOSED";

  function applyCloseState(state: CloseState) {
    setChecklist(state.checklist);
    setTimeline(state.timeline);
    setTimelineError(state.timelineError);
  }

  async function handleClose() {
    if (!canCloseAction || !monthId || !checklist || checklist.blockers.some((item) => item.blocking)) return;
    setClosing(true);
    setCloseError(null);
    try {
      await api.post<CloseOutcome>(`/months/${monthId}/close`, {
        expected_revision: checklist.expected_revision,
      });
      const state = await loadCloseState(api, monthId);
      applyCloseState(state);
      setConfirmClose(false);
    } catch (e: unknown) {
      if (isStaleRevision(e)) {
        try {
          const state = await loadCloseState(api, monthId);
          applyCloseState(state);
          setConfirmClose(false);
          setCloseError(
            `Revizia s-a schimbat. Validările au fost reîncărcate la revizia ${state.checklist.expected_revision}; verifică din nou înainte de închidere.`,
          );
        } catch (refreshError: unknown) {
          setCloseError(
            `Revizia s-a schimbat, iar reîncărcarea validărilor a eșuat: ${requestErrorMessage(refreshError)}`,
          );
        }
      } else {
        setCloseError(requestErrorMessage(e));
      }
    } finally {
      setClosing(false);
    }
  }

  async function handleReopen() {
    if (!canReopenCurrentMonth || !monthId || !reasonValid) return;
    setReopenError(null);
    try {
      const response = await api.post<CloseChecklist>(`/months/${monthId}/reopen-admin`, {
        reason: trimmedReason,
      });
      setChecklist(response);
      setReason("");
      const timelineRead = await isolatedRead(api.get<CloseEventOut[]>(`/months/${monthId}/close-events`));
      setTimeline(timelineRead.value ?? []);
      setTimelineError(timelineRead.error);
    } catch (e: unknown) {
      setReopenError(requestErrorMessage(e));
    }
  }

  return (
    <section className="card" aria-label="Management lună">
      <header className="card-header">
        <h2>Management lună</h2>
        <MonthSelector
          months={months}
          value={monthId}
          onChange={setMonthId}
          error={monthsError}
        />
      </header>
      {error && <RequestError message={error} onRetry={retry} />}
      {loading && <LoadingState>Încarc validările și istoricul de management…</LoadingState>}
      {!loading && !error && checklist && (
        <div className="close-grid">
          <section className="close-checklist" aria-label="Validări">
            <h3>Validări</h3>
            <p className="muted">
              Stare: <strong>{monthStateLabel(checklist.state)}</strong> · rev. <strong>{checklist.revision}</strong> · revizia așteptată <strong>{checklist.expected_revision}</strong>
            </p>
            <p className="muted">
              Condiții blocante: <strong>{blockingCount}</strong> · avertismente: <strong>{advisoryCount}</strong>
            </p>
            {checklist.blockers.length === 0 ? (
              <div className="empty-state"><strong>Nicio condiție blocantă.</strong><span>Validările curente nu conțin condiții blocante sau avertismente.</span></div>
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
            {!canCloseAction ? (
              <p className="muted">Sesiunea curentă este doar pentru consultare; închiderea lunii nu este permisă.</p>
            ) : (
              <>
                <p className="muted">Confirmă explicit după verificarea validărilor. Revizia trimisă este {checklist.expected_revision}.</p>
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
              </>
            )}
          </section>
          <section className="close-reopen" aria-label="Redeschidere lună">
            <h3>Redeschidere lună</h3>
            {!canReopenAction ? (
              <p className="muted">Sesiunea curentă este doar pentru consultare; redeschiderea lunii nu este permisă.</p>
            ) : (
              <>
                <p className="muted">
                  Redeschiderea este disponibilă numai pentru o lună închisă și necesită un motiv de minim 4 caractere.
                  Tranziția este jurnalizată în istoricul de audit.
                </p>
                <label>
                  <span className="muted">Motiv</span>
                  <textarea
                    value={reason}
                    onChange={(event) => setReason(event.target.value)}
                    rows={3}
                    disabled={!canReopenCurrentMonth}
                    aria-invalid={canReopenCurrentMonth && !reasonValid && reason.length > 0}
                    aria-describedby="reopen-reason-help"
                  />
                </label>
                <small
                  id="reopen-reason-help"
                  className={reasonValid || reason.length === 0 || !canReopenCurrentMonth ? "muted" : "error-text"}
                >
                  {checklist.state !== "CLOSED"
                    ? "Redeschiderea devine disponibilă când luna este închisă."
                    : reason.length === 0
                      ? "Scrie motivul."
                      : reasonValid
                        ? "Motiv valid."
                        : "Minim 4 caractere."}
                </small>
                <button
                  type="button"
                  className="primary"
                  disabled={!canReopenCurrentMonth || !reasonValid}
                  onClick={handleReopen}
                >
                  Redeschide
                </button>
                {reopenError && (
                  <p className="error" role="alert">
                    {reopenError}
                  </p>
                )}
              </>
            )}
          </section>
          <section className="close-timeline" aria-label="Istoric audit">
            <h3>Istoric audit</h3>
            {timelineError && <RequestError message={`Istoricul audit este indisponibil: ${timelineError}`} onRetry={retry} />}
            {!timelineError && timeline.length === 0 ? (
              <div className="empty-state"><strong>Fără evenimente de audit.</strong><span>Nu există încă evenimente de închidere sau redeschidere pentru luna selectată.</span></div>
            ) : (
              <ol className="audit-timeline">
                {timeline.map((event) => {
                  const auditBlockers = parseAuditBlockers(event.blockers);
                  return (
                    <li key={event.id}>
                      <strong>{auditActionLabel(event.action)}</strong>{" "}
                      <span className="muted">#{event.id}</span> · {monthStateLabel(event.previous_state)} → {monthStateLabel(event.new_state)} · rev. {event.revision_before} → {event.revision_after} · {event.actor_id}
                      {event.reason && <> · motiv: {event.reason}</>}
                      {auditBlockers.length > 0 ? (
                        <details>
                          <summary>Validări la momentul acțiunii ({auditBlockers.length})</summary>
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
                        <p className="muted">Validări la momentul acțiunii: fără condiții.</p>
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

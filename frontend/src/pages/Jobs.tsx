import { useCallback, useEffect, useState } from "react";
import type { ApiClient } from "../api/client";
import { LoadingState, RequestError, requestErrorMessage } from "../components/RequestState";

type QueueState = "QUEUED" | "RETRY" | "RUNNING" | "FAILED" | "DONE";

interface JobCounts {
  queued: number;
  retrying: number;
  running: number;
  failed: number;
  done: number;
}

interface JobDiagnostic {
  id: number;
  kind: string;
  state: QueueState;
  attempts: number;
  max_attempts: number;
  run_after: string;
  locked_at: string | null;
  created_at: string;
  updated_at: string;
  last_error: string | null;
  month_id: string | null;
  store_ids: string[];
}

interface JobDiagnostics {
  counts: JobCounts;
  jobs: JobDiagnostic[];
  terminal_history_limit: number;
}

export interface JobsProps {
  api: ApiClient;
}

export function Jobs({ api }: JobsProps) {
  const [diagnostics, setDiagnostics] = useState<JobDiagnostics | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [refreshedAt, setRefreshedAt] = useState<Date | null>(null);

  const load = useCallback(() => {
    setLoading(true);
    setError(null);
    return api
      .get<JobDiagnostics>("/worker/jobs/diagnostics?terminal_limit=50")
      .then((response) => {
        setDiagnostics(response);
        setRefreshedAt(new Date());
      })
      .catch((e: unknown) => {
        setError(requestErrorMessage(e));
      })
      .finally(() => setLoading(false));
  }, [api]);

  useEffect(() => {
    void load();
  }, [load]);

  return (
    <div className="command-page">
      <section className="command-hero">
        <div>
          <span className="eyebrow">OPERAȚIUNI / ASYNC</span>
          <h2>Joburi și sincronizări</h2>
          <p>Coada activă, retry-urile și ultimele rezultate ale operațiunilor asincrone.</p>
        </div>
        <div className="command-hero-actions">
          {refreshedAt && <span className="context-pill">Actualizat {formatTime(refreshedAt)}</span>}
          <button type="button" className="button-secondary" onClick={() => void load()} disabled={loading}>
            {loading ? "Actualizez…" : "Actualizează"}
          </button>
        </div>
      </section>

      {error && <RequestError message={error} onRetry={() => void load()} />}
      {error && diagnostics && <p className="muted">Datele afișate sunt ultima stare încărcată cu succes.</p>}
      {!diagnostics && loading && <LoadingState>Încarc starea joburilor…</LoadingState>}

      {diagnostics && (
        <>
          <section className="kpi-strip" aria-label="Stare joburi">
            <Metric label="În așteptare" value={diagnostics.counts.queued} detail="joburi noi" tone="neutral" />
            <Metric label="Retry" value={diagnostics.counts.retrying} detail="așteaptă următoarea încercare" tone={diagnostics.counts.retrying > 0 ? "warn" : "ok"} />
            <Metric label="Rulează" value={diagnostics.counts.running} detail="execuție activă" tone="neutral" />
            <Metric label="Eșuate" value={diagnostics.counts.failed} detail="istoric recent" tone={diagnostics.counts.failed > 0 ? "err" : "ok"} />
            <Metric label="Finalizate" value={diagnostics.counts.done} detail={`ultimele ${diagnostics.terminal_history_limit}`} tone="ok" />
          </section>

          <section className="panel">
            <div className="panel-heading">
              <div><span className="eyebrow">COADĂ / ISTORIC</span><h3>Stare operațională</h3></div>
              <span className="count-badge">{diagnostics.jobs.length}</span>
            </div>

            {diagnostics.jobs.length === 0 ? (
              <div className="empty-state"><strong>Nicio activitate recentă.</strong><span>Nu există joburi vizibile în scope-ul curent.</span></div>
            ) : (
              <div className="retail-overview-table">
                <div className="retail-overview-row head">
                  <span>Job</span><span>Stare</span><span>Scope</span><span>Încercări</span><span>Programare</span><span>Eroare</span>
                </div>
                {diagnostics.jobs.map((job) => (
                  <div className="retail-overview-row" key={job.id}>
                    <span><strong>#{job.id}</strong><br /><small>{friendlyKind(job.kind)}</small></span>
                    <span className="retail-status">
                      <span className={`status-dot status-${stateTone(job.state)}`} />
                      {stateLabel(job.state)}
                    </span>
                    <span title={job.store_ids.join(", ")}>{scopeLabel(job)}</span>
                    <span>{job.attempts}/{job.max_attempts}</span>
                    <span>{scheduleLabel(job)}</span>
                    <span title={job.last_error ?? ""}>{job.last_error ? truncate(job.last_error, 42) : "—"}</span>
                  </div>
                ))}
              </div>
            )}
          </section>
        </>
      )}
    </div>
  );
}

interface MetricProps {
  label: string;
  value: number;
  detail: string;
  tone: "ok" | "warn" | "err" | "neutral";
}

function Metric({ label, value, detail, tone }: MetricProps) {
  return (
    <article className={`metric-card metric-${tone}`}>
      <span>{label}</span>
      <strong>{value}</strong>
      <small>{detail}</small>
    </article>
  );
}

function stateTone(state: QueueState): "online" | "checking" | "offline" {
  if (state === "FAILED") return "offline";
  if (state === "RETRY" || state === "RUNNING" || state === "QUEUED") return "checking";
  return "online";
}

function stateLabel(state: QueueState): string {
  const labels: Record<QueueState, string> = {
    QUEUED: "În așteptare",
    RETRY: "Retry",
    RUNNING: "Rulează",
    FAILED: "Eșuat",
    DONE: "Finalizat",
  };
  return labels[state];
}

function friendlyKind(kind: string): string {
  return kind.replaceAll("_", " ").toLowerCase();
}

function scopeLabel(job: JobDiagnostic): string {
  if (job.store_ids.length === 0) return job.month_id ? "lună" : "tenant";
  if (job.store_ids.length === 1) return job.store_ids[0] ?? "magazin";
  return `${job.store_ids.length} magazine`;
}

function scheduleLabel(job: JobDiagnostic): string {
  if (job.state === "RUNNING") return job.locked_at ? `din ${formatIsoTime(job.locked_at)}` : "activ";
  if (job.state === "RETRY" || job.state === "QUEUED") return formatIsoTime(job.run_after);
  return formatIsoTime(job.updated_at);
}

function formatIsoTime(value: string): string {
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? "—" : formatTime(date);
}

function formatTime(value: Date): string {
  return new Intl.DateTimeFormat("ro-RO", { hour: "2-digit", minute: "2-digit" }).format(value);
}

function truncate(value: string, max: number): string {
  return value.length <= max ? value : `${value.slice(0, max - 1)}…`;
}

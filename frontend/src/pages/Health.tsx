import type { HealthReport } from "../api/client";
import type { ApiClient } from "../api/client";

export interface HealthProps {
  health: HealthReport | null;
  error: string | null;
  api: ApiClient;
}

export function Health({ health, error }: HealthProps) {
  return (
    <section className="card" aria-label="Backend health">
      <h2>Backend health</h2>
      {error && (
        <p className="error" role="alert">
          Could not reach backend: {error}
        </p>
      )}
      {health && (
        <dl>
          <dt>Status</dt>
          <dd className={`status status-${health.status}`}>{health.status}</dd>
          <dt>Database reachable</dt>
          <dd>{health.database ? "yes" : "no"}</dd>
          <dt>Schema version</dt>
          <dd>{health.schema_version}</dd>
          <dt>App version</dt>
          <dd>{health.app_version}</dd>
        </dl>
      )}
    </section>
  );
}
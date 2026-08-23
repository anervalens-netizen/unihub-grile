export type QueueState = "QUEUED" | "RETRY" | "RUNNING" | "FAILED" | "DONE";
export type StatusDotTone = "online" | "checking" | "offline";

export function monthStateLabel(state: string): string {
  const labels: Record<string, string> = {
    OPEN: "Deschisă",
    CLOSED: "Închisă",
    REOPENED: "Redeschisă",
  };
  return labels[state] ?? humanizeState(state);
}

export function auditActionLabel(action: string): string {
  const labels: Record<string, string> = {
    CLOSE: "Închidere",
    REOPEN: "Redeschidere",
  };
  return labels[action] ?? humanizeState(action);
}

export function queueStateLabel(state: QueueState): string {
  const labels: Record<QueueState, string> = {
    QUEUED: "În așteptare",
    RETRY: "Reîncercare",
    RUNNING: "În execuție",
    FAILED: "Eșuat",
    DONE: "Finalizat",
  };
  return labels[state];
}

export function queueStateTone(state: QueueState): StatusDotTone {
  if (state === "FAILED") return "offline";
  if (state === "DONE") return "online";
  return "checking";
}

export function dataFreshnessLabel(isFresh: boolean): string {
  return isFresh ? "Actualizat" : "Neactualizat";
}

export function healthStateLabel(tone: StatusDotTone): string {
  if (tone === "online") return "Disponibil";
  if (tone === "offline") return "Indisponibil";
  return "Verificare";
}

function humanizeState(value: string): string {
  const normalized = value.trim().replaceAll("_", " ").toLocaleLowerCase("ro-RO");
  return normalized ? `${normalized[0]?.toLocaleUpperCase("ro-RO") ?? ""}${normalized.slice(1)}` : "—";
}

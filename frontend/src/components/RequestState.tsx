export function requestErrorMessage(error: unknown): string {
  if (typeof error === "object" && error !== null) {
    const candidate = error as { status?: unknown; code?: unknown; message?: unknown };
    if (candidate.status === 403) return "Acces refuzat pentru această operațiune.";
    if (candidate.status === 409 && candidate.code === "STALE_REVISION") {
      return "Datele s-au schimbat între timp. Reîncarcă starea curentă înainte de a continua.";
    }
    if (candidate.status === 409 && candidate.code === "MONTH_CLOSED") {
      return "Luna este închisă și nu mai acceptă modificări.";
    }
    if (typeof candidate.message === "string" && candidate.message) return candidate.message;
  }
  return error instanceof Error ? error.message : String(error);
}

export function RequestError({ message, onRetry }: { message: string; onRetry?: () => void }) {
  return (
    <div className="error" role="alert">
      <span>{message}</span>
      {onRetry && (
        <button type="button" className="button-secondary" onClick={onRetry}>
          Reîncearcă
        </button>
      )}
    </div>
  );
}

export function LoadingState({ children }: { children: string }) {
  return <div className="loading-panel" role="status">{children}</div>;
}

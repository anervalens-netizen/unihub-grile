import { requestErrorMessage } from "../api/requestError";

export { requestErrorMessage };

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

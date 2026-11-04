import { AlertCircle, CheckCircle2 } from "lucide-react";

export function ReadError({
  message,
  onRetry,
}: {
  message: string;
  onRetry: () => void;
}) {
  return (
    <div className="error-banner cm-feedback" role="alert">
      <AlertCircle aria-hidden="true" size={20} />
      <span>{message}</span>
      <button className="secondary" onClick={onRetry}>
        Retry
      </button>
    </div>
  );
}

export function SuccessNotice({ children }: { children: string }) {
  return (
    <div className="success-banner cm-feedback" role="status">
      <CheckCircle2 aria-hidden="true" size={20} />
      <span>{children}</span>
    </div>
  );
}

export function WorkspaceSkeleton({
  label = "Loading workspace…",
}: {
  label?: string;
}) {
  return (
    <section
      className="workspace-skeleton"
      role="status"
      aria-busy="true"
      aria-label={label}
    >
      <span className="sr-only">{label}</span>
      {[0, 1, 2].map((item) => (
        <div className="skeleton-card" key={item} aria-hidden="true">
          <span />
          <span />
          <span />
        </div>
      ))}
    </section>
  );
}

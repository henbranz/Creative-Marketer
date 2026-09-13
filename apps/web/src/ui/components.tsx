import type { ButtonHTMLAttributes, ReactNode } from "react";

import { statusVariant, type StatusVariant } from "./status";

function classes(...values: Array<string | undefined | false>) {
  return values.filter(Boolean).join(" ");
}

export function BrandMark({ compact = false }: { compact?: boolean }) {
  return (
    <span
      className={classes("cm-brand-mark", compact && "compact")}
      aria-hidden="true"
    >
      <span>C</span>
      <i />
    </span>
  );
}

export function BrandLockup({ compact = false }: { compact?: boolean }) {
  return (
    <span className={classes("cm-brand-lockup", compact && "compact")}>
      <BrandMark compact={compact} />
      <span>
        <strong>Creative Manager</strong>
        {!compact && <small>Creative intelligence for modern growth.</small>}
      </span>
    </span>
  );
}

export function Button({
  variant = "secondary",
  className,
  ...props
}: ButtonHTMLAttributes<HTMLButtonElement> & {
  variant?: "primary" | "secondary" | "ghost" | "danger";
}) {
  return (
    <button className={classes("cm-button", variant, className)} {...props} />
  );
}

export function StatusBadge({
  status,
  label,
  variant,
}: {
  status: string;
  label?: string;
  variant?: StatusVariant;
}) {
  const resolved = variant ?? statusVariant(status);
  return (
    <span className={classes("cm-status-badge", resolved)}>
      <i aria-hidden="true" />
      {label ?? status.replaceAll("_", " ")}
    </span>
  );
}

export function EmptyState({
  icon,
  title,
  children,
  action,
}: {
  icon: string;
  title: string;
  children: ReactNode;
  action?: ReactNode;
}) {
  return (
    <section className="cm-empty-state">
      <span className="cm-empty-icon" aria-hidden="true">
        {icon}
      </span>
      <div>
        <h2>{title}</h2>
        <div className="cm-empty-copy">{children}</div>
      </div>
      {action}
    </section>
  );
}

export function Metric({
  label,
  value,
  note,
}: {
  label: string;
  value: ReactNode;
  note?: string;
}) {
  return (
    <article className="cm-metric">
      <span>{label}</span>
      <strong>{value}</strong>
      {note && <small>{note}</small>}
    </article>
  );
}

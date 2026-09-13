import type { ButtonHTMLAttributes, ReactNode } from "react";
import Image from "next/image";

import { statusVariant, type StatusVariant } from "./status";

function classes(...values: Array<string | undefined | false>) {
  return values.filter(Boolean).join(" ");
}

export function BrandMark({ compact = false }: { compact?: boolean }) {
  const artwork = compact
    ? {
        src: "/brand/creative-manager-app-icon.png",
        width: 512,
        height: 512,
      }
    : {
        src: "/brand/creative-manager-mark.png",
        width: 640,
        height: 546,
      };

  return (
    <span
      className={classes("cm-brand-mark", compact && "compact")}
      aria-hidden="true"
    >
      <Image
        src={artwork.src}
        alt=""
        width={artwork.width}
        height={artwork.height}
        sizes={compact ? "34px" : "50px"}
      />
    </span>
  );
}

export function BrandLockup({ compact = false }: { compact?: boolean }) {
  if (!compact) {
    return (
      <span
        className="cm-brand-lockup"
        role="img"
        aria-label="Creative Manager"
      >
        <Image
          src="/brand/creative-manager-lockup-light.png"
          alt=""
          width={1320}
          height={430}
          sizes="(max-width: 520px) 82vw, 390px"
          priority
        />
      </span>
    );
  }

  return (
    <span className="cm-brand-lockup compact">
      <BrandMark compact />
      <strong>Creative Manager</strong>
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

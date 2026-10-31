import type { ButtonHTMLAttributes, ReactNode } from "react";
import Image from "next/image";

import { statusVariant, type StatusVariant } from "./status";

function classes(...values: Array<string | undefined | false>) {
  return values.filter(Boolean).join(" ");
}

export function NavigationIcon({ name }: { name: string }) {
  const paths: Record<string, string> = {
    "Command Center": "M3 10 12 3l9 7M5 9v12h5v-7h4v7h5V9",
    Products: "m12 3 9 5v9l-9 5-9-5V8l9-5Zm0 9v10M3 8l9 4 9-4",
    Agents: "M8 3v3m8-3v3M5 6h14v14H5V6Zm3 6h1m6 0h1m-7 4h6",
    Approvals: "M9 3h6v4H9V3Zm-3 2H4v17h16V5h-2M8 14l3 3 5-6",
    Activity: "M3 12h4l3-8 4 16 3-8h4",
    Settings: "M4 6h16M4 12h16M4 18h16M8 3v6m8 0v6M9 15v6",
  };
  return (
    <svg
      className="navigation-icon"
      viewBox="0 0 24 24"
      width="20"
      height="20"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.6"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
      focusable="false"
    >
      <path d={paths[name] ?? paths.Products} />
    </svg>
  );
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

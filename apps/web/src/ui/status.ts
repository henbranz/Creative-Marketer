export type StatusVariant =
  "neutral" | "info" | "success" | "warning" | "danger" | "accent";

const variants: Record<string, StatusVariant> = {
  active: "success",
  approved: "success",
  approved_for_production: "success",
  approved_for_publishing: "success",
  succeeded: "success",
  ready: "success",
  current: "success",
  processing: "info",
  running: "info",
  starting: "info",
  importing: "info",
  shortlisted: "accent",
  draft: "neutral",
  pending: "warning",
  pending_approval: "warning",
  needs_review: "warning",
  waiting: "warning",
  outdated: "warning",
  rejected: "danger",
  failed: "danger",
  outcome_unknown: "danger",
};

export function statusVariant(status: string): StatusVariant {
  return (
    variants[status.trim().toLowerCase().replaceAll(" ", "_")] ?? "neutral"
  );
}

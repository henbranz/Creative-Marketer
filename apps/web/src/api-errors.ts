type ValidationIssue = {
  loc?: unknown;
  msg?: unknown;
  type?: unknown;
};

const forbiddenKey =
  /(?:input|password|secret|token|authorization|cookie|signed[_-]?url|api[_-]?key)/i;

function safeText(value: unknown): string | null {
  if (typeof value !== "string") return null;
  const text = value.trim();
  return text && text.length <= 500 ? text : null;
}

function humanPath(value: unknown): string {
  if (!Array.isArray(value)) return "Request";
  const parts = value
    .filter((item) => typeof item === "string" || typeof item === "number")
    .map(String)
    .filter(
      (item) =>
        !["body", "query", "path"].includes(item) && !forbiddenKey.test(item),
    );
  return parts.length
    ? parts.map((item) => item.replaceAll("_", " ")).join(" → ")
    : "Request";
}

function formatIssue(value: unknown): string | null {
  if (!value || typeof value !== "object" || Array.isArray(value)) return null;
  const issue = value as ValidationIssue;
  const message = safeText(issue.msg);
  if (!message) return null;
  return `${humanPath(issue.loc)}: ${message}`;
}

export function formatApiErrorPayload(payload: unknown): string {
  if (typeof payload === "string")
    return safeText(payload) ?? "The request could not be completed.";
  if (!payload || typeof payload !== "object")
    return "The request could not be completed.";
  const detail = (payload as { detail?: unknown }).detail;
  if (typeof detail === "string")
    return safeText(detail) ?? "The request could not be completed.";
  if (Array.isArray(detail)) {
    const messages = detail
      .map(formatIssue)
      .filter((item): item is string => Boolean(item));
    return messages.length
      ? messages.join("; ")
      : "The request contains invalid fields.";
  }
  const issue = formatIssue(detail);
  if (issue) return issue;
  const message = safeText((detail as { message?: unknown } | null)?.message);
  return message ?? "The request could not be completed.";
}

export async function readApiError(response: Response): Promise<string> {
  const contentType = response.headers?.get?.("content-type") ?? "";
  if (contentType.includes("application/json")) {
    try {
      return formatApiErrorPayload(await response.json());
    } catch {
      return "The request could not be completed.";
    }
  }
  return "The request could not be completed.";
}

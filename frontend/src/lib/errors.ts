export const NO_ACCESS = "Not found or you don't have access.";
export const AI_UNAVAILABLE = "The AI service is unavailable, try again.";
export const TOO_MANY_REQUESTS = "Too many requests, wait a moment.";

type ErrorKind = "http" | "network" | "timeout";

export class ApiError extends Error {
  constructor(
    readonly kind: ErrorKind,
    readonly status: number,
    /** The backend's `detail`, which may be a string, an object or a validation list. Never show it directly. */
    readonly detail: unknown = undefined,
    readonly code: string | undefined = undefined,
    readonly retryAfter: string | null = null,
  ) {
    super(`Request failed: ${kind} ${status}`);
  }

  get isNoAccess() {
    return this.status === 403 || this.status === 404;
  }
}

function validationMessage(detail: unknown): string | null {
  if (!Array.isArray(detail) || detail.length === 0) return null;
  const first = detail[0] as { msg?: unknown; loc?: unknown[] };
  if (typeof first?.msg !== "string") return null;
  const field = Array.isArray(first.loc) ? String(first.loc[first.loc.length - 1] ?? "") : "";
  const message = first.msg.replace(/^Value error,\s*/, "");
  return field && field !== "body" ? `${field.replace(/_/g, " ")}: ${message}` : message;
}

function plainDetail(detail: unknown): string | null {
  if (typeof detail === "string") return detail;
  if (detail && typeof detail === "object" && "message" in detail && typeof detail.message === "string") return detail.message;
  return validationMessage(detail);
}

/** The only way an error becomes text on screen, so raw JSON and internals never reach users. */
export function friendlyMessage(error: unknown): string {
  if (!(error instanceof ApiError)) return "Something went wrong. Try again.";
  if (error.kind === "network") return "We couldn't reach the server. Check your connection and try again.";
  if (error.kind === "timeout") return "That took too long. Check your connection and try again.";
  switch (error.status) {
    case 401:
      return "Your session has ended. Sign in again.";
    case 403: {
      // The one 403 that is about the request, not about whether something exists.
      const detail = plainDetail(error.detail);
      return detail?.startsWith("You cannot grant") ? detail : NO_ACCESS;
    }
    case 404:
      return NO_ACCESS;
    case 429:
      return TOO_MANY_REQUESTS;
    case 400:
    case 409:
    case 413:
    case 415:
    case 422:
      return plainDetail(error.detail) ?? "That request couldn't be completed. Check the details and try again.";
    default:
      return "Something went wrong on our side. Try again.";
  }
}

/** Chat has its own wording, because its 5xx responses are about the AI service. */
export function friendlyChatMessage(error: unknown): string {
  if (error instanceof ApiError && error.kind === "http") {
    if (error.status === 429) return TOO_MANY_REQUESTS;
    if ([502, 503, 504].includes(error.status)) return AI_UNAVAILABLE;
  }
  return friendlyMessage(error);
}

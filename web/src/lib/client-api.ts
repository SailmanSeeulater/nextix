/**
 * Browser-side calls. They go to this app's own /api/* proxy, which adds the
 * API token server-side; the browser never sees it.
 */
import type {
  CreateTicketResult,
  RunDetail,
  RunEvent,
  RunEventsPage,
  TicketCard,
  TicketDetail,
} from "./types";

export interface CreateTicketInput {
  repo: string;
  prompt: string;
  labels: string[];
  triage: boolean;
}

export class ApiError extends Error {}

const UNREACHABLE = "Can't reach nexTix. Check that it's running, then try again.";

/**
 * Turn an error response into a sentence a person can act on. The API's own `detail`
 * wins; `fallbacks` words a status for this call when the API gave no sentence.
 */
export async function describeError(
  res: Response,
  fallbacks: Partial<Record<number, string>> = {},
): Promise<string> {
  let detail: unknown;
  try {
    detail = ((await res.json()) as { detail?: unknown }).detail;
  } catch {
    detail = undefined;
  }
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail) && detail.length > 0) {
    const first = detail[0] as { loc?: unknown[]; msg?: string };
    const field = (first.loc ?? []).slice(1).join(".") || "request";
    return `Check the ${field}: ${first.msg ?? "invalid value"}.`;
  }
  const fallback = fallbacks[res.status];
  if (fallback) return fallback;
  if (res.status === 401) return "Your session expired. Sign in again.";
  if (res.status === 502 || res.status === 503) return "The nexTix API is unavailable right now.";
  return `Something went wrong (HTTP ${res.status}).`;
}

export function parseLabels(raw: string): string[] {
  return [
    ...new Set(
      raw
        .split(",")
        .map((l) => l.trim())
        .filter(Boolean),
    ),
  ];
}

export async function createTicket(
  input: CreateTicketInput,
  fetchImpl: typeof fetch = fetch,
): Promise<CreateTicketResult> {
  let res: Response;
  try {
    res = await fetchImpl("/api/tickets", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ ...input, created_via: "web" }),
    });
  } catch {
    throw new ApiError(UNREACHABLE);
  }
  if (!res.ok) throw new ApiError(await describeError(res));
  return (await res.json()) as CreateTicketResult;
}

export async function loadTickets(fetchImpl: typeof fetch = fetch): Promise<TicketCard[] | null> {
  try {
    const res = await fetchImpl("/api/tickets", { cache: "no-store" });
    return res.ok ? ((await res.json()) as TicketCard[]) : null;
  } catch {
    return null;
  }
}

/** Re-read one ticket and its runs (after a retry, a cancel, or a reconnect). */
export async function loadTicketDetail(
  id: string,
  fetchImpl: typeof fetch = fetch,
): Promise<TicketDetail | null> {
  try {
    const res = await fetchImpl(`/api/tickets/${encodeURIComponent(id)}`, { cache: "no-store" });
    return res.ok ? ((await res.json()) as TicketDetail) : null;
  } catch {
    return null;
  }
}

/** The API's page ceiling for GET /api/runs/{id}/events. */
export const EVENTS_PAGE_LIMIT = 500;
/** Stop paging after this many pages (100k events); the live stream still fills in. */
const MAX_EVENT_PAGES = 200;

export interface RunHistory {
  events: RunEvent[];
  /** False when a page failed; what loaded so far is still returned. */
  complete: boolean;
}

/** Every stored event of a run after `after`, following `next_after` page by page. */
export async function loadRunHistory(
  runId: string,
  after: number | null = null,
  fetchImpl: typeof fetch = fetch,
): Promise<RunHistory> {
  const events: RunEvent[] = [];
  let cursor = after;
  for (let page = 0; page < MAX_EVENT_PAGES; page++) {
    const query = new URLSearchParams({ limit: String(EVENTS_PAGE_LIMIT) });
    if (cursor !== null) query.set("after", String(cursor));
    let body: RunEventsPage;
    try {
      const res = await fetchImpl(
        `/api/runs/${encodeURIComponent(runId)}/events?${query.toString()}`,
        { cache: "no-store" },
      );
      if (!res.ok) return { events, complete: false };
      body = (await res.json()) as RunEventsPage;
    } catch {
      return { events, complete: false };
    }
    events.push(...body.events);
    // A cursor that doesn't advance would loop forever; treat it as the end.
    if (body.next_after === null || body.next_after === cursor) return { events, complete: true };
    cursor = body.next_after;
  }
  return { events, complete: true };
}

/** Start a new run on a ticket (POST /api/tickets/{id}/runs). */
export async function retryTicket(
  ticketId: string,
  fetchImpl: typeof fetch = fetch,
): Promise<RunDetail> {
  let res: Response;
  try {
    res = await fetchImpl(`/api/tickets/${encodeURIComponent(ticketId)}/runs`, { method: "POST" });
  } catch {
    throw new ApiError(UNREACHABLE);
  }
  if (!res.ok) {
    throw new ApiError(
      await describeError(res, {
        404: "This ticket is no longer on the board.",
        409: "A run is already active for this ticket.",
      }),
    );
  }
  return (await res.json()) as RunDetail;
}

/**
 * Ask the worker to stop a run (POST /api/runs/{id}/cancel). The API answers 202; when
 * the body carries the updated run, it is returned so the page can show it at once.
 */
export async function cancelRun(
  runId: string,
  fetchImpl: typeof fetch = fetch,
): Promise<RunDetail | null> {
  let res: Response;
  try {
    res = await fetchImpl(`/api/runs/${encodeURIComponent(runId)}/cancel`, { method: "POST" });
  } catch {
    throw new ApiError(UNREACHABLE);
  }
  if (!res.ok) {
    throw new ApiError(
      await describeError(res, {
        404: "That run no longer exists.",
        409: "This run has already finished.",
        410: "This run has already finished.",
      }),
    );
  }
  try {
    const body = (await res.json()) as Partial<RunDetail> | null;
    return body && typeof body.id === "string" && typeof body.status === "string"
      ? (body as RunDetail)
      : null;
  } catch {
    return null; // 202 with no body is fine too
  }
}

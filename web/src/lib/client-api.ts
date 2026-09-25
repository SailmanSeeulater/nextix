/**
 * Browser-side calls. They go to this app's own /api/* proxy, which adds the
 * API token server-side; the browser never sees it.
 */
import type { CreateTicketResult, TicketCard } from "./types";

export interface CreateTicketInput {
  repo: string;
  prompt: string;
  labels: string[];
  triage: boolean;
}

export class ApiError extends Error {}

/** Turn an error response into a sentence a person can act on. */
export async function describeError(res: Response): Promise<string> {
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
    throw new ApiError("Can't reach nexTix. Check that it's running, then try again.");
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

/**
 * Server-side access to the nexTix API. The API token never reaches the browser:
 * server components call these helpers, and the browser goes through the
 * /api/[...path] route handler, which adds the token.
 */
import type { RepoOption, TicketCard, TicketDetail } from "./types";

export type HealthStatus = "ok" | "error";

export interface HealthResponse {
  status: HealthStatus;
  version: string;
  checks: Record<string, HealthStatus>;
  /** Which Claude credential triage uses; never the credential itself. */
  claude?: ClaudeAuth;
}

export type ClaudeAuth = "subscription" | "api_key" | "none";

export function apiBaseUrl(): string {
  return process.env.API_INTERNAL_URL ?? "http://localhost:8000";
}

export function authHeaders(): Record<string, string> {
  const token = process.env.NEXTIX_API_TOKEN;
  return token ? { Authorization: `Bearer ${token}` } : {};
}

export async function fetchHealth(
  fetchImpl: typeof fetch = fetch,
): Promise<HealthResponse | null> {
  try {
    const res = await fetchImpl(`${apiBaseUrl()}/api/health`, { cache: "no-store" });
    // 503 still carries a valid body describing which check failed.
    if (res.status !== 200 && res.status !== 503) return null;
    return (await res.json()) as HealthResponse;
  } catch {
    return null;
  }
}

export async function fetchTickets(fetchImpl: typeof fetch = fetch): Promise<TicketCard[] | null> {
  try {
    const res = await fetchImpl(`${apiBaseUrl()}/api/tickets`, {
      cache: "no-store",
      headers: authHeaders(),
    });
    if (!res.ok) return null;
    return (await res.json()) as TicketCard[];
  } catch {
    return null;
  }
}

/** Ticket ids are UUIDs; anything else can't name a ticket, so it is a 404 without asking. */
const TICKET_ID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

export function isTicketId(id: string): boolean {
  return TICKET_ID.test(id);
}

export type TicketDetailResult =
  | { ticket: TicketDetail }
  /** "not_found": the API says there is no such ticket. "unavailable": we couldn't ask. */
  | { error: "not_found" | "unavailable" };

export async function fetchTicketDetail(
  id: string,
  fetchImpl: typeof fetch = fetch,
): Promise<TicketDetailResult> {
  if (!isTicketId(id)) return { error: "not_found" };
  try {
    const res = await fetchImpl(`${apiBaseUrl()}/api/tickets/${encodeURIComponent(id)}`, {
      cache: "no-store",
      headers: authHeaders(),
    });
    if (res.status === 404) return { error: "not_found" };
    if (!res.ok) return { error: "unavailable" };
    return { ticket: (await res.json()) as TicketDetail };
  } catch {
    return { error: "unavailable" };
  }
}

export async function fetchRepos(fetchImpl: typeof fetch = fetch): Promise<RepoOption[] | null> {
  try {
    const res = await fetchImpl(`${apiBaseUrl()}/api/repos`, {
      cache: "no-store",
      headers: authHeaders(),
    });
    if (!res.ok) return null;
    return (await res.json()) as RepoOption[];
  } catch {
    return null;
  }
}

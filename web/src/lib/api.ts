/**
 * Server-side access to the nexTix API. The API token never reaches the browser:
 * server components call these helpers, and the browser goes through the
 * /api/[...path] route handler, which adds the token.
 */
import type { TicketCard } from "./types";

export type HealthStatus = "ok" | "error";

export interface HealthResponse {
  status: HealthStatus;
  version: string;
  checks: Record<string, HealthStatus>;
}

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

export type HealthStatus = "ok" | "error";

export interface HealthResponse {
  status: HealthStatus;
  version: string;
  checks: Record<string, HealthStatus>;
}

/** Base URL for server-side calls (inside compose) or the browser (public). */
export function apiBaseUrl(): string {
  if (typeof window === "undefined" && process.env.API_INTERNAL_URL) {
    return process.env.API_INTERNAL_URL;
  }
  return process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";
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

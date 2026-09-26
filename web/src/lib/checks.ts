/**
 * Pure helpers for the Checks tab: what a CI check run's status and conclusion mean,
 * how long it took, and a one-line summary of the PR head's checks.
 */
import { formatSeconds } from "./board";
import type { CheckRun } from "./types";

/** failed and passed carry the theme red and green; the rest stay neutral ink. */
export type CheckTone = "failed" | "passed" | "running" | "waiting" | "neutral";

export interface CheckOutcome {
  tone: CheckTone;
  word: string;
}

const CONCLUSIONS: Record<string, CheckOutcome> = {
  success: { tone: "passed", word: "Passed" },
  failure: { tone: "failed", word: "Failed" },
  timed_out: { tone: "failed", word: "Timed out" },
  action_required: { tone: "failed", word: "Action required" },
  startup_failure: { tone: "failed", word: "Couldn't start" },
  cancelled: { tone: "neutral", word: "Cancelled" },
  skipped: { tone: "neutral", word: "Skipped" },
  neutral: { tone: "neutral", word: "Neutral" },
  stale: { tone: "neutral", word: "Stale" },
};

export function checkOutcome(check: CheckRun): CheckOutcome {
  if (check.status === "in_progress") return { tone: "running", word: "Running" };
  if (check.status !== "completed") {
    return { tone: "waiting", word: check.status === "queued" ? "Queued" : "Waiting" };
  }
  return (check.conclusion && CONCLUSIONS[check.conclusion]) || { tone: "neutral", word: "Done" };
}

/** Wall time of a finished check, or how long a running one has been going. */
export function checkDuration(check: CheckRun, now: number): string | null {
  if (!check.started_at) return null;
  const start = Date.parse(check.started_at);
  const end = check.completed_at ? Date.parse(check.completed_at) : check.status === "in_progress" ? now : NaN;
  if (Number.isNaN(start) || Number.isNaN(end)) return null;
  return formatSeconds(Math.max(0, (end - start) / 1000));
}

const TONE_ORDER: Record<CheckTone, number> = { failed: 0, running: 1, waiting: 2, passed: 3, neutral: 4 };

/** Failures first, then what is still running, then the rest; by name within each. */
export function sortChecks(runs: readonly CheckRun[]): CheckRun[] {
  return [...runs].sort(
    (a, b) =>
      TONE_ORDER[checkOutcome(a).tone] - TONE_ORDER[checkOutcome(b).tone] ||
      a.name.localeCompare(b.name),
  );
}

export interface ChecksSummary {
  total: number;
  failed: number;
  passed: number;
  pending: number;
  /** "3 checks: 1 failed, 2 passed" */
  text: string;
}

export function summarizeChecks(runs: readonly CheckRun[]): ChecksSummary {
  let failed = 0;
  let passed = 0;
  let pending = 0;
  for (const run of runs) {
    const tone = checkOutcome(run).tone;
    if (tone === "failed") failed++;
    else if (tone === "passed") passed++;
    else if (tone === "running" || tone === "waiting") pending++;
  }
  const total = runs.length;
  const parts = [
    failed ? `${failed} failed` : null,
    pending ? `${pending} pending` : null,
    passed ? `${passed} passed` : null,
  ].filter(Boolean);
  const head = `${total} ${total === 1 ? "check" : "checks"}`;
  return { total, failed, passed, pending, text: parts.length ? `${head}: ${parts.join(", ")}` : head };
}

/** A link from GitHub's data, only when it is a plain https URL. */
export function safeLink(url: string | null | undefined): string | null {
  if (!url) return null;
  try {
    return new URL(url).protocol === "https:" ? url : null;
  } catch {
    return null;
  }
}

/** The short commit id GitHub shows ("3f9a1c2"). */
export function shortSha(sha: string | null | undefined): string | null {
  return sha ? sha.slice(0, 7) : null;
}

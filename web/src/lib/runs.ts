/**
 * Pure logic for the ticket detail page: which run is active, what the header pass
 * shows for a run, which actions apply, and how live updates merge into the runs.
 */
import {
  formatCost,
  formatElapsed,
  formatSeconds,
  isHeartbeatStale,
  passFields,
  runWord,
  type PassField,
} from "./board";
import type { Usage } from "./transcript";
import type { Column, RunDetail, RunSummary, TicketCard } from "./types";

const ACTIVE = new Set(["queued", "claimed", "running"]);

/** queued, claimed and running: the run can still change, and can be cancelled. */
export function isActiveStatus(status: string): boolean {
  return ACTIVE.has(status);
}

export function activeRun(runs: readonly RunDetail[]): RunDetail | null {
  return runs.find((r) => isActiveStatus(r.status)) ?? null;
}

/** A queued run waits this long before the page says no worker is picking it up. */
export const QUEUE_PATIENCE_MS = 10 * 60_000;

export function noWorkerAvailable(run: RunDetail, now: number): boolean {
  return (
    run.status === "queued" &&
    run.queued_at !== null &&
    now - Date.parse(run.queued_at) > QUEUE_PATIENCE_MS
  );
}

const RETRYABLE: ReadonlySet<Column> = new Set<Column>(["failed", "todo", "needs_input"]);

/** Retry shows for Failed, Todo and Needs Input tickets when no run is active. */
export function canRetry(column: Column, runs: readonly RunDetail[]): boolean {
  return RETRYABLE.has(column) && activeRun(runs) === null;
}

/** Money and token counts may arrive as strings (Decimal) or be missing; never NaN. */
export function toNumber(value: unknown): number {
  const n = typeof value === "string" ? Number(value) : value;
  return typeof n === "number" && Number.isFinite(n) ? n : 0;
}

/** Newest first: highest attempt, then latest queued. */
export function sortRuns(runs: readonly RunDetail[]): RunDetail[] {
  return [...runs].sort(
    (a, b) => b.attempt - a.attempt || (b.queued_at ?? "").localeCompare(a.queued_at ?? ""),
  );
}

const STATUS_RANK: Record<string, number> = { queued: 0, claimed: 1, running: 2 };

function rank(status: string): number {
  return STATUS_RANK[status] ?? 3; // every other status is terminal
}

/**
 * A run only moves forward (queued → claimed → running → a terminal status). Updates
 * arrive on two streams and a re-read, so a late one must not move it back.
 */
export function laterStatus(prev: string, next: string): string {
  return rank(next) >= rank(prev) ? next : prev;
}

/** The later of two ISO timestamps; a missing or unreadable one loses to the other. */
export function laterTime(a: string | null, b: string | null): string | null {
  const ta = a ? Date.parse(a) : NaN;
  const tb = b ? Date.parse(b) : NaN;
  if (Number.isNaN(tb)) return a ?? b;
  if (Number.isNaN(ta)) return b;
  return tb > ta ? b : a;
}

/**
 * Merge a newer copy of a run over what we have. Usage streams in cumulatively and
 * may run ahead of the stored run (or a stale event may trail it), so counters only
 * ever move up, the heartbeat only moves later, and the status only moves forward.
 */
function mergeRun(next: RunDetail, prev: RunDetail | undefined): RunDetail {
  if (!prev) return next;
  const status = laterStatus(prev.status, next.status);
  const base = status === next.status ? next : prev;
  // The review fields follow the fresher copy too: a ticket read that started before the
  // run ended must not wipe the tests and artifacts it finished with. Stream payloads
  // leave them out (only a ticket read carries artifacts), so the other copy fills in.
  const other = base === next ? prev : next;
  return {
    ...base,
    artifacts: base.artifacts ?? other.artifacts,
    tests: base.tests !== undefined ? base.tests : other.tests,
    review_errors: base.review_errors !== undefined ? base.review_errors : other.review_errors,
    last_heartbeat: laterTime(next.last_heartbeat, prev.last_heartbeat),
    input_tokens: Math.max(toNumber(next.input_tokens), toNumber(prev.input_tokens)),
    output_tokens: Math.max(toNumber(next.output_tokens), toNumber(prev.output_tokens)),
    cost_usd: Math.max(toNumber(next.cost_usd), toNumber(prev.cost_usd)),
  };
}

/** Add or replace one run (from run.updated, run.state or a retry), newest first. */
export function upsertRun(runs: readonly RunDetail[], run: RunDetail): RunDetail[] {
  const prev = runs.find((r) => r.id === run.id);
  const next = mergeRun(run, prev);
  return sortRuns(prev ? runs.map((r) => (r.id === run.id ? next : r)) : [...runs, next]);
}

/** Replace the list with a fresh read, keeping live progress that ran ahead of it. */
export function mergeRuns(current: readonly RunDetail[], fresh: readonly RunDetail[]): RunDetail[] {
  const byId = new Map(current.map((r) => [r.id, r]));
  return sortRuns(fresh.map((r) => mergeRun(r, byId.get(r.id))));
}

/**
 * Fold a board card's latest_run into the list. Returns null when the run isn't
 * known yet, so the caller can re-read the ticket to get its full detail.
 */
export function mergeRunSummary(
  runs: readonly RunDetail[],
  summary: RunSummary,
): RunDetail[] | null {
  const prev = runs.find((r) => r.id === summary.id);
  if (!prev) return null;
  const status = laterStatus(prev.status, summary.status);
  if (status !== summary.status) return [...runs]; // a stale card; keep what we have
  const next: RunDetail = {
    ...prev,
    attempt: summary.attempt,
    status,
    agent_id: summary.agent_id ?? prev.agent_id,
    started_at: summary.started_at ?? prev.started_at,
    last_heartbeat: laterTime(summary.last_heartbeat, prev.last_heartbeat),
    cost_usd: Math.max(toNumber(summary.cost_usd), toNumber(prev.cost_usd)),
  };
  return runs.map((r) => (r.id === summary.id ? next : r));
}

/** Apply a cumulative usage event to its run. */
export function applyUsage(run: RunDetail, usage: Usage): RunDetail {
  const next = {
    ...run,
    input_tokens: Math.max(toNumber(run.input_tokens), usage.input_tokens),
    output_tokens: Math.max(toNumber(run.output_tokens), usage.output_tokens),
    cost_usd: Math.max(toNumber(run.cost_usd), usage.cost_usd),
  };
  const same =
    next.input_tokens === run.input_tokens &&
    next.output_tokens === run.output_tokens &&
    next.cost_usd === run.cost_usd;
  return same ? run : next;
}

/** A card's fields merged into the detail (the card doesn't carry body or runs). */
export function mergeCard<T extends TicketCard>(detail: T, card: TicketCard): T {
  return { ...detail, ...card, latest_run: card.latest_run };
}

const NUMBER = new Intl.NumberFormat("en-US");

export function formatCount(n: number): string {
  return NUMBER.format(Math.round(n));
}

const EXIT_REASONS: Record<string, string> = {
  heartbeat_lost: "Heartbeat lost",
  no_changes: "No changes to commit",
  timeout: "Ran out of time",
  timed_out: "Ran out of time",
  max_turns: "Hit the turn limit",
  max_cost: "Hit the cost limit",
  usage_limit: "Claude usage limit reached",
  sandbox_error: "Sandbox crashed",
  clone_failed: "Couldn't clone the repo",
  push_failed: "Couldn't push the branch",
  enqueue_failed: "Couldn't reach the queue",
  no_claude_credentials: "No Claude credential",
  runner_error: "Runner error",
  cancelled: "Cancelled",
};

/** "heartbeat_lost" → "Heartbeat lost"; unknown reasons are de-snaked, not hidden. */
export function exitReasonWord(reason: string): string {
  const known = EXIT_REASONS[reason];
  if (known) return known;
  const words = reason.replace(/[_-]+/g, " ").trim();
  return words ? words[0]!.toUpperCase() + words.slice(1) : reason;
}

const TRIGGER_WORDS: Record<string, string> = {
  initial: "First run",
  retry: "Retry",
  review_feedback: "Review feedback",
};

export function triggerWord(trigger: string | null): string | null {
  if (!trigger) return null;
  return TRIGGER_WORDS[trigger] ?? exitReasonWord(trigger);
}

export interface DetailField extends PassField {
  /** A second, smaller line under the value (e.g. the token split). */
  detail?: string;
}

/**
 * The header pass's label-over-value fields for one run. Only facts the API carries;
 * a ticket that never ran shows its board fields instead.
 */
export function runFields(card: TicketCard, run: RunDetail | null, now: number): DetailField[] {
  if (!run) return passFields(card, now);
  const fields: DetailField[] = [{ label: "Status", value: runWord(run.status) }];
  const attemptDetail = triggerWord(run.trigger);
  fields.push({
    label: "Attempt",
    value: String(run.attempt),
    ...(attemptDetail ? { detail: attemptDetail } : {}),
  });

  // A queued run has no agent yet; the readout above the notch already says it's waiting.
  if (run.agent_id) fields.push({ label: "Agent", value: run.agent_id });

  if (run.status === "queued" && run.queued_at) {
    fields.push({ label: "Queued", value: formatElapsed(run.queued_at, now) });
  } else if (run.started_at && isActiveStatus(run.status)) {
    fields.push({ label: "Elapsed", value: formatElapsed(run.started_at, now) });
  } else if (run.started_at && run.finished_at) {
    fields.push({
      label: "Duration",
      value: formatElapsed(run.started_at, Date.parse(run.finished_at)),
    });
  }

  if (run.status === "claimed" || run.status === "running") {
    const stale = isHeartbeatStale(run, now);
    fields.push({
      label: "Heartbeat",
      value: stale
        ? run.last_heartbeat
          ? `Lost ${formatElapsed(run.last_heartbeat, now)} ago`
          : "None yet"
        : "Live",
      tone: stale ? "alert" : undefined,
    });
  }

  const cost = toNumber(run.cost_usd);
  if (run.status !== "queued" || cost > 0) fields.push({ label: "Spent", value: formatCost(cost) });

  const input = toNumber(run.input_tokens);
  const output = toNumber(run.output_tokens);
  if (input + output > 0) {
    fields.push({
      label: "Tokens",
      value: formatCount(input + output),
      detail: `${formatCount(input)} in · ${formatCount(output)} out`,
    });
  }

  if (run.tests) {
    const took = formatSeconds(toNumber(run.tests.duration_s));
    fields.push({
      label: "Tests",
      value: run.tests.passed ? "Passed" : "Failed",
      tone: run.tests.passed ? undefined : "alert",
      detail: run.tests.passed ? took : `exit ${run.tests.exit_code} · ${took}`,
    });
  }

  // A cancelled run's reason is "cancelled"; saying it twice adds nothing.
  const reason = run.exit_reason ? exitReasonWord(run.exit_reason) : null;
  if (reason && reason !== runWord(run.status)) fields.push({ label: "Reason", value: reason });
  return fields;
}

export type RunReadout =
  | { state: "live"; elapsed: string }
  | { state: "stalled"; quietFor: string | null }
  | { state: "queued"; noWorker: boolean };

/** The big primary field on the header pass, for a run that is still active. */
export function runReadout(run: RunDetail | null, now: number): RunReadout | null {
  if (!run || !isActiveStatus(run.status)) return null;
  if (run.status === "queued") return { state: "queued", noWorker: noWorkerAvailable(run, now) };
  if (isHeartbeatStale(run, now)) {
    return {
      state: "stalled",
      quietFor: run.last_heartbeat ? formatElapsed(run.last_heartbeat, now) : null,
    };
  }
  return { state: "live", elapsed: run.started_at ? formatElapsed(run.started_at, now) : "0s" };
}

/** Option text for the attempt selector: "Attempt 3 · Running". */
export function attemptLabel(run: RunDetail): string {
  return `Attempt ${run.attempt} · ${runWord(run.status)}`;
}

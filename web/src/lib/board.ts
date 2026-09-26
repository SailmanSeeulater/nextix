import type { BoardEvent, Column, RunSummary, TicketCard } from "./types";

export interface ColumnMeta {
  key: Column;
  title: string;
  /** Teaches what lands in this stack when it is empty. */
  empty: string;
}

export const COLUMNS: ReadonlyArray<ColumnMeta> = [
  {
    key: "todo",
    title: "Todo",
    empty: "Nothing queued. Describe a change above, or label an issue nextix on GitHub.",
  },
  { key: "doing", title: "Doing", empty: "No agents running right now." },
  { key: "needs_input", title: "Needs Input", empty: "No questions waiting on you." },
  { key: "failed", title: "Failed", empty: "No failed runs." },
  { key: "in_review", title: "In Review", empty: "No pull requests waiting for review." },
  { key: "done", title: "Done", empty: "Merged and closed tickets collect here." },
];

export const HEARTBEAT_STALE_MS = 30_000;

export type CardIndex = Record<string, TicketCard>;

export function indexCards(cards: TicketCard[]): CardIndex {
  return Object.fromEntries(cards.map((c) => [c.id, c]));
}

/** Apply one live event. Returns a new index; never mutates the input. */
export function applyEvent(index: CardIndex, event: BoardEvent): CardIndex {
  if (event.type === "ticket.updated") {
    return { ...index, [event.data.id]: event.data };
  }
  if (!(event.data.id in index)) return index;
  const next = { ...index };
  delete next[event.data.id];
  return next;
}

/** True when applying these events adds, removes, or moves a pass between stacks. */
export function changesLayout(current: CardIndex, events: BoardEvent[]): boolean {
  return events.some((e) => {
    if (e.type === "ticket.removed") return e.data.id in current;
    const before = current[e.data.id];
    return before === undefined || before.column !== e.data.column;
  });
}

export function groupByColumn(
  index: CardIndex,
  repo: string | null = null,
  now: number | null = null,
): Record<Column, TicketCard[]> {
  const groups = Object.fromEntries(COLUMNS.map((c) => [c.key, [] as TicketCard[]])) as Record<
    Column,
    TicketCard[]
  >;
  for (const card of Object.values(index)) {
    if (repo && card.repo !== repo) continue;
    groups[card.column].push(card);
  }
  for (const list of Object.values(groups)) {
    list.sort((a, b) => b.updated_at.localeCompare(a.updated_at));
  }
  // A Doing pass whose agent went quiet is the thing the owner most needs to see:
  // it leads its stack (and so opens by default).
  if (now !== null) {
    groups.doing.sort(
      (a, b) => Number(isStalled(b, now)) - Number(isStalled(a, now)),
    );
  }
  return groups;
}

/**
 * Short repo names for pass strips ("storefront"), keeping "owner/name" only where
 * two repos on the board share a name.
 */
export function repoLabels(repos: string[]): Record<string, string> {
  const byName = new Map<string, number>();
  for (const full of new Set(repos)) {
    const name = full.split("/")[1] ?? full;
    byName.set(name, (byName.get(name) ?? 0) + 1);
  }
  return Object.fromEntries(
    [...new Set(repos)].map((full) => {
      const name = full.split("/")[1] ?? full;
      return [full, (byName.get(name) ?? 0) > 1 ? full : name];
    }),
  );
}

export function repoOptions(index: CardIndex): string[] {
  return [...new Set(Object.values(index).map((c) => c.repo))].sort();
}

export function isHeartbeatStale(run: RunSummary, now: number): boolean {
  if (!run.last_heartbeat) return true;
  return now - Date.parse(run.last_heartbeat) > HEARTBEAT_STALE_MS;
}

/**
 * Heartbeats reach the database at least every 15 s, but nothing pushes them to the
 * browser (docs/phase3.md: a heartbeat updates `last_heartbeat` only; streams carry
 * status changes and usage). So once a live run's last known heartbeat is this old, the
 * page re-reads it, well before it would show as lost at HEARTBEAT_STALE_MS.
 */
export const HEARTBEAT_RECHECK_MS = 20_000;
/** At most one heartbeat re-read per this interval, even while a run stays quiet. */
export const HEARTBEAT_RECHECK_INTERVAL_MS = 10_000;

/** True when a claimed or running run's heartbeat is old enough to re-read. */
export function heartbeatNeedsRecheck(run: RunSummary | null, now: number): boolean {
  if (!run || (run.status !== "claimed" && run.status !== "running")) return false;
  if (!run.last_heartbeat) return true;
  return now - Date.parse(run.last_heartbeat) > HEARTBEAT_RECHECK_MS;
}

/**
 * Take newer heartbeats from a fresh read of the board, and nothing else: moves between
 * stacks keep arriving as events, so a slow read can't undo one. Returns `index` itself
 * when no heartbeat is newer.
 */
export function mergeHeartbeats(index: CardIndex, fresh: readonly TicketCard[]): CardIndex {
  let next: CardIndex | null = null;
  for (const card of fresh) {
    const current = index[card.id];
    const run = current?.latest_run;
    const beat = card.latest_run?.last_heartbeat;
    if (!current || !run || !beat || card.latest_run?.id !== run.id) continue;
    if (run.last_heartbeat && Date.parse(run.last_heartbeat) >= Date.parse(beat)) continue;
    next ??= { ...index };
    next[card.id] = { ...current, latest_run: { ...run, last_heartbeat: beat } };
  }
  return next ?? index;
}

export function formatElapsed(fromIso: string, now: number): string {
  const s = Math.max(0, Math.floor((now - Date.parse(fromIso)) / 1000));
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  const sec = s % 60;
  if (h > 0) return `${h}h ${m}m`;
  if (m > 0) return `${m}m ${sec.toString().padStart(2, "0")}s`;
  return `${sec}s`;
}

/** A measured duration in seconds, as formatElapsed writes times: "4.2s", "12s", "1m 05s", "1h 2m". */
export function formatSeconds(seconds: number): string {
  if (!Number.isFinite(seconds) || seconds < 0) return "–";
  if (seconds < 9.95) return `${Number(seconds.toFixed(1))}s`;
  const s = Math.round(seconds);
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  const sec = s % 60;
  if (h > 0) return `${h}h ${m}m`;
  if (m > 0) return `${m}m ${sec.toString().padStart(2, "0")}s`;
  return `${sec}s`;
}

export function formatCost(usd: number): string {
  return `$${usd.toFixed(2)}`;
}

/** Coarse age for things that change slowly: "just now", "12m", "3h", "5d". */
export function formatAgo(fromIso: string, now: number): string {
  const s = Math.max(0, Math.floor((now - Date.parse(fromIso)) / 1000));
  if (s < 60) return "just now";
  if (s < 3600) return `${Math.floor(s / 60)}m`;
  if (s < 86_400) return `${Math.floor(s / 3600)}h`;
  return `${Math.floor(s / 86_400)}d`;
}

export interface PassField {
  label: string;
  value: string;
  /** "alert" marks a value the owner should act on (e.g. a lost heartbeat). */
  tone?: "alert";
}

const RUN_WORDS: Record<string, string> = {
  queued: "Queued",
  claimed: "Claimed",
  running: "Running",
  succeeded: "Succeeded",
  failed: "Failed",
  timed_out: "Timed out",
  cancelled: "Cancelled",
  needs_input: "Asked a question",
};

export function runWord(status: string): string {
  return RUN_WORDS[status] ?? status.replace(/_/g, " ");
}

/**
 * The label-over-value fields a pass shows below its notch, chosen per state.
 * Only facts the API actually carries; nothing is invented to fill a slot.
 */
export function passFields(card: TicketCard, now: number): PassField[] {
  const run = card.latest_run;
  switch (card.column) {
    case "doing": {
      const fields: PassField[] = [
        { label: "Agent", value: run?.agent_id ?? "Waiting for a worker" },
      ];
      if (run && run.status !== "queued") {
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
      if (run) fields.push({ label: "Spent", value: formatCost(run.cost_usd) });
      return fields;
    }
    case "needs_input":
      return [
        { label: "Waiting", value: formatAgo(card.updated_at, now) },
        { label: "Answer", value: "On the GitHub issue" },
      ];
    case "failed":
      return [
        { label: "Run", value: run ? runWord(run.status) : "Failed" },
        ...(run ? [{ label: "Attempt", value: String(run.attempt) }] : []),
        ...(run && run.cost_usd > 0 ? [{ label: "Spent", value: formatCost(run.cost_usd) }] : []),
      ];
    case "in_review":
      return [
        { label: "Pull request", value: `#${card.pr_number}` },
        ...(run && run.cost_usd > 0 ? [{ label: "Spent", value: formatCost(run.cost_usd) }] : []),
        { label: "Updated", value: formatAgo(card.updated_at, now) },
      ];
    case "done":
      return [
        {
          label: "Outcome",
          value: card.pr_state === "merged" ? `Merged #${card.pr_number}` : "Closed",
        },
        { label: "Updated", value: formatAgo(card.updated_at, now) },
      ];
    default: {
      const labels = card.labels.filter((l) => !l.startsWith("nextix"));
      return [
        { label: "Filed", value: formatAgo(card.updated_at, now) },
        ...(card.created_via ? [{ label: "Via", value: VIA_WORDS[card.created_via] ?? card.created_via }] : []),
        ...(labels.length ? [{ label: "Labels", value: labels.join(", ") }] : []),
      ];
    }
  }
}

const VIA_WORDS: Record<string, string> = {
  cli: "CLI",
  web: "Board",
  mcp: "MCP",
  github: "GitHub label",
};

/** True for a Doing pass whose run has started but whose heartbeat went quiet. */
export function isStalled(card: TicketCard, now: number): boolean {
  const run = card.latest_run;
  return (
    card.column === "doing" && run !== null && run.status !== "queued" && isHeartbeatStale(run, now)
  );
}

export type Liveness =
  | { state: "live"; elapsed: string }
  | { state: "stalled"; quietFor: string | null }
  | { state: "queued" };

/** The big readout on a Doing pass, visible even when the pass is collapsed. */
export function liveness(card: TicketCard, now: number): Liveness | null {
  const run = card.latest_run;
  if (card.column !== "doing") return null;
  if (!run || run.status === "queued") return { state: "queued" };
  if (isHeartbeatStale(run, now)) {
    return {
      state: "stalled",
      quietFor: run.last_heartbeat ? formatElapsed(run.last_heartbeat, now) : null,
    };
  }
  return { state: "live", elapsed: run.started_at ? formatElapsed(run.started_at, now) : "0s" };
}

/** The value shown top-right on every pass (PassKit's "header field"). */
export function headerField(card: TicketCard): string {
  return `#${card.issue_number}`;
}

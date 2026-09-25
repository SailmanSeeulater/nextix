import type { BoardEvent, Column, RunSummary, TicketCard } from "./types";

export const COLUMNS: ReadonlyArray<{ key: Column; title: string }> = [
  { key: "todo", title: "Todo" },
  { key: "doing", title: "Doing" },
  { key: "needs_input", title: "Needs Input" },
  { key: "failed", title: "Failed" },
  { key: "in_review", title: "In Review" },
  { key: "done", title: "Done" },
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

export function groupByColumn(
  index: CardIndex,
  repo: string | null = null,
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
  return groups;
}

export function repoOptions(index: CardIndex): string[] {
  return [...new Set(Object.values(index).map((c) => c.repo))].sort();
}

export function isHeartbeatStale(run: RunSummary, now: number): boolean {
  if (!run.last_heartbeat) return true;
  return now - Date.parse(run.last_heartbeat) > HEARTBEAT_STALE_MS;
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

export function formatCost(usd: number): string {
  return `$${usd.toFixed(2)}`;
}

/** Short status text for a card's badge. */
export function statusLabel(card: TicketCard): string {
  const run = card.latest_run;
  switch (card.column) {
    case "doing":
      return run ? run.status : "queued";
    case "in_review":
      return `PR #${card.pr_number} open`;
    case "done":
      if (card.pr_state === "merged") return `PR #${card.pr_number} merged`;
      return "closed";
    case "failed":
      return run ? run.status.replace("_", " ") : "failed";
    case "needs_input":
      return "question";
    default:
      return "ready";
  }
}

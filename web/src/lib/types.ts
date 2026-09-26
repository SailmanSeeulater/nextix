export type Column = "todo" | "doing" | "needs_input" | "failed" | "in_review" | "done";

export interface RunSummary {
  id: string;
  attempt: number;
  status: string;
  agent_id: string | null;
  started_at: string | null;
  last_heartbeat: string | null;
  cost_usd: number;
}

export interface TicketCard {
  id: string;
  repo: string;
  issue_number: number;
  title: string;
  labels: string[];
  issue_state: string | null;
  issue_url: string;
  pr_number: number | null;
  pr_state: "open" | "closed" | "merged" | null;
  pr_url: string | null;
  column: Column;
  latest_run: RunSummary | null;
  created_via?: string | null;
  updated_at: string;
}

export type BoardEvent =
  | { type: "ticket.updated"; data: TicketCard }
  | { type: "ticket.removed"; data: { id: string } };

export interface RepoOption {
  id: string;
  full_name: string;
  default_branch: string;
}

/** Run statuses (docs/phase3.md): queued → claimed → running → a terminal status. */
export type RunStatus =
  | "queued"
  | "claimed"
  | "running"
  | "succeeded"
  | "failed"
  | "timed_out"
  | "needs_input"
  | "cancelled";

/** One agent run on a ticket, as GET /api/tickets/{id} returns it. */
export interface RunDetail {
  id: string;
  attempt: number;
  /** initial | retry | review_feedback */
  trigger: string | null;
  /** A RunStatus; typed as string so an unknown future status still renders. */
  status: string;
  agent_id: string | null;
  branch: string | null;
  queued_at: string | null;
  started_at: string | null;
  finished_at: string | null;
  last_heartbeat: string | null;
  exit_reason: string | null;
  input_tokens: number;
  output_tokens: number;
  cost_usd: number;
  /** Not in docs/phase3.md, but the API sends them: the agent's closing summary, and
   * the question it asked when the run ended in needs_input. Optional so an API
   * without them still type-checks at the edges. */
  summary?: string | null;
  question?: string | null;
}

/** A ticket with its issue body and every run, newest first. */
export interface TicketDetail extends TicketCard {
  body: string | null;
  runs: RunDetail[];
}

export type RunEventKind =
  | "state"
  | "log"
  | "message"
  | "tool_use"
  | "tool_result"
  | "usage"
  | "error";

/** One stored transcript event. `payload` depends on `kind` (see docs/phase3.md). */
export interface RunEvent {
  id: number;
  ts: string;
  /** A RunEventKind; typed as string so an unknown future kind is skipped, not a crash. */
  kind: string;
  payload: Record<string, unknown>;
}

export interface RunEventsPage {
  events: RunEvent[];
  next_after: number | null;
}

export interface CreateTicketResult {
  ticket: TicketCard;
  issue_url: string;
  board_url: string;
  needs_input: boolean;
  clarifying_question: string | null;
}

/** Board-stream `run.state` payload: a run on some ticket changed. */
export interface RunStateEvent {
  ticket_id: string;
  run: RunDetail;
}

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

export interface CreateTicketResult {
  ticket: TicketCard;
  issue_url: string;
  board_url: string;
  needs_input: boolean;
  clarifying_question: string | null;
}

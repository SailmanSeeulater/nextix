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
  /** True when the latest run's tests failed (docs/phase4.md); absent on older APIs. */
  tests_failing?: boolean;
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
  /** The `test` command's result, or null when the repo has none configured. Optional
   * because a stream payload or an older API may leave it out; merges keep what we had. */
  tests?: TestsResult | null;
  /** Test report and screenshots. Only GET /api/tickets/{id} carries them; run payloads
   * on the streams don't, so merges keep the list we already have. */
  artifacts?: Artifact[];
  /** Review steps that went wrong (setup, app start, a screenshot route, a rejected file). */
  review_errors?: ReviewError[] | null;
}

/** result.json's `tests` (docs/phase4.md). */
export interface TestsResult {
  command: string;
  exit_code: number;
  passed: boolean;
  duration_s: number;
}

export type ArtifactKind =
  | "test_report"
  | "screenshot_before"
  | "screenshot_after"
  | "screenshot_diff";

/** A stored file of a run, served same-origin at `url` (through the /api proxy). */
export interface Artifact {
  id: string;
  /** An ArtifactKind; typed as string so an unknown future kind is skipped, not a crash. */
  kind: string;
  /** The route for screenshots ("/settings"), the command for a test report. */
  label: string | null;
  url: string;
  /** Kind-specific: width/height, diff_pixels/diff_pct, exit_code/passed/duration_s/truncated. */
  meta: Record<string, unknown> | null;
}

/** One entry of the manifest's `errors`: a review step that failed without failing the run. */
export interface ReviewError {
  step: string;
  label: string | null;
  message: string;
}

/** A CI check run on the PR's head commit (newest per name). */
export interface CheckRun {
  id: number;
  name: string;
  /** queued | in_progress | completed; typed as string so a new value still renders. */
  status: string;
  /** success | failure | neutral | cancelled | skipped | timed_out | action_required | stale */
  conclusion: string | null;
  html_url: string | null;
  app_name: string | null;
  started_at: string | null;
  completed_at: string | null;
}

export interface TicketChecks {
  /** False when GitHub refused (the App lacks the Checks permission). */
  connected: boolean;
  runs: CheckRun[];
}

/** A ticket with its issue body and every run, newest first. */
export interface TicketDetail extends TicketCard {
  body: string | null;
  runs: RunDetail[];
  /** The commit the open PR points at; CI checks are for this sha. */
  pr_head_sha?: string | null;
  /** Optional so an older API still renders; the Checks tab then shows no CI. */
  checks?: TicketChecks;
}

/** GET /api/tickets/{id}/diff */
export interface TicketDiff {
  pr_number: number;
  head_sha: string | null;
  /** The unified diff GitHub returns for the PR, capped at 2 MB. */
  diff: string;
  /** True when the diff was cut at the cap, or GitHub wouldn't send one that large. */
  truncated: boolean;
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

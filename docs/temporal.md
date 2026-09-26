# Moving nexTix from Celery to Temporal (design note, not implemented)

nexTix runs agents with Celery on Redis: one `execute_run` task per run, a beat-driven
reaper that fails silent runs, and database row locks to keep the run state machine
consistent. That is enough for one machine and a handful of runs a day. This note
describes what a move to [Temporal](https://temporal.io) would look like if nexTix ever
needs durable, long-running, many-worker execution. Nothing here is built.

## Why it might be worth it

| Today (Celery) | With Temporal |
|---|---|
| A run is one long task; if the worker dies mid-run, the run is failed (`worker_restarted`) and must be retried from scratch | Each step is an activity with its own retry policy; the workflow resumes after the last completed step |
| The reaper (beat, every 30 s) turns lost heartbeats into failures | Activity heartbeats and heartbeat timeouts are built in |
| "Wait for the sandbox" is a polling loop in `_wait` | A long-running activity that heartbeats, or a signal from the callback endpoint |
| Cancel = a database status the worker notices on its next poll | Workflow cancellation propagates into the running activity |
| A review that arrives mid-run is parked in `tickets.pending_review_id` | A signal to the ticket's workflow, queued in workflow state |
| Per-repo limits = advisory lock + Celery retry every 20 s | One workflow per repo (or a semaphore workflow) serialising runs |

## Mapping

- **Workflow `TicketWorkflow(ticket_id)`**, one per ticket, long-lived. Receives signals:
  `start_run(trigger, task snapshot)`, `review(review_id)`, `answer()`, `cancel()`,
  `rerun()`. It owns the rule "one active run per ticket" and the pending-review queue.
- **Child workflow `RunWorkflow(run_id)`** for each attempt, with the same states as today
  (`queued → claimed → running → succeeded | failed | timed_out | needs_input |
  cancelled`), recorded in Postgres by an activity at every transition so the board, the
  API, and GitHub comments keep working unchanged.
- **Activities** (idempotent, each with a timeout and retry policy):
  1. `load_repo_config` (GitHub read, retry on 5xx).
  2. `mint_tokens` (read-only clone token, later the write token).
  3. `start_sandbox` → container id.
  4. `await_sandbox` (long-running; heartbeats while the container runs; returns on exit).
  5. `collect_outputs` (result.json, bundle, artifacts; stored under `ARTIFACT_DIR`).
  6. `push_branch` (never forced; safe to retry because a repeat push is a no-op).
  7. `open_or_update_pr`, `post_comment`, `record_transition`.
  8. `remove_sandbox` (in a `finally`/cleanup path; also run on cancellation).
- **The sandbox callback endpoint** stays as it is (HMAC per run). It may additionally
  signal `RunWorkflow` on `state: running`, which replaces the claimed → running
  transition done by the API today.
- **Reaper**: replaced by the `await_sandbox` heartbeat timeout; a lost worker means the
  activity times out and is retried on another worker, which re-attaches to the container
  by its `nextix.run_id` label (or fails the run if the container is gone).

## What stays the same

GitHub stays the source of truth; the board is still a projection of it. The database
schema, the sandbox image and its contracts (`docs/phase3.md`, `docs/phase4.md`), the web
app, the CLI, and the MCP server do not change.

## Migration order

1. Run a Temporal server next to the stack (the `temporalio/auto-setup` image for local
   use) and add a `temporal-worker` service built from the backend image.
2. Implement the activities as thin wrappers around the existing functions in
   `nextix/runs/` (they already take their dependencies through `WorkerContext`).
3. Write `RunWorkflow` and route new runs to it behind a setting
   (`NEXTIX_ORCHESTRATOR=temporal`), keeping Celery as the default.
4. Move signals (reviews, answers, cancel, rerun) from the webhook/API handlers to
   `TicketWorkflow`, still writing the same rows.
5. Once stable, retire `execute_run`, the reaper, and the `runner` Celery worker; keep
   Celery (or cron) only for housekeeping, or move that to Temporal schedules too.

## Costs of switching

A Temporal server (or Temporal Cloud) to operate, another SDK to learn, and workflow code
that must stay deterministic. For a single-user, one-run-at-a-time setup the current
Celery design is simpler and sufficient; switch when runs need to survive worker restarts
mid-way, or when many workers and repos run agents concurrently.

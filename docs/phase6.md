# Phase 6: scale and extras — plan and contracts

Scope (spec §11): per-repo concurrency limits and global worker concurrency; a cost
dashboard; an MCP server with `create_ticket`, `list_tickets`, `get_ticket_status`;
optional model routing by label; a documented (not implemented) path from Celery to
Temporal. The owner asked for phases to continue without check-ins (2026-09-26).

## Decisions beyond the spec

1. **Concurrency is two numbers.** `RUNNER_CONCURRENCY` (default 1) is how many agent
   runs the `runner` service executes at once (Celery `--concurrency`), and
   `NEXTIX_MAX_RUNS_PER_REPO` (default 1) caps active runs per repository so two agents
   never race on one repo. On a Claude plan, keep `RUNNER_CONCURRENCY=1`: parallel runs
   share one allowance.
2. **The per-repo cap is enforced at claim time.** When a worker picks up a run whose repo
   is already at its cap, the run stays `queued` and the task is retried after a short
   delay (Celery `retry`, 20 s, no limit while the run is queued). Order is first come,
   first served per repo: a run only starts if no older queued run for the same repo is
   waiting. The board keeps showing "Waiting for a worker".
3. **Sandboxes are owned by the worker that started them.** Containers get a
   `nextix.worker=<hostname>` label and each worker only sweeps its own, so the Phase 3
   orphan sweep stays correct with more than one run at a time.
4. **Costs come from runs.** `GET /api/costs?days=30` returns totals and breakdowns by
   day, repo, and ticket (cost, input and output tokens, run count), computed from
   `runs` (the only place costs live). On a Claude plan the figures are Claude Code's
   estimates, labelled as such. The board gets a `/costs` page.
5. **The MCP server lives in the CLI.** `nextix mcp` starts a stdio MCP server that uses
   the CLI's saved login (API URL and token), so there is one place for credentials and
   one install. Tools: `create_ticket(prompt, repo?, labels?, triage?)`,
   `list_tickets(repo?, column?)`, `get_ticket_status(ticket)` where `ticket` is a
   ticket id, an issue number (with the default repo), or `owner/name#n`. Register it
   with `claude mcp add nextix -- nextix mcp`. Tickets created this way have
   `created_via = "mcp"`.
6. **Model routing by label.** `NEXTIX_MODEL_ROUTES`, e.g.
   `nextix:small=claude-haiku-4-5,nextix:large=claude-opus-5-5`, picks the agent model
   from the ticket's labels (first match in the order listed); otherwise
   `ANTHROPIC_MODEL`. The chosen model is recorded on the run (`runs.model`) and shown
   on the ticket page. Labels are set by trusted people only in practice (triage picks
   from existing labels; the webhook trust rule applies to runs).
7. **Temporal** is documented in `docs/temporal.md`: what maps to workflows and
   activities, how heartbeats and cancellation carry over, and a migration order. No code.

## Database (migration 0007)

- `runs.model text null`.

## API

- `GET /api/costs?days=<1..365>` →
  `{"days", "total": Totals, "by_day": [{"date", ...Totals}], "by_repo": [{"repo", ...Totals}],
    "by_ticket": [{"ticket_id", "repo", "issue_number", "title", ...Totals}] (top 20)}` where
  `Totals = {"cost_usd", "input_tokens", "output_tokens", "runs"}`.
- `RunDetail.model: str | null`.

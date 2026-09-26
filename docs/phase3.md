# Phase 3: agent runner — plan and contracts

The owner asked for Phase 3 to proceed without a check-in (2026-09-25). This file records
the plan, the decisions the spec left open, and the contracts between the pieces, so the
runner, the worker, the API and the web app agree.

## Decisions beyond the spec

1. **The sandbox never gets push rights.** It receives a *read-only* installation token
   (contents:read, scoped to the one repo) to clone. The runner commits and writes a git
   bundle of `nextix/issue-<n>`. After the container stops, the worker copies the bundle
   out and pushes that one branch with a separate write token. The agent (which has Bash)
   therefore cannot push anything, to any branch. This is how "push only `nextix/*`" is
   enforced in the worker, structurally rather than by inspection.
2. **Issue assignment is skipped.** GitHub does not let a GitHub App's bot account be an
   issue assignee, so "assign to the bot" becomes the "picked up" comment instead.
3. **Claude credential per run** follows `NEXTIX_CLAUDE_AUTH` (see `nextix/claude_auth.py`):
   the container gets exactly one of `CLAUDE_CODE_OAUTH_TOKEN` or `ANTHROPIC_API_KEY`.
4. **One run at a time.** Runs go to a dedicated Celery queue (`runs`) consumed with
   concurrency 1, because subscription runs share the owner's personal allowance.
   Per-repo limits and wider concurrency are Phase 6.
5. **Needs input.** The agent is told to write its question to `.nextix/needs-input.md` and
   stop without other changes when it cannot proceed. The runner reports `needs_input`.
6. **Heartbeats update the run, not the transcript.** A heartbeat callback sets
   `runs.last_heartbeat` and is not stored as a `run_events` row.
7. **Per-run callback secret** lives in `runs.callback_secret` (32 random bytes, hex) and is
   cleared when the run finishes.
8. **`.nextix.yml`** parsing is Phase 4. Phase 3 uses settings defaults: max turns 60,
   timeout 30 min, max cost $3, tools Read/Edit/Write/Bash/Glob/Grep.
9. **Sandboxes get their own network.** They join `nextix_agents`, which only the API
   shares, so they can reach `http://api:8000` (callbacks) and the internet but not
   Postgres or Redis by name. On Docker Desktop, containers can also reach every port
   published on the host through the host gateway (192.168.65.254), even ports bound to
   127.0.0.1, so compose no longer publishes Postgres or Redis at all
   (`docker-compose.ports.yml` opts back in for development). The sandbox's
   `host.docker.internal` / `gateway.docker.internal` point at 127.0.0.1. Residual risk:
   a sandbox can still reach the API and web ports (both need the API token) and any
   other project's published ports on the host. An egress allowlist (proxy) is Phase 6.
10. **Only the `runner` service has the Docker socket.** It is a Celery worker for the
    `runs` queue (concurrency 1) and runs as root because the socket is root-owned. The
    reaper runs on the ordinary `worker` (no socket): it fails silent runs, and the runner
    removes their containers (its `_wait` loop sees the terminal status, and every new
    run first sweeps containers whose run is finished).
11. **A run the queue refuses is cancelled** (`exit_reason = "enqueue_failed"`), so a
    Redis outage never leaves a ticket stuck behind a queued run that nobody will claim.

## What starts a run

- `POST /api/tickets` (CLI and web) when triage finds the request actionable, or when
  triage is off. Tickets that land in Needs Input wait for an answer.
- `issues.labeled` with `nextix`, sent by the repo owner or a user in
  `NEXTIX_ALLOWED_GITHUB_USERS`, on an open issue without `nextix:needs-input`. The app's
  own label events (from tickets it created) come from its bot and are ignored.
- `POST /api/tickets/{id}/runs` (Retry on the board).

## Run lifecycle (worker, `execute_run`)

1. Claim: `queued → claimed` with `SELECT … FOR UPDATE SKIP LOCKED`; set `agent_id`
   (`agent-<6 hex>`), `callback_secret`, `started_at`, `last_heartbeat = now`.
2. Comment on the issue: "🤖 Picked up by agent-xxxxxx."
3. Mint the read-only clone token; start the container (below); `claimed → running`
   when the runner's first `state: running` callback arrives (or immediately after start).
4. Wait for exit or the hard timeout (`timeout_min` + 2 min grace, then kill).
5. Read `/work/.nextix-out/result.json` and `/work/.nextix-out/branch.bundle` from the
   container (Docker `get_archive`), then remove it (always, in `finally`).
6. Outcome:
   - `succeeded` with commits: push the bundle's branch, open or update the PR, link it
     to the ticket, comment "✅ Opened PR #n", `running → succeeded`.
   - `succeeded` without commits: `failed` with `exit_reason = "no_changes"`.
   - `needs_input`: comment the question, add `nextix:needs-input`, `running → needs_input`.
   - anything else: `failed` / `timed_out` with a comment.

## Sandbox container

- Image `AGENT_IMAGE` (default `nextix-agent:latest`), built from `agent-image/`.
- Runs as uid 1000, `cap_drop=ALL`, `no-new-privileges`, `mem_limit=4g`,
  `nano_cpus=2e9`, `pids_limit=512`, no volumes, no Docker socket.
- Joins the `nextix_agents` network so it can reach `http://api:8000` (see decision 9).
- Labels: `nextix.run_id=<uuid>` (the reaper finds containers by this label).

### Environment (the whole contract; nothing else is passed)

| Variable | Meaning |
|---|---|
| `NEXTIX_RUN_ID` | run UUID |
| `NEXTIX_CALLBACK_URL` | `http://api:8000/api/internal/runs/<id>/events` |
| `NEXTIX_CALLBACK_SECRET` | per-run HMAC key (hex) |
| `NEXTIX_REPO` | `owner/name` |
| `NEXTIX_DEFAULT_BRANCH` | e.g. `main` |
| `NEXTIX_BRANCH` | `nextix/issue-<n>` |
| `NEXTIX_ISSUE_NUMBER` | issue number |
| `NEXTIX_TASK_JSON` | JSON: `{"title", "body", "extra_instructions", "review_comments": []}` |
| `NEXTIX_MODEL` | model id |
| `NEXTIX_MAX_TURNS` | int |
| `NEXTIX_TIMEOUT_MIN` | int |
| `NEXTIX_MAX_COST_USD` | float |
| `NEXTIX_ALLOWED_TOOLS` | comma-separated |
| `GITHUB_TOKEN` | read-only installation token (clone only) |
| `CLAUDE_CODE_OAUTH_TOKEN` *or* `ANTHROPIC_API_KEY` | exactly one |

### Output files (read by the worker after exit)

- `/work/.nextix-out/result.json`:
  `{"status": "succeeded"|"needs_input"|"failed"|"timed_out", "summary": str,
    "question": str|null, "commits": int, "exit_reason": str|null,
    "input_tokens": int, "output_tokens": int, "cost_usd": float, "num_turns": int}`
- `/work/.nextix-out/branch.bundle`: `git bundle` of `NEXTIX_BRANCH` (only when commits > 0).

## Callback API (runner → API)

`POST /api/internal/runs/{run_id}/events`, JSON body `{"events": [Event, ...]}`, header
`X-Nextix-Signature: sha256=<hex HMAC-SHA256(secret, raw body)>`. 401 on a bad signature,
410 when the run is already finished. Events:

| `kind` | `payload` |
|---|---|
| `heartbeat` | `{}` — updates `last_heartbeat` only |
| `state` | `{"status": "running"}` — runner started the agent |
| `log` | `{"text": str}` — runner progress lines ("Cloning…", "Committing…") |
| `message` | `{"text": str}` — assistant text |
| `tool_use` | `{"id": str, "name": str, "input": object}` |
| `tool_result` | `{"tool_use_id": str, "content": str, "is_error": bool}` (content truncated to 20k chars) |
| `usage` | `{"input_tokens": int, "output_tokens": int, "cost_usd": float}` — cumulative |
| `error` | `{"text": str}` |

The API redacts anything shaped like a token (`sk-ant-…`, `ghs_…`, `ghp_…`, `github_pat_…`)
before storing, stores every event except heartbeats in `run_events`, and publishes each
stored event to Redis channel `nextix:run:<id>`.

## Read API (web → API)

- `GET /api/tickets/{id}` → `TicketDetail`: the `TicketCard` fields plus
  `body: str|null`, `runs: RunDetail[]` (newest first).
  `RunDetail`: `id, attempt, trigger, status, agent_id, branch, started_at, finished_at,
  last_heartbeat, exit_reason, input_tokens, output_tokens, cost_usd, queued_at`.
- `GET /api/runs/{id}/events?after=<event id>&limit=<n≤500>` → `{"events": RunEvent[],
  "next_after": int|null}`; `RunEvent = {id, ts, kind, payload}`.
- `GET /api/runs/{id}/stream` (SSE): replays stored events (event name = `kind`, data =
  `RunEvent` JSON, SSE `id` = event id), then a `ready` event, then live events. Also emits
  `run.updated` with a `RunDetail` when the run's status changes.
- `POST /api/tickets/{id}/runs` → retry: 201 with `RunDetail`; 409 if a run is active.
- `POST /api/runs/{id}/cancel` → 202; the worker stops the container and marks `cancelled`.

Board events gain `run.state` with `{"ticket_id", "run": RunDetail}`; the board keeps
using `ticket.updated` for card changes.

## Reaper (Celery beat, every 30 s)

- `claimed`/`running` with `last_heartbeat` older than 90 s → `failed`,
  `exit_reason = "heartbeat_lost"`, kill the container (by label), comment.
- `queued` for more than 10 minutes → the card shows "No worker available" (the board
  derives this from `queued_at`; nothing is failed).
- The reaper has no Docker socket; the runner removes the dead run's container (see
  decision 10).

## Done when (spec)

A simple ticket goes Todo → Doing → In Review with a real PR; killing the container
mid-run moves the ticket to Failed with a comment within about 90 s; retry works.

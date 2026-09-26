# Phase 4: review surface — plan and contracts

Scope (spec §11): `.nextix.yml`, test reports, before/after screenshots with pixel diffs,
the Diff / Before-After / Checks tabs, and CI status from `check_*` webhooks.

**Done when:** a UI change on a small sample Next.js repo shows accurate before/after/diff
images, and a backend-only repo gracefully shows the diff and tests only.

## Decisions beyond the spec

1. **The worker reads `.nextix.yml`, from the default branch.** Before the sandbox starts,
   the worker fetches the file through the GitHub API at the default branch, validates it,
   and caches it in `repos.config`. The agent's branch is never consulted, so an agent (or
   a PR) cannot raise its own turn, time, or cost limits by editing the file. A missing
   file means defaults; an invalid one fails the run (`exit_reason = "bad_config"`) with
   the validation errors in the comment.
2. **Artifacts leave the sandbox the same way the bundle does.** The runner writes files
   under `/work/.nextix-out/artifacts/` with a `manifest.json`; after the container exits
   the worker copies the directory out (Docker `get_archive`), checks every file (type by
   magic bytes, size caps, no path tricks), stores it under `ARTIFACT_DIR/<run_id>/`, and
   records `artifacts` rows. No upload endpoint is exposed to the sandbox.
3. **Before = the default branch, every time.** The runner checks out the default branch
   in a separate git worktree, runs setup there, starts the app, and captures the
   "before" screenshots before the agent starts. Reruns compare against the default
   branch too, not the previous attempt.
4. **Screenshots are viewport-sized** (default 1280×800, per-route override) with
   animations disabled, so before and after have the same dimensions and pixel diffs are
   meaningful. Pixel matching uses pixelmatch (threshold 0.02 so subtle colour changes count, anti-aliasing ignored).
5. **Tests never block the PR** (spec). The runner runs `test` after the agent, stores the
   output as a `test_report` artifact, and the result is shown in the PR body, the closing
   comment, and the Checks tab.
6. **The Diff tab shows the PR diff from GitHub** (the source of truth), fetched by the
   API on demand. A run without a PR shows no diff.
7. **CI checks** come from `check_run` / `check_suite` webhooks and are also fetched on
   demand when the ticket page is opened (at most once a minute per PR head), so the tab
   is right even if a webhook was missed. Both need the GitHub App's **Checks: read**
   permission; without it the Checks tab says CI isn't connected and still shows tests.
8. **Sample repo:** `SailmanSeeulater/nextix-board` doubles as the sample. First as a
   backend-only Node repo (no `app` section), then with a minimal Next.js app added.

## `.nextix.yml` (validated with Pydantic, unknown keys rejected)

```yaml
setup:                  # list of shell commands, run in the repo root (max 20)
  - npm ci
test: npm test          # optional; one shell command
app:                    # optional; omit for non-visual repos
  start: npm run dev    # long-running command that serves the app
  port: 3000
  ready_path: /         # polled until it returns < 500
  ready_timeout_s: 90   # 5..600
  screenshots:          # 1..10 routes
    - path: /
    - path: /settings
      viewport: { width: 1280, height: 800 }   # 320..3840 × 240..2160
agent:                  # every key optional; defaults come from .env
  max_turns: 60         # 1..200
  timeout_min: 30       # 1..120
  max_cost_usd: 3.00    # 0.1..50
  allowed_tools: [Read, Edit, Write, Bash, Glob, Grep]   # subset of the known tools
  extra_instructions: | # up to 8000 characters
    Follow existing code style.
```

Command strings are at most 1000 characters. `screenshots[].path` must start with `/`.

## Sandbox environment (additions to docs/phase3.md)

| Variable | Meaning |
|---|---|
| `NEXTIX_CONFIG_JSON` | `{"setup": [str], "test": str\|null, "app": {...}\|null}`: the validated `setup`, `test`, and `app` sections. Agent limits keep arriving through the existing variables (`NEXTIX_MAX_TURNS`, `NEXTIX_TIMEOUT_MIN`, `NEXTIX_MAX_COST_USD`, `NEXTIX_ALLOWED_TOOLS`), now resolved from `.nextix.yml` over `.env`; `extra_instructions` arrives in `NEXTIX_TASK_JSON`. Missing variable = `{"setup": [], "test": null, "app": null}`. |

`app` inside `NEXTIX_CONFIG_JSON`:
`{"start": str, "port": int, "ready_path": str, "ready_timeout_s": int,
  "screenshots": [{"path": str, "viewport": {"width": int, "height": int}}]}`
(viewport always filled in by the worker).

## Runner order of work

1. Clone, prepare `nextix/issue-<n>` (as in Phase 3).
2. If `app` is set: `git worktree add --detach /work/base origin/<default>`, run `setup`
   there, start the app, wait for `ready_path`, capture every route as **before**, stop
   the app (whole process group), remove the worktree.
3. Run `setup` in the repo (so the agent can run the tests itself).
4. Run the agent (Phase 3 behaviour; `extra_instructions` added to the prompt).
5. If the agent's changes touched dependency manifests or lockfiles, run `setup` again.
6. If `test` is set: run it (bash, repo root, its own 10-minute cap within the run's
   deadline), keep stdout+stderr (last 200 KB), exit code, and duration.
7. If `app` is set and the agent succeeded: start the app in the repo, capture **after**,
   compute diffs, stop the app.
8. Commit, bundle, write the manifest and `result.json` (always, as before).

Each step posts `log` events ("Running setup: npm ci", "Tests failed (exit 1, 12 s)",
"Captured 2 before screenshots"). A step that fails (setup, app start, screenshots) is
logged and recorded in the manifest; it never fails the run by itself. A `setup` failure
before the agent is reported to the agent in its prompt ("setup failed: …").

## Output files (additions)

`/work/.nextix-out/artifacts/manifest.json`:

```json
{
  "artifacts": [
    {"kind": "test_report", "label": "npm test", "file": "test-report.txt",
     "meta": {"exit_code": 1, "passed": false, "duration_s": 12.3, "truncated": false}},
    {"kind": "screenshot_before", "label": "/settings", "file": "shots/settings-before.png",
     "meta": {"width": 1280, "height": 800}},
    {"kind": "screenshot_after", "label": "/settings", "file": "shots/settings-after.png",
     "meta": {"width": 1280, "height": 800}},
    {"kind": "screenshot_diff", "label": "/settings", "file": "shots/settings-diff.png",
     "meta": {"width": 1280, "height": 800, "diff_pixels": 1234, "diff_pct": 1.21}}
  ],
  "errors": [{"step": "app_after", "label": null, "message": "app did not answer on :3000 within 90 s"}]
}
```

- `kind` ∈ `test_report`, `screenshot_before`, `screenshot_after`, `screenshot_diff`.
- `file` is relative to the artifacts directory; only `.png` and `.txt`.
- Caps enforced by the worker: 10 MB per file, 60 MB per run, 64 files.
- `result.json` gains `"tests": {"command": str, "exit_code": int, "passed": bool,
  "duration_s": float} | null`. Everything else is unchanged.

## Database (migration 0005)

- `check_runs`: `id bigint pk` (GitHub's check run id), `repo_id fk`, `head_sha`,
  `head_branch`, `name`, `status` (queued | in_progress | completed), `conclusion`
  (success | failure | neutral | cancelled | skipped | timed_out | action_required |
  stale | null), `html_url`, `details_url`, `app_name`, `started_at`, `completed_at`,
  `updated_at`. Index `(repo_id, head_sha)`.
- `tickets.pr_head_sha text` (mirrored from `pull_request` webhooks and the worker's PR
  call), `tickets.checks_synced_at timestamptz`.
- `repos.config` holds `{"sha": <blob sha>, "config": {...}, "error": str|null}`.

## API

- `GET /api/tickets/{id}`: `TicketDetail` gains
  - `pr_head_sha: str|null`
  - `checks: {"connected": bool, "runs": CheckRun[]}` where
    `CheckRun = {id, name, status, conclusion, html_url, app_name, started_at, completed_at}`
    for the PR's head (newest per name). `connected=false` when GitHub refused (403).
  - every `RunDetail` gains `tests: {command, exit_code, passed, duration_s}|null` and
    `artifacts: Artifact[]` where
    `Artifact = {id, kind, label, url: "/api/artifacts/<id>", meta}`.
- `GET /api/artifacts/{id}`: the file (`image/png` or `text/plain; charset=utf-8`),
  `Cache-Control: private, max-age=31536000, immutable`. Token required.
- `GET /api/tickets/{id}/diff`: `{"pr_number": int, "head_sha": str|null, "diff": str,
  "truncated": bool}` (the unified diff GitHub returns for the PR, capped at 2 MB);
  404 when the ticket has no PR; 502 when GitHub fails.
- Board SSE: check changes publish `ticket.updated` for the linked ticket.

## Web (`/tickets/[id]`)

Tabs: **Transcript** (Phase 3) · **Diff** · **Before / After** (only when the selected run
has screenshots) · **Checks**.

- **Diff:** the PR diff in a diff viewer (file list, per-file collapsible hunks, unified
  view; split view on wide screens if the library supports it). Empty state when there's
  no PR yet.
- **Before / After:** one section per route label: side-by-side before/after, a slider
  overlay (drag or arrow keys), and the diff image with `diff_pct` ("1.2% of pixels
  changed"; "No visible change" at 0). Errors from the manifest shown in place.
- **Checks:** the selected run's test report first (passed/failed, command, duration,
  output in monospace, failures prominent), then CI check runs for the PR head with
  status and links; "CI isn't connected" note when `connected` is false; "No CI checks
  reported" when empty.
- Board cards: a small "tests failing" marker when the latest run's tests failed.

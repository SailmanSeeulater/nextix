# nexTix — Agent Task Board

A ticketing system for coding agents. Describe a change; nexTix turns it into a
structured GitHub issue, hands it to a Claude agent running in an isolated sandbox,
shows live status on a kanban board, and opens a pull request for review.

**GitHub is the source of truth.** The board is a projection of GitHub state
(issues, PRs, labels, merge status) kept in sync via webhooks. The database stores
only what GitHub doesn't have: agent runs, logs, heartbeats, costs, and screenshots.

## Layout

```
backend/      FastAPI API, Celery worker + beat, SQLAlchemy models, Alembic migrations
web/          Next.js (App Router) board and ticket review pages
agent-image/  Sandbox Docker image + runner.py (Phase 3)
cli/          `nextix` Typer CLI (Phase 2)
docs/         e2e checklist and operational docs
```

## Quick start (local, docker compose)

Prerequisites: Docker Desktop (or Docker Engine + Compose v2).

```bash
cp .env.example .env
docker compose up --build
```

| Service   | URL / role                                   |
|-----------|----------------------------------------------|
| web       | http://localhost:3000 — the board (sign in with `NEXTIX_API_TOKEN`) |
| api       | http://localhost:8000 — FastAPI, `/api/health`, `/docs` |
| worker    | Celery worker (agent runs)                   |
| beat      | Celery beat (heartbeat reaper, Phase 3)      |
| postgres  | localhost:5432, user/pass/db `nextix`        |
| redis     | localhost:6379                               |

The `api` container runs `alembic upgrade head` on start, so the schema is always current.

If 3000 or 8000 are already taken on your machine, set `WEB_PORT` / `API_PORT` in `.env`.

Verify:

```bash
curl http://localhost:8000/api/health
# {"status":"ok","version":"0.1.0","checks":{"db":"ok","redis":"ok"}}
```

Open the board and sign in with the value of `NEXTIX_API_TOKEN`. The browser never
holds that token: the Next.js server keeps it and proxies board requests to the API.

## Creating the GitHub App

nexTix acts on GitHub as a GitHub App (installation tokens, webhooks, a bot identity).
Personal access tokens are not supported.

1. **Start a webhook relay** so GitHub can reach your laptop. Create a channel at
   https://smee.io/new, then forward it to the API and leave this running:

   ```bash
   npx smee-client --url https://smee.io/<your-channel> --target http://localhost:8000/api/github/webhook
   ```

   If you set `API_PORT` in `.env`, use that port in `--target` instead of 8000.
   Always pass `--target`: without it smee posts to port 3000, which is the web app.

2. **Create the app** at GitHub, Settings, Developer settings, GitHub Apps, New GitHub App
   (or under your organization's settings).
   - **Webhook URL:** your smee.io channel URL. **Webhook secret:** a long random string.
   - **Repository permissions:**

     | Permission      | Access         | Used for                                   |
     |-----------------|----------------|--------------------------------------------|
     | Metadata        | Read           | required by GitHub                         |
     | Issues          | Read and write | mirror issues, comments, labels            |
     | Pull requests   | Read and write | link and open PRs                          |
     | Contents        | Read and write | agent pushes `nextix/*` branches (Phase 3) |
     | Checks          | Read           | CI status on the review page (Phase 4)     |
     | Commit statuses | Read           | CI status on the review page (Phase 4)     |

   - **Subscribe to events:** Issues, Issue comment, Pull request, Pull request review,
     Check run, Check suite. Installation events are always delivered.
   - **Where can this app be installed:** "Only on this account" is fine for the MVP.

3. **Generate a private key** on the app's page and save it as `secrets/github-app.pem`.
   Compose mounts `./secrets` read-only at `/run/secrets`. The folder is gitignored.

4. **Fill in `.env`:** `GITHUB_APP_ID`, `GITHUB_APP_CLIENT_ID` (the `Iv23li...` value,
   which GitHub recommends as the JWT issuer), `GITHUB_WEBHOOK_SECRET`, and
   `GITHUB_BOT_LOGIN` (`<app-slug>[bot]`). Then restart with `docker compose up -d`.

5. **Install the app** on a throwaway test repo (app page, Install App). The
   `installation` webhook adds the repo to nexTix. Create a label named `nextix`
   in that repo.

6. **Try it.** Label an issue `nextix` and it appears in Todo within seconds. Push a
   `nextix/issue-<n>` branch and open a PR from it. The card moves to In Review, and
   to Done when merged.

**Backfill** issues and PRs that existed before the app was installed, or repair any
drift (GitHub always wins):

```bash
docker compose exec api python -m nextix.sync owner/repo
```

**Protect the default branch.** The worker only ever pushes `nextix/*` branches, but
add a branch protection rule (or ruleset) requiring pull requests on the default
branch as a second line of defense.

### How the board maps GitHub state

A ticket is any issue labeled `nextix` in an enabled repo. Its column is derived, never
stored. The first matching rule wins:

| Column      | Rule                                                         |
|-------------|--------------------------------------------------------------|
| Done        | linked PR merged, or issue closed                            |
| Doing       | an agent run is queued, claimed, or running                  |
| Needs Input | `nextix:needs-input` label, or the last run asked a question |
| In Review   | open PR from the `nextix/issue-<n>` branch                   |
| Failed      | last run failed, timed out, or was cancelled                 |
| Todo        | everything else                                              |

Removing the `nextix` label, deleting, or transferring the issue takes the card off the
board.

Whenever the installation changes, nexTix asks GitHub for the full list of repos the app
can reach and matches its own list to it. Repos the app lost are disabled if they have
tickets, so run history is kept, and removed if they never had one. The sync command
does the same check, so it also repairs a stale repo list.

## The board

Every ticket is a pass, in the spirit of Apple Wallet, and every state is a stack of
passes. The newest pass in each stack is open; click any pass to open it.

- **Doing** passes show a live readout: elapsed time while the agent's heartbeat is fresh,
  or a hollow pass reading "No heartbeat" when it goes quiet for 30 seconds. Stalled passes
  move to the front of the stack.
- **Needs Input** is the brightest pass on the board: it's waiting on you.
- **The composer** at the top files a ticket in one sentence. Press Enter to create it;
  Shift+Enter adds a line. "Write it up with Claude" turns the sentence into a structured
  issue; switch it off to file your text as-is.
- **Themes**: the palette button in the top bar offers the same 15 themes as KeyUp. The
  choice is saved in your browser; until you pick one, the board follows your OS light or
  dark setting. Every theme passes an automated contrast check (`web/src/lib/themes.test.ts`).

## Creating tickets

Install the CLI and sign in (details in [cli/README.md](cli/README.md)):

```bash
pip install -e ./cli
nextix login
```

Then describe a change:

```bash
nextix new "add a dark mode toggle to settings" --repo owner/name
```

Claude turns the prompt into a GitHub issue with context, acceptance criteria, and
likely files, labeled `nextix`. If the request is too vague, the issue gets the
`nextix:needs-input` label, Claude's clarifying question is posted as a comment, and the
card lands in **Needs Input**. `--no-triage` skips Claude and files your text as-is.

Triage needs `ANTHROPIC_API_KEY` in `.env` and uses `ANTHROPIC_MODEL` (default
`claude-opus-5`). Refused requests are retried on Anthropic's recommended fallback
model automatically. Claude may only apply labels that already exist in the repo.

## Developing without compose

Backend (Python 3.12):

```bash
cd backend
python -m venv .venv && . .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
ruff check . && ruff format --check . && mypy nextix && pytest
```

Point `DATABASE_URL` / `REDIS_URL` at local services (or run just `docker compose up postgres redis`), then:

```bash
alembic upgrade head
uvicorn nextix.main:app --reload
celery -A nextix.celery_app worker --loglevel=info
```

Web (Node 22):

```bash
cd web
npm ci
npm run dev          # http://localhost:3000
npm run lint && npm run typecheck && npm test -- --run
```

## Migrations

```bash
cd backend
alembic revision --autogenerate -m "describe change"
alembic upgrade head
```

## CI

`.github/workflows/ci.yml` runs on every push and PR:

- **backend**: ruff (lint + format), mypy (strict), pytest
- **web**: eslint, tsc, vitest
- **compose-smoke**: builds the stack, waits for `api` to be healthy, curls `/api/health`

No real network calls to GitHub or Anthropic happen in CI; those are mocked.

## Roadmap

Built in phases; see the build spec.

- [x] Phase 0: scaffolding, compose, CI
- [x] Phase 1: GitHub App, webhooks, backfill, live read-only board
- [x] Phase 2: ticket creation (`nextix new`, triage, needs-input)
- [ ] Phase 3: agent runner end to end
- [ ] Phase 4: review surface (diff, screenshots, checks)
- [ ] Phase 5: feedback loop
- [ ] Phase 6: scale and extras

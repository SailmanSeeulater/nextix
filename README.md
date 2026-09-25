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
| web       | http://localhost:3000 — the board            |
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

Built in phases; see the build spec. Phase 0 (this scaffold) is complete.
Phase 1 adds the GitHub App, webhooks, and the read-only board.

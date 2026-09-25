# Product

<!-- impeccable:product-schema 1 -->

> Every fact below is inferred from the project build spec and the owner's requests in
> the build session (2026-09-25); no interview round was held. Confirm or correct them.

## Platform

web

## Users

One developer, the owner of a handful of GitHub repositories, who hands small code
changes to Claude coding agents instead of writing them. They file work from the
terminal (`nextix new "..."`) or the board, then glance at the board between other
tasks to see what the agents are doing, what is stuck, and what is ready to review.
Single-user MVP; teams and GitHub OAuth come later.

## Product Purpose

nexTix is a ticketing system for coding agents. A short description becomes a
structured GitHub issue, an agent picks it up in an isolated sandbox, and the result
comes back as a pull request with its diff, CI status, transcript, and before/after
screenshots. Success: the owner trusts the board at a glance, never has to wonder
whether an agent is silently stuck, and reviews finished work in one place.

## Positioning

The board is a live projection of GitHub, not a second tracker. Issues, PRs, labels,
and merges stay in GitHub; nexTix adds only what GitHub lacks: agent runs, liveness
(heartbeats), cost, logs, and screenshots. "Didn't get picked up" becomes visible
instead of silent.

## Operating Context

- Tickets move through six derived states: Todo, Doing, Needs Input, Failed,
  In Review, Done. The column is computed from GitHub state plus the latest run and
  is never set by hand; the only drags allowed are Failed to Todo (retry) and
  In Review to Todo (cancel and rerun), from Phase 5.
- Doing tickets carry an agent id, elapsed time, a heartbeat that goes stale after
  30 seconds, and cost so far.
- Needs Input tickets carry a clarifying question answered on the GitHub issue.
- Work arrives from the CLI, the web composer, webhooks (labeling an existing issue
  `nextix`), and later an MCP server.
- The board updates live over server-sent events and must survive reconnects.
- Runs locally via docker compose; the owner often has other projects open.

## Capabilities and Constraints

- Next.js App Router, TypeScript, Tailwind v4; the browser never holds the API
  token (a server-side proxy adds it).
- Ticket detail page, transcript, diff, before/after, and checks arrive in Phases 3 to 5.
- Undecided: multi-user roles, hosted deployment, mobile use beyond glancing.

## Brand Commitments

- Name: nexTix.
- The owner asked for the board to take inspiration from Apple Wallet (binding for
  the board redesign of 2026-09-25).

## Evidence on Hand

No customers, testimonials, metrics, or screenshots exist. Demonstration tickets used
in previews are synthetic and must be labeled as such.

## Product Principles

1. GitHub wins: never show state the board cannot back with GitHub or a recorded run.
2. Liveness over decoration: whether an agent is alive, stuck, or waiting is the most
   important thing on screen.
3. Glanceable first, detailed on demand.
4. Filing work should take one sentence.

## Accessibility & Inclusion

No product-specific requirement established; WCAG 2.2 AA is the working floor.

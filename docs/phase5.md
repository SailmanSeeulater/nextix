# Phase 5: feedback loop — plan and contracts

Scope (spec §11): `pull_request_review` → a `review_feedback` run on the same branch with
the review comments in the agent's prompt; answering a needs-input question with an issue
comment re-queues the run; board drag actions for retry and cancel-and-rerun.

**Done when:** leaving a "changes requested" review with a comment produces a new commit
on the same PR that addresses it.

## Decisions beyond the spec

1. **Only trusted reviewers start runs.** A review (or an issue comment answering a
   question) starts a run only when its author is the repo owner or in
   `NEXTIX_ALLOWED_GITHUB_USERS`, the same rule as the `nextix` label. The app's own bot
   never triggers anything. Other people's reviews are left alone.
2. **Which reviews count.** `pull_request_review.submitted` with state
   `changes_requested`, or state `commented` with a non-empty body or at least one inline
   comment. Approvals never start a run. A review on a PR that isn't from a
   `nextix/issue-<n>` branch of a tracked ticket is ignored.
3. **What the agent gets.** The review's body plus its inline comments, read from GitHub
   when the run starts (`GET /repos/{o}/{r}/pulls/{n}/reviews/{id}/comments`), as
   `review_comments: [{"path", "line", "author", "body"}]` in `NEXTIX_TASK_JSON` (the
   runner already renders these). Bodies are capped (4000 chars each, 50 comments, 60 KB
   total) so the task stays inside one environment variable. The review id is stored on
   the run (`runs.review_id`) so a retry of a feedback run re-reads the same review.
   The PR description the agent wrote is not re-sent; the issue task snapshot is.
4. **Same branch, same PR.** A feedback run checks out the existing `nextix/issue-<n>`
   (the runner already does on reruns), commits on top, and the worker pushes without
   force and updates the open PR (title, body) instead of opening another.
5. **One run at a time still holds.** If a run is already active when a review arrives,
   the review is remembered (`tickets.pending_review_id`) and a feedback run is queued as
   soon as the active run finishes; a newer review replaces an older pending one. This
   keeps "review while the agent is still working" from being lost.
6. **Answering by comment.** `issue_comment.created` on a ticket in Needs Input, by a
   trusted author who isn't the bot, removes `nextix:needs-input` and queues a run
   (trigger `retry`). The clarification builder (Phase 3) already passes the question and
   trusted replies to the agent. Comments on tickets not waiting for input do nothing.
7. **Drag actions.** On the board, a pass can be dragged from **Failed** onto **Todo**
   (retry: `POST /api/tickets/{id}/runs`) and from **In Review** onto **Todo**
   (cancel any active run, then start a new run on the same branch:
   `POST /api/tickets/{id}/rerun`). Every other drop is refused with a short explanation,
   because GitHub drives state. Keyboard users get the same actions from the ticket page
   (Retry / Run again) and from a pass's menu.

## API

- `POST /api/tickets/{id}/rerun` → 201 `RunDetail`. Cancels an active run first (like
  `POST /runs/{id}/cancel`), then queues a new run with trigger `retry`. 409 when the issue
  is closed.
- `RunDetail.trigger` gains `review_feedback`; `RunDetail` gains `review: {"id", "author",
  "state", "html_url", "comments": int} | null` for feedback runs.

## Database (migration 0006)

- `runs.review_id bigint null`, `runs.review_meta jsonb null` (author, state, html_url,
  comment count; shown on the ticket page).
- `tickets.pending_review_id bigint null`.

## Comments on the issue

- Queued by a review: "🕒 Queued for an agent (attempt n) to address @reviewer's review."
- The closing comment on a feedback run: "✅ Updated PR #n with the requested changes."

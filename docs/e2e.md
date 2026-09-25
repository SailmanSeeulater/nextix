# End-to-end checklist

Manual verification against a throwaway GitHub repo. Filled in per phase.

## Phase 0

- [ ] `cp .env.example .env`
- [ ] `docker compose up --build`
- [ ] `curl http://localhost:8000/api/health` returns `{"status":"ok",...}`
- [ ] http://localhost:3000 shows the board skeleton with a green "API ok" indicator
- [ ] `docker compose logs worker` shows the Celery worker ready with `nextix.ping` registered
- [ ] `docker compose logs beat` shows beat started without errors

## Phase 1

Setup: follow "Creating the GitHub App" in the README against a throwaway repo, with
smee forwarding running.

- [ ] Installing the app on the repo logs an `installation` delivery in `docker compose logs api`
- [ ] Opening http://localhost:3000 redirects to `/login`. A wrong token is rejected and the right one shows the board
- [ ] The board shows "live" once the event stream connects
- [ ] Labeling an issue `nextix` makes it appear in **Todo** within a few seconds, without reloading
- [ ] Editing the issue title updates the card
- [ ] Removing the `nextix` label removes the card. Re-adding it brings it back
- [ ] Pushing `nextix/issue-<n>` and opening a PR moves the card to **In Review** with a PR link
- [ ] Merging that PR moves the card to **Done**
- [ ] A PR from any other branch name does not link to the ticket
- [ ] Closing an issue without a PR moves it to **Done** and shows "closed"
- [ ] Redelivering a past delivery (app settings, Advanced) returns `{"status":"duplicate"}`
- [ ] Stop the api container, label another issue, start it again, then run
      `docker compose exec api python -m nextix.sync owner/repo`. The missed issue appears
- [ ] The repo filter at the top narrows the board to one repo

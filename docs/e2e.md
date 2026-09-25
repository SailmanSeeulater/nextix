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

## Phase 2

Setup: `ANTHROPIC_API_KEY` set in `.env`, `docker compose up -d`, `pip install -e ./cli`,
`nextix login` (API URL uses your `API_PORT`), default repo set to the throwaway repo.

- [ ] `nextix new "add a CONTRIBUTING.md that explains how to run the tests"` prints the issue and board links
- [ ] The issue has a short imperative title and Context, Acceptance criteria (checkboxes), and Likely files sections, plus the original request in a collapsed block
- [ ] The issue is labeled `nextix`; any other labels already existed in the repo
- [ ] The card appears in **Todo** on the board without reloading
- [ ] `nextix new "make it better"` lands in **Needs Input**, with a sensible question posted as a comment and the `nextix:needs-input` label
- [ ] Removing `nextix:needs-input` on GitHub moves that card to **Todo**
- [ ] `nextix new "Add a LICENSE file" --no-triage --label chore` files the text as-is with labels `nextix` and `chore`
- [ ] `nextix ls` and `nextix ls --column needs_input` list the right tickets
- [ ] `nextix open <n>` opens the issue in the browser
- [ ] With `ANTHROPIC_API_KEY` removed, `nextix new "x"` explains that triage needs the key and suggests `--no-triage`
- [ ] `nextix new "x" --repo you/not-installed` says the repo isn't connected

## Board redesign (Wallet passes, KeyUp themes)

- [ ] The board shows six stacks of passes; the newest pass in each is open and clicking another opens it
- [ ] A running agent's pass shows a large elapsed readout; stop its heartbeat for 30s and it turns hollow with "No heartbeat" and moves to the front
- [ ] Typing a sentence in the composer and pressing Enter creates the ticket; its pass grows out of the composer into Todo (or Needs Input with Claude's question shown under the composer)
- [ ] A ticket that changes state on GitHub slides from one stack to the other without a reload
- [ ] The palette button lists 15 themes; picking one recolors the board immediately and survives a reload
- [ ] At phone width, a segmented switcher shows all six states with counts

# nexTix user guide

nexTix turns a one-sentence request into a pull request. You describe a change; nexTix
files it as a GitHub issue, a Claude agent works on it in a locked-down container, and
you review the pull request it opens. GitHub stays the source of truth: every ticket is
an issue labeled `nextix`, and everything the agent does is posted there as a comment.

This guide covers setting nexTix up once, using it day to day, and fixing the usual
problems. Addresses below use the ports in your `.env` (board on **3001**, API on
**8010**); the defaults are 3000 and 8000.

---

## How a ticket moves

```
You describe a change  ──►  GitHub issue (label: nextix)  ──►  Todo
                                                              │
                                  an agent picks it up  ◄─────┘
                                            │
                                          Doing  ── asks a question ──►  Needs Input
                                            │                               │ you answer
                                            │  ◄────────────────────────────┘
                                            ├── pushes nextix/issue-N, opens PR ──►  In Review
                                            │                                           │ you merge
                                            └── fails / times out ──►  Failed         Done
```

The board's six columns are derived from GitHub, never stored separately:

| Column | Means |
|---|---|
| **Todo** | Filed, no agent run yet |
| **Doing** | A run is queued, starting, or working |
| **Needs Input** | The agent (or triage) asked you a question |
| **Failed** | The last run failed, timed out, or was cancelled |
| **In Review** | A pull request from `nextix/issue-N` is open |
| **Done** | The PR was merged or the issue was closed |

---

## 1. One-time setup

You've done most of this already. It's here so you can redo it on another machine.

### What you need

- **Docker Desktop**, running.
- **The nexTix GitHub App** (yours is `nextix-perfect`), installed on each repository
  you want agents to work on. Install it only on repos you're happy for an agent to open
  pull requests against.
- **A Claude credential**: your Claude Pro/Max plan (recommended for personal use) or an
  Anthropic API key.
- **Python 3.11+**, only if you want the command-line tool.

### The `.env` file

Copy `.env.example` to `.env` in the project folder and fill it in. The important
settings:

| Setting | What it is |
|---|---|
| `GITHUB_APP_ID`, `GITHUB_APP_CLIENT_ID` | From the GitHub App's settings page |
| `GITHUB_WEBHOOK_SECRET` | The secret you typed into the App's webhook settings |
| `GITHUB_BOT_LOGIN` | The App's bot name, e.g. `nextix-perfect[bot]` |
| `NEXTIX_API_TOKEN` | Your password for the board and the CLI. Make it long and random |
| `NEXTIX_PUBLIC_URL` | Where you open the board, e.g. `http://localhost:3001` |
| `NEXTIX_CLAUDE_AUTH` | `subscription` (your plan) or `api_key` |
| `CLAUDE_CODE_OAUTH_TOKEN` | Your plan token (see below) |
| `ANTHROPIC_API_KEY` | Only if you use `api_key` |
| `NEXTIX_ALLOWED_GITHUB_USERS` | Other GitHub users allowed to start agents (optional) |
| `WEB_PORT`, `API_PORT` | Ports on your machine for the board and the API |

The App's private key goes in the `secrets/` folder, as described in the README. Never
commit `.env` or anything in `secrets/`.

### Your Claude plan token

In a terminal where Claude Code is installed:

```bash
claude setup-token
```

Paste the token it prints into `.env` as `CLAUDE_CODE_OAUTH_TOKEN=...` (one line, no
spaces or quotes) and set `NEXTIX_CLAUDE_AUTH=subscription`. The token lasts one year.

> Anthropic allows plan tokens only through Claude Code (which is what nexTix uses) and
> only for your own use. If anyone else starts using your nexTix, switch to an API key.
> Agent runs count against the same 5-hour and weekly limits as your own Claude use.

### Receive GitHub's webhooks

GitHub has to reach your computer to tell nexTix about label changes, PRs, and merges.
Your webhook URL is a smee.io channel. Keep this running whenever nexTix is running:

```bash
npx smee-client --url https://smee.io/<your-channel> --target http://localhost:8010/api/github/webhook
```

(Tickets you create from the board or CLI still work without it, but the board won't
notice merges, closed issues, or labels you change on GitHub until it's running again.)

### Build and start

From the project folder:

```bash
docker compose build agent
```

```bash
docker compose up -d --build
```

The first command builds the agent sandbox image (about 1 GB, a few minutes). Rebuild it
only when `agent-image/` changes. The second starts everything else.

Check it's healthy:

```bash
curl http://localhost:8010/api/health
```

You want `"status":"ok"` and `"claude":"subscription"` (or `"api_key"`).

### Sign in

- **Board:** open `http://localhost:3001` and enter your `NEXTIX_API_TOKEN`.
- **CLI (optional):**

  ```bash
  pip install -e ./cli
  ```

  ```bash
  nextix login
  ```

  Enter `http://localhost:8010` as the API URL, your token, and a default repo such as
  `SailmanSeeulater/nextix-board`.

---

## 2. Everyday use

### Start and stop

```bash
docker compose up -d
```

```bash
docker compose down
```

`down` keeps your data (it lives in a Docker volume). Start smee again whenever you start
nexTix.

### File a ticket

Three ways, all ending in the same GitHub issue:

**On the board.** Type the change in the box at the top and press **Enter**
(Shift+Enter for a new line). With **Write it up with Claude** on, Claude turns your
sentence into a proper issue: context, acceptance criteria, and likely files. Switch it
off to file your words exactly as typed.

**From the terminal.**

```bash
nextix new "add a dark mode toggle to settings"
```

Add `--repo owner/name` for another repo, `--label ui` for extra labels, or `--no-triage`
to skip Claude's write-up.

**On GitHub.** Open an issue as usual and add the `nextix` label. When you (the repo
owner) or someone in `NEXTIX_ALLOWED_GITHUB_USERS` adds the label, an agent starts. If
anyone else adds it, the ticket shows on the board but no agent starts until you press
**Start run**.

**Writing a good request.** Say what should change and how you'll know it's done. "Add a
dark mode toggle to the settings page that remembers the choice" works better than "make
the app nicer". If Claude can't tell what you mean, the ticket goes to **Needs Input**
with a question instead of guessing.

### What happens next

As soon as a ticket is actionable, an agent run is queued. On the issue you'll see
comments like these (from a real run):

1. 🕒 **Queued for an agent (attempt 1).**
2. 🤖 **Picked up by agent-a3dc90.**
3. ▶️ **agent-a3dc90 started working on `nextix/issue-3`.**
4. One of:
   - ✅ **Opened PR #4 from `nextix/issue-3` ($0.14, 7 turns).** The agent's work is
     committed to `nextix/issue-3` and the pull request closes the issue when merged.
     Its description is the agent's own summary of what it changed and how it checked,
     plus a link to the transcript and a footer with cost, tokens, and duration.
   - 🤖 **agent-… needs more information**, with a question for you (see below).
   - ❌ **Failed: …** or ⏱️ **Timed out: …**, with the reason in plain words.

A small change like "add a CONTRIBUTING.md and link it from the README" takes about a
minute from Retry to pull request.

Each run works from the issue **as it was when the run started**. If you (or anyone)
edit the issue later, the change applies to the next run, not the one in progress. This
also stops someone from rewriting an issue after you've approved it.

One run happens at a time. If you file several tickets, they wait in **Doing** as
"Waiting for a worker" and run in order.

### Watch the agent work

On the board, open a pass and click **Details** (or go to
`http://localhost:3001/tickets/<id>`). The ticket page shows:

- **The header**: status, agent, branch, elapsed time, tokens, cost, and the PR link.
  A running pass reading **No heartbeat** means the agent hasn't reported for 30 seconds;
  after 90 seconds nexTix gives up on it (see Troubleshooting).
- **The transcript**, live: what the agent says, each tool it uses (reading files,
  running commands, editing), and the results. Click a tool row to see its full input
  and output. **Jump to latest** follows new lines.
- **Earlier attempts**, from the attempt selector under the header.
- **Issue description**, collapsed above the transcript.

### Review and merge

The pull request is an ordinary GitHub PR from `nextix/issue-N`: read the diff, run it,
and merge when you're happy. Merging moves the ticket to **Done** and closes the issue.

If it needs changes, you have two options:

- **Fix it yourself** on the branch, like any PR.
- **Send the agent back**: edit the issue description to say what to change, then press
  **Retry** on the ticket page. The next run starts from the existing branch, adds
  commits, and updates the same PR. To start over instead, close the PR and delete the
  `nextix/issue-N` branch first. (Agents reading PR review comments directly is planned
  for a later version.)

### When the agent asks a question

The ticket moves to **Needs Input**, the issue gets the `nextix:needs-input` label, and
the question is posted as a comment. To answer:

1. **Reply on the issue** with your answer (a normal comment).
2. **Remove the `nextix:needs-input` label** on GitHub. That starts a new run.
   (Or press **Retry** on the ticket page; it removes the label for you.)

The new run gets the question and your reply. Only replies from you (the repo owner) or
`NEXTIX_ALLOWED_GITHUB_USERS` are passed to the agent; other people's comments are
ignored. You can also answer by editing the issue description instead.

### Retry, start, and cancel

- **Start run** / **Retry** appears on Todo, Failed, and Needs Input tickets when nothing
  is running. Each retry is a new attempt on the same branch.
- **Cancel run** appears while a run is active. You'll be asked to confirm; the agent's
  container is stopped within a few seconds and nothing is pushed.

### Tell nexTix how to build your repo (`.nextix.yml`)

Add a `.nextix.yml` file to the root of a repo to have every run install dependencies,
run your tests, and take screenshots. Commit it to the **default branch** (`main`):
that's the only copy nexTix reads, so an agent can't loosen its own limits by editing it.

A repo with tests but no web UI:

```yaml
test: npm test
```

A web app, with screenshots of two pages:

```yaml
setup:
  - npm ci
test: npm test
app:
  start: npx next dev --port 3000 --hostname 127.0.0.1
  port: 3000
  screenshots:
    - path: /
    - path: /settings
```

What each part does:

- **`setup`**: commands run before the agent starts (and again after, if it changed
  your dependencies), so the agent can run your tests while it works.
- **`test`**: run after the agent finishes. The result appears on the ticket page, in
  the pull request, and in the closing comment. Failing tests never stop the PR; they're
  just shown clearly, and the board pass gets a "tests failing" marker.
- **`app`**: how to start your app. nexTix screenshots each `path` on `main` before the
  agent starts and again after it finishes, and highlights every pixel that changed.
  `ready_timeout_s` (default 90) is how long to wait for the app to answer.
- **`agent`** (optional): `max_turns`, `timeout_min` (up to 120), `max_cost_usd`,
  `allowed_tools`, and `extra_instructions` (house rules the agent should follow) for
  this repo, overriding the defaults below.

If the file has a mistake, the next run stops straight away and the issue comment lists
exactly what's wrong (for example `agent.timeout_min: Input should be less than or equal
to 120`). Fix it on `main` and press **Retry**.

### Limits on each run

| Limit | Default | Setting |
|---|---|---|
| Time | 30 minutes | `AGENT_DEFAULT_TIMEOUT_MIN` |
| Cost estimate | $3 | `AGENT_DEFAULT_MAX_COST_USD` |
| Agent turns | 60 | `AGENT_MAX_TURNS` |
| Memory / CPU | 4 GB / 2 CPUs | `AGENT_MEM_LIMIT`, `AGENT_CPUS` |
| Tools | Read, Edit, Write, Bash, Glob, Grep | `AGENT_ALLOWED_TOOLS` |

On your plan nothing is billed per run; the cost figure is Claude Code's estimate, and
it still stops a run that would use too much of your allowance. After changing any of
these, restart the runner: `docker compose restart runner`.

### Themes

The palette button in the top bar switches between 15 colour themes. Your choice is
saved in the browser.

---

## 3. Troubleshooting

| You see | What it means | What to do |
|---|---|---|
| **Waiting for a worker** for a long time, or **No worker available** | The run is queued but the `runner` service isn't taking it | `docker compose ps` should show `runner` as Up. Start it with `docker compose up -d runner`, and check `docker compose logs runner` |
| **❌ Failed: the agent stopped reporting (no heartbeat for 90 s)** | The sandbox died or hung mid-run | Press **Retry**. If it keeps happening, check Docker Desktop has enough memory |
| **❌ Failed: your Claude plan's usage limit was reached** | Your 5-hour or weekly allowance is used up | Wait for it to reset, then **Retry**. File tickets without triage meanwhile |
| **❌ Failed: Claude rejected the credential** | The plan token is wrong or expired | Run `claude setup-token`, update `.env`, then `docker compose up -d` |
| **❌ Failed: the agent finished without changing any files** | The agent decided nothing needed changing, or got stuck | Read the transcript, make the issue more specific, **Retry** |
| **❌ Failed: pushing the branch failed** | Someone pushed to `nextix/issue-N` in a way the agent's work can't sit on top of | Close the PR, delete the branch on GitHub, **Retry** |
| **❌ Failed: the sandbox could not start or crashed** | The agent image is missing, Docker had a problem, or the container was stopped from outside (e.g. in Docker Desktop) | `docker compose build agent` if the image is missing, then **Retry** |
| **❌ Failed: …has no commits yet** | The repository is empty, so there's nothing to branch from | Push a first commit (a README is enough), then **Retry** |
| **❌ Failed: `.nextix.yml` on `main` can't be used** | The file has a typo, an unknown key, or a value out of range | The comment lists each problem. Fix the file on `main`, then **Retry** |
| **⏱️ Timed out** | The run hit its time limit | Split the ticket into smaller ones, or raise `AGENT_DEFAULT_TIMEOUT_MIN` |
| Labels or merges on GitHub don't show on the board | Webhooks aren't arriving | Start smee (above). Check the App's **Advanced → Recent Deliveries** on GitHub |
| Board says it can't reach the API | The API container is down | `docker compose ps`, then `docker compose logs api` |
| "Write it up with Claude" is greyed out | No Claude credential is configured | Set `CLAUDE_CODE_OAUTH_TOKEN` (or an API key) in `.env` and restart |

Every run's full story is in three places: the issue's comments, the ticket page's
transcript, and `docker compose logs runner`.

---

## 4. Safety: what the agent can and can't do

- **It works in a throwaway container**: no access to your files, your Docker, or your
  other containers. It runs as an unprivileged user with memory, CPU, and process limits,
  and the container is deleted when the run ends. (That's the short-lived container named
  `nextix-run-…` you may notice in Docker Desktop while a run is going.)
- **It can't push.** Its GitHub token is read-only. nexTix itself pushes the agent's work,
  and only ever to `nextix/issue-N`, never forced, never to `main`. Merging is always yours.
- **It can't reach nexTix's database or Redis**; sandboxes are on their own network,
  and compose doesn't publish those two on your machine.
- **It can reach the internet, and other ports published on your machine.** On Docker
  Desktop any container can reach services you publish on your computer (for example
  another project's database on port 5433). Don't leave sensitive, password-less
  services published while agents run.
- **Secrets are scrubbed**: tokens that appear in the transcript or comments are replaced
  with `[redacted]`.
- **Issue text is treated as a task, not as orders.** The agent is told the rules can't be
  changed by what an issue says. Still, only let people you trust start agents
  (`NEXTIX_ALLOWED_GITHUB_USERS`), because an agent does what its ticket asks.
- **Protect `main` on GitHub** (Settings → Branches → add a rule requiring a pull request)
  as a second line of defence.

---

## 5. Looking after it

- **Renew the plan token yearly**: `claude setup-token`, update `.env`,
  `docker compose up -d`.
- **After pulling new nexTix code**: `docker compose up -d --build`, and
  `docker compose build agent` if `agent-image/` changed.
- **Back up** by keeping GitHub as the record: tickets, questions, and PRs all live on
  GitHub, so the board can be rebuilt from the issues.
- **Clean up branches**: GitHub can delete `nextix/issue-N` branches automatically after
  merge (Settings → General → "Automatically delete head branches").

---

## Cheat sheet

| Task | Command |
|---|---|
| Start everything | `docker compose up -d` |
| Stop everything | `docker compose down` |
| Forward webhooks | `npx smee-client --url https://smee.io/<channel> --target http://localhost:8010/api/github/webhook` |
| Health check | `curl http://localhost:8010/api/health` |
| Rebuild the agent image | `docker compose build agent` |
| Follow agent runs | `docker compose logs -f runner` |
| New ticket | `nextix new "describe the change"` |
| List tickets | `nextix ls` (add `--column failed` to filter) |
| Open an issue / its PR | `nextix open 7` / `nextix open 7 --pr` |
| Board | `http://localhost:3001` |

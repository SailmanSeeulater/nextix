# agent-image

The sandbox every agent run executes in. The worker starts one container per run from
this image; `runner.py` (the entrypoint) clones the repo, runs Claude Code on the issue,
streams what the agent does back to the API, and leaves a commit bundle for the worker
to push. The full contract is in [docs/phase3.md](../docs/phase3.md); this file is the
short version.

## What's in the image

| Piece | Why |
|---|---|
| Python 3.12 (`python:3.12-slim`) | runs `runner.py` |
| git, ca-certificates, curl | clone, commit, bundle |
| Node.js 22 + npm (NodeSource) | repos often need `npm ci` to run their tests |
| `claude-agent-sdk` (bundles Claude Code) + `httpx` | the agent, and the callback client |
| `/usr/local/bin/claude` | a link to the SDK's bundled Claude Code binary |
| user `agent` (uid 1000), `WORKDIR /work` | the run never has root |

No secrets are baked in. Everything run-specific arrives as environment variables.

```bash
docker build -t nextix-agent:latest agent-image
docker run --rm --entrypoint claude nextix-agent:latest --version   # Claude Code is there
docker run --rm --entrypoint id nextix-agent:latest                 # uid=1000(agent)
```

## How the worker runs it

`uid 1000`, `cap_drop=ALL`, `no-new-privileges`, `mem_limit=4g`, `nano_cpus=2e9`,
`pids_limit=512`, no volumes, no Docker socket, on the compose network (to reach
`http://api:8000`), label `nextix.run_id=<uuid>`. Also pass `init=True` (Docker's tiny
init): the runner is otherwise PID 1, and the agent's shell commands can leave orphaned
processes that only an init reaps.

### Environment (nothing else is passed)

`NEXTIX_RUN_ID`, `NEXTIX_CALLBACK_URL`, `NEXTIX_CALLBACK_SECRET`, `NEXTIX_REPO`
(`owner/name`), `NEXTIX_DEFAULT_BRANCH`, `NEXTIX_BRANCH` (`nextix/issue-<n>`),
`NEXTIX_ISSUE_NUMBER`, `NEXTIX_TASK_JSON` (`{"title", "body", "extra_instructions",
"review_comments": []}`), `NEXTIX_MODEL`, `NEXTIX_MAX_TURNS`, `NEXTIX_TIMEOUT_MIN`,
`NEXTIX_MAX_COST_USD`, `NEXTIX_ALLOWED_TOOLS` (comma-separated), `GITHUB_TOKEN` (read-only,
clone only), and exactly one of `CLAUDE_CODE_OAUTH_TOKEN` / `ANTHROPIC_API_KEY`.

## What a run does

1. **Clone** `github.com/$NEXTIX_REPO` into `/work/repo`. The token travels as an HTTP
   header set through `GIT_CONFIG_*` variables for that one command, so it is never in
   the clone URL, the process arguments, or `.git/config`.
2. **Branch**: check out `NEXTIX_BRANCH` from the remote if it exists (a rerun), else
   create it from `NEXTIX_DEFAULT_BRANCH`. Commits are by `nexTix agent`.
3. **Agent**: send `state: running`, then run Claude Code in `/work/repo` with
   `permission_mode="bypassPermissions"` (the container is the sandbox), no settings
   sources, the allowed tools, and the turn, cost, and model limits. The system prompt
   holds the issue and the rules: stay focused, never touch CI config, never push, and
   write any blocking question to `.nextix/needs-input.md` and stop. It is capped at
   100 KB (rules first, so only the issue text is ever cut), because the SDK passes it on
   Claude Code's command line and Linux rejects a single argument over 128 KiB.
   `NEXTIX_TIMEOUT_MIN` counts from container start, so the worker's 2-minute grace is
   left for the commit. Claude Code runs through the SDK's client (`sdk_query`), whose
   `disconnect()` stops the process for certain when a run ends early (timeout, 410,
   usage limit, or a rejected credential, which Claude Code would otherwise retry for
   minutes).
4. **Finish**:
   - a question in `.nextix/needs-input.md` means `needs_input`, and nothing is committed;
   - otherwise everything except `.nextix/` and `.github/workflows/` is committed as
     `nextix: <title> (#<n>)`, and if the branch is ahead of its base (the default branch,
     or the remote branch on a rerun) a self-contained bundle of it is written;
   - unless those commits contain one of the run's own credentials (the agent's
     environment holds the Claude token): then the run fails and nothing is bundled.

Before Claude Code starts, the runner drops `GITHUB_TOKEN` and `NEXTIX_CALLBACK_SECRET`
from its environment and marks itself non-dumpable, so the agent (same uid, has Bash) can
read neither. The Claude credential stays, because Claude Code needs it.

## Output

`/work/.nextix-out/result.json` is always written, even if the runner crashes:

```json
{"status": "succeeded", "summary": "...", "question": null, "commits": 1,
 "exit_reason": null, "input_tokens": 1200, "output_tokens": 340,
 "cost_usd": 0.42, "num_turns": 4}
```

`status` is `succeeded`, `needs_input`, `failed`, or `timed_out`. `summary` is the agent's
final message (at most 4000 characters) or, on failure, what went wrong. `exit_reason`:

| Value | Meaning |
|---|---|
| `max_turns` / `max_cost` | the agent hit `NEXTIX_MAX_TURNS` / `NEXTIX_MAX_COST_USD` |
| `usage_limit` | the Claude plan's usage limit (or the API account's limit) was reached |
| `auth_failed` | Claude rejected the credential |
| `timeout` | the agent ran past `NEXTIX_TIMEOUT_MIN` (status `timed_out`) |
| `clone_failed` | the repo or its default branch could not be fetched |
| `agent_error` | Claude Code failed for another reason |
| `secret_in_changes` | the agent's commits contain a credential of this run; nothing is pushed |
| `cancelled` | the API answered 410, or the container was sent SIGTERM |
| `runner_error` | a bad environment or a bug in the runner |

A `succeeded` result with `commits: 0` means the agent changed nothing; the worker turns
that into `no_changes`. `/work/.nextix-out/branch.bundle` exists only when `commits > 0`.

Exit code: 0 for `succeeded`/`needs_input`, 1 for `failed`/`timed_out`, 2 when stopped by
a 410 or a signal.

## Callbacks

Events go to `NEXTIX_CALLBACK_URL` as `POST {"events": [...]}` with
`X-Nextix-Signature: sha256=<hex HMAC-SHA256>`, keyed with the `NEXTIX_CALLBACK_SECRET`
string as given, over the exact body bytes. They are batched (sent at least every 2 s or
20 events); 5xx, 429, and network errors are retried with backoff; a 410 stops the agent
and the runner exits at once. A heartbeat goes out every 10 s for the whole run.

| Event | From |
|---|---|
| `message` | the agent's text |
| `tool_use` / `tool_result` | each tool call and its output (output cut to 20,000 characters) |
| `usage` | the final token counts and cost |
| `log` / `error` | the runner's own progress and failures |

Anything shaped like a token (`sk-ant-…`, `ghs_…`, `ghp_…`, `github_pat_…`) and the run's
own secrets are replaced with `[REDACTED]` in every event and in `result.json`.

## Developing

```bash
cd agent-image
python -m venv .venv && . .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
ruff check . && ruff format --check . && mypy runner.py && pytest -q
```

The tests need git but not Docker, network access, or Claude: the Agent SDK, the HTTP
client, and the clock are fakes, and a temporary bare repository stands in for GitHub.

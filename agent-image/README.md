# agent-image

The sandbox every agent run executes in. The worker starts one container per run from
this image; `runner.py` (the entrypoint) clones the repo, takes the "before" screenshots,
runs Claude Code on the issue, streams what the agent does back to the API, runs the
repo's tests, takes the "after" screenshots and their pixel diffs, and leaves a commit
bundle and the artifacts for the worker to collect. The full contracts are in
[docs/phase3.md](../docs/phase3.md) and [docs/phase4.md](../docs/phase4.md); this file is
the short version.

## What's in the image

| Piece | Why |
|---|---|
| Python 3.12 (`python:3.12-slim`, Debian 13) | runs `runner.py` |
| git, ca-certificates, curl | clone, commit, bundle |
| Node.js 22 + npm (NodeSource) | repos often need `npm ci` to run their tests or their app |
| `claude-agent-sdk` (bundles Claude Code) + `httpx` | the agent, and the callback client |
| Playwright for Python + Chromium (headless shell) with its OS libraries and fonts | the screenshots; the browser lives in `/ms-playwright`, root-owned and read-only |
| Pillow + pixelmatch | the pixel diffs |
| `/usr/local/bin/claude` | a link to the SDK's bundled Claude Code binary |
| user `agent` (uid 1000), `WORKDIR /work` | the run never has root |

No secrets are baked in. Everything run-specific arrives as environment variables. The
image is about 2.1 GB (570 MB compressed); Chromium and its libraries are most of the
growth from Phase 3's 1.05 GB.

```bash
docker build -t nextix-agent:latest agent-image
docker run --rm --entrypoint claude nextix-agent:latest --version   # Claude Code is there
docker run --rm --entrypoint id nextix-agent:latest                 # uid=1000(agent)
```

## How the worker runs it

`uid 1000`, `cap_drop=ALL`, `no-new-privileges`, `mem_limit=4g`, `nano_cpus=2e9`,
`pids_limit=512`, no volumes, no Docker socket, on the compose network (to reach
`http://api:8000`), label `nextix.run_id=<uuid>`, and `init=True` (Docker's tiny init
reaps the processes the agent orphans; the runner also relies on it, see below).

Chromium runs without its own sandbox (`chromium_sandbox=False`): it needs privileges the
container does not have, and the container is the sandbox. Measured under exactly these
limits: Chromium plus a small Node server peaked at 97 processes and threads (256 MB),
and Chromium plus a Next.js dev server (Turbopack), npm, and the runner at 134 (1.2 GB).
512 leaves plenty of room.

### Environment (nothing else is passed)

`NEXTIX_RUN_ID`, `NEXTIX_CALLBACK_URL`, `NEXTIX_CALLBACK_SECRET`, `NEXTIX_REPO`
(`owner/name`), `NEXTIX_DEFAULT_BRANCH`, `NEXTIX_BRANCH` (`nextix/issue-<n>`),
`NEXTIX_ISSUE_NUMBER`, `NEXTIX_TASK_JSON` (`{"title", "body", "extra_instructions",
"review_comments": []}`), `NEXTIX_MODEL`, `NEXTIX_MAX_TURNS`, `NEXTIX_TIMEOUT_MIN`,
`NEXTIX_MAX_COST_USD`, `NEXTIX_ALLOWED_TOOLS` (comma-separated), `GITHUB_TOKEN` (read-only,
clone only), exactly one of `CLAUDE_CODE_OAUTH_TOKEN` / `ANTHROPIC_API_KEY`, and
`NEXTIX_CONFIG_JSON`: the repo's validated `.nextix.yml` sections,

```json
{"setup": ["npm ci"], "test": "npm test",
 "app": {"start": "npm run dev", "port": 3000, "ready_path": "/", "ready_timeout_s": 90,
         "screenshots": [{"path": "/settings", "viewport": {"width": 1280, "height": 800}}]}}
```

Missing or empty means no setup, no test, no app. Malformed JSON, a wrong type, or a
value outside the `.nextix.yml` limits fails the run at once with `runner_error` and a
summary that names the field. Unknown keys are ignored.

The image itself sets `PLAYWRIGHT_BROWSERS_PATH=/ms-playwright` (the runner takes it out
of the environment the agent and the repo's commands see, so a repo's own Playwright
installs its browsers under `$HOME`) and `NEXTIX_SANDBOX=1` (see "Leftover processes").

## What a run does

1. **Clone** `github.com/$NEXTIX_REPO` into `/work/repo`. The token travels as an HTTP
   header set through `GIT_CONFIG_*` variables for that one command, so it is never in
   the clone URL, the process arguments, or `.git/config`.
2. **Branch**: check out `NEXTIX_BRANCH` from the remote if it exists (a rerun), else
   create it from `NEXTIX_DEFAULT_BRANCH`. Commits are by `nexTix agent`.
3. **Before screenshots** (only with an `app`): `git worktree add --detach /work/base
   origin/<default>`, run `setup` there, start the app, wait for `ready_path`, capture
   every route, stop the app, and delete the worktree (files, `node_modules`, and git's
   record of it). The agent never sees it. Before is always the default branch, also on
   reruns.
4. **Setup** in `/work/repo`, so the agent can run the tests itself. Commands run one by
   one and stop at the first failure; a failure is logged, recorded in the manifest, and
   told to the agent in its prompt ("Setup failed: `npm ci` exited with 1 after 12 s",
   with the end of the output). It never fails the run.
5. **Agent**: send `state: running`, then run Claude Code in `/work/repo` with
   `permission_mode="bypassPermissions"` (the container is the sandbox), no settings
   sources, the allowed tools, and the turn, cost, and model limits. The system prompt
   holds the rules (stay focused, never touch CI config, never push, write any blocking
   question to `.nextix/needs-input.md` and stop), then what setup did, the test command,
   and the screenshot routes, then the issue, the owner's `extra_instructions`, and any
   review comments. It is capped at 100 KB (only the issue text is ever cut), because the
   SDK passes it on Claude Code's command line and Linux rejects a single argument over
   128 KiB. Claude Code runs through the SDK's client (`sdk_query`), whose `disconnect()`
   stops the process for certain when a run ends early.
6. **Commit**:
   - a question in `.nextix/needs-input.md` means `needs_input`, and nothing is committed;
   - otherwise the agent's changes are committed as `nextix: <title> (#<n>)`, except
     `.nextix/`, `.github/workflows/`, and whatever setup created or changed in step 4
     that the agent did not touch afterwards (installed files, generated files, a
     rewritten lockfile). If the branch is ahead of its base, a self-contained bundle of
     it is written, unless those commits contain one of the run's own credentials: then
     the run fails with `secret_in_changes` and nothing is bundled.
7. **Checks** (only when the agent succeeded with commits):
   - `setup` again if the agent's commit touched `package.json`, `package-lock.json`,
     `npm-shrinkwrap.json`, `pnpm-lock.yaml`, `yarn.lock`, `requirements*.txt`,
     `pyproject.toml`, `poetry.lock`, `uv.lock`, or `Pipfile*` (or if setup failed before);
   - `test`, in the repo root: its combined output (the last 200 KB, colours stripped,
     secrets redacted) becomes `artifacts/test-report.txt`;
   - the **after** screenshots (same steps as before, in `/work/repo`) and a pixel diff
     per route.

   These run after the commit, so they check exactly what the pull request holds and
   nothing they write can end up in it. Tests never block the pull request.
8. **Guard the bundle** (after step 7 only): the tests and the app ran the repo's own
   code as the agent's user, so whatever they left running is stopped, and if the bundle
   is no longer byte-for-byte the one written in step 6, the branch is put back on the
   commit made there, checked for the run's credentials again, and bundled again.
9. **Write** the artifacts and `manifest.json`, then `result.json` (always, even when the
   runner crashes).

Every step posts `log` events ("Running setup (1/2): npm ci", "Captured 2 before
screenshot(s).", "Tests failed (exit 1, 12 s).", "/settings: 1.21% of pixels changed.").
A failed step (setup, the app, a route, a diff) is logged and recorded in the manifest's
`errors`; it never fails the run by itself.

### The repo's commands

`setup`, `test`, and `app.start` run as `bash -lc <command>`, each in its own process
group, with a scrubbed environment: no `GITHUB_TOKEN`, no callback secret, no Claude
credential, no other `NEXTIX_*`, `CLAUDE*`, or `ANTHROPIC*` variable, nothing named like a
secret (`*TOKEN*`, `*SECRET*`, `*PASSW*`, `*API_KEY*`, ...), and nothing whose value holds
one of the run's secrets. `CI=true` is set so test runners do not wait in watch mode. The
app also gets `PORT=<port>`, `HOST=127.0.0.1`, and `HOSTNAME=127.0.0.1`; its start command
must serve on that port. Each setup or test command may run for at most 10 minutes (and
never past its step's deadline); when it ends, whatever it left running in its process
group is stopped too. The app is stopped the same way: SIGTERM to the whole group, then
SIGKILL after 5 seconds.

The app counts as ready when `http://127.0.0.1:<port><ready_path>` (or `[::1]`) answers
with a status below 500 within `ready_timeout_s`. If the port is already taken before the
app starts, the step is skipped rather than screenshotting whatever holds it.

### Screenshots and diffs

Headless Chromium through Playwright, one fresh browser context per route: the route's
viewport (default 1280x800), device scale 1, reduced motion, service workers blocked,
`en-US`, UTC. The runner waits for `load`, up to 3 s of network idle, and the fonts, then
takes a viewport-sized PNG with animations disabled (so an infinite spinner does not show
up as a change). A navigation error, an HTTP status of 400 or more, or a timeout records
an error for that route and the others go on.

Diffs use pixelmatch (threshold 0.1; anti-aliased pixels are detected and not counted):
changed pixels in red, anti-aliasing in yellow, everything else the before image faded.
Screenshots of different sizes are both padded to the larger size on neutral grey first.
Only the box around the changed pixels goes through pixelmatch, which is pure Python; the
count is the same as for the whole image. `diff_pct` is the percentage of all pixels,
to 2 decimals.

### Time budget

The whole run fits in `NEXTIX_TIMEOUT_MIN` (the worker kills the container 2 minutes
after that; the grace is for writing the results). When `test` or `app` is configured,
the agent is stopped early enough to keep **min(10 minutes, 30% of the run)** for the
checks after it (9 minutes of the default 30). Before the agent, the before screenshots
and setup may use at most half of the agent's window, so the agent always keeps at least
half; the before screenshots get the first 60% of that. After the agent, setup again may
use at most 60% of what is left. A step with less than 5 seconds left is skipped and
recorded. Without `test` and `app`, the agent gets the whole timeout, as in Phase 3.

### Leftover processes

After the before screenshots, after the agent, and after the tests and the after
screenshots, the runner stops every other process of the agent's user (a dev server the
agent left running, a watcher): nothing it started can hold the app's port, change files
while the runner commits or bundles, or run during the tests.
This only happens in the real sandbox: the image sets `NEXTIX_SANDBOX=1`, and the runner
must be PID 1 or a child of Docker's init.

### Security

Before Claude Code starts, the runner drops `GITHUB_TOKEN` and `NEXTIX_CALLBACK_SECRET`
from its environment and marks itself non-dumpable, so the agent (same uid, has Bash)
cannot read them from the runner. The Claude credential stays, because Claude Code needs
it; the repo's own commands, Chromium, Playwright's driver, and the runner's own git
commands never get it, and the runner's git runs no hooks or fsmonitor.

**Known gap:** with `init=True`, PID 1 is Docker's init (`docker-init`), running as uid
1000 with the container's whole environment, and it is dumpable: any process in the
sandbox can read `GITHUB_TOKEN`, `NEXTIX_CALLBACK_SECRET`, and the Claude credential from
`/proc/1/environ` (verified in this image). The runner cannot close this from inside
(Yama blocks writing to an ancestor's memory); the worker has to stop passing the secrets
through the environment (for example, a file copied in before start that the runner
reads and deletes) or start the container without Docker's init.

The before screenshots and every other artifact stay in the runner's memory until the
end, so neither the agent nor the repo's code can alter them; then
`/work/.nextix-out/artifacts/` is replaced as a whole (anything planted there is gone),
and the output directory is checked to be a real directory.

## Output

`/work/.nextix-out/result.json` is always written, even if the runner crashes:

```json
{"status": "succeeded", "summary": "...", "question": null, "commits": 1,
 "exit_reason": null, "input_tokens": 1200, "output_tokens": 340,
 "cost_usd": 0.42, "num_turns": 4,
 "tests": {"command": "npm test", "exit_code": 1, "passed": false, "duration_s": 12.3}}
```

`status` is `succeeded`, `needs_input`, `failed`, or `timed_out`. `summary` is the agent's
final message (at most 4000 characters) or, on failure, what went wrong. `tests` is null
when no test is configured, when the agent did not succeed or changed nothing, or when no
time was left to run them. Its `exit_code` follows the shell: 124 when nexTix stopped the
tests after 10 minutes, 128 + N when signal N killed them. `exit_reason`:

| Value | Meaning |
|---|---|
| `max_turns` / `max_cost` | the agent hit `NEXTIX_MAX_TURNS` / `NEXTIX_MAX_COST_USD` |
| `usage_limit` | the Claude plan's usage limit (or the API account's limit) was reached |
| `auth_failed` | Claude rejected the credential |
| `timeout` | the agent ran past its deadline (status `timed_out`) |
| `clone_failed` | the repo or its default branch could not be fetched |
| `agent_error` | Claude Code failed for another reason |
| `secret_in_changes` | the agent's commits contain a credential of this run; nothing is pushed |
| `cancelled` | the API answered 410, or the container was sent SIGTERM |
| `runner_error` | a bad environment (including `NEXTIX_CONFIG_JSON`) or a bug in the runner |

A `succeeded` result with `commits: 0` means the agent changed nothing; the worker turns
that into `no_changes`. `/work/.nextix-out/branch.bundle` exists only when `commits > 0`.

`/work/.nextix-out/artifacts/manifest.json` is written at the end of every run that got
past the environment check (empty lists when nothing was configured):

```json
{"artifacts": [
   {"kind": "test_report", "label": "npm test", "file": "test-report.txt",
    "meta": {"exit_code": 1, "passed": false, "duration_s": 12.3, "truncated": false}},
   {"kind": "screenshot_before", "label": "/settings", "file": "shots/settings-before.png",
    "meta": {"width": 1280, "height": 800}},
   {"kind": "screenshot_after", "label": "/settings", "file": "shots/settings-after.png",
    "meta": {"width": 1280, "height": 800}},
   {"kind": "screenshot_diff", "label": "/settings", "file": "shots/settings-diff.png",
    "meta": {"width": 1280, "height": 800, "diff_pixels": 1234, "diff_pct": 1.21}}],
 "errors": [{"step": "app_after", "label": null,
             "message": "app did not answer on :3000 within 90 s"}]}
```

Files are `.png` or `.txt`, relative to the artifacts directory. Screenshot labels are the
route paths; file names are made from them (`/` is `index`). The runner keeps within the
worker's caps (10 MB per file, 60 MB in all, 64 files counting the manifest): the test
report goes first, then each route's before, after, and diff; whatever does not fit is
left out and recorded as an `artifacts` error. Error `step`s: `setup_before`,
`app_before`, `screenshot_before` (the default branch), `setup`, `setup_rerun`, `test`,
`app_after`, `screenshot_after`, `diff`, `artifacts`. `label` is the command or route an
error concerns, or null.

Exit code: 0 for `succeeded`/`needs_input`, 1 for `failed`/`timed_out`, 2 when stopped by
a 410 or a signal.

## Callbacks

Events go to `NEXTIX_CALLBACK_URL` as `POST {"events": [...]}` with
`X-Nextix-Signature: sha256=<hex HMAC-SHA256>`, keyed with the `NEXTIX_CALLBACK_SECRET`
string as given, over the exact body bytes. They are batched (sent at least every 2 s or
20 events); 5xx, 429, and network errors are retried with backoff; a 410 stops the agent
and the runner exits at once. A heartbeat goes out every 10 s for the whole run,
including setup, tests, and screenshots.

| Event | From |
|---|---|
| `message` | the agent's text |
| `tool_use` / `tool_result` | each tool call and its output (output cut to 20,000 characters) |
| `usage` | the final token counts and cost |
| `log` / `error` | the runner's own progress and failures |

Anything shaped like a token (`sk-ant-…`, `ghs_…`, `ghp_…`, `github_pat_…`) and the run's
own secrets are replaced with `[REDACTED]` in every event, the test report, the manifest,
and `result.json`.

## Developing

```bash
cd agent-image
python -m venv .venv && . .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
ruff check . && ruff format --check . && mypy runner.py tests && pytest -q
```

The tests need git but not Docker, network access, or Claude: the Agent SDK, the HTTP
client, the shell, the browser, and the clock are fakes, and a temporary bare repository
stands in for GitHub. The tests of real process groups need Linux (they are skipped on
Windows and macOS), and one test drives the real Chromium when Playwright's browsers are
installed. To run everything as the sandbox would, inside the image:

```bash
docker run --rm --init --cap-drop ALL --security-opt no-new-privileges:true \
  --pids-limit 512 --memory 4g -v "$PWD/agent-image:/src:ro" --entrypoint bash \
  nextix-agent:latest -c 'pip install --user -q pytest pytest-asyncio &&
    cp -r /src /tmp/src && cd /tmp/src && rm -rf .venv && python -m pytest -q -p no:cacheprovider'
```

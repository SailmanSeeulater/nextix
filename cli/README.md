# nextix CLI

Create and track nexTix tickets from your terminal.

## Install

Needs Python 3.11 or newer. From the repo root:

```bash
pip install -e ./cli
```

`pipx install ./cli` also works if you prefer an isolated install.

## Sign in

```bash
nextix login
```

It asks for the API URL (for example `http://localhost:8000`, or whatever `API_PORT`
is in your `.env`), your `NEXTIX_API_TOKEN`, and an optional default repo. The token
is checked against the API, then saved to `~/.config/nextix/config.toml`, readable
only by you. `NEXTIX_API_URL`, `NEXTIX_API_TOKEN` and `NEXTIX_REPO` override the file.

## Use

```bash
nextix new "add a dark mode toggle to settings" --repo owner/name --label ui
nextix new "Add CONTRIBUTING.md explaining how to run tests" --no-triage
nextix ls
nextix ls --column needs_input
nextix open 7          # the issue in your browser
nextix open 7 --pr     # its pull request
```

`new` asks Claude to turn your prompt into an issue with context, acceptance criteria
and likely files. If the request is too vague, the issue lands in **Needs Input** and
Claude's question is posted as a comment. `--no-triage` skips Claude and uses your
text as-is.

Add `--json` to `new` or `ls` for machine-readable output.

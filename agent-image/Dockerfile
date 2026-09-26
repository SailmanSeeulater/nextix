# nexTix sandbox image: one container per agent run (see README.md, docs/phase3.md and
# docs/phase4.md).
#
# The container gets a read-only clone token and one Claude credential through its
# environment at run time. Nothing secret is baked into the image.
FROM python:3.12-slim

# PLAYWRIGHT_BROWSERS_PATH: where the runner's Chromium lives (root-owned, read-only for
# the agent). The runner takes it out of the environment before the agent and the repo's
# own commands run, so a repo's own Playwright installs its browsers under $HOME as usual.
# NEXTIX_SANDBOX marks the real sandbox: only there does the runner clean up processes
# the agent left running.
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    DISABLE_AUTOUPDATER=1 \
    NODE_MAJOR=22 \
    PLAYWRIGHT_BROWSERS_PATH=/ms-playwright \
    NEXTIX_SANDBOX=1

# git for clone, commit, and bundle; Node.js 22 with npm from NodeSource, because the
# repos the agent works on often need `npm ci` to run their tests.
RUN apt-get update \
 && apt-get install -y --no-install-recommends ca-certificates curl git gnupg \
 && mkdir -p /etc/apt/keyrings \
 && curl -fsSL https://deb.nodesource.com/gpgkey/nodesource-repo.gpg.key \
    | gpg --dearmor -o /etc/apt/keyrings/nodesource.gpg \
 && echo "deb [signed-by=/etc/apt/keyrings/nodesource.gpg] https://deb.nodesource.com/node_${NODE_MAJOR}.x nodistro main" \
    > /etc/apt/sources.list.d/nodesource.list \
 && apt-get update \
 && apt-get install -y --no-install-recommends nodejs \
 && apt-get purge -y --auto-remove gnupg \
 && rm -rf /var/lib/apt/lists/* \
 && node --version && npm --version && git --version

# The unprivileged user every run executes as (the worker also forces uid 1000).
RUN groupadd --gid 1000 agent \
 && useradd --uid 1000 --gid 1000 --create-home --shell /bin/bash agent

# The Agent SDK's manylinux wheel bundles the Claude Code binary; expose it as `claude`
# too. Playwright drives Chromium for the before/after screenshots; Pillow and
# pixelmatch compute the pixel diffs. Installed as root, so the agent cannot modify the
# SDK, the browser, or the runner. Keep in step with pyproject.toml.
RUN pip install \
      "claude-agent-sdk>=0.2.160,<0.3" \
      "httpx>=0.27,<1" \
      "playwright>=1.49,<2" \
      "pillow>=11,<13" \
      "pixelmatch>=0.4,<0.5" \
 && ln -s "$(python -c 'import pathlib, claude_agent_sdk; print(pathlib.Path(claude_agent_sdk.__file__).parent / "_bundled" / "claude")')" \
    /usr/local/bin/claude \
 && claude --version

# Headless Chromium (the headless shell only: screenshots never need a headed browser)
# plus the OS libraries and fonts it needs, installed by Playwright as root. The browser
# directory is root-owned and world-readable. Chromium runs without its own sandbox: the
# container is the sandbox (non-root, no capabilities, no new privileges).
RUN playwright install --with-deps --only-shell chromium \
 && chmod -R a+rX "$PLAYWRIGHT_BROWSERS_PATH" \
 && rm -rf /var/lib/apt/lists/* /tmp/* \
 && python -c "import PIL, pixelmatch.contrib.PIL, playwright.async_api"

COPY runner.py /opt/nextix/runner.py

RUN mkdir -p /work && chown agent:agent /work \
 && mkdir -p /run/nextix && chown agent:agent /run/nextix && chmod 700 /run/nextix
WORKDIR /work
USER agent

ENTRYPOINT ["python", "/opt/nextix/runner.py"]

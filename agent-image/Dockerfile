# nexTix sandbox image: one container per agent run (see README.md and docs/phase3.md).
#
# The container gets a read-only clone token and one Claude credential through its
# environment at run time. Nothing secret is baked into the image.
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    DISABLE_AUTOUPDATER=1 \
    NODE_MAJOR=22

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
# too. Installed as root, so the agent cannot modify the SDK or the runner.
RUN pip install "claude-agent-sdk>=0.2.160,<0.3" "httpx>=0.27,<1" \
 && ln -s "$(python -c 'import pathlib, claude_agent_sdk; print(pathlib.Path(claude_agent_sdk.__file__).parent / "_bundled" / "claude")')" \
    /usr/local/bin/claude \
 && claude --version

COPY runner.py /opt/nextix/runner.py

RUN mkdir -p /work && chown agent:agent /work
WORKDIR /work
USER agent

ENTRYPOINT ["python", "/opt/nextix/runner.py"]

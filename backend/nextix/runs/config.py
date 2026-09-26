"""Per-repo `.nextix.yml`: the model, validation, and loading it for a run.

The worker reads the file from the repo's **default branch** through the GitHub API before
a sandbox starts (docs/phase4.md, decision 1), so an agent or a PR cannot raise its own
limits by editing it. A missing file means defaults; an invalid one fails the run with
the validation errors in the issue comment.
"""

import logging
from dataclasses import dataclass
from typing import Annotated, Any

import yaml
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    ValidationError,
    field_validator,
)
from sqlalchemy.ext.asyncio import AsyncSession

from nextix.config import Settings
from nextix.db.models import Repo
from nextix.github.client import GitHubClient

log = logging.getLogger(__name__)

CONFIG_PATH = ".nextix.yml"
MAX_CONFIG_BYTES = 64 * 1024
# Upper bound for timeout_min; Celery's per-run time limits are derived from it.
MAX_TIMEOUT_MIN = 120
# Claude Code tools an agent may be given. Anything else is a typo or not supported.
KNOWN_TOOLS = frozenset(
    {
        "Read",
        "Edit",
        "Write",
        "Bash",
        "Glob",
        "Grep",
        "WebFetch",
        "WebSearch",
        "NotebookEdit",
        "TodoWrite",
    }
)

# One shell command: no NUL (the sandbox can't pass one to a shell).
Command = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=1000, pattern=r"^[^\x00]*$"),
]
# A URL path on the app: starts with "/", no control characters.
RoutePath = Annotated[
    str, StringConstraints(strip_whitespace=True, pattern=r"^/[^\x00-\x1f\x7f]*$", max_length=500)
]


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Viewport(_Strict):
    width: int = Field(default=1280, ge=320, le=3840)
    height: int = Field(default=800, ge=240, le=2160)


class Screenshot(_Strict):
    path: RoutePath
    viewport: Viewport = Field(default_factory=Viewport)


class AppConfig(_Strict):
    start: Command
    port: int = Field(ge=1, le=65535)
    ready_path: RoutePath = "/"
    ready_timeout_s: int = Field(default=90, ge=5, le=600)
    screenshots: list[Screenshot] = Field(min_length=1, max_length=10)

    @field_validator("screenshots")
    @classmethod
    def _unique_paths(cls, value: list[Screenshot]) -> list[Screenshot]:
        paths = [s.path for s in value]
        duplicates = sorted({p for p in paths if paths.count(p) > 1})
        if duplicates:
            raise ValueError(f"each screenshot path may appear once: {', '.join(duplicates)}")
        return value


class AgentConfig(_Strict):
    max_turns: int | None = Field(default=None, ge=1, le=200)
    timeout_min: int | None = Field(default=None, ge=1, le=MAX_TIMEOUT_MIN)
    max_cost_usd: float | None = Field(default=None, ge=0.1, le=50)
    allowed_tools: list[str] | None = Field(default=None, min_length=1, max_length=len(KNOWN_TOOLS))
    extra_instructions: str | None = Field(default=None, max_length=8000)

    @field_validator("allowed_tools")
    @classmethod
    def _known_tools(cls, value: list[str] | None) -> list[str] | None:
        if value is None:
            return None
        unknown = sorted(set(value) - KNOWN_TOOLS)
        if unknown:
            known = ", ".join(sorted(KNOWN_TOOLS))
            raise ValueError(f"unknown tool(s) {', '.join(unknown)}; choose from {known}")
        return list(dict.fromkeys(value))  # de-duplicated, order kept


class NextixConfig(_Strict):
    setup: list[Command] = Field(default_factory=list, max_length=20)
    test: Command | None = None
    app: AppConfig | None = None
    agent: AgentConfig = Field(default_factory=AgentConfig)

    @field_validator("setup", mode="before")
    @classmethod
    def _one_command_is_a_list(cls, value: Any) -> Any:
        if value is None:
            return []
        return [value] if isinstance(value, str) else value

    def sandbox_json(self) -> dict[str, Any]:
        """The NEXTIX_CONFIG_JSON contract: setup, test and app, viewports filled in."""
        return {
            "setup": list(self.setup),
            "test": self.test,
            "app": self.app.model_dump(mode="json") if self.app else None,
        }


class ConfigError(Exception):
    """`.nextix.yml` could not be used. The message is written for the repo owner."""


def parse_config(text: str) -> NextixConfig:
    """Parse and validate `.nextix.yml`. Raises ConfigError with readable problems."""
    if len(text.encode("utf-8")) > MAX_CONFIG_BYTES:
        raise ConfigError(f"the file is larger than {MAX_CONFIG_BYTES // 1024} KB")
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        mark = getattr(exc, "problem_mark", None)
        where = f" (line {mark.line + 1}, column {mark.column + 1})" if mark else ""
        problem = getattr(exc, "problem", None) or "it is not valid YAML"
        raise ConfigError(f"{problem}{where}") from exc
    if data is None:
        return NextixConfig()
    if not isinstance(data, dict):
        raise ConfigError("the top level must be a mapping of keys (setup, test, app, agent)")
    try:
        return NextixConfig.model_validate(data)
    except ValidationError as exc:
        raise ConfigError(_describe(exc)) from exc


def _describe(exc: ValidationError) -> str:
    lines = []
    for error in exc.errors()[:10]:
        where = ".".join(str(part) for part in error["loc"]) or "(top level)"
        message = error["msg"]
        if error["type"] == "extra_forbidden":
            message = "not a recognised key"
        lines.append(f"{where}: {message}")
    more = len(exc.errors()) - len(lines)
    if more > 0:
        lines.append(f"…and {more} more")
    return "\n".join(lines)


@dataclass(frozen=True)
class RunLimits:
    max_turns: int
    timeout_min: int
    max_cost_usd: float
    allowed_tools: str  # comma-separated, as NEXTIX_ALLOWED_TOOLS

    @classmethod
    def resolve(cls, settings: Settings, config: NextixConfig) -> "RunLimits":
        agent = config.agent
        tools = (
            ",".join(agent.allowed_tools) if agent.allowed_tools else settings.agent_allowed_tools
        )
        return cls(
            max_turns=agent.max_turns or settings.agent_max_turns,
            timeout_min=agent.timeout_min or settings.agent_default_timeout_min,
            max_cost_usd=agent.max_cost_usd or settings.agent_default_max_cost_usd,
            allowed_tools=tools,
        )


@dataclass(frozen=True)
class LoadedConfig:
    config: NextixConfig
    sha: str | None  # blob sha of the file, None when there is none
    source: str  # "file" | "default" | "cache"


async def load_repo_config(session: AsyncSession, gh: GitHubClient, repo: Repo) -> LoadedConfig:
    """Read `.nextix.yml` from the default branch, validate it, and cache it on the repo.

    Raises ConfigError when the file exists but can't be used. If GitHub can't be reached,
    the last good cached config is used (or the defaults, when there is none).
    """
    try:
        found = await gh.get_file(
            repo.installation_id, repo.owner, repo.name, CONFIG_PATH, ref=repo.default_branch
        )
    except Exception:
        log.exception(
            "could not read %s from %s; using the cached copy", CONFIG_PATH, repo.full_name
        )
        cached = (repo.config or {}).get("config")
        if isinstance(cached, dict):
            try:
                return LoadedConfig(NextixConfig.model_validate(cached), None, "cache")
            except ValidationError:
                pass
        return LoadedConfig(NextixConfig(), None, "default")

    if found is None:
        await _cache(session, repo, sha=None, config=NextixConfig(), error=None)
        return LoadedConfig(NextixConfig(), None, "default")
    text, sha = found
    try:
        config = parse_config(text)
    except ConfigError as exc:
        await _cache(session, repo, sha=sha, config=None, error=str(exc))
        raise
    await _cache(session, repo, sha=sha, config=config, error=None)
    return LoadedConfig(config, sha, "file")


async def _cache(
    session: AsyncSession,
    repo: Repo,
    *,
    sha: str | None,
    config: NextixConfig | None,
    error: str | None,
) -> None:
    repo.config = {
        "sha": sha,
        "config": config.model_dump(mode="json") if config else None,
        "error": error,
    }
    await session.commit()

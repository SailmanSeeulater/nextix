"""Typed views of the parts of GitHub payloads nexTix reads.

Unknown fields are ignored so GitHub can add fields without breaking us.
The same models parse both webhook payloads and REST responses.
"""

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict


class _Gh(BaseModel):
    model_config = ConfigDict(extra="ignore")


class GhUser(_Gh):
    login: str
    type: str | None = None


class GhLabel(_Gh):
    name: str


class GhRepoRef(_Gh):
    """Minimal repository object. Installation payloads only carry these fields."""

    id: int
    name: str
    full_name: str

    @property
    def owner_login(self) -> str:
        return self.full_name.split("/", 1)[0]


class GhRepository(GhRepoRef):
    owner: GhUser
    default_branch: str
    private: bool = False


class GhIssue(_Gh):
    number: int
    title: str
    body: str | None = None
    state: str
    labels: list[GhLabel] = []
    user: GhUser | None = None
    # Present only when the "issue" is really a pull request (REST issue lists).
    pull_request: dict[str, Any] | None = None

    @property
    def label_names(self) -> list[str]:
        return [label.name for label in self.labels]


class GhComment(_Gh):
    id: int
    body: str | None = None
    user: GhUser | None = None
    created_at: datetime


class GhRef(_Gh):
    ref: str
    sha: str | None = None
    repo: GhRepoRef | None = None


class GhApp(_Gh):
    name: str | None = None


class GhCheckSuiteRef(_Gh):
    head_branch: str | None = None


class GhCommitStatus(_Gh):
    context: str
    state: str  # pending | success | failure | error
    target_url: str | None = None
    description: str | None = None
    updated_at: datetime | None = None


class GhCheckRun(_Gh):
    """A check run, as in check_run webhooks and GET .../commits/{sha}/check-runs."""

    id: int
    name: str
    head_sha: str
    status: str
    conclusion: str | None = None
    html_url: str | None = None
    details_url: str | None = None
    started_at: datetime | None = None
    completed_at: datetime | None = None
    app: GhApp | None = None
    check_suite: GhCheckSuiteRef | None = None


class GhPullRequest(_Gh):
    number: int
    state: str  # open | closed
    html_url: str | None = None
    merged: bool | None = None  # webhook payloads
    merged_at: str | None = None  # REST list responses
    head: GhRef
    base: GhRef

    @property
    def mirrored_state(self) -> str:
        """open | closed | merged, as stored on tickets.pr_state."""
        if self.merged or self.merged_at:
            return "merged"
        return self.state


class GhInstallation(_Gh):
    id: int
    account: GhUser | None = None

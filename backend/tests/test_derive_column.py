import pytest

from nextix.tickets.schemas import Column
from nextix.tickets.service import PrView, RunView, TicketView, derive_column

NX = ["nextix"]
NX_NEEDS = ["nextix", "nextix:needs-input"]

# (description, issue_state, labels, latest_run_status, pr_state, expected)
CASES: list[tuple[str, str, list[str], str | None, str | None, Column]] = [
    # Todo
    ("fresh ticket", "open", NX, None, None, Column.TODO),
    ("succeeded run, no PR", "open", NX, "succeeded", None, Column.TODO),
    ("PR closed unmerged, last run ok", "open", NX, "succeeded", "closed", Column.TODO),
    # Doing
    ("queued", "open", NX, "queued", None, Column.DOING),
    ("claimed", "open", NX, "claimed", None, Column.DOING),
    ("running", "open", NX, "running", None, Column.DOING),
    ("review-feedback run over open PR", "open", NX, "running", "open", Column.DOING),
    ("active run beats needs-input label", "open", NX_NEEDS, "running", None, Column.DOING),
    # Needs input
    ("triage asked a question", "open", NX_NEEDS, None, None, Column.NEEDS_INPUT),
    ("agent asked a question", "open", NX, "needs_input", None, Column.NEEDS_INPUT),
    (
        "question on a ticket with open PR",
        "open",
        NX_NEEDS,
        "succeeded",
        "open",
        Column.NEEDS_INPUT,
    ),
    ("needs-input beats failed", "open", NX_NEEDS, "failed", None, Column.NEEDS_INPUT),
    # Failed
    ("failed", "open", NX, "failed", None, Column.FAILED),
    ("timed out", "open", NX, "timed_out", None, Column.FAILED),
    ("cancelled", "open", NX, "cancelled", None, Column.FAILED),
    ("failed, PR was closed", "open", NX, "failed", "closed", Column.FAILED),
    # In review
    ("open PR", "open", NX, "succeeded", "open", Column.IN_REVIEW),
    ("failed feedback run, PR still open", "open", NX, "failed", "open", Column.IN_REVIEW),
    ("open PR, no run recorded", "open", NX, None, "open", Column.IN_REVIEW),
    # Done
    ("merged and closed", "closed", NX, "succeeded", "merged", Column.DONE),
    ("merged, issue not closed yet", "open", NX, "succeeded", "merged", Column.DONE),
    ("closed without PR", "closed", NX, None, None, Column.DONE),
    ("closed while running", "closed", NX, "running", None, Column.DONE),
    ("closed with open PR", "closed", NX, "succeeded", "open", Column.DONE),
]


@pytest.mark.parametrize(
    ("issue_state", "labels", "run_status", "pr_state", "expected"),
    [c[1:] for c in CASES],
    ids=[c[0] for c in CASES],
)
def test_derive_column(
    issue_state: str,
    labels: list[str],
    run_status: str | None,
    pr_state: str | None,
    expected: Column,
) -> None:
    ticket = TicketView(issue_state=issue_state, labels=labels)
    run = RunView(status=run_status) if run_status else None
    pr = PrView(state=pr_state) if pr_state else None
    assert derive_column(ticket, run, pr) is expected


def test_every_column_is_reachable() -> None:
    assert {c[-1] for c in CASES} == set(Column)

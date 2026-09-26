"""GET /api/costs: what agent runs cost, by day, repo, and ticket (docs/phase6.md).

Costs live only on `runs` (cost_usd, tokens). On a Claude plan they are Claude Code's
estimates: nothing is billed per run, but they show how the allowance is being used.
"""

from datetime import UTC, date, datetime, timedelta
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query
from sqlalchemy import Date, Numeric, cast, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from nextix.api.auth import require_user
from nextix.claude_auth import ClaudeAuth, resolve
from nextix.config import Settings, get_settings
from nextix.db.models import Repo, Run, Ticket
from nextix.db.session import get_db

router = APIRouter(tags=["costs"], dependencies=[Depends(require_user)])

TOP_TICKETS = 20


def _totals(row: Any) -> dict[str, Any]:
    return {
        "cost_usd": round(float(row.cost_usd or 0), 4),
        "input_tokens": int(row.input_tokens or 0),
        "output_tokens": int(row.output_tokens or 0),
        "runs": int(row.runs or 0),
    }


@router.get("/costs")
async def get_costs(
    session: Annotated[AsyncSession, Depends(get_db)],
    settings: Annotated[Settings, Depends(get_settings)],
    days: Annotated[int, Query(ge=1, le=365)] = 30,
) -> dict[str, Any]:
    since = datetime.now(UTC) - timedelta(days=days)
    # A run's day is when it was queued (UTC); every run has a queued_at.
    day = cast(Run.queued_at, Date).label("day")
    measures = (
        func.coalesce(func.sum(cast(Run.cost_usd, Numeric(12, 4))), 0).label("cost_usd"),
        func.coalesce(func.sum(Run.input_tokens), 0).label("input_tokens"),
        func.coalesce(func.sum(Run.output_tokens), 0).label("output_tokens"),
        func.count(Run.id).label("runs"),
    )
    recent = Run.queued_at >= since

    total = (await session.execute(select(*measures).where(recent))).one()
    by_day_rows = (
        await session.execute(select(day, *measures).where(recent).group_by(day).order_by(day))
    ).all()
    by_repo_rows = (
        await session.execute(
            select(Repo.owner, Repo.name, *measures)
            .join(Ticket, Ticket.id == Run.ticket_id)
            .join(Repo, Repo.id == Ticket.repo_id)
            .where(recent)
            .group_by(Repo.owner, Repo.name)
            .order_by(func.sum(Run.cost_usd).desc())
        )
    ).all()
    by_ticket_rows = (
        await session.execute(
            select(Ticket.id, Ticket.issue_number, Ticket.title, Repo.owner, Repo.name, *measures)
            .join(Ticket, Ticket.id == Run.ticket_id)
            .join(Repo, Repo.id == Ticket.repo_id)
            .where(recent)
            .group_by(Ticket.id, Ticket.issue_number, Ticket.title, Repo.owner, Repo.name)
            .order_by(func.sum(Run.cost_usd).desc())
            .limit(TOP_TICKETS)
        )
    ).all()

    # Every day in the window, including days with no runs, so charts have no gaps.
    by_day_map = {row.day: _totals(row) for row in by_day_rows}
    start: date = since.date()
    empty = {"cost_usd": 0.0, "input_tokens": 0, "output_tokens": 0, "runs": 0}
    by_day = [
        {
            "date": (start + timedelta(days=i)).isoformat(),
            **by_day_map.get(start + timedelta(days=i), empty),
        }
        for i in range(days + 1)
        if start + timedelta(days=i) <= datetime.now(UTC).date()
    ]
    return {
        "days": days,
        # On a plan, costs are Claude Code's estimates, not charges.
        "estimated": resolve(settings) is ClaudeAuth.SUBSCRIPTION,
        "total": _totals(total),
        "by_day": by_day,
        "by_repo": [{"repo": f"{r.owner}/{r.name}", **_totals(r)} for r in by_repo_rows],
        "by_ticket": [
            {
                "ticket_id": str(r.id),
                "repo": f"{r.owner}/{r.name}",
                "issue_number": r.issue_number,
                "title": r.title,
                **_totals(r),
            }
            for r in by_ticket_rows
        ],
    }

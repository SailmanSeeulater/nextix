"""Periodic cleanup, run by Celery beat on the default queue.

`nextix.prune_webhook_deliveries` (daily) drops old webhook dedupe rows. The table only
needs to outlive GitHub's redelivery window (deliveries can be redelivered for a few
days), so a month of history is plenty and the table stops growing forever.
"""

import asyncio
import logging
from datetime import UTC, datetime, timedelta

from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from nextix.celery_app import celery_app
from nextix.config import get_settings
from nextix.db.models import WebhookDelivery

log = logging.getLogger(__name__)

WEBHOOK_DELIVERY_RETENTION = timedelta(days=30)


async def prune_webhook_deliveries(
    session: AsyncSession, *, older_than: timedelta = WEBHOOK_DELIVERY_RETENTION
) -> int:
    """Delete deliveries received more than ``older_than`` ago. Returns how many."""
    cutoff = datetime.now(UTC) - older_than
    result = await session.execute(
        delete(WebhookDelivery).where(WebhookDelivery.received_at < cutoff)
    )
    await session.commit()
    return int(getattr(result, "rowcount", 0) or 0)


async def _prune() -> int:
    # Each task runs in its own event loop (asyncio.run), and pooled connections can't
    # cross loops, so this gets a throwaway engine like the run tasks do.
    engine = create_async_engine(get_settings().database_url, poolclass=NullPool)
    try:
        async with async_sessionmaker(engine, expire_on_commit=False)() as session:
            return await prune_webhook_deliveries(session)
    finally:
        await engine.dispose()


@celery_app.task(name="nextix.prune_webhook_deliveries", ignore_result=True)
def prune_webhook_deliveries_task() -> int:
    removed = asyncio.run(_prune())
    log.info("pruned %d webhook deliveries older than %s", removed, WEBHOOK_DELIVERY_RETENTION)
    return removed

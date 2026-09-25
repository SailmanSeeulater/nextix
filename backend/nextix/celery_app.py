"""Celery application shared by the worker and beat processes.

Settings follow the spec: late acks, prefetch 1, hard time limits, so a
crashed worker returns its run to the queue instead of losing it.
"""

from celery import Celery

from nextix.config import get_settings

settings = get_settings()

celery_app = Celery("nextix", broker=settings.redis_url, backend=settings.redis_url)
celery_app.conf.update(
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    worker_prefetch_multiplier=1,
    task_track_started=True,
    # Hard ceiling; individual run tasks set tighter per-run limits.
    task_time_limit=60 * 60,
    task_soft_time_limit=55 * 60,
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    timezone="UTC",
    enable_utc=True,
    broker_connection_retry_on_startup=True,
    beat_schedule={},  # reaper is registered here in Phase 3
)


@celery_app.task(name="nextix.ping")
def ping() -> str:
    return "pong"

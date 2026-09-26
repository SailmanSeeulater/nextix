"""Celery application shared by the worker and beat processes.

Settings follow the spec: late acks, prefetch 1, hard time limits, so a
crashed worker returns its run to the queue instead of losing it.
"""

from celery import Celery

from nextix.config import get_settings
from nextix.log_config import configure_logging

configure_logging()

settings = get_settings()

celery_app = Celery(
    "nextix",
    broker=settings.redis_url,
    backend=settings.redis_url,
    include=["nextix.runs.tasks"],
)
celery_app.conf.update(
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    worker_prefetch_multiplier=1,
    task_track_started=True,
    # Defaults for housekeeping tasks. Agent runs get their own limits at enqueue time,
    # derived from AGENT_DEFAULT_TIMEOUT_MIN (see nextix.api.deps.run_time_limits).
    task_time_limit=60 * 60,
    task_soft_time_limit=55 * 60,
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    timezone="UTC",
    enable_utc=True,
    broker_connection_retry_on_startup=True,
    # Agent runs get their own queue, consumed by one worker with concurrency 1 (runs on a
    # Claude plan share its allowance); everything else uses the default queue.
    task_routes={"nextix.execute_run": {"queue": "runs"}},
    beat_schedule={
        "reap-runs": {"task": "nextix.reap_runs", "schedule": 30.0},
    },
)


@celery_app.task(name="nextix.ping")
def ping() -> str:
    return "pong"

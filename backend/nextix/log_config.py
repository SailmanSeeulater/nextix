"""Logging for nexTix's own modules.

Uvicorn and Celery configure only their own loggers, so without this our INFO
lines (for example why a webhook was ignored) would be silently dropped.
"""

import logging
import os

_FORMAT = "%(asctime)s %(levelname)s %(name)s: %(message)s"


def configure_logging() -> None:
    logger = logging.getLogger("nextix")
    if logger.handlers:  # already configured (e.g. uvicorn --reload re-imports)
        return
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter(_FORMAT))
    logger.addHandler(handler)
    logger.setLevel(os.environ.get("NEXTIX_LOG_LEVEL", "INFO").upper())
    logger.propagate = False  # avoid duplicate lines when a framework configures root

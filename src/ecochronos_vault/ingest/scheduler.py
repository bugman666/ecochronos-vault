from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import UTC, datetime

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.interval import IntervalTrigger

from ecochronos_vault.config import Settings
from ecochronos_vault.ingest.runner import run_daily_ingest

logger = logging.getLogger(__name__)

JOB_ID = "openaq-daily-ingest"


def start_scheduler(
    settings: Settings,
    job_func: Callable[[], object] | None = None,
    *,
    run_immediately: bool = True,
) -> BackgroundScheduler:
    """Interval job (default 86400s). Safe to start once per process."""
    scheduler = BackgroundScheduler(timezone="UTC")
    func = job_func or (lambda: run_daily_ingest(settings))
    job_kwargs: dict[str, object] = {}
    if run_immediately:
        job_kwargs["next_run_time"] = datetime.now(UTC)
    scheduler.add_job(
        func,
        IntervalTrigger(seconds=settings.ingest_interval_seconds),
        id=JOB_ID,
        replace_existing=True,
        max_instances=1,
        coalesce=True,
        **job_kwargs,
    )
    scheduler.start()
    logger.info(
        "ingest scheduler started interval_seconds=%s run_immediately=%s",
        settings.ingest_interval_seconds,
        run_immediately,
    )
    return scheduler

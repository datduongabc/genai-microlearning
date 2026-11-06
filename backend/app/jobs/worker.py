"""The job worker as a process of its own: python -m app.jobs.worker.

Lets the API and the workers scale separately, and keeps long jobs from competing with requests
for one process. Set RUN_JOB_WORKER=false on the API when this runs."""

import logging
import signal
import threading

from app.jobs import ecs_protection
from app.jobs.queue import has_active_jobs, start_worker, stop_worker
from app.logging_config import configure_app_logging

logger = logging.getLogger(__name__)


def run(stop: threading.Event) -> None:
    start_worker()
    # Lets ECS scale in only workers that have no job running; does nothing off ECS.
    protection = ecs_protection.start(stop, has_active_jobs)
    logger.info("[job_queue] worker process started")
    stop.wait()
    stop_worker()
    if protection is not None:
        protection.join(timeout=5)
    logger.info("[job_queue] worker process stopped")


def main() -> None:
    configure_app_logging()
    stop = threading.Event()
    for signum in (signal.SIGTERM, signal.SIGINT):
        signal.signal(signum, lambda *_: stop.set())
    run(stop)


if __name__ == "__main__":
    main()

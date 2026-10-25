from __future__ import annotations

from celery import Celery, signals

from app.config import get_settings
from app.utils.observability import init_sentry

settings = get_settings()


@signals.setup_logging.connect
def _use_app_logging(**_kwargs) -> None:
    # Connecting this stops Celery installing its own stderr handler. Workers
    # and beat then log like the API: one JSON object per line on stdout,
    # with a "level" field, so a platform log view (Railway's "Error logs")
    # shows only real errors instead of every INFO line written to stderr.
    from app.utils.logging import setup_logging

    setup_logging()


@signals.worker_process_init.connect
def _init_worker_sentry(**_kwargs) -> None:
    # Initialize per worker/beat process so uncaught task errors are reported.
    init_sentry("worker")


@signals.task_prerun.connect
def _refresh_instance_settings(**_kwargs) -> None:
    # Pick up LLM / SMTP settings saved on the setup screen (#220), at most
    # once a minute and only when secrets can be stored at all.
    from app.utils import secretbox

    if not secretbox.configured():
        return
    import asyncio

    try:
        asyncio.get_running_loop()
        return  # eager / in-loop execution (tests): nothing to refresh
    except RuntimeError:
        pass
    from app.services.instance_config import refresh_in_worker

    refresh_in_worker()


@signals.beat_init.connect
def _init_beat_sentry(**_kwargs) -> None:
    init_sentry("beat")


celery_app = Celery(
    "readprism",
    broker=settings.celery_broker_url,
    backend=settings.celery_result_backend,
    include=[
        "app.workers.tasks.ingest_feeds",
        "app.workers.tasks.compute_embeddings",
        "app.workers.tasks.build_digest",
        "app.workers.tasks.deliver_digest",
        "app.workers.tasks.update_interest_graph",
        "app.workers.tasks.prune_content",
        "app.workers.tasks.prune_telemetry",
        "app.workers.tasks.heartbeat",
        "app.workers.tasks.reengagement",
        "app.workers.tasks.trial_emails",
        "app.workers.tasks.paused_accounts",
        "app.workers.tasks.product_metrics",
        "app.workers.tasks.discovery",
        "app.workers.tasks.import_jobs",
        "app.workers.tasks.weekly_learning",
        "app.workers.tasks.alerts",
        "app.workers.tasks.demo",
    ],
)

# Route tasks to dedicated queues so a slow scrape can't block embeddings or
# digest delivery on the same solo worker (audit 07-2). One worker container per
# queue consumes these; the digest worker also drains the default queue.
celery_app.conf.task_routes = {
    "app.workers.tasks.ingest_feeds.*": {"queue": "scrape"},
    "app.workers.tasks.compute_embeddings.*": {"queue": "embed"},
    "app.workers.tasks.build_digest.*": {"queue": "digest"},
    "app.workers.tasks.deliver_digest.*": {"queue": "digest"},
    "app.workers.tasks.update_interest_graph.*": {"queue": "digest"},
    "app.workers.tasks.prune_content.*": {"queue": "digest"},
    "app.workers.tasks.prune_telemetry.*": {"queue": "digest"},
    "app.workers.tasks.heartbeat.*": {"queue": "digest"},
    "app.workers.tasks.reengagement.*": {"queue": "digest"},
    "app.workers.tasks.trial_emails.*": {"queue": "digest"},
    "app.workers.tasks.paused_accounts.*": {"queue": "digest"},
    "app.workers.tasks.product_metrics.*": {"queue": "digest"},
    "app.workers.tasks.discovery.*": {"queue": "scrape"},
    "app.workers.tasks.import_jobs.*": {"queue": "digest"},
    "app.workers.tasks.weekly_learning.*": {"queue": "digest"},
    # Scheduled on scrape and digest explicitly (see the beat schedule).
    "app.workers.tasks.alerts.*": {"queue": "digest"},
    "app.workers.tasks.demo.*": {"queue": "digest"},
}

celery_app.conf.update(
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    timezone="UTC",
    enable_utc=True,
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    worker_prefetch_multiplier=1,
    # Redis connections (broker and results): keepalives, timeouts and a
    # health check, so a connection the network dropped while idle is
    # replaced quickly instead of hanging .delay() inside an API request.
    broker_transport_options={
        "socket_keepalive": True,
        "socket_connect_timeout": 5,
        "socket_timeout": 10,
        "health_check_interval": 30,
        "retry_on_timeout": True,
    },
    redis_socket_keepalive=True,
    redis_socket_connect_timeout=5,
    redis_socket_timeout=10,
    redis_retry_on_timeout=True,
    redis_backend_health_check_interval=30,
    broker_connection_retry_on_startup=True,
    task_publish_retry_policy={
        "max_retries": 3,
        "interval_start": 0,
        "interval_step": 0.2,
        "interval_max": 1,
    },
)

# Import schedules (beat config is set in schedules.py)
import app.workers.queue_metrics  # noqa: F401  (signal handlers, #239)
from app.workers.schedules import setup_beat_schedule

setup_beat_schedule(celery_app)

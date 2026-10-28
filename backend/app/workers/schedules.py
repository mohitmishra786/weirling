from __future__ import annotations

from celery import Celery
from celery.schedules import crontab


def setup_beat_schedule(app: Celery) -> None:
    app.conf.beat_schedule = {
        "ingest-feeds": {
            "task": "app.workers.tasks.ingest_feeds.ingest_all_feeds",
            "schedule": 30 * 60,  # every 30 minutes
        },
        "ingest-creator-feeds": {
            "task": "app.workers.tasks.ingest_feeds.ingest_creator_feeds",
            "schedule": 60 * 60,  # every 60 minutes
        },
        "schedule-daily-digests": {
            "task": "app.workers.tasks.build_digest.schedule_daily_digests",
            # Run every 30 minutes so per-user timezone checks fire at the right local time.
            "schedule": 30 * 60,
        },
        "apply-decay": {
            "task": "app.workers.tasks.update_interest_graph.apply_decay_all_users",
            "schedule": crontab(hour=2, minute=0),  # daily at 2:00 AM UTC
        },
        "learn-send-times": {
            "task": "app.workers.tasks.build_digest.learn_send_times",
            "schedule": crontab(hour=3, minute=15, day_of_week=0),  # weekly, Sun 03:15 UTC
        },
        "prune-old-full-text": {
            "task": "app.workers.tasks.prune_content.prune_old_full_text",
            "schedule": crontab(hour=3, minute=30),  # daily at 3:30 AM UTC
        },
        "repair-missing-embeddings": {
            "task": "app.workers.tasks.compute_embeddings.repair_missing_embeddings",
            "schedule": 30 * 60,
        },
        "prune-raw-telemetry": {
            "task": "app.workers.tasks.prune_telemetry.prune_raw_telemetry",
            "schedule": crontab(hour=3, minute=45),  # daily, after the full-text prune
        },
        "refresh-discovery-pools": {
            "task": "app.workers.tasks.discovery.refresh_discovery_pools",
            "schedule": crontab(hour=4, minute=30),  # daily, before morning digests
        },
        "beat-heartbeat": {
            "task": "app.workers.tasks.heartbeat.beat_heartbeat",
            "schedule": 60,  # every minute — drives the beat liveness healthcheck
        },
        "operator-alerts-scrape": {
            "task": "app.workers.tasks.alerts.check_alerts",
            "schedule": 600,  # every 10 minutes (#171); first copy per window wins
            "options": {"queue": "scrape"},
        },
        "operator-alerts-digest": {
            "task": "app.workers.tasks.alerts.check_alerts",
            "schedule": 600,  # every 10 minutes (#171); first copy per window wins
            "options": {"queue": "digest"},
        },
        "worker-heartbeat-scrape": {
            "task": "app.workers.tasks.heartbeat.queue_heartbeat",
            "schedule": 300,  # every 5 minutes, on that queue (#234)
            "args": ("scrape",),
            "options": {"queue": "scrape"},
        },
        "worker-heartbeat-embed": {
            "task": "app.workers.tasks.heartbeat.queue_heartbeat",
            "schedule": 300,  # every 5 minutes, on that queue (#234)
            "args": ("embed",),
            "options": {"queue": "embed"},
        },
        "worker-heartbeat-digest": {
            "task": "app.workers.tasks.heartbeat.queue_heartbeat",
            "schedule": 300,  # every 5 minutes, on that queue (#234)
            "args": ("digest",),
            "options": {"queue": "digest"},
        },
        # After an embedding model switch (#244): re-encode old vectors and
        # interest topics in batches. A single quick query when nothing is left.
        "reembed-outdated": {
            "task": "app.workers.tasks.compute_embeddings.reembed_outdated_items",
            "schedule": 30 * 60,
            "kwargs": {"max_batches": 50},
        },
        "product-metrics-rollup": {
            "task": "app.workers.tasks.product_metrics.rollup_product_metrics",
            "schedule": crontab(hour=0, minute=40),  # daily, for the day before (#131)
        },
        "paused-accounts": {
            "task": "app.workers.tasks.paused_accounts.process_paused_accounts",
            "schedule": crontab(hour=5, minute=30),  # daily (#341)
        },
        "trial-emails": {
            "task": "app.workers.tasks.trial_emails.send_trial_emails",
            # Hourly, so "trial started" follows soon after the email is confirmed (#235).
            "schedule": crontab(minute=20),
        },
        "reengagement-emails": {
            "task": "app.workers.tasks.reengagement.send_reengagement_emails",
            "schedule": crontab(hour=16, minute=0),  # daily at 16:00 UTC
        },
        "weekly-learning-emails": {
            "task": "app.workers.tasks.weekly_learning.send_weekly_learning_emails",
            "schedule": crontab(hour=9, minute=0, day_of_week="sun"),  # Sundays 09:00 UTC (#184)
        },
    }
    # Only a public demo instance re-seeds the demo persona. Scheduled
    # unconditionally, it created demo@weirling.local, with its sources and
    # history, on every instance.
    from app.config import get_settings

    if get_settings().demo_mode:
        app.conf.beat_schedule["reset-demo-account"] = {
            "task": "app.workers.tasks.demo.reset_demo_account",
            "schedule": crontab(hour=4, minute=0),  # daily at 04:00 UTC (#229)
        }

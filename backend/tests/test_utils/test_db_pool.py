"""Workers must not pool asyncpg connections across event loops."""

from sqlalchemy.pool import NullPool

from app.database import pool_options


def test_celery_processes_do_not_pool():
    for argv in (
        ["/usr/local/bin/celery", "-A", "app.workers.celery_app", "worker", "-Q", "embed"],
        ["celery", "-A", "app.workers.celery_app", "beat"],
        # python -m celery: argv[0] is the package's __main__.py.
        [
            "/usr/local/lib/python3.12/site-packages/celery/__main__.py",
            "-A",
            "app.workers.celery_app",
            "worker",
        ],
    ):
        assert pool_options(argv, "auto", 10, 10) == {"poolclass": NullPool}


def test_api_pools_and_settings_override():
    api = ["/usr/local/bin/uvicorn", "app.main:app"]
    assert pool_options(api, "auto", 5, 3) == {
        "pool_size": 5,
        "max_overflow": 3,
        "pool_recycle": 300,
    }
    assert pool_options(api, "none", 5, 3) == {"poolclass": NullPool}
    worker = ["celery", "-A", "app.workers.celery_app", "worker"]
    assert pool_options(worker, "queue", 5, 3) == {
        "pool_size": 5,
        "max_overflow": 3,
        "pool_recycle": 300,
    }


def test_unknown_db_pool_is_rejected(monkeypatch):
    import pytest

    from app.config import Settings

    monkeypatch.setenv("DB_POOL", "nona")
    with pytest.raises(ValueError, match="DB_POOL"):
        Settings(_env_file=None)
    monkeypatch.setenv("DB_POOL", " None ")
    assert Settings(_env_file=None).db_pool == "none"

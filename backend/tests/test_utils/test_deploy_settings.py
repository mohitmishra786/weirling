"""Settings that make managed hosting (Railway etc.) a copy-paste."""

from app.config import Settings


def test_plain_postgres_url_is_accepted(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://u:p@postgres.railway.internal:5432/railway")
    monkeypatch.delenv("DATABASE_SYNC_URL", raising=False)
    s = Settings(_env_file=None)
    assert s.database_url == "postgresql+asyncpg://u:p@postgres.railway.internal:5432/railway"
    assert s.database_sync_url == "postgresql+psycopg2://u:p@postgres.railway.internal:5432/railway"

    monkeypatch.setenv("DATABASE_URL", "postgres://u:p@h:5432/db")
    assert Settings(_env_file=None).database_url == "postgresql+asyncpg://u:p@h:5432/db"


def test_explicit_urls_are_kept(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://a:b@db:5432/x")
    monkeypatch.setenv("DATABASE_SYNC_URL", "postgresql+psycopg2://c:d@other:5432/y")
    s = Settings(_env_file=None)
    assert s.database_url == "postgresql+asyncpg://a:b@db:5432/x"
    assert s.database_sync_url == "postgresql+psycopg2://c:d@other:5432/y"


def test_smtp_settings_and_sender(monkeypatch):
    for k in ("SMTP_HOST", "SMTP_PORT", "SMTP_USER", "SMTP_PASSWORD", "FROM_EMAIL"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("SMTP_HOST", "mail.spacemail.com")
    monkeypatch.setenv("SMTP_PORT", "465")
    monkeypatch.setenv("SMTP_USER", "hello@weirling.com")
    monkeypatch.setenv("SMTP_PASSWORD", "x")
    s = Settings(_env_file=None)
    assert (s.smtp_host, s.smtp_port, s.smtp_user, s.smtp_password) == (
        "mail.spacemail.com",
        465,
        "hello@weirling.com",
        "x",
    )
    assert s.sender_email == "hello@weirling.com"  # FROM_EMAIL empty: send as the login
    monkeypatch.setenv("FROM_EMAIL", "news@weirling.com")
    assert Settings(_env_file=None).sender_email == "news@weirling.com"

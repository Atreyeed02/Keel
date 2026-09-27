"""
Configuration for running on a container host: DATABASE_URL in the forms
hosts hand out, TLS to a managed Postgres, PORT, the production guard, and
the migrate-then-serve start command.

No database needed.
"""

import pytest
from pydantic import ValidationError

from alembic.config import Config
from app import serve
from app.config import DEFAULT_DATABASE_URL, Settings, database_target


@pytest.fixture
def clean_env(monkeypatch):
    """Settings built from nothing but what a test passes, not this shell's environment."""
    for name in ("DATABASE_URL", "DATABASE_SSL", "ENVIRONMENT", "PORT", "HOST"):
        monkeypatch.delenv(name, raising=False)


def _settings(**values) -> Settings:
    return Settings(_env_file=None, **values)


# --- DATABASE_URL, whatever the host calls it ----------------------------------------


@pytest.mark.parametrize(
    "url",
    [
        "postgres://u:p@db.example.com:5432/keel",  # Heroku, Render, Fly
        "postgresql://u:p@db.example.com:5432/keel",  # Railway, libpq's own spelling
        "postgresql+asyncpg://u:p@db.example.com:5432/keel",  # this app's own
        "postgresql+psycopg://u:p@db.example.com:5432/keel",
    ],
)
def test_every_host_spelling_of_the_url_reaches_both_drivers(url):
    target = database_target(url)
    assert target.async_url == "postgresql+asyncpg://u:p@db.example.com:5432/keel"
    assert target.sync_url == "postgresql+psycopg://u:p@db.example.com:5432/keel"
    assert target.ssl is None


def test_sslmode_in_the_url_moves_out_of_asyncpgs_url():
    """asyncpg refuses sslmode in the URL; psycopg wants it there."""
    target = database_target("postgres://u:p@h/keel?sslmode=require&application_name=keel")
    assert target.async_url == "postgresql+asyncpg://u:p@h/keel?application_name=keel"
    assert target.ssl == "require"
    assert (
        target.sync_url == "postgresql+psycopg://u:p@h/keel?application_name=keel&sslmode=require"
    )


def test_the_database_ssl_setting_overrides_the_url(clean_env):
    settings = _settings(
        database_url="postgres://u:p@h/keel?sslmode=disable", database_ssl="verify-full"
    )
    assert settings.database.ssl == "verify-full"
    assert "sslmode=verify-full" in settings.database.sync_url
    assert "sslmode" not in settings.database.async_url


@pytest.mark.parametrize(
    ("values", "fragment"),
    [
        ({"database_url": "mysql://u:p@h/keel"}, "postgres:// or postgresql://"),
        (
            {"database_url": "postgres://u:p@h/keel", "database_ssl": "on"},
            "SSL mode must be one of",
        ),
        ({"database_url": "postgres://u:p@h/keel?sslmode=yes"}, "SSL mode must be one of"),
    ],
)
def test_a_bad_url_or_ssl_mode_stops_startup(clean_env, values, fragment):
    with pytest.raises(ValidationError, match=fragment):
        _settings(**values)


def test_a_percent_encoded_password_survives_alembics_config():
    """Alembic's ini-style config treats % as interpolation; a generated password often has one."""
    target = database_target("postgres://keel:p%40ss%25word@h:5432/keel")
    config = Config()
    config.set_main_option("sqlalchemy.url", target.alembic_url)
    assert config.get_main_option("sqlalchemy.url") == target.sync_url
    assert target.sync_url == "postgresql+psycopg://keel:p%40ss%25word@h:5432/keel"


def test_the_engine_passes_tls_as_asyncpgs_ssl_argument(monkeypatch):
    """Rebuild app.db.engine with DATABASE_SSL set and look at what reaches create_async_engine."""
    import importlib

    import app.config
    import app.db.engine as engine_module

    seen = {}

    def fake_create(url, **kwargs):
        seen.update(url=url, **kwargs)
        return object()

    monkeypatch.setattr(
        app.config,
        "settings",
        Settings(_env_file=None, database_url="postgres://u:p@h/keel?sslmode=require&x=1"),
    )
    monkeypatch.setattr("sqlalchemy.ext.asyncio.create_async_engine", fake_create)
    try:
        importlib.reload(engine_module)
        assert seen["url"] == "postgresql+asyncpg://u:p@h/keel?x=1"
        assert seen["connect_args"] == {"ssl": "require"}
    finally:
        monkeypatch.undo()
        importlib.reload(engine_module)


# --- the production guard -----------------------------------------------------------


def test_production_refuses_to_start_on_the_default_database(clean_env):
    with pytest.raises(ValidationError, match="DATABASE_URL is not set"):
        _settings(environment="production")
    # the guard is about production only: development keeps its local default
    assert _settings().database_url == DEFAULT_DATABASE_URL


def test_production_starts_once_database_url_is_set(clean_env, monkeypatch):
    assert _settings(environment="Production", database_url="postgres://u:p@h/keel")
    # the usual way: from the environment the host provides
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.setenv("DATABASE_URL", "postgres://u:p@h/keel")
    assert _settings().environment == "production"


@pytest.mark.parametrize(
    "url",
    [DEFAULT_DATABASE_URL, "postgres://ledger:ledger@db:5432/ledger"],
    ids=["as-written", "respelled"],
)
def test_production_refuses_the_default_url_even_when_set_explicitly(clean_env, url):
    with pytest.raises(ValidationError, match="is the local development default"):
        _settings(environment="production", database_url=url)


# --- PORT and the start command ------------------------------------------------------


def test_port_comes_from_the_environment(clean_env, monkeypatch):
    assert _settings().port == 8000
    monkeypatch.setenv("PORT", "10000")
    assert _settings().port == 10000
    monkeypatch.setenv("PORT", "0")
    with pytest.raises(ValidationError):
        _settings()


def test_the_start_command_migrates_then_serves_on_port(monkeypatch):
    calls = []
    monkeypatch.setattr(
        serve.subprocess, "run", lambda args, check: calls.append(("migrate", args[1:], check))
    )
    monkeypatch.setattr(serve.uvicorn, "run", lambda app, **kw: calls.append(("serve", app, kw)))
    monkeypatch.setattr(serve, "settings", Settings(_env_file=None, port=10000))

    serve.main([])

    assert calls[0] == ("migrate", ["-m", "alembic", "upgrade", "head"], True)
    assert calls[1][0:2] == ("serve", "app.main:app")
    assert calls[1][2]["port"] == 10000 and calls[1][2]["host"] == "0.0.0.0"


def test_a_failed_migration_means_no_server(monkeypatch):
    def failing(args, check):
        raise serve.subprocess.CalledProcessError(1, args)

    served = []
    monkeypatch.setattr(serve.subprocess, "run", failing)
    monkeypatch.setattr(serve.uvicorn, "run", lambda *a, **k: served.append(True))
    with pytest.raises(serve.subprocess.CalledProcessError):
        serve.main([])
    assert served == []


def test_no_migrate_skips_the_migration(monkeypatch):
    calls = []
    monkeypatch.setattr(serve.subprocess, "run", lambda *a, **k: calls.append("migrate"))
    monkeypatch.setattr(serve.uvicorn, "run", lambda *a, **k: calls.append("serve"))
    serve.main(["--no-migrate"])
    assert calls == ["serve"]

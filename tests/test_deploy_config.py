"""
Configuration for running on a container host: DATABASE_URL in the forms
hosts hand out, TLS to a managed Postgres, PORT, the guard on hosted
environments, and the migrate-then-serve start command.

No database needed.
"""

import re

import pytest
from pydantic import ValidationError

from alembic.config import Config
from app import serve
from app.config import DEFAULT_DATABASE_URL, Settings, database_target


@pytest.fixture
def clean_env(monkeypatch):
    """Settings built from nothing but what a test passes, not this shell's environment."""
    for name in (
        "DATABASE_URL",
        "DATABASE_SSL",
        "ENVIRONMENT",
        "PORT",
        "HOST",
        "FORWARDED_ALLOW_IPS",
    ):
        monkeypatch.delenv(name, raising=False)


def _settings(**values) -> Settings:
    return Settings(_env_file=None, **values)


# Everything a hosted environment needs to start. Each guard test below takes
# this and breaks one thing, so the refusal it sees is that thing's.
HOSTED = {
    "database_url": "postgres://u:p@h/keel",
    "database_ssl": "require",
    "forwarded_allow_ips": "10.0.0.0/8,172.16.0.0/12,192.168.0.0/16",
}


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


# --- the guard on hosted environments (production, demo) ----------------------------


def test_production_refuses_to_start_on_the_default_database(clean_env):
    with pytest.raises(ValidationError, match="DATABASE_URL is not set"):
        _settings(environment="production")
    # the guard is about production only: development keeps its local default
    assert _settings().database_url == DEFAULT_DATABASE_URL


def test_production_starts_once_it_is_configured_for_a_host(clean_env, monkeypatch):
    assert _settings(environment="Production", **HOSTED)
    # the usual way: from the environment the host provides
    monkeypatch.setenv("ENVIRONMENT", "production")
    for name, value in HOSTED.items():
        monkeypatch.setenv(name.upper(), value)
    assert _settings().environment == "production"


@pytest.mark.parametrize(
    "url",
    [DEFAULT_DATABASE_URL, "postgres://ledger:ledger@db:5432/ledger"],
    ids=["as-written", "respelled"],
)
def test_production_refuses_the_default_url_even_when_set_explicitly(clean_env, url):
    with pytest.raises(ValidationError, match="is the local development default"):
        _settings(environment="production", **{**HOSTED, "database_url": url})


def test_the_public_demo_is_guarded_like_production(clean_env):
    """The demo is hosted too, so it must not come up on the local default either."""
    with pytest.raises(ValidationError, match="ENVIRONMENT=demo but DATABASE_URL is not set"):
        _settings(environment="demo")
    with pytest.raises(ValidationError, match="is the local development default"):
        _settings(environment="Demo", **{**HOSTED, "database_url": DEFAULT_DATABASE_URL})
    demo = _settings(environment="Demo", **HOSTED)
    assert demo.is_demo
    assert not _settings(environment="production", **HOSTED).is_demo
    assert not _settings().is_demo


@pytest.mark.parametrize("environment", ["production", "demo"])
@pytest.mark.parametrize(
    ("tls", "found"),
    [
        ({"database_ssl": None}, "no SSL mode"),
        ({"database_ssl": "disable"}, "SSL mode 'disable'"),
        # both fall back to plaintext when the server offers no TLS
        ({"database_ssl": "allow"}, "SSL mode 'allow'"),
        ({"database_ssl": "prefer"}, "SSL mode 'prefer'"),
        (
            {"database_ssl": None, "database_url": "postgres://u:p@h/keel?sslmode=disable"},
            "SSL mode 'disable'",
        ),
        # DATABASE_SSL overrides the URL, so turning TLS off there wins
        (
            {"database_ssl": "disable", "database_url": "postgres://u:p@h/keel?sslmode=require"},
            "SSL mode 'disable'",
        ),
    ],
    ids=["unset", "disable", "allow", "prefer", "disable-in-url", "disable-overrides-url"],
)
def test_a_hosted_environment_refuses_to_start_without_tls(clean_env, environment, tls, found):
    with pytest.raises(
        ValidationError, match=re.escape(f"database connection has {found}. Refusing")
    ):
        _settings(environment=environment, **{**HOSTED, **tls})


@pytest.mark.parametrize(
    "tls",
    [
        {"database_ssl": "require"},
        {"database_ssl": "verify-ca"},
        {"database_ssl": "verify-full"},
        # a managed database's own URL, with DATABASE_SSL left unset
        {"database_ssl": None, "database_url": "postgres://u:p@h/keel?sslmode=require"},
    ],
    ids=["require", "verify-ca", "verify-full", "require-in-url"],
)
def test_a_hosted_environment_starts_with_tls_from_either_setting(clean_env, tls):
    assert _settings(environment="production", **{**HOSTED, **tls}).database.ssl in (
        "require",
        "verify-ca",
        "verify-full",
    )


@pytest.mark.parametrize("environment", ["production", "demo"])
@pytest.mark.parametrize(
    ("trusted", "refusal"),
    [
        ("*", "trusts '*'"),
        (" * ", "trusts '*'"),
        ("10.0.0.0/8,*", "trusts '*'"),
        ("", "is empty"),
        (" , ", "is empty"),
    ],
    ids=["star", "star-padded", "star-in-a-list", "empty", "only-commas"],
)
def test_a_hosted_environment_refuses_to_trust_every_proxy_or_none(
    clean_env, environment, trusted, refusal
):
    with pytest.raises(
        ValidationError, match=re.escape(f"FORWARDED_ALLOW_IPS {refusal}. Refusing")
    ):
        _settings(environment=environment, **{**HOSTED, "forwarded_allow_ips": trusted})


def test_the_hosted_guard_leaves_development_alone(clean_env):
    """A laptop has no managed Postgres and may put anything in FORWARDED_ALLOW_IPS."""
    development = _settings(database_ssl="disable", forwarded_allow_ips="*")
    assert development.database.ssl == "disable"
    assert _settings(forwarded_allow_ips="").forwarded_allow_ips == ""


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


# --- live reload: dev only -----------------------------------------------------------


def test_reload_is_on_only_when_asked_for(monkeypatch):
    runs = []
    monkeypatch.setattr(serve.subprocess, "run", lambda *a, **k: None)
    monkeypatch.setattr(serve.uvicorn, "run", lambda app, **kw: runs.append(kw))
    serve.main(["--reload"])
    serve.main([])
    dev, production = runs
    assert dev["reload"] is True and dev["reload_dirs"] == ["app"]
    assert "reload" not in production and "reload_dirs" not in production


def test_the_image_never_reloads_and_the_dev_override_does():
    """The Dockerfile's CMD is what production runs; the override is dev-only."""
    import json
    import re
    from pathlib import Path

    import yaml

    root = Path(__file__).resolve().parent.parent
    (cmd,) = re.findall(r"^CMD (.+)$", (root / "Dockerfile").read_text(), re.M)
    assert json.loads(cmd) == ["python", "-m", "app.serve"]
    base = yaml.safe_load((root / "docker-compose.yml").read_text())["services"]["app"]
    assert "command" not in base  # so CI and production run the image's CMD
    dev = yaml.safe_load((root / "docker-compose.override.yml").read_text())["services"]["app"]
    assert dev["command"] == ["python", "-m", "app.serve", "--reload"]
    # bind mounts from Windows/macOS hosts deliver no change events: poll
    assert dev["environment"]["WATCHFILES_FORCE_POLLING"] == "true"

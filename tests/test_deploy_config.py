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
    target = database_target("postgres://u:p@h/keel?sslmode=require")
    assert target.async_url == "postgresql+asyncpg://u:p@h/keel"
    assert target.ssl == "require"
    assert target.sync_url == "postgresql+psycopg://u:p@h/keel?sslmode=require"


# The URL Neon hands out, which took the live demo down: migrations (psycopg)
# connected, and every connection the app made (asyncpg) failed.
NEON_URL = (
    "postgresql://neondb_owner:pw@ep-x.ap-southeast-1.aws.neon.tech/neondb"
    "?sslmode=require&channel_binding=require"
)


def _neon(binding: str) -> str:
    return NEON_URL.replace("channel_binding=require", f"channel_binding={binding}")


@pytest.mark.parametrize("environment", ["development", "demo"])
def test_neons_channel_binding_require_stops_startup(clean_env, environment):
    """asyncpg cannot bind the channel, and dropping the demand would quietly weaken it."""
    with pytest.raises(ValidationError, match="channel_binding=require") as refused:
        _settings(
            database_url=NEON_URL,
            environment=environment,
            forwarded_allow_ips=HOSTED["forwarded_allow_ips"],
        )
    assert "set it to prefer" in str(refused.value)
    # the refusal names the parameter, never the URL it came from
    assert "neondb_owner" not in str(refused.value)


@pytest.mark.parametrize("binding", ["prefer", "disable"])
def test_channel_binding_that_asks_nothing_of_asyncpg_reaches_psycopg_only(binding):
    target = database_target(_neon(binding))
    assert target.async_url == (
        "postgresql+asyncpg://neondb_owner:pw@ep-x.ap-southeast-1.aws.neon.tech/neondb"
    )
    assert target.ssl == "require"
    assert target.sync_url.endswith(f"/neondb?channel_binding={binding}&sslmode=require")


def test_a_bad_channel_binding_stops_startup(clean_env):
    with pytest.raises(ValidationError, match="channel_binding must be one of"):
        _settings(database_url="postgres://u:p@h/keel?channel_binding=yes")


@pytest.mark.parametrize(
    "query", ["application_name=keel", "connect_timeout=10", "options=-csearch_path%3Dx"]
)
def test_query_parameters_asyncpg_would_reject_stop_startup(clean_env, query):
    with pytest.raises(ValidationError, match="does not accept: " + query.split("=")[0]):
        _settings(database_url=f"postgres://u:p@h/keel?sslmode=require&{query}")


@pytest.mark.parametrize("binding", ["prefer", "disable"])
def test_asyncpg_is_given_only_arguments_it_accepts(binding):
    """
    What SQLAlchemy actually hands asyncpg.connect() for the URL, checked
    against asyncpg's own signature: the check that would have caught
    channel_binding before it reached the live demo.
    """
    import inspect

    import asyncpg
    from sqlalchemy.dialects.postgresql.asyncpg import dialect
    from sqlalchemy.engine import make_url

    async_url = make_url(database_target(_neon(binding)).async_url)
    _, kwargs = dialect().create_connect_args(async_url)
    assert set(kwargs) <= set(inspect.signature(asyncpg.connect).parameters)


@pytest.mark.parametrize(
    ("database_ssl", "url_mode", "used"),
    [
        ("verify-full", "disable", "verify-full"),
        ("require", "disable", "require"),
        # the other way round: DATABASE_SSL cannot weaken what the URL asks for
        ("disable", "require", "require"),
        ("require", "verify-full", "verify-full"),
        ("verify-ca", "verify-full", "verify-full"),
        ("allow", "prefer", "prefer"),
        ("require", "require", "require"),
    ],
)
def test_the_stricter_of_database_ssl_and_the_urls_sslmode_is_used(
    clean_env, database_ssl, url_mode, used
):
    settings = _settings(
        database_url=f"postgres://u:p@h/keel?sslmode={url_mode}", database_ssl=database_ssl
    )
    assert settings.database.ssl == used
    assert settings.database.sync_url == f"postgresql+psycopg://u:p@h/keel?sslmode={used}"
    assert "sslmode" not in settings.database.async_url


def test_a_bad_sslmode_in_the_url_stops_startup_even_with_database_ssl_set(clean_env):
    with pytest.raises(ValidationError, match="SSL mode must be one of"):
        _settings(database_url="postgres://u:p@h/keel?sslmode=yes", database_ssl="require")


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
        Settings(_env_file=None, database_url="postgres://u:p@h/keel?sslmode=require"),
    )
    monkeypatch.setattr("sqlalchemy.ext.asyncio.create_async_engine", fake_create)
    try:
        importlib.reload(engine_module)
        assert seen["url"] == "postgresql+asyncpg://u:p@h/keel"
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
        # neither setting encrypts, so neither can rescue the other
        (
            {"database_ssl": "prefer", "database_url": "postgres://u:p@h/keel?sslmode=disable"},
            "SSL mode 'prefer'",
        ),
    ],
    ids=["unset", "disable", "allow", "prefer", "disable-in-url", "weak-in-both"],
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
        # conflicting: the stricter one is used, so these start encrypted
        {"database_ssl": "require", "database_url": "postgres://u:p@h/keel?sslmode=disable"},
        {"database_ssl": "disable", "database_url": "postgres://u:p@h/keel?sslmode=require"},
    ],
    ids=[
        "require",
        "verify-ca",
        "verify-full",
        "require-in-url",
        "require-beats-disable-in-url",
        "require-in-url-beats-disable",
    ],
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
    assert calls[1][0:2] == ("serve", "app.main:served")
    assert calls[1][2]["port"] == 10000 and calls[1][2]["host"] == "0.0.0.0"
    # request.completed is the one request log; uvicorn's access line would
    # log query strings, where the posting form's no-JavaScript reloads carry
    # what was typed
    assert calls[1][2]["access_log"] is False


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


def test_base_images_come_from_ecr_public_pinned_by_digest():
    """Docker Hub's anonymous limit fails CI; a tag alone changes under a build."""
    import re
    from pathlib import Path

    import yaml

    root = Path(__file__).resolve().parent.parent
    pinned = r"public\.ecr\.aws/docker/library/{}:[\w.-]+@sha256:[0-9a-f]{{64}}"
    (base,) = re.findall(r"^FROM (\S+)", (root / "Dockerfile").read_text(), re.M)
    assert re.fullmatch(pinned.format("python"), base)
    compose = yaml.safe_load((root / "docker-compose.yml").read_text())["services"]["db"]
    ci = yaml.safe_load((root / ".github" / "workflows" / "ci.yml").read_text())
    service = ci["jobs"]["lint-and-test"]["services"]["postgres"]
    assert re.fullmatch(pinned.format("postgres"), compose["image"])
    assert service["image"] == compose["image"]  # CI tests against what compose runs

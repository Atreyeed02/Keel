from dataclasses import dataclass
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# The local-development database. Never acceptable in production; see
# `Settings.refuse_default_database_in_production`.
DEFAULT_DATABASE_URL = "postgresql+asyncpg://ledger:ledger@db:5432/ledger"

# libpq's sslmode values. asyncpg accepts the same strings for its `ssl`
# argument, and psycopg takes them as `sslmode` in the URL.
SSL_MODES = ("disable", "allow", "prefer", "require", "verify-ca", "verify-full")


@dataclass(frozen=True)
class DatabaseTarget:
    """One database, spelled for each driver that connects to it."""

    # For the app's asyncpg engine. Carries no sslmode: asyncpg rejects it in
    # the URL, so the mode travels separately, as `ssl`.
    async_url: str
    # For Alembic, which runs synchronously on psycopg. psycopg is libpq
    # underneath and takes `sslmode` in the URL, so the mode goes there.
    sync_url: str
    # An entry of SSL_MODES, or None to leave the driver's default.
    ssl: str | None

    @property
    def alembic_url(self) -> str:
        """
        `sync_url` escaped for Alembic's ini-style config, where `%` starts an
        interpolation. A percent-encoded password, which generated passwords
        often need, would otherwise break every migration.
        """
        return self.sync_url.replace("%", "%%")


def database_target(url: str, ssl: str | None = None) -> DatabaseTarget:
    """
    Accept a Postgres URL in any of the forms hosts hand out and spell it for
    asyncpg and for psycopg.

    `postgres://`, `postgresql://` and `postgresql+<driver>://` are all the
    same database. `sslmode` in the URL is honoured, and `ssl` (the
    DATABASE_SSL setting) overrides it. Any other query parameter is passed
    through untouched.
    """
    parts = urlsplit(url)
    if parts.scheme.split("+", 1)[0].lower() not in ("postgres", "postgresql"):
        raise ValueError(
            f"DATABASE_URL must be a postgres:// or postgresql:// URL, not {parts.scheme}://"
        )
    query = parse_qsl(parts.query, keep_blank_values=True)
    url_mode = next((value for key, value in query if key == "sslmode"), None)
    others = [(key, value) for key, value in query if key != "sslmode"]
    mode = ssl or url_mode
    if mode is not None and mode not in SSL_MODES:
        raise ValueError(f"SSL mode must be one of {', '.join(SSL_MODES)}, not {mode!r}")

    def spelled(scheme: str, params: list[tuple[str, str]]) -> str:
        return urlunsplit((scheme, parts.netloc, parts.path, urlencode(params), parts.fragment))

    return DatabaseTarget(
        async_url=spelled("postgresql+asyncpg", others),
        sync_url=spelled("postgresql+psycopg", others + ([("sslmode", mode)] if mode else [])),
        ssl=mode,
    )


class Settings(BaseSettings):
    """Central app config, loaded from environment / .env."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_name: str = "ledger-service"
    # "production" turns on the checks in `refuse_default_database_in_production`.
    environment: str = "development"

    # Any Postgres URL: postgres://, postgresql:// or postgresql+asyncpg://.
    # `database` below spells it for each driver.
    database_url: str = DEFAULT_DATABASE_URL
    # TLS to Postgres, one of SSL_MODES. Overrides an sslmode in the URL; unset
    # means whatever the URL says, else the driver's default.
    database_ssl: str | None = None

    # Pool sizing — conservative defaults for a single small container
    db_pool_size: int = 5
    db_max_overflow: int = 10

    # Where `python -m app.serve` listens. Most container hosts set PORT.
    host: str = "0.0.0.0"
    port: int = Field(8000, ge=1, le=65535)

    # Which proxies uvicorn believes about X-Forwarded-For and
    # X-Forwarded-Proto: a comma-separated list of addresses and networks, or
    # "*". The default trusts only a proxy on the same machine. uvicorn reads
    # X-Forwarded-For from the right and takes the first address that is not
    # trusted: the one the nearest untrusted hop connected from. The client
    # it finds is the one the log shows and the write rate limit counts.
    #
    # On a host whose router connects from a private address (Render), set
    # "10.0.0.0/8,172.16.0.0/12,192.168.0.0/16". Not "*": with "*" uvicorn
    # takes the header's leftmost address, which a client writes itself when
    # the router appends to the header rather than replacing it, as Render
    # has said its router does. Any client could then claim a fresh address,
    # and a fresh write allowance, with every request.
    forwarded_allow_ips: str = "127.0.0.1"

    # Largest request body accepted, in bytes (app/security.py). A posting with
    # dozens of lines is a few kilobytes.
    max_request_body_bytes: int = Field(65536, ge=1024)

    # How many writes (any method but GET, HEAD and OPTIONS) one client may make
    # in any WRITE_RATE_WINDOW_SECONDS; past that, a 429 (app/ratelimit.py).
    # Counted in memory, so per instance. 0 turns the limit off.
    write_rate_limit: int = Field(30, ge=0)
    write_rate_window_seconds: int = Field(60, ge=1)

    # Level for the `keel` JSON logger (app/observability.py)
    log_level: str = "INFO"

    @model_validator(mode="after")
    def refuse_default_database_in_production(self) -> "Settings":
        # Parsing here, not lazily, so a malformed URL or SSL mode stops the
        # process at start rather than at the first query.
        target = database_target(self.database_url, self.database_ssl)
        if self.environment.lower() == "production":
            if "database_url" not in self.model_fields_set:
                raise ValueError(
                    "ENVIRONMENT=production but DATABASE_URL is not set. Refusing to start "
                    "with the local development default (ledger:ledger@db)."
                )
            # Set, but to the default itself, in any spelling: just as unsafe.
            if target.async_url == database_target(DEFAULT_DATABASE_URL).async_url:
                raise ValueError(
                    "ENVIRONMENT=production but DATABASE_URL is the local development "
                    "default (ledger:ledger@db). Refusing to start."
                )
        return self

    @property
    def database(self) -> DatabaseTarget:
        return database_target(self.database_url, self.database_ssl)


settings = Settings()

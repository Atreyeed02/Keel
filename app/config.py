from dataclasses import dataclass
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# The local-development database. Never acceptable in production; see
# `Settings.refuse_unsafe_hosted_config`.
DEFAULT_DATABASE_URL = "postgresql+asyncpg://ledger:ledger@db:5432/ledger"

# Environments that run on a host, not a laptop: each refuses to start on the
# local development database, without TLS to Postgres, or trusting any
# proxy's forwarded headers (`Settings.refuse_unsafe_hosted_config`).
# "demo" is the public demo: it also shows the demo notice on every page and is
# the only environment scripts/reset_demo_data.py will wipe.
HOSTED_ENVIRONMENTS = ("production", "demo")

# libpq's sslmode values. asyncpg accepts the same strings for its `ssl`
# argument, and psycopg takes them as `sslmode` in the URL.
SSL_MODES = ("disable", "allow", "prefer", "require", "verify-ca", "verify-full")
# The modes that never fall back to plaintext. "allow" and "prefer" do, quietly,
# when the server does not offer TLS, so a hosted environment refuses them.
ENCRYPTED_SSL_MODES = ("require", "verify-ca", "verify-full")


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
    # "development" (the default), "production", or "demo" for the public demo.
    # Either hosted one turns on `refuse_unsafe_hosted_config`.
    environment: str = "development"

    # Any Postgres URL: postgres://, postgresql:// or postgresql+asyncpg://.
    # `database` below spells it for each driver.
    database_url: str = DEFAULT_DATABASE_URL
    # TLS to Postgres, one of SSL_MODES. Overrides an sslmode in the URL; unset
    # means whatever the URL says, else the driver's default. A hosted
    # environment needs one of ENCRYPTED_SSL_MODES, from here or the URL.
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
    # and a fresh write allowance, with every request. A hosted environment
    # refuses to start on "*", or on nothing at all.
    forwarded_allow_ips: str = "127.0.0.1"

    # Largest request body accepted, in bytes (app/security.py). A posting with
    # dozens of lines is a few kilobytes.
    max_request_body_bytes: int = Field(65536, ge=1024)

    # How many writes (any method but GET, HEAD and OPTIONS) one client may make
    # in any WRITE_RATE_WINDOW_SECONDS; past that, a 429 (app/ratelimit.py).
    # Counted in memory, so per instance. 0 turns the limit off.
    write_rate_limit: int = Field(30, ge=0)
    write_rate_window_seconds: int = Field(60, ge=1)

    # The most accounts and transactions the ledger will hold; a write that
    # would add one more is refused (app/domain/capacity.py). Sized for a free
    # 0.5 GB database: the largest transaction the body limit admits measured
    # about 91 KB on disk with its entries, event and key, so 2000 of them are
    # under 200 MB, and a typical two-entry one is under 2 KB. An account is
    # about 1 KB. 0 means no cap, which a real ledger wants.
    max_accounts: int = Field(200, ge=0)
    max_transactions: int = Field(2000, ge=0)

    # Level for the `keel` JSON logger (app/observability.py)
    log_level: str = "INFO"

    @model_validator(mode="after")
    def refuse_unsafe_hosted_config(self) -> "Settings":
        # Parsing here, not lazily, so a malformed URL or SSL mode stops the
        # process at start rather than at the first query.
        target = database_target(self.database_url, self.database_ssl)
        environment = self.environment.lower()
        if environment in HOSTED_ENVIRONMENTS:
            if "database_url" not in self.model_fields_set:
                raise ValueError(
                    f"ENVIRONMENT={environment} but DATABASE_URL is not set. Refusing to start "
                    "with the local development default (ledger:ledger@db)."
                )
            # Set, but to the default itself, in any spelling: just as unsafe.
            if target.async_url == database_target(DEFAULT_DATABASE_URL).async_url:
                raise ValueError(
                    f"ENVIRONMENT={environment} but DATABASE_URL is the local development "
                    "default (ledger:ledger@db). Refusing to start."
                )
            # The effective mode, so an sslmode in the URL counts as much as
            # DATABASE_SSL does: the managed databases' own URLs carry one.
            if target.ssl not in ENCRYPTED_SSL_MODES:
                found = "no SSL mode" if target.ssl is None else f"SSL mode {target.ssl!r}"
                raise ValueError(
                    f"ENVIRONMENT={environment} but the database connection has {found}. "
                    "Refusing to start without TLS to Postgres: set DATABASE_SSL (or sslmode "
                    f"in DATABASE_URL) to one of {', '.join(ENCRYPTED_SSL_MODES)}."
                )
            trusted = [entry.strip() for entry in self.forwarded_allow_ips.split(",")]
            if "*" in trusted:
                raise ValueError(
                    f"ENVIRONMENT={environment} but FORWARDED_ALLOW_IPS trusts '*'. Refusing to "
                    "start: any client could then choose its own address, and a fresh write "
                    "allowance with it. Name the proxy's networks instead."
                )
            if not any(trusted):
                raise ValueError(
                    f"ENVIRONMENT={environment} but FORWARDED_ALLOW_IPS is empty. Refusing to "
                    "start: name the proxy's networks, or 127.0.0.1 if there is no proxy."
                )
        return self

    @property
    def is_demo(self) -> bool:
        """The public demo: shows the demo notice, and may be reset."""
        return self.environment.lower() == "demo"

    @property
    def database(self) -> DatabaseTarget:
        return database_target(self.database_url, self.database_ssl)


settings = Settings()

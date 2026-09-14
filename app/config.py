from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Central app config, loaded from environment / .env."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_name: str = "ledger-service"
    environment: str = "development"

    # SQLAlchemy async DSN, e.g. postgresql+asyncpg://user:pass@host:5432/db
    database_url: str = "postgresql+asyncpg://ledger:ledger@db:5432/ledger"

    # Pool sizing — conservative defaults for a single small container
    db_pool_size: int = 5
    db_max_overflow: int = 10


settings = Settings()

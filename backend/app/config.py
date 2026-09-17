from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="SP_", env_file=".env", extra="ignore")

    database_url: str = "postgresql+asyncpg://sberpodbor:sberpodbor@db:5432/sberpodbor"

    jwt_secret: str = "dev-only-change-me"
    jwt_algorithm: str = "HS256"
    access_token_ttl_min: int = 60
    refresh_token_ttl_days: int = 14

    # Fernet key protecting ATS account passwords / proxy URLs at rest.
    encryption_key: str = ""

    bootstrap_admin_email: str = "admin@local"
    bootstrap_admin_password: str = ""

    cors_origins: str = ""

    export_dir: str = "/exports"
    media_dir: str = "/media"

    # media.sberpodbor.ru is a plain CDN, unrelated to the API's latency throttling and its
    # one-token-per-account rule, so it takes far more parallelism than the crawl safely can.
    media_concurrency: int = 24
    media_timeout_s: int = 60

    # ── Scraper ─────────────────────────────────────────────────────────────
    api_base: str = "https://api.sberpodbor.ru"
    lane_concurrency: int = 14
    min_interval_ms: int = 100
    req_timeout_s: int = 30
    max_retries_per_req: int = 6
    min_relogin_s: int = 90
    block_cooldown_s: int = 180
    block_cooldown_max_s: int = 1200
    profiles_per_page: int = 50

    # ── Continuous refresh ──────────────────────────────────────────────────
    # A full crawl is ~0.7M requests / 40-55h, so "always up to date" is built from a
    # cheap frequent list sweep plus a slow rolling re-check of already-known records.
    sweep_interval_min: int = 60
    rolling_recheck_enabled: bool = True
    rolling_batch_size: int = 2000
    # Candidates whose activity was last verified longer ago than this are re-queued.
    recheck_after_days: int = 30

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()

"""Application configuration. All secrets come from the server environment only."""
from __future__ import annotations

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # Core
    database_url: str = "postgresql+psycopg://lab:lab@localhost:5432/lab"
    # "demo" = deterministic synthetic data namespace; "live" = real sourced observations (paper only)
    lab_mode: str = Field(default="demo", pattern="^(demo|live)$")
    secret_key: str = "change-me-in-production-please-32chars"
    admin_email: str = "admin@example.com"
    admin_password: str = "change-me"
    cookie_secure: bool = False
    cors_origins: str = ""

    # Scheduling (America/Chicago display; exchange calendar America/New_York)
    display_timezone: str = "America/Chicago"
    research_cron_weekday: int = 0  # Monday
    research_hour_local: int = 7
    daily_data_ready_hour_et: int = 18  # finalized EOD considered available after 18:30 ET
    daily_data_ready_minute_et: int = 30
    cron_secret: str = ""  # required to call /internal/cron/* endpoints

    # Market data
    market_provider: str = "demo"  # demo | tiingo | alphavantage | stooq
    tiingo_api_key: str = ""
    alphavantage_api_key: str = ""
    market_universe: str = (
        "SPY,QQQ,IWM,XLK,XLF,XLV,XLE,XLY,XLP,XLI,AAPL,MSFT,AMZN,GOOGL,META,NVDA,JPM,JNJ,"
        "PG,XOM,UNH,HD,V,MA,KO,PEP,COST,WMT,BAC,CVX,MRK,ABBV,AVGO,ORCL,CSCO,ADBE,CRM,NFLX,AMD,INTC"
    )
    benchmark_symbol: str = "SPY"

    # Research / AI
    llm_provider: str = "auto"  # auto | anthropic | template
    anthropic_api_key: str = ""
    llm_model: str = "claude-opus-5-5"
    llm_effort: str = "medium"
    research_max_llm_calls: int = 12
    research_max_cost_usd: float = 2.0
    sec_user_agent: str = ""  # SEC requires "Company/Name contact@domain" — set by the owner
    openalex_mailto: str = ""
    reddit_client_id: str = ""
    reddit_client_secret: str = ""
    x_bearer_token: str = ""
    http_timeout_seconds: float = 20.0

    # Notifications
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_password: str = ""
    smtp_from: str = ""
    notify_email_to: str = ""

    # Worker
    worker_poll_seconds: float = 5.0
    job_lease_seconds: int = 900

    @property
    def universe(self) -> list[str]:
        return [s.strip().upper() for s in self.market_universe.split(",") if s.strip()]

    @property
    def is_demo(self) -> bool:
        return self.lab_mode == "demo"


@lru_cache
def get_settings() -> Settings:
    return Settings()

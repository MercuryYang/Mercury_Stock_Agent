from pathlib import Path
from typing import Literal

import yaml
from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from trading_agent.core.models import Fraction, Model, Positive, stable_id


class RiskConfig(Model):
    max_position_weight: Fraction | None = None
    max_single_trade_risk_pct: Fraction | None = None
    max_portfolio_drawdown_pct: Fraction | None = None
    max_sector_weight: Fraction | None = None
    max_add_count: int | None = Field(default=None, ge=0)
    max_gross_exposure: Fraction | None = None
    min_cash_weight: Fraction | None = None
    max_portfolio_heat: Fraction | None = None
    min_risk_reward_ratio: Positive | None = None
    max_spread_pct: Fraction | None = None
    min_daily_volume: Positive | None = None
    max_slippage_pct: Fraction | None = None
    stress_shock_pct: Fraction | None = None
    max_stress_loss_pct: Fraction | None = None
    max_correlation: Fraction | None = None
    earnings_blackout_enabled: bool = True
    earnings_blackout_days: int | None = Field(default=None, ge=0)
    stale_quote_seconds: Positive | None = None
    stale_portfolio_seconds: Positive | None = None
    allow_extended_hours_trigger: bool = False

    def missing(self) -> list[str]:
        return [k for k, v in self.model_dump().items() if v is None]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore", env_ignore_empty=True)
    app_env: Literal["development", "test", "production"] = "development"
    data_mode: Literal["demo", "live"] = "demo"
    database_url: str = "sqlite:///data/trading_agent.db"
    admin_api_key: SecretStr = SecretStr("")
    config_dir: Path = Path("config")
    data_dir: Path = Path("data")
    scheduler_enabled: bool = False
    llm_enabled: bool = False
    llm_base_url: str | None = None
    llm_model: str | None = None
    llm_api_key: SecretStr = SecretStr("")
    llm_api_mode: Literal["chat_completions", "responses"] = "chat_completions"
    schwab_client_id: SecretStr = SecretStr("")
    schwab_client_secret: SecretStr = SecretStr("")
    schwab_redirect_uri: str | None = None
    schwab_token_encryption_key: SecretStr = SecretStr("")
    schwab_refresh_token_max_age_seconds: int | None = Field(default=None, gt=0)
    schwab_history_adjustment: Literal["UNADJUSTED", "SPLIT_ADJUSTED", "UNKNOWN"] = "UNKNOWN"

    @model_validator(mode="after")
    def production(self):
        if self.app_env == "production":
            if len(self.admin_api_key.get_secret_value()) < 32:
                raise ValueError("Production ADMIN_API_KEY must have at least 32 characters")
            if not self.database_url.startswith("postgresql"):
                raise ValueError("Production requires PostgreSQL")
        if self.llm_enabled and not all((self.llm_base_url, self.llm_model)):
            raise ValueError("LLM_BASE_URL and LLM_MODEL are required")
        return self


class Config:
    def __init__(self, settings: Settings):
        self.documents = {}
        for name in ("app", "portfolio", "strategy", "risk", "scheduler"):
            self.documents[name] = yaml.safe_load(
                (settings.config_dir / f"{name}.yaml").read_text(encoding="utf-8")
            )
        self.app = self.documents["app"]
        self.portfolio = self.documents["portfolio"]
        self.strategy = self.documents["strategy"]
        self.scheduler = self.documents["scheduler"]
        self.risk = RiskConfig.model_validate(self.documents["risk"]["risk"])
        self.version = stable_id(self.documents)
        if not 5 <= self.scheduler["intraday_interval_minutes"] <= 15:
            raise ValueError("Intraday interval must be between 5 and 15 minutes")

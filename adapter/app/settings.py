from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    adapter_host: str = "127.0.0.1"
    adapter_port: int = 8765

    db_path: str = "./trade_ledger.db"
    replay_dir: str = "./replay_tapes"

    internal_hmac_key: str = "change-me-dev-only"
    hmac_required: bool = False

    decider: str = "dummy_trend_breakout"
    adapter_default_max_deviation_points: int = 20

    anthropic_api_key: str = ""
    anthropic_model: str = "claude-opus-4-7"
    anthropic_timeout_seconds: float = 1.6


settings = Settings()


from pydantic import field_validator, SecretStr, Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file='.env',
        env_file_encoding='utf-8',
        extra='ignore',
        validate_default=True,
        str_strip_whitespace=True,
        hide_input_in_errors=True
    )
    # App
    log_level: str = 'INFO'
    node_env: str = 'development'
    use_mock_broker: bool = False

    allow_after_hours_orders: bool = True

    # MongoDB
    mongo_uri: str = ''
    mongo_db_name: str = 'test'

    # Redis
    redis_host: str = "redis"
    redis_port: int = 6379

    # AES
    aes_secret_key: str = ""

    # Kotak Neo
    kotak_base_url: str = "https://gw-napi.kotaksecurities.com"

    health_port: int = 8001

    ai_enabled: bool = False
    groq_api_key: SecretStr = SecretStr("")  # SecretStr: never shows up in repr()/logs
    groq_model: str = ""  # no hard-coded model names: check the provider docs
    ai_timeout_sec: float = Field(30.0, gt=0, le=300)
    ai_max_tokens: int = Field(1500, ge=64, le=8000)
    ai_daily_call_cap: int = Field(200, ge=1)
    ai_min_seconds_between_calls: float = Field(2.5, ge=0)

    @field_validator("mongo_uri")
    @classmethod
    def mongo_uri_required(cls, v: str) -> str:
        if not v:
            raise ValueError("mongo_uri is required")
        return v

    @field_validator("aes_secret_key")
    @classmethod
    def aes_secret_key_required(cls, v: str) -> str:
        if len(v.encode("utf-8")) != 32:
            raise ValueError("aes_secret_key must be exactly 32 bytes")
        return v

    @model_validator(mode="after")
    def _ai_config_complete(self):
        """AI_ENABLED=true without a key/model fails at startup, naming the variable (never its value)."""
        if self.ai_enabled:
            missing = [n for n, v in (("GROQ_API_KEY", self.groq_api_key.get_secret_value()),
                                      ("GROQ_MODEL", self.groq_model)) if not v]
            if missing:
                raise ValueError("AI_ENABLED=true but " + " and ".join(missing) + " must be set")
        return self

settings = Settings()
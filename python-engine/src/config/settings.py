from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file='.env',
        env_file_encoding='utf-8',
        extra='ignore',
        validate_default=True,
        str_strip_whitespace=True
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

settings = Settings()
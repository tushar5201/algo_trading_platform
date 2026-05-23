from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file='.env',
        env_file_encoding='utf-8',
        extra='ignore'
    )
    # App
    log_level: str = 'INFO'
    node_env: str = 'production'

    # MongoDB
    mongo_uri: str = 'mongodb://tusharlakadiya:tushar5201@ac-roqb5vs-shard-00-00.srme8ol.mongodb.net:27017,ac-roqb5vs-shard-00-01.srme8ol.mongodb.net:27017,ac-roqb5vs-shard-00-02.srme8ol.mongodb.net:27017/trade-net?ssl=true&replicaSet=atlas-1ujkup-shard-0&authSource=admin&appName=Cluster0'

    # Redis
    redis_host: str = "redis"
    redis_port: int = 6379

    # AES
    aes_secret_key: str = "12345678901234567890123456789012"

    # Kotak Neo
    kotak_base_url: str = "https://gw-napi.kotaksecurities.com"

settings = Settings()
